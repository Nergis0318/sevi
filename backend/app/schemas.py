from pydantic import BaseModel


class JobCreated(BaseModel):
    id: str
    status: str


class JobStatusOut(BaseModel):
    id: str
    status: str
    attempt: int
    progress: float
    source_name: str
    source_bytes: int | None = None
    result_bytes: int | None = None
    duration_s: float | None = None
    error: str | None = None
    expires_at: str


class ProfileOption(BaseModel):
    name: str
    label: str


class ConfigOut(BaseModel):
    max_upload_bytes: int
    target_bytes: int
    max_duration_s: float
    profiles: list[ProfileOption]
    default_profile: str
