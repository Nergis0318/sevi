import uuid
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, SQLModel, create_engine, select

from app.models import Job, JobStatus


def test_job_roundtrip():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    now = datetime.now(timezone.utc)
    job = Job(
        id=uuid.uuid4(),
        session_id="abc123",
        status=JobStatus.queued,
        source_name="holiday.mp4",
        created_at=now,
        expires_at=now + timedelta(hours=24),
    )
    with Session(engine) as s:
        s.add(job)
        s.commit()
        got = s.exec(select(Job)).one()
        assert got.status is JobStatus.queued
        assert got.session_id == "abc123"
        assert got.attempt == 0
        assert got.progress == 0.0


def test_job_status_values():
    assert JobStatus.queued.value == "queued"
    assert JobStatus.done_oversize.value == "done_oversize"
    assert JobStatus.cancelled.value == "cancelled"


def test_count_active_counts_only_live_states():
    from datetime import timedelta

    from app.models import count_active

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    now = datetime.now(timezone.utc)
    with Session(engine) as s:
        for st in (JobStatus.queued, JobStatus.probing, JobStatus.encoding,
                   JobStatus.done, JobStatus.failed, JobStatus.cancelled, JobStatus.done_oversize):
            s.add(Job(session_id="sid", status=st, created_at=now,
                      expires_at=now + timedelta(hours=24)))
        s.add(Job(session_id="other", status=JobStatus.queued, created_at=now,
                  expires_at=now + timedelta(hours=24)))
        s.commit()
        assert count_active(s, "sid") == 3
        assert count_active(s, "other") == 1
        assert count_active(s, "nobody") == 0
