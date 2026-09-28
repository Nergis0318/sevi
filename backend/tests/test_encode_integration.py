import json
import subprocess
from pathlib import Path

import pytest

from app.ffmpeg import EncodeError, build_cmd, encode_once
from app.plan import EncodePlan, plan_encode


@pytest.fixture(scope="module")
def src_av(tmp_path_factory):
    d = tmp_path_factory.mktemp("enc")
    out = d / "source.bin"  # 확장자 없음 — 내용으로 판별되어야 함
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=30:duration=4",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            "-shortest", "-f", "mp4", "-y", str(out),
        ],
        check=True,
    )
    return out


@pytest.fixture(scope="module")
def src_video_only(tmp_path_factory):
    d = tmp_path_factory.mktemp("encv")
    out = d / "source.bin"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc=size=640x360:rate=25:duration=3",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", "-f", "mp4", "-y", str(out),
        ],
        check=True,
    )
    return out


def test_build_cmd_uses_sw_scale_not_hwaccel():
    plan = EncodePlan(video_kbps=500, audio_kbps=96, height=360, has_audio=True, fps=30.0, duration_s=4.0)
    cmd = build_cmd(ffmpeg_path="ffmpeg", src=Path("in.bin"), dst=Path("out.webm"), plan=plan, video_kbps=500)
    joined = " ".join(cmd)
    assert "-hwaccel" not in cmd
    assert "scale_qsv" not in joined
    assert "hwupload" not in joined
    assert "scale=-2:360:flags=bicubic,format=nv12" in joined
    assert "-map_metadata" in cmd and "-1" in cmd
    assert "vp9_qsv" in cmd and "libopus" in cmd
    assert cmd[-2:] == ["-f", "webm"] or "webm" in cmd


def test_build_cmd_no_audio_uses_an():
    plan = EncodePlan(video_kbps=500, audio_kbps=0, height=240, has_audio=False, fps=25.0, duration_s=3.0)
    cmd = build_cmd(ffmpeg_path="ffmpeg", src=Path("in.bin"), dst=Path("out.webm"), plan=plan, video_kbps=500)
    assert "-an" in cmd
    assert "libopus" not in cmd


def test_encode_produces_target_format(src_av, tmp_path):
    plan = plan_encode(duration_s=4.0, fps=30.0, src_width=1280, src_height=720, has_audio=True, target_bytes=2_000_000)
    dst = tmp_path / "out.webm"
    seen = []
    size = encode_once(src=src_av, dst=dst, plan=plan, video_kbps=plan.video_kbps, on_progress=seen.append)
    assert dst.is_file()
    assert size > 0
    assert dst.stat().st_size == size
    assert seen and max(seen) <= 1.0 and max(seen) > 0.0

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", str(dst)],
        capture_output=True, text=True, check=True,
    )
    streams = json.loads(probe.stdout)["streams"]
    codecs = {s["codec_type"]: s["codec_name"] for s in streams}
    assert codecs["video"] == "vp9"
    assert codecs["audio"] == "opus"


def test_encode_video_only(src_video_only, tmp_path):
    plan = plan_encode(duration_s=3.0, fps=25.0, src_width=640, src_height=360, has_audio=False, target_bytes=1_000_000)
    dst = tmp_path / "out.webm"
    encode_once(src=src_video_only, dst=dst, plan=plan, video_kbps=plan.video_kbps)
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", str(dst)],
        capture_output=True, text=True, check=True,
    )
    streams = json.loads(probe.stdout)["streams"]
    assert all(s["codec_type"] != "audio" for s in streams)


def test_encode_failure_raises(tmp_path):
    plan = EncodePlan(video_kbps=500, audio_kbps=0, height=240, has_audio=False, fps=30.0, duration_s=1.0)
    missing = tmp_path / "nope.bin"
    with pytest.raises(EncodeError) as ei:
        encode_once(src=missing, dst=tmp_path / "o.webm", plan=plan, video_kbps=500)
    assert ei.value.stderr


def test_encode_reaps_process_when_on_progress_raises(src_av, tmp_path, monkeypatch):
    """on_progress 가 예외를 던져도 ffmpeg 자식이 회수되어 QSV 가 점유되지 않는다."""
    import subprocess as sp

    procs = []
    real_popen = sp.Popen

    def spy(*a, **k):
        p = real_popen(*a, **k)
        procs.append(p)
        return p

    monkeypatch.setattr("app.ffmpeg.subprocess.Popen", spy)
    plan = plan_encode(duration_s=4.0, fps=30.0, src_width=1280, src_height=720, has_audio=True, target_bytes=2_000_000)

    def boom(_fraction: float) -> None:
        raise RuntimeError("cancel")

    with pytest.raises(RuntimeError):
        encode_once(src=src_av, dst=tmp_path / "out.webm", plan=plan, video_kbps=plan.video_kbps, on_progress=boom)
    assert procs and procs[0].poll() is not None
