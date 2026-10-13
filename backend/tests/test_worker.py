import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import Job, JobStatus
from app.paths import MediaPaths
from app.plan import EncodePlan
from app.probe import ProbeResult
from app.settings import Settings
from app.worker import claim_next, process_job, sweep


@pytest.fixture
def engine():
    e = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(e)
    return e


@pytest.fixture
def session(engine):
    with Session(engine) as s:
        yield s


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(database_url="sqlite://", media_dir=tmp_path / "media", session_secret="t")


@pytest.fixture
def paths(settings) -> MediaPaths:
    return MediaPaths(settings.media_dir)


def _make_job(session: Session, paths: MediaPaths, **kw) -> Job:
    jid = uuid.uuid4()
    paths.prepare(jid)
    paths.source(jid).write_bytes(b"fake")
    now = datetime.now(timezone.utc)
    job = Job(
        id=jid,
        session_id=kw.pop("session_id", "sid"),
        status=kw.pop("status", JobStatus.queued),
        source_path=str(paths.source(jid)),
        source_bytes=4,
        created_at=kw.pop("created_at", now),
        expires_at=kw.pop("expires_at", now + timedelta(hours=24)),
        **kw,
    )
    session.add(job)
    session.commit()
    return job


def test_claim_next_picks_oldest_queued(session, paths):
    j1 = _make_job(session, paths, created_at=datetime.now(timezone.utc) - timedelta(minutes=2))
    j2 = _make_job(session, paths)
    first = claim_next(session)
    assert first is not None and first.id == j1.id
    assert first.status is JobStatus.encoding
    second = claim_next(session)
    assert second is not None and second.id == j2.id


def test_claim_next_ignores_non_queued(session, paths):
    _make_job(session, paths, status=JobStatus.done)
    assert claim_next(session) is None


def test_sweep_removes_expired(session, paths):
    old = _make_job(session, paths, expires_at=datetime.now(timezone.utc) - timedelta(hours=1))
    fresh = _make_job(session, paths)
    removed = sweep(session, paths)
    assert removed == 1
    assert session.get(Job, old.id) is None
    assert session.get(Job, fresh.id) is not None
    assert not paths.job_dir(old.id).exists()
    assert paths.job_dir(fresh.id).exists()


def test_process_job_success(session, paths, settings, monkeypatch):
    job = _make_job(session, paths)

    monkeypatch.setattr(
        "app.worker.probe",
        lambda *a, **k: ProbeResult(duration_s=2.0, width=640, height=360, fps=30.0, has_audio=True),
    )
    monkeypatch.setattr(
        "app.worker.plan_encode",
        lambda **k: EncodePlan(video_kbps=500, audio_kbps=96, height=360, has_audio=True, fps=30.0, duration_s=2.0),
    )
    def fake_converge(**k):
        k["encode"](k["initial_kbps"])
        return type("R", (), {"video_kbps": 500, "attempts": 1, "result_bytes": 1234, "converged": True})()

    monkeypatch.setattr("app.worker.converge", fake_converge)

    def fake_encode(**kwargs):
        dst = kwargs["dst"]
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(b"r" * 1234)
        return 1234

    monkeypatch.setattr("app.worker.encode_once", fake_encode)

    process_job(session, job, settings=settings, paths=paths)
    session.refresh(job)

    assert job.status is JobStatus.done
    assert job.result_bytes == 1234
    assert job.width == 640 and job.height == 360
    assert job.source_path is None  # 원본은 즉시 삭제
    assert not paths.source(job.id).exists()
    assert paths.result(job.id).exists()


def test_process_job_oversize_marks_done_oversize(session, paths, settings, monkeypatch):
    job = _make_job(session, paths)
    monkeypatch.setattr(
        "app.worker.probe",
        lambda *a, **k: ProbeResult(duration_s=2.0, width=640, height=360, fps=30.0, has_audio=False),
    )
    monkeypatch.setattr(
        "app.worker.plan_encode",
        lambda **k: EncodePlan(video_kbps=80, audio_kbps=0, height=240, has_audio=False, fps=30.0, duration_s=2.0),
    )
    monkeypatch.setattr(
        "app.worker.converge",
        lambda **k: type("R", (), {"video_kbps": 80, "attempts": 3, "result_bytes": 60_000_000, "converged": False})(),
    )
    def fake_encode_oversize(**kwargs):
        kwargs["dst"].parent.mkdir(parents=True, exist_ok=True)
        kwargs["dst"].write_bytes(b"x")
        return 60_000_000

    monkeypatch.setattr("app.worker.encode_once", fake_encode_oversize)
    process_job(session, job, settings=settings, paths=paths)
    session.refresh(job)
    assert job.status is JobStatus.done_oversize
    assert job.error is not None


def test_process_job_probe_failure(session, paths, settings, monkeypatch):
    job = _make_job(session, paths)

    def boom(*a, **k):
        from app.probe import ProbeError

        raise ProbeError("비디오 스트림을 찾을 수 없습니다.")

    monkeypatch.setattr("app.worker.probe", boom)
    process_job(session, job, settings=settings, paths=paths)
    session.refresh(job)
    assert job.status is JobStatus.failed
    assert "비디오" in (job.error or "")
    assert not paths.source(job.id).exists()


