from collections.abc import Iterator

from sqlmodel import Session, SQLModel, create_engine

from app.settings import get_settings

engine = create_engine(get_settings().database_url, pool_pre_ping=True)


def init_db() -> None:
    from app import models  # noqa: F401  # 등록 보장

    SQLModel.metadata.create_all(engine)


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
