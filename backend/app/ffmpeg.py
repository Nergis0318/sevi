import subprocess
import threading
from collections.abc import Callable
from pathlib import Path

from app.plan import EncodePlan


class EncodeError(RuntimeError):
    def __init__(self, message: str, stderr: str = "") -> None:
        super().__init__(message)
        self.stderr = stderr


def build_cmd(
    *,
    ffmpeg_path: str,
    src: Path,
    dst: Path,
    plan: EncodePlan,
    video_kbps: int,
) -> list[str]:
    v = int(video_kbps)
    cmd = [
        ffmpeg_path, "-nostdin", "-y", "-hide_banner", "-nostats",
        "-progress", "pipe:1", "-loglevel", "error",
        "-i", str(src),
        "-map", "0:v:0",
    ]
    if plan.has_audio:
        cmd += ["-map", "0:a:0"]
    cmd += [
        "-c:v", "vp9_qsv", "-preset", "medium",
        "-b:v", f"{v}k",
        "-maxrate", f"{int(v * 1.2)}k",
        "-bufsize", f"{int(v * 2)}k",
        "-vf", f"scale=-2:{int(plan.height)}:flags=bicubic,format=nv12",
    ]
    if plan.has_audio:
        cmd += [
            "-c:a", "libopus",
            "-b:a", f"{int(plan.audio_kbps)}k",
            "-ac", "2", "-ar", "48000", "-application", "audio",
        ]
    else:
        cmd += ["-an"]
    cmd += [
        "-map_metadata", "-1", "-map_chapters", "-1",
        "-f", "webm", str(dst),
    ]
    return cmd


def encode_once(
    *,
    src: Path,
    dst: Path,
    plan: EncodePlan,
    video_kbps: int,
    ffmpeg_path: str = "ffmpeg",
    on_progress: Callable[[float], None] | None = None,
) -> int:
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = build_cmd(ffmpeg_path=ffmpeg_path, src=src, dst=dst, plan=plan, video_kbps=video_kbps)

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
    except OSError as exc:
        raise EncodeError(f"ffmpeg 실행에 실패했습니다: {exc}") from exc

    err_lines: list[str] = []

    def _drain() -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            err_lines.append(line)

    t = threading.Thread(target=_drain, daemon=True)
    t.start()

    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.strip()
        if line.startswith("out_time_ms=") and on_progress is not None:
            raw = line.split("=", 1)[1]
            if raw and raw != "N/A" and plan.duration_s > 0:
                try:
                    ms = int(raw)
                except ValueError:
                    continue
                on_progress(min(1.0, ms / 1_000_000 / plan.duration_s))
        elif line.startswith("progress=") and line.endswith("end"):
            if on_progress is not None:
                on_progress(1.0)

    rc = proc.wait()
    t.join(timeout=5)

    if rc != 0:
        stderr = "".join(err_lines)[-2000:]
        raise EncodeError("인코딩에 실패했습니다.", stderr=stderr)

    if not dst.is_file() or dst.stat().st_size == 0:
        raise EncodeError("인코딩 결과가 비어 있습니다.", stderr="".join(err_lines)[-2000:])

    return dst.stat().st_size
