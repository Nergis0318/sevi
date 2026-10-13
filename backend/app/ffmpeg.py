import subprocess
import threading
from collections.abc import Callable
from pathlib import Path

from app.plan import EncodePlan
from app.profiles import Profile


class EncodeError(RuntimeError):
    def __init__(self, message: str, stderr: str = "") -> None:
        self.stderr = stderr
        # log.exception 은 str(exc)만 남긴다 — ffmpeg 원인이 메시지에 섞여야 로그로 진단된다.
        tail = [ln for ln in stderr.strip().splitlines() if ln.strip()][-6:]
        super().__init__(message + ("\n" + "\n".join(tail) if tail else ""))


def check_qsv(*, ffmpeg_path: str = "ffmpeg") -> None:
    """vp9_qsv 를 실제로 열어본다. 실패하면 명확한 원인으로 중단한다.

    런타임 누락(예: 컨테이너에 libmfx-gen 부재)은 잡마다 같은 오류를 낼 뿐이고
    로그엔 ffmpeg 원인도 남지 않는다 — 기동 시 한 번 확인해 빨리 죽인다.
    """
    cmd = [
        ffmpeg_path, "-nostdin", "-hide_banner", "-loglevel", "error",
        # 64x64 은 QSV 인코더가 거부한다(-22) — 최소한 320x240 을 쓴다.
        "-f", "lavfi", "-i", "testsrc=size=320x240:rate=1:duration=0.1",
        "-c:v", "vp9_qsv", "-frames:v", "1", "-f", "null", "-",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise EncodeError(f"QSV 자체 점검을 실행할 수 없습니다: {exc}") from exc
    if proc.returncode != 0:
        raise EncodeError(
            "QSV(vp9_qsv) 인코더를 열 수 없습니다 — 하드웨어 가속 런타임을 확인하세요.",
            stderr=proc.stderr or "",
        )


def _filter_chain(plan: EncodePlan, profile: Profile) -> str:
    """sw 필터 체인. 불변: -2 폭, format=nv12 / hwaccel·scale_qsv·hwupload 금지."""
    parts: list[str] = []
    if plan.fps > 60:
        parts.append("fps=60")
    if profile.denoise:
        parts.append(profile.denoise)
    parts.append(f"scale=-2:{int(plan.height)}:flags={profile.scale_flags},format=nv12")
    return ",".join(parts)


def build_cmd(
    *,
    ffmpeg_path: str,
    src: Path,
    dst: Path,
    plan: EncodePlan,
    video_kbps: int,
    profile: Profile,
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
        "-c:v", "vp9_qsv", "-preset", profile.preset,
        "-b:v", f"{v}k",
        "-maxrate", f"{int(v * profile.maxrate_mult)}k",
        "-bufsize", f"{int(v * profile.bufsize_mult)}k",
    ]
    if profile.tile_cols:
        cmd += ["-tile_cols", str(profile.tile_cols)]
    if profile.tile_rows:
        cmd += ["-tile_rows", str(profile.tile_rows)]
    cmd += ["-vf", _filter_chain(plan, profile)]
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
    profile: Profile,
    ffmpeg_path: str = "ffmpeg",
    on_progress: Callable[[float], None] | None = None,
) -> int:
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = build_cmd(
        ffmpeg_path=ffmpeg_path, src=src, dst=dst, plan=plan, video_kbps=video_kbps, profile=profile
    )

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

    rc = 1
    try:
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
    except BaseException:
        proc.terminate()
        raise
    finally:
        rc = proc.wait()
        t.join(timeout=5)

    if rc != 0:
        stderr = "".join(err_lines)[-2000:]
        raise EncodeError("인코딩에 실패했습니다.", stderr=stderr)

    if not dst.is_file() or dst.stat().st_size == 0:
        raise EncodeError("인코딩 결과가 비어 있습니다.", stderr="".join(err_lines)[-2000:])

    return dst.stat().st_size
