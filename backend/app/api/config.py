from fastapi import APIRouter, Depends

from app.api.deps import get_settings_dep
from app.profiles import PROFILES
from app.schemas import ConfigOut, ProfileOption
from app.settings import Settings

router = APIRouter(tags=["config"])


@router.get("/config", response_model=ConfigOut)
def get_config(settings: Settings = Depends(get_settings_dep)) -> ConfigOut:
    return ConfigOut(
        max_upload_bytes=settings.max_upload_bytes,
        target_bytes=settings.target_bytes,
        max_duration_s=settings.max_duration_s,
        profiles=[ProfileOption(name=p.name, label=p.label) for p in PROFILES.values()],
        default_profile=settings.default_profile,
    )
