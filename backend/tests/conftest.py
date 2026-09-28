import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import Settings


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(database_url="sqlite://", media_dir=tmp_path / "media", session_secret="test-secret")


@pytest.fixture
def client(settings) -> TestClient:
    app = create_app(settings)
    return TestClient(app)
