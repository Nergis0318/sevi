from dataclasses import dataclass


@dataclass(frozen=True)
class Profile:
    name: str
    label: str
    preset: str  # vp9_qsv -preset (target usage)
    max_attempts: int  # converge 시도 횟수
    bpp_target: float  # 해상도 사다리 하한 bpp
    maxrate_mult: float
    bufsize_mult: float
    audio_kbps: int
    tile_cols: int
    tile_rows: int
    scale_flags: str
    denoise: str  # hqdn3d 인자 문자열, "" 이면 비활성


PROFILES: dict[str, Profile] = {
    p.name: p
    for p in (
        Profile(
            name="fast",
            label="빠름",
            preset="fast",
            max_attempts=1,
            bpp_target=0.075,
            maxrate_mult=1.3,
            bufsize_mult=2.0,
            audio_kbps=64,
            tile_cols=2,
            tile_rows=1,
            scale_flags="bilinear",
            denoise="",
        ),
        Profile(
            name="balanced",
            label="균형",
            preset="medium",
            max_attempts=3,
            bpp_target=0.06,
            maxrate_mult=1.2,
            bufsize_mult=2.0,
            audio_kbps=96,
            tile_cols=0,
            tile_rows=0,
            scale_flags="bicubic",
            denoise="",
        ),
        Profile(
            name="quality",
            label="품질",
            preset="slow",
            max_attempts=3,
            bpp_target=0.045,
            maxrate_mult=1.1,
            bufsize_mult=2.0,
            audio_kbps=128,
            tile_cols=0,
            tile_rows=0,
            scale_flags="lanczos",
            denoise="hqdn3d=4:3:6:4.5",
        ),
    )
}

DEFAULT_PROFILE = "balanced"