def test_process_job_marks_cancelled_if_cancelled_midflight(session, paths, settings):
    job = _make_job(session, paths, status=JobStatus.cancelled)
    process_job(session, job, settings=settings, paths=paths)
    session.refresh(job)
    assert job.status is JobStatus.cancelled


def test_process_job_stops_on_cancel(session, paths, settings, monkeypatch):
    """진행 콜백 중에 취소되면 인코딩을 멈추고 부분 결과를 정리한다."""
    job = _make_job(session, paths)
    monkeypatch.setattr(
        "app.worker.probe",
        lambda *a, **k: ProbeResult(duration_s=2.0, width=640, height=360, fps=30.0, has_audio=True),
    )
    monkeypatch.setattr(
        "app.worker.plan_encode",
        lambda **k: EncodePlan(video_kbps=500, audio_kbps=96, height=360, has_audio=True, fps=30.0, duration_s=2.0),
    )

    def fake_encode(**kwargs):
        cb = kwargs["on_progress"]
        dst = kwargs["dst"]
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(b"partial")

        def cb_cancel(fraction: float) -> None:
            job.status = JobStatus.cancelled  # API DELETE 와 동일한 시점의 취소
            session.commit()
            cb(fraction)  # 워커 콜백이 refresh 로 취소를 감지해야 한다

        cb_cancel(0.5)
        raise AssertionError("encode must stop after cancel")

    process_job(session, job, settings=settings, paths=paths, encode_fn=fake_encode)
    session.refresh(job)
    assert job.status is JobStatus.cancelled
    assert job.error == "사용자가 취소했습니다."
    assert not paths.source(job.id).exists()
    assert not paths.result(job.id).exists()


def test_run_forever_survives_deleted_row(session, paths, settings, monkeypatch):
    """틱이 ObjectDeletedError 를 삼키고 롤백한 뒤 다음 틱으로 계속 진행한다."""
    from unittest import mock

    from sqlalchemy.orm.exc import ObjectDeletedError

    from app.worker import tick

    _make_job(session, paths)

    def boom(*a, **k):
        raise ObjectDeletedError(None, "row deleted")

    monkeypatch.setattr("app.worker.process_job", boom)
    with mock.patch.object(session, "rollback", wraps=session.rollback) as rb:
        tick(session, paths, settings)  # raise 되면 안 된다
    assert rb.called


def test_reset_stale_fails_stale_inflight_and_cleans_files(session, paths):
    from app.worker import reset_stale

    encoding = _make_job(session, paths, status=JobStatus.encoding)
    probing = _make_job(session, paths, status=JobStatus.probing)
    queued = _make_job(session, paths, status=JobStatus.queued)
    done = _make_job(session, paths, status=JobStatus.done)

    assert reset_stale(session, paths) == 2

    for job in (encoding, probing):
        session.refresh(job)
        assert job.status is JobStatus.failed
        assert job.error is not None
        assert job.source_path is None
        assert not paths.job_dir(job.id).exists()
    session.refresh(queued)
    assert queued.status is JobStatus.queued
    session.refresh(done)
    assert done.status is JobStatus.done
    assert paths.job_dir(done.id).exists()


def test_process_job_passes_profile_params(session, paths, settings, monkeypatch):
    from app.profiles import PROFILES

    job = _make_job(session, paths, profile="fast")
    captured = {}
    monkeypatch.setattr(
        "app.worker.probe",
        lambda *a, **k: ProbeResult(duration_s=2.0, width=640, height=360, fps=30.0, has_audio=True),
    )

    def fake_plan_encode(**k):
        captured["plan_kwargs"] = k
        return EncodePlan(video_kbps=500, audio_kbps=96, height=360, has_audio=True, fps=30.0, duration_s=2.0)

    monkeypatch.setattr("app.worker.plan_encode", fake_plan_encode)

    def fake_converge(**k):
        captured["max_attempts"] = k["max_attempts"]
        k["encode"](k["initial_kbps"])
        return type("R", (), {"video_kbps": 500, "attempts": 1, "result_bytes": 1234, "converged": True})()

    monkeypatch.setattr("app.worker.converge", fake_converge)

    def fake_encode(**kwargs):
        captured["profile"] = kwargs["profile"]
        kwargs["dst"].parent.mkdir(parents=True, exist_ok=True)
        kwargs["dst"].write_bytes(b"r")
        return 1234

    monkeypatch.setattr("app.worker.encode_once", fake_encode)
    process_job(session, job, settings=settings, paths=paths)
    session.refresh(job)

    assert job.status is JobStatus.done
    assert captured["profile"] is PROFILES["fast"]
    assert captured["max_attempts"] == 1
    assert captured["plan_kwargs"]["bpp_target"] == 0.075
    assert captured["plan_kwargs"]["audio_default_kbps"] == 64
