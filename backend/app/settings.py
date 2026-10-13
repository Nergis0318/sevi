from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.profiles import DEFAULT_PROFILE


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SEVI_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://sevi:sevi@localhost:5432/sevi"
    media_dir: Path = Path("/tmp/sevi-media")
    target_bytes: int = 49_900_000
    max_upload_bytes: int = 800 * 1024 * 1024
    max_duration_s: float = 21600.0
    retention_hours: int = 24
    session_secret: str = "dev-secret-change-me"
    session_cookie: str = "sv_session"
    session_ttl_days: int = 7
    rate_limit_per_hour: int = 5
    rate_limit_per_hour_per_ip: int = 20
    max_queued_jobs: int = 20
    min_video_kbps: int = 80
    default_profile: str = DEFAULT_PROFILE
    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"
    worker_poll_s: float = 1.0
    log_level: str = "INFO"


def get_settings() -> Settings:
    return Settings()
