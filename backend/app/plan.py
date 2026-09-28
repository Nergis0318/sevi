from dataclasses import dataclass

BPP_TARGET = 0.06
LADDER: tuple[int, ...] = (2160, 1440, 1080, 720, 480, 360, 240)
CONTAINER_MARGIN = 0.97
AUDIO_SHARE_CAP = 0.12
AUDIO_DEFAULT_KBPS = 96
AUDIO_MIN_KBPS = 48


@dataclass(frozen=True)
class EncodePlan:
    video_kbps: int
    audio_kbps: int
    height: int
    has_audio: bool
    fps: float
    duration_s: float


def pick_height(src_width: int, src_height: int, fps: float, video_bps: int) -> int:
    if src_width <= 0 or src_height <= 0:
        raise ValueError("소스 해상도가 유효하지 않습니다.")
    fps = fps if fps > 0 else 30.0
    candidates = sorted({h for h in LADDER if h <= src_height} | {src_height}, reverse=True)
    for h in candidates:
        w = src_width * h / src_height
        if video_bps / (w * h * fps) >= BPP_TARGET:
            return h
    return candidates[-1]


def plan_encode(
    *,
    duration_s: float,
    fps: float,
    src_width: int,
    src_height: int,
    has_audio: bool,
    target_bytes: int = 49_900_000,
    min_video_kbps: int = 80,
) -> EncodePlan:
    if duration_s <= 0:
        raise ValueError("재생 시간이 유효하지 않습니다.")
    if target_bytes <= 0:
        raise ValueError("목표 크기가 유효하지 않습니다.")

    budget_bps = target_bytes * 8 / duration_s

    audio_kbps = 0
    if has_audio:
        capped = int(budget_bps * AUDIO_SHARE_CAP / 1000)
        audio_kbps = min(AUDIO_DEFAULT_KBPS, capped)
        if capped >= AUDIO_MIN_KBPS:
            audio_kbps = max(audio_kbps, AUDIO_MIN_KBPS)

    video_bps = budget_bps * CONTAINER_MARGIN - audio_kbps * 1000
    video_kbps = max(int(video_bps / 1000), min_video_kbps)

    height = pick_height(src_width, src_height, fps, video_kbps * 1000)
    return EncodePlan(
        video_kbps=video_kbps,
        audio_kbps=audio_kbps,
        height=height,
        has_audio=has_audio,
        fps=fps if fps > 0 else 30.0,
        duration_s=duration_s,
    )
