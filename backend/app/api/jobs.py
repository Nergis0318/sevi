import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse
from sqlmodel import Session, select

from app.api.deps import client_ip, get_ip_limiter, get_limiter, get_settings_dep, require_session
from app.db import get_session
from app.models import Job, JobStatus, count_active, count_queued
from app.paths import MediaPaths
from app.profiles import PROFILES
from app.ratelimit import RateLimitError, RateLimiter
from app.schemas import JobCreated, JobStatusOut
from app.settings import Settings

router = APIRouter(tags=["jobs"])


def _paths(settings: Settings) -> MediaPaths:
    return MediaPaths(settings.media_dir)


def _to_out(job: Job) -> JobStatusOut:
    return JobStatusOut(
        id=str(job.id),
        status=job.status.value,
        attempt=job.attempt,
        progress=job.progress,
        source_name=job.source_name,
        source_bytes=job.source_bytes,
        result_bytes=job.result_bytes,
        duration_s=job.duration_s,
        error=job.error,
        expires_at=job.expires_at.isoformat(),
    )


def _get_owned(job_id: uuid.UUID, session_id: str, session: Session) -> Job:
    job = session.get(Job, job_id)
    if job is None or job.session_id != session_id:
        raise HTTPException(status_code=404, detail="작업을 찾을 수 없습니다.")
    return job


@router.post("/jobs", response_model=JobCreated, status_code=201)
async def create_job(
    request: Request,
    file: UploadFile,
    profile: str | None = Form(None),
    session_id: str = Depends(require_session),
    settings: Settings = Depends(get_settings_dep),
    limiter: RateLimiter = Depends(get_limiter),
    ip_limiter: RateLimiter = Depends(get_ip_limiter),
    session: Session = Depends(get_session),
) -> JobCreated:
    profile_name = profile or settings.default_profile
    if profile_name not in PROFILES:
        raise HTTPException(status_code=400, detail="지원하지 않는 프로필입니다.")
    ip = client_ip(request)
    try:
        limiter.check_upload(session_id)
        ip_limiter.check_upload(ip)
    except RateLimitError as exc:
        raise HTTPException(
            status_code=429,
            detail=str(exc),
            headers={"Retry-After": str(exc.retry_after_s or 60)},
        ) from exc

    if count_active(session, session_id) >= 1:
        raise HTTPException(
            status_code=429,
            detail="이미 진행 중인 작업이 있습니다. 완료 후 다시 시도해 주세요.",
            headers={"Retry-After": "30"},
        )

    if count_queued(session) >= settings.max_queued_jobs:
        raise HTTPException(
            status_code=429,
            detail="현재 대기 중인 작업이 많습니다. 잠시 후 다시 시도해 주세요.",
            headers={"Retry-After": "60"},
        )

    job_id = uuid.uuid4()
    paths = _paths(settings)
    paths.prepare(job_id)
    dest = paths.source(job_id)

    size = 0
    try:
        with dest.open("wb") as out:
            while chunk := await file.read(1 << 20):
                size += len(chunk)
                if size > settings.max_upload_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail=f"업로드 한도({settings.max_upload_bytes // (1024 * 1024)}MB)를 초과했습니다.",
                    )
                out.write(chunk)
        if size == 0:
            raise HTTPException(status_code=400, detail="빈 파일은 업로드할 수 없습니다.")
    except BaseException:
        paths.remove_job(job_id)
        raise

    now = datetime.now(timezone.utc)
    job = Job(
        id=job_id,
        session_id=session_id,
        status=JobStatus.queued,
        profile=profile_name,
        source_name=(file.filename or "video")[:200],
        source_path=str(dest),
        source_bytes=size,
        created_at=now,
        expires_at=now + timedelta(hours=settings.retention_hours),
    )
    session.add(job)
    session.commit()
    return JobCreated(id=str(job.id), status=job.status.value)


@router.get("/jobs/{job_id}", response_model=JobStatusOut)
def get_job(
    job_id: uuid.UUID,
    session_id: str = Depends(require_session),
    session: Session = Depends(get_session),
) -> JobStatusOut:
    return _to_out(_get_owned(job_id, session_id, session))


@router.get("/jobs/{job_id}/download")
def download_job(
    job_id: uuid.UUID,
    session_id: str = Depends(require_session),
    session: Session = Depends(get_session),
) -> FileResponse:
    job = _get_owned(job_id, session_id, session)
    if job.status not in (JobStatus.done, JobStatus.done_oversize):
        raise HTTPException(status_code=409, detail="아직 완료되지 않은 작업입니다.")
    if not job.result_path:
        raise HTTPException(status_code=404, detail="결과 파일이 없습니다.")
    return FileResponse(
        job.result_path,
        media_type="video/webm",
        filename=f"compressed_{job.id}.webm",
    )


@router.delete("/jobs/{job_id}", status_code=204)
def delete_job(
    job_id: uuid.UUID,
    session_id: str = Depends(require_session),
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> Response:
    job = _get_owned(job_id, session_id, session)
    if job.status in (JobStatus.probing, JobStatus.encoding):
        job.status = JobStatus.cancelled
        job.error = "사용자가 취소했습니다."
        session.add(job)
        session.commit()
        return Response(status_code=202)
    # queued 이거나 종료 상태 — 아무도 읽지 않으므로 행과 파일을 즉시 제거한다.
    # 소프트 취소로 넘기면 파일이 24h 만료 스윕까지 남고 count_queued 상한이 뚫린다.
    _paths(settings).remove_job(job_id)
    session.delete(job)
    session.commit()
    return Response(status_code=204)
