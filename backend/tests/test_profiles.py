from pathlib import Path

from app.ffmpeg import build_cmd
from app.plan import EncodePlan, pick_height, plan_encode
from app.profiles import PROFILES

TARGET = 49_900_000


def _plan(fps: float = 30.0) -> EncodePlan:
    return EncodePlan(
        video_kbps=500, audio_kbps=96, height=360, has_audio=True, fps=fps, duration_s=4.0
    )


def _cmd(profile, plan=None):
    return build_cmd(
        ffmpeg_path="ffmpeg",
        src=Path("in.bin"),
        dst=Path("out.webm"),
        plan=plan or _plan(),
        video_kbps=500,
        profile=profile,
    )


def test_profiles_has_three_names():
    assert set(PROFILES) == {"fast", "balanced", "quality"}


def test_profile_labels_are_korean():
    assert PROFILES["fast"].label == "빠름"
    assert PROFILES["balanced"].label == "균형"
    assert PROFILES["quality"].label == "품질"


def test_balanced_matches_current_behaviour():
    p = PROFILES["balanced"]
    assert p.preset == "medium"
    assert p.max_attempts == 3
    assert p.bpp_target == 0.06
    assert p.maxrate_mult == 1.2
    assert p.bufsize_mult == 2.0
    assert p.audio_kbps == 96
    assert (p.tile_cols, p.tile_rows) == (0, 0)
    assert p.scale_flags == "bicubic"
    assert p.denoise == ""


def test_fast_profile_params():
    p = PROFILES["fast"]
    assert p.preset == "fast"
    assert p.max_attempts == 1
    assert p.bpp_target == 0.075
    assert p.maxrate_mult == 1.3
    assert p.audio_kbps == 64
    assert (p.tile_cols, p.tile_rows) == (2, 1)
    assert p.scale_flags == "bilinear"
    assert p.denoise == ""


def test_quality_profile_params():
    p = PROFILES["quality"]
    assert p.preset == "slow"
    assert p.max_attempts == 3
    assert p.bpp_target == 0.045
    assert p.maxrate_mult == 1.1
    assert p.audio_kbps == 128
    assert (p.tile_cols, p.tile_rows) == (0, 0)
    assert p.scale_flags == "lanczos"
    assert p.denoise == "hqdn3d=4:3:6:4.5"


def test_build_cmd_balanced_keeps_current_args():
    joined = " ".join(_cmd(PROFILES["balanced"]))
    assert "-preset medium" in joined
    assert "maxrate 600k" in joined
    assert "bufsize 1000k" in joined
    assert "scale=-2:360:flags=bicubic,format=nv12" in joined
    assert "hqdn3d" not in joined
    assert "tile_cols" not in joined


def test_build_cmd_fast_profile_args():
    joined = " ".join(_cmd(PROFILES["fast"]))
    assert "-preset fast" in joined
    assert "maxrate 650k" in joined
    assert "scale=-2:360:flags=bilinear,format=nv12" in joined
    assert "tile_cols 2" in joined and "tile_rows 1" in joined
    assert "hqdn3d" not in joined


def test_build_cmd_quality_profile_args():
    joined = " ".join(_cmd(PROFILES["quality"]))
    assert "-preset slow" in joined
    assert "maxrate 550k" in joined
    assert "hqdn3d=4:3:6:4.5,scale=-2:360:flags=lanczos,format=nv12" in joined
    assert "tile_cols" not in joined


def test_build_cmd_caps_fps_above_60():
    joined = " ".join(_cmd(PROFILES["balanced"], plan=_plan(fps=120.0)))
    assert "fps=60," in joined


def test_build_cmd_keeps_source_fps_below_60():
    joined = " ".join(_cmd(PROFILES["balanced"], plan=_plan(fps=30.0)))
    assert "fps=" not in joined


def test_build_cmd_invariants_hold_for_every_profile():
    for prof in PROFILES.values():
        cmd = _cmd(prof)
        joined = " ".join(cmd)
        assert "-hwaccel" not in cmd
        assert "scale_qsv" not in joined
        assert "hwupload" not in joined
        assert "format=nv12" in joined
        assert "scale=-2:360:" in joined
        assert "-map_metadata" in cmd and "-1" in cmd


def test_pick_height_quality_bpp_keeps_resolution():
    # 1920x1080 30fps 1.4 Mbps: 720p bpp = 1.4e6/(1280*720*30) = 0.051
    # 품질(0.045)은 720p 유지, 균형(0.06)은 480p로 하강
    assert pick_height(1920, 1080, 30.0, 1_400_000, bpp_target=0.045) == 720
    assert pick_height(1920, 1080, 30.0, 1_400_000, bpp_target=0.06) == 480


def test_pick_height_fast_bpp_downscales_more():
    # 800 kbps: 480p bpp = 800e3/(854*480*30) = 0.065
    # 빠름(0.075)은 360p로, 균형(0.06)은 480p 유지
    assert pick_height(1920, 1080, 30.0, 800_000, bpp_target=0.075) == 360
    assert pick_height(1920, 1080, 30.0, 800_000, bpp_target=0.06) == 480


def test_plan_encode_respects_audio_default_kbps():
    assert plan_encode(
        duration_s=60.0, fps=30.0, src_width=1920, src_height=1080, has_audio=True,
        target_bytes=TARGET, audio_default_kbps=128,
    ).audio_kbps == 128
    assert plan_encode(
        duration_s=60.0, fps=30.0, src_width=1920, src_height=1080, has_audio=True,
        target_bytes=TARGET, audio_default_kbps=64,
    ).audio_kbps == 64
