import logging
import time
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm.exc import ObjectDeletedError, StaleDataError
from sqlmodel import Session, select

from app.converge import converge
from app.db import engine, init_db
from app.ffmpeg import encode_once
from app.models import Job, JobStatus
from app.paths import MediaPaths
from app.plan import plan_encode
from app.probe import ProbeError, probe
from app.settings import Settings, get_settings

log = logging.getLogger("sevi.worker")


class JobCancelled(Exception):
    """진행 중 잡이 사용자 취소되었을 때 인코딩을 중단하는 신호."""


def _refresh_or_cancel(session: Session, job: Job) -> None:
    """최신 상태를 반영한다. 잡이 없어졌거나 취소됐으면 중단 신호를 던진다.

    DELETE가 대기 중 잡을 즉시 제거하므로 행이 사라진 채 워커가 붙어 있을 수 있다 —
    ObjectDeletedError 를 그대로 두면 이후 커밋이 깨진다.
    """
    try:
        session.refresh(job)
    except (ObjectDeletedError, StaleDataError) as exc:
        raise JobCancelled() from exc
    if job.status is JobStatus.cancelled:
        raise JobCancelled()


def sweep(session: Session, paths: MediaPaths, *, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    expired = session.exec(select(Job).where(Job.expires_at < now)).all()
    for job in expired:
        paths.remove_job(job.id)
        session.delete(job)
    session.commit()
    return len(expired)


def reset_stale(session: Session, paths: MediaPaths) -> int:
    stale = session.exec(
        select(Job).where(Job.status.in_([JobStatus.probing, JobStatus.encoding]))
    ).all()
    for job in stale:
        paths.remove_job(job.id)
        job.status = JobStatus.failed
        job.error = "워커 재시작으로 작업이 중단되었습니다."
        job.source_path = None
        job.result_path = None
        session.add(job)
    session.commit()
    return len(stale)


def claim_next(session: Session) -> Job | None:
    job = session.exec(
        select(Job).where(Job.status == JobStatus.queued).order_by(Job.created_at)
    ).first()
    if job is None:
        return None
    job.status = JobStatus.encoding
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def process_job(
    session: Session,
    job: Job,
    *,
    settings: Settings,
    paths: MediaPaths,
    encode_fn=None,
) -> None:
    if job.status is JobStatus.cancelled:
        return

    encode = encode_fn or encode_once
    src = paths.source(job.id)

    try:
        job.status = JobStatus.probing
        session.add(job)
        session.commit()

        info = probe(
            src,
            ffprobe_path=settings.ffprobe_path,
            max_duration_s=settings.max_duration_s,
        )
        job.duration_s = info.duration_s
        job.width = info.width
        job.height = info.height
        session.add(job)
        session.commit()

        plan = plan_encode(
            duration_s=info.duration_s,
            fps=info.fps,
            src_width=info.width,
            src_height=info.height,
            has_audio=info.has_audio,
            target_bytes=settings.target_bytes,
            min_video_kbps=settings.min_video_kbps,
        )

        dst = paths.result(job.id)

        def _run(kbps: int) -> int:
            _refresh_or_cancel(session, job)
            job.status = JobStatus.encoding
            job.attempt += 1
            job.progress = 0.0
            session.add(job)
            session.commit()
            return encode(
                src=src,
                dst=dst,
                plan=plan,
                video_kbps=kbps,
                ffmpeg_path=settings.ffmpeg_path,
                on_progress=_make_progress_cb(session, job),
            )

        result = converge(
            target_bytes=settings.target_bytes,
            initial_kbps=plan.video_kbps,
            encode=_run,
            max_attempts=settings.max_attempts,
            floor_kbps=settings.min_video_kbps,
        )

        job.result_path = str(dst)
        job.result_bytes = result.result_bytes
        job.progress = 1.0
        if result.result_bytes <= settings.target_bytes:
            job.status = JobStatus.done
            job.error = None
        else:
            job.status = JobStatus.done_oversize
            job.error = (
                f"목표 {settings.target_bytes // 1_000_000}MB를 "
                f"{(result.result_bytes - settings.target_bytes) // 1_000_000}MB 초과했습니다."
            )

    except JobCancelled:
        job.status = JobStatus.cancelled
        job.error = "사용자가 취소했습니다."
    except ProbeError as exc:
        job.status = JobStatus.failed
        job.error = str(exc)
    except Exception as exc:  # noqa: BLE001 — 워커는 어떤 실패도 잡아야 함
        log.exception("job %s failed", job.id)
        job.status = JobStatus.failed
        job.error = "인코딩 중 오류가 발생했습니다."
    finally:
        src.unlink(missing_ok=True)
        if job.status is JobStatus.cancelled:
            paths.result(job.id).unlink(missing_ok=True)
        job.source_path = None
        try:
            session.add(job)
            session.commit()
        except (ObjectDeletedError, StaleDataError):
            # DELETE가 이미 행을 지운 경우 — 워커는 살아남아야 한다.
            session.rollback()
            paths.result(job.id).unlink(missing_ok=True)


def _make_progress_cb(session: Session, job: Job):
    last = {"t": 0.0}

    def cb(fraction: float) -> None:
        now = time.monotonic()
        if now - last["t"] < 1.0 and fraction < 1.0:
            return
        last["t"] = now
        _refresh_or_cancel(session, job)
        job.progress = max(0.0, min(1.0, fraction))
        session.add(job)
        session.commit()

    return cb


def tick(session: Session, paths: MediaPaths, settings: Settings) -> None:
    try:
        removed = sweep(session, paths)
        if removed:
            log.info("swept %d expired jobs", removed)
        job = claim_next(session)
        if job is not None:
            log.info("processing job %s", job.id)
            process_job(session, job, settings=settings, paths=paths)
    except Exception:  # noqa: BLE001
        log.exception("worker tick failed")
        try:
            session.rollback()
        except Exception:  # noqa: BLE001
            log.exception("session rollback failed")


def run_forever(settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    logging.basicConfig(level=settings.log_level)
    init_db()
    paths = MediaPaths(settings.media_dir)
    settings.media_dir.mkdir(parents=True, exist_ok=True)
    log.info("worker started; media_dir=%s", settings.media_dir)

    with Session(engine) as session:
        stale = reset_stale(session, paths)
        if stale:
            log.info("reset %d stale in-flight jobs", stale)
        while True:
            tick(session, paths, settings)
            time.sleep(settings.worker_poll_s)


if __name__ == "__main__":
    run_forever()
