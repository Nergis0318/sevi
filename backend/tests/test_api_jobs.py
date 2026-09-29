import io
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.db import get_session
from app.main import create_app
from app.models import Job, JobStatus
from app.paths import MediaPaths
from app.settings import Settings


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        database_url="sqlite://",
        media_dir=tmp_path / "media",
        session_secret="test-secret",
        max_upload_bytes=1024 * 1024,
        rate_limit_per_hour=100,
    )


@pytest.fixture
def client(settings):
    engine = create_engine(
        settings.database_url,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def _override():
        with Session(engine) as s:
            yield s

    app = create_app(settings)
    app.dependency_overrides[get_session] = _override
    with TestClient(app) as c:
        c.engine = engine
        yield c


def _upload(client, name="clip.mp4", data=b"fake-video-bytes"):
    return client.post("/api/jobs", files={"file": (name, io.BytesIO(data), "video/mp4")})


def test_config_endpoint(client):
    r = client.get("/api/config")
    assert r.status_code == 200
    body = r.json()
    assert body["target_bytes"] == 49_900_000
    assert body["max_upload_bytes"] == 1024 * 1024


def test_upload_creates_job_and_sets_cookie(client):
    r = _upload(client)
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "queued"
    assert "sv_session" in r.cookies


def test_upload_then_get_status(client):
    job_id = _upload(client).json()["id"]
    r = client.get(f"/api/jobs/{job_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "queued"
    assert body["source_name"] == "clip.mp4"
    assert body["source_bytes"] == len(b"fake-video-bytes")
    assert body["progress"] == 0.0


def test_download_requires_done_status(client):
    job_id = _upload(client).json()["id"]
    r = client.get(f"/api/jobs/{job_id}/download")
    assert r.status_code == 409


def test_foreign_session_gets_404(client):
    job_id = _upload(client).json()["id"]
    client.cookies.clear()
    r = client.get(f"/api/jobs/{job_id}")
    assert r.status_code == 404


def test_oversize_upload_rejected(settings):
    engine = create_engine(
        settings.database_url, connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)

    def _override():
        with Session(engine) as s:
            yield s

    app = create_app(settings)
    app.dependency_overrides[get_session] = _override
    with TestClient(app) as c:
        big = b"x" * (settings.max_upload_bytes + 1)
        r = c.post("/api/jobs", files={"file": ("big.mp4", io.BytesIO(big), "video/mp4")})
        assert r.status_code == 413


def test_empty_upload_rejected(client):
    r = _upload(client, data=b"")
    assert r.status_code == 400


def test_rate_limit_returns_429(tmp_path):
    settings = Settings(
        database_url="sqlite://",
        media_dir=tmp_path / "media",
        session_secret="test-secret",
        rate_limit_per_hour=1,
    )
    engine = create_engine(
        settings.database_url,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def _override():
        with Session(engine) as s:
            yield s

    app = create_app(settings)
    app.dependency_overrides[get_session] = _override
    with TestClient(app) as c:
        assert c.post("/api/jobs", files={"file": ("a.mp4", io.BytesIO(b"x"), "video/mp4")}).status_code == 201
        r = c.post("/api/jobs", files={"file": ("b.mp4", io.BytesIO(b"x"), "video/mp4")})
        assert r.status_code == 429
        assert "Retry-After" in r.headers


def test_rate_limit_honours_settings(tmp_path):
    """Settings.rate_limit_per_hour 가 실제로 적용되어야 한다 — 데드 노브 회귀 가드."""
    for per_hour, expect_second in ((2, 201), (1, 429)):
        settings = Settings(
            database_url="sqlite://",
            media_dir=tmp_path / f"m{per_hour}",
            session_secret="test-secret",
            rate_limit_per_hour=per_hour,
        )
        engine = create_engine(
            settings.database_url,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(engine)

        def _override():
            with Session(engine) as s:
                yield s

        app = create_app(settings)
        app.dependency_overrides[get_session] = _override
        with TestClient(app) as c:
            first = c.post("/api/jobs", files={"file": ("a.mp4", io.BytesIO(b"x"), "video/mp4")})
            assert first.status_code == 201
            c.delete(f"/api/jobs/{first.json()['id']}")  # 활성 슬롯 해제 — 리밋만 검사
            r = c.post("/api/jobs", files={"file": ("b.mp4", io.BytesIO(b"x"), "video/mp4")})
            assert r.status_code == expect_second


def test_active_slot_blocks_second_upload(settings):
    """진행 중 잡이 있으면 같은 세션에서 추가 업로드가 거부된다 (DB 기반)."""
    engine = create_engine(
        settings.database_url, connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)

    def _override():
        with Session(engine) as s:
            yield s

    app = create_app(settings)
    app.dependency_overrides[get_session] = _override
    with TestClient(app) as c:
        first = c.post("/api/jobs", files={"file": ("a.mp4", io.BytesIO(b"x"), "video/mp4")})
        assert first.status_code == 201
        second = c.post("/api/jobs", files={"file": ("b.mp4", io.BytesIO(b"x"), "video/mp4")})
        assert second.status_code == 429
        assert "진행 중" in second.json()["detail"]


def test_ip_rate_limit_survives_cookie_discard(tmp_path):
    """쿠키를 매번 버려도 IP 리밋은 남는다 — 세션 리밋만으로는 무제한 업로드가 된다."""
    settings = Settings(
        database_url="sqlite://",
        media_dir=tmp_path / "media",
        session_secret="test-secret",
        rate_limit_per_hour=100,
        rate_limit_per_hour_per_ip=1,
        max_queued_jobs=100,
    )
    engine = create_engine(
        settings.database_url,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def _override():
        with Session(engine) as s:
            yield s

    app = create_app(settings)
    app.dependency_overrides[get_session] = _override
    with TestClient(app) as c:
        assert c.post("/api/jobs", files={"file": ("a.mp4", io.BytesIO(b"x"), "video/mp4")}).status_code == 201
        c.cookies.clear()  # 새 세션을 받지만 IP 는 같다
        r2 = c.post("/api/jobs", files={"file": ("b.mp4", io.BytesIO(b"x"), "video/mp4")})
        assert r2.status_code == 429
        assert "Retry-After" in r2.headers


def test_session_rate_limit_still_applies(tmp_path):
    """세션 리밋은 별도로 유지된다 — 공유 NAT 아래서도 한 사람이 대량 업로드 못 함."""
    settings = Settings(
        database_url="sqlite://",
        media_dir=tmp_path / "media",
        session_secret="test-secret",
        rate_limit_per_hour=1,
        rate_limit_per_hour_per_ip=100,
        max_queued_jobs=100,
    )
    engine = create_engine(
        settings.database_url,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def _override():
        with Session(engine) as s:
            yield s

    app = create_app(settings)
    app.dependency_overrides[get_session] = _override
    with TestClient(app) as c:
        first = c.post("/api/jobs", files={"file": ("a.mp4", io.BytesIO(b"x"), "video/mp4")})
        assert first.status_code == 201
        c.delete(f"/api/jobs/{first.json()['id']}")  # 활성 슬롯 해제 — 세션 리밋만 검사
        assert c.post("/api/jobs", files={"file": ("b.mp4", io.BytesIO(b"x"), "video/mp4")}).status_code == 429


def test_delete_queued_hard_deletes(tmp_path):
    """queued 는 아직 워커가 붙지 않아 행과 파일을 즉시 제거한다.

    소프트 취소로 넘기면 파일이 만료 스윕까지 남고 count_queued 상한이 뚫린다.
    """
    settings = Settings(database_url="sqlite://", media_dir=tmp_path / "media", session_secret="t")
    engine = create_engine(
        settings.database_url,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def _override():
        with Session(engine) as s:
            yield s

    app = create_app(settings)
    app.dependency_overrides[get_session] = _override
    with TestClient(app) as c:
        job_id = c.post("/api/jobs", files={"file": ("a.mp4", io.BytesIO(b"x"), "video/mp4")}).json()["id"]
        paths = MediaPaths(settings.media_dir)
        assert paths.source(UUID(job_id)).is_file()
        r = c.delete(f"/api/jobs/{job_id}")
        assert r.status_code == 204
        assert not paths.job_dir(UUID(job_id)).exists()


def test_delete_in_flight_marks_cancelled_not_deleted(tmp_path):
    """probing/encoding 은 워커가 붙어 있으므로 소프트 취소하고 행을 남긴다."""
    settings = Settings(database_url="sqlite://", media_dir=tmp_path / "media", session_secret="t")
    engine = create_engine(
        settings.database_url,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def _override():
        with Session(engine) as s:
            yield s

    app = create_app(settings)
    app.dependency_overrides[get_session] = _override
    with TestClient(app) as c:
        job_id = c.post("/api/jobs", files={"file": ("a.mp4", io.BytesIO(b"x"), "video/mp4")}).json()["id"]
        with Session(engine) as s:
            job = s.get(Job, UUID(job_id))
            job.status = JobStatus.encoding
            s.add(job)
            s.commit()
        paths = MediaPaths(settings.media_dir)
        r = c.delete(f"/api/jobs/{job_id}")
        assert r.status_code == 202
        with Session(engine) as s:
            job = s.get(Job, UUID(job_id))
            assert job is not None and job.status is JobStatus.cancelled
        assert paths.source(UUID(job_id)).exists()  # 워커가 정리한다


def test_delete_terminal_job_removes_row(tmp_path):
    settings = Settings(database_url="sqlite://", media_dir=tmp_path / "media", session_secret="t")
    engine = create_engine(
        settings.database_url,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def _override():
        with Session(engine) as s:
            yield s

    app = create_app(settings)
    app.dependency_overrides[get_session] = _override
    with TestClient(app) as c:
        job_id = c.post("/api/jobs", files={"file": ("a.mp4", io.BytesIO(b"x"), "video/mp4")}).json()["id"]
        with Session(engine) as s:
            job = s.get(Job, UUID(job_id))
            job.status = JobStatus.done
            s.add(job)
            s.commit()
        r = c.delete(f"/api/jobs/{job_id}")
        assert r.status_code == 204
        with Session(engine) as s:
            assert s.get(Job, UUID(job_id)) is None


def test_global_queue_cap(tmp_path):
    """전역 대기 큐 상한 — 세션을 바꿔도 디스크 포화 공격이 불가능하다."""
    settings = Settings(
        database_url="sqlite://",
        media_dir=tmp_path / "media",
        session_secret="test-secret",
        rate_limit_per_hour=100,
        max_queued_jobs=1,
    )
    engine = create_engine(
        settings.database_url,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def _override():
        with Session(engine) as s:
            yield s

    app = create_app(settings)
    app.dependency_overrides[get_session] = _override
    with TestClient(app) as c:
        assert c.post("/api/jobs", files={"file": ("a.mp4", io.BytesIO(b"x"), "video/mp4")}).status_code == 201
        c.cookies.clear()  # 완전히 다른 세션
        r2 = c.post("/api/jobs", files={"file": ("b.mp4", io.BytesIO(b"x"), "video/mp4")})
        assert r2.status_code == 429
        assert r2.headers["Retry-After"] == "60"
