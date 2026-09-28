import json
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path


class ProbeError(ValueError):
    pass


@dataclass(frozen=True)
class ProbeResult:
    duration_s: float
    width: int
    height: int
    fps: float
    has_audio: bool


def _parse_fps(raw: str | None) -> float:
    if not raw or raw in {"0/0", "N/A"}:
        return 30.0
    try:
        value = float(Fraction(raw))
    except (ValueError, ZeroDivisionError):
        return 30.0
    return value if value > 0 else 30.0


def probe(
    path: Path,
    *,
    ffprobe_path: str = "ffprobe",
    max_duration_s: float = 21600.0,
) -> ProbeResult:
    if not path.is_file() or path.stat().st_size == 0:
        raise ProbeError("파일이 비어 있거나 존재하지 않습니다.")

    cmd = [
        ffprobe_path, "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProbeError("파일 분석에 실패했습니다.") from exc

    if proc.returncode != 0:
        raise ProbeError("지원하지 않는 파일 형식입니다.")

    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise ProbeError("파일 분석 결과를 읽을 수 없습니다.") from exc

    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise ProbeError("비디오 스트림을 찾을 수 없습니다.")

    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    if width <= 0 or height <= 0:
        raise ProbeError("비디오 해상도를 읽을 수 없습니다.")

    duration_s = 0.0
    try:
        duration_s = float(data.get("format", {}).get("duration") or 0.0)
    except (TypeError, ValueError):
        duration_s = 0.0
    if duration_s <= 0:
        try:
            duration_s = float(video.get("duration") or 0.0)
        except (TypeError, ValueError):
            duration_s = 0.0
    if duration_s <= 0:
        raise ProbeError("재생 시간을 읽을 수 없습니다.")
    if duration_s > max_duration_s:
        raise ProbeError(
            f"재생 시간이 한도({int(max_duration_s / 3600)}시간)를 초과합니다."
        )

    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    fps = _parse_fps(video.get("avg_frame_rate"))

    return ProbeResult(
        duration_s=duration_s,
        width=width,
        height=height,
        fps=fps,
        has_audio=has_audio,
    )
