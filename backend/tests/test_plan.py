import pytest

from app.plan import EncodePlan, pick_height, plan_encode

TARGET = 49_900_000


def test_pick_height_keeps_source_when_bpp_satisfied():
    # 1920x1080 30fps, 8 Mbps -> bpp = 8e6/(1920*1080*30) = 0.128 >= 0.06
    assert pick_height(1920, 1080, 30.0, 8_000_000) == 1080


def test_pick_height_downscales_when_bpp_low():
    # 1080p에 500 kbps -> 360p(640x360)에서 bpp = 5e5/(640*360*30) = 0.072 >= 0.06
    assert pick_height(1920, 1080, 30.0, 500_000) == 360


def test_pick_height_never_exceeds_source():
    assert pick_height(1280, 400, 30.0, 50_000_000) == 400


def test_pick_height_mid_ladder():
    # 1280x720 30fps, 1.2 Mbps -> bpp = 1.2e6/(1280*720*30) = 0.043
    # 720p: 0.043 < 0.06, 480p: 1.2e6/(854*480*30) = 0.097 >= 0.06
    assert pick_height(1280, 720, 30.0, 1_200_000) == 480


def test_pick_height_floor_is_240():
    assert pick_height(3840, 2160, 30.0, 100_000) == 240


def test_plan_audio_split():
    plan = plan_encode(duration_s=600.0, fps=30.0, src_width=1920, src_height=1080, has_audio=True, target_bytes=TARGET)
    assert isinstance(plan, EncodePlan)
    assert plan.has_audio is True
    assert plan.audio_kbps >= 48
    total = plan.video_kbps * 1000 + plan.audio_kbps * 1000
    assert total <= (TARGET * 8 / 600.0)


def test_plan_no_audio_allots_everything_to_video():
    with_a = plan_encode(duration_s=600.0, fps=30.0, src_width=1920, src_height=1080, has_audio=True, target_bytes=TARGET)
    without = plan_encode(duration_s=600.0, fps=30.0, src_width=1920, src_height=1080, has_audio=False, target_bytes=TARGET)
    assert without.audio_kbps == 0
    assert without.video_kbps > with_a.video_kbps


def test_plan_respects_min_video_kbps():
    # 6시간짜리 아주 긴 영상 -> 예산이 작아도 하한 유지
    plan = plan_encode(duration_s=21600.0, fps=30.0, src_width=1920, src_height=1080, has_audio=True, target_bytes=TARGET)
    assert plan.video_kbps >= 80


def test_plan_audio_cap_is_binding():
    # 800s: budget_bps = 49.9e6*8/800 = 499000, capped = int(499000*0.12/1000) = 59
    # 기본값 96 이 아니라 12% 상한이 실제로 구속되는 구간이다.
    plan = plan_encode(duration_s=800.0, fps=30.0, src_width=640, src_height=360, has_audio=True, target_bytes=TARGET)
    assert plan.audio_kbps == 59
    assert plan.audio_kbps < 96
    budget_bps = TARGET * 8 / 800.0
    assert plan.audio_kbps * 1000 <= budget_bps * 0.12


def test_plan_audio_cap_wins_over_floor():
    # 1200s: budget_bps = 332666, capped = 39 < 하한 48
    # 상한이 하한보다 우선한다 — 아니면 오디오가 예산을 먹어 긴 영상이 항상 초과된다.
    plan = plan_encode(duration_s=1200.0, fps=30.0, src_width=640, src_height=360, has_audio=True, target_bytes=TARGET)
    assert plan.audio_kbps == 39
    assert plan.audio_kbps < 48
    budget_bps = TARGET * 8 / 1200.0
    assert plan.audio_kbps * 1000 <= budget_bps * 0.12


def test_plan_zero_duration_raises():
    with pytest.raises(ValueError):
        plan_encode(duration_s=0.0, fps=30.0, src_width=640, src_height=360, has_audio=False, target_bytes=TARGET)
