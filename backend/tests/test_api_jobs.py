import io
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.db import get_session
from app.main import create_app
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


def test_delete_removes_job_and_files(client, settings):
    job_id = _upload(client).json()["id"]
    paths = MediaPaths(settings.media_dir)
    assert paths.source(UUID(job_id)).is_file()
    r = client.delete(f"/api/jobs/{job_id}")
    assert r.status_code == 204
    assert not paths.job_dir(UUID(job_id)).exists()
    assert client.get(f"/api/jobs/{job_id}").status_code == 404


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
