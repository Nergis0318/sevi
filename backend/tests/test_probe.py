import json
import subprocess
from types import SimpleNamespace

import pytest

from app.probe import ProbeError, ProbeResult, probe


def _stub_probe(monkeypatch, *, streams, fmt):
    def fake_run(cmd, capture_output=True, text=True, timeout=None):
        payload = {"streams": streams, "format": fmt}
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr("app.probe.subprocess.run", fake_run)


_VIDEO_ONLY = [{"codec_type": "video", "width": 640, "height": 360, "avg_frame_rate": "30/1"}]


def _touch(tmp_path):
    f = tmp_path / "source.bin"
    f.write_bytes(b"x")  # 비어 있지 않게 — 빈 파일 가드를 통과시키기 위함
    return f


@pytest.fixture(scope="module")
def clip_with_audio(tmp_path_factory) -> "Path":
    d = tmp_path_factory.mktemp("probe")
    out = d / "av.mp4"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc=size=640x360:rate=30:duration=2",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            "-shortest", "-y", str(out),
        ],
        check=True,
    )
    return out


@pytest.fixture(scope="module")
def clip_no_audio(tmp_path_factory) -> "Path":
    d = tmp_path_factory.mktemp("probe")
    out = d / "v.mp4"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=1",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", "-y", str(out),
        ],
        check=True,
    )
    return out


def test_probe_with_audio(clip_with_audio):
    r = probe(clip_with_audio)
    assert isinstance(r, ProbeResult)
    assert 1.5 <= r.duration_s <= 2.5
    assert r.width == 640 and r.height == 360
    assert r.fps == pytest.approx(30.0)
    assert r.has_audio is True


def test_probe_without_audio(clip_no_audio):
    r = probe(clip_no_audio)
    assert r.has_audio is False
    assert r.width == 320 and r.height == 240


def test_probe_accepts_bin_extension(clip_with_audio, tmp_path):
    # 확장자를 신뢰하지 않는다 — 내용으로 판별
    disguised = tmp_path / "source.bin"
    disguised.write_bytes(clip_with_audio.read_bytes())
    r = probe(disguised)
    assert r.width == 640


def test_probe_rejects_non_video(tmp_path):
    junk = tmp_path / "source.bin"
    junk.write_bytes(b"this is not a video at all" * 100)
    with pytest.raises(ProbeError):
        probe(junk)


def test_probe_rejects_missing_file(tmp_path):
    with pytest.raises(ProbeError):
        probe(tmp_path / "nope.bin")


def test_probe_rejects_overlong_duration(clip_with_audio):
    with pytest.raises(ProbeError):
        probe(clip_with_audio, max_duration_s=0.5)


def test_probe_rejects_empty_file(tmp_path):
    empty = tmp_path / "empty.bin"
    empty.write_bytes(b"")
    with pytest.raises(ProbeError):
        probe(empty)


def test_probe_zero_duration_rejected(monkeypatch, tmp_path):
    f = _touch(tmp_path)
    _stub_probe(monkeypatch, streams=_VIDEO_ONLY, fmt={"duration": "0.000000"})
    with pytest.raises(ProbeError):
        probe(f)


def test_probe_fps_parsed_from_avg_frame_rate(monkeypatch, tmp_path):
    f = _touch(tmp_path)
    streams = [dict(_VIDEO_ONLY[0], avg_frame_rate="25/1")]
    _stub_probe(monkeypatch, streams=streams, fmt={"duration": "2.0"})
    assert probe(f).fps == 25.0


def test_probe_fps_fallback_when_unknown_rate(monkeypatch, tmp_path):
    f = _touch(tmp_path)
    streams = [dict(_VIDEO_ONLY[0], avg_frame_rate="0/0")]
    _stub_probe(monkeypatch, streams=streams, fmt={"duration": "2.0"})
    assert probe(f).fps == 30.0


def test_probe_fps_fallback_when_unparseable(monkeypatch, tmp_path):
    f = _touch(tmp_path)
    streams = [dict(_VIDEO_ONLY[0], avg_frame_rate="garbage")]
    _stub_probe(monkeypatch, streams=streams, fmt={"duration": "2.0"})
    assert probe(f).fps == 30.0
