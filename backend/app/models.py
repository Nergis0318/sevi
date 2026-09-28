import enum
import uuid
from datetime import datetime, timezone

from sqlmodel import Field, Session, SQLModel


class JobStatus(str, enum.Enum):
    queued = "queued"
    probing = "probing"
    encoding = "encoding"
    done = "done"
    done_oversize = "done_oversize"
    failed = "failed"
    cancelled = "cancelled"


class Job(SQLModel, table=True):
    __tablename__ = "jobs"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    session_id: str = Field(index=True)
    status: JobStatus = Field(default=JobStatus.queued, index=True)
    attempt: int = Field(default=0)
    progress: float = Field(default=0.0)
    source_name: str = Field(default="")
    source_path: str | None = Field(default=None)
    result_path: str | None = Field(default=None)
    source_bytes: int | None = Field(default=None)
    result_bytes: int | None = Field(default=None)
    duration_s: float | None = Field(default=None)
    width: int | None = Field(default=None)
    height: int | None = Field(default=None)
    error: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime


ACTIVE_STATES = (JobStatus.queued, JobStatus.probing, JobStatus.encoding)


def count_active(session: Session, session_id: str) -> int:
    """세션의 진행 중 잡 수. 활성 슬롯의 유일한 진실 원천."""
    from sqlmodel import func, select

    stmt = (
        select(func.count())
        .select_from(Job)
        .where(Job.session_id == session_id, Job.status.in_(ACTIVE_STATES))
    )
    return int(session.exec(stmt).one())


def count_queued(session: Session) -> int:
    """전역 대기 중 잡 수 — 디스크 포화 방지용 상한."""
    from sqlmodel import func, select

    stmt = select(func.count()).select_from(Job).where(Job.status == JobStatus.queued)
    return int(session.exec(stmt).one())
