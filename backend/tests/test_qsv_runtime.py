"""QSV 가용성과 인코딩 실패 진단 — 컨테이너에서 런타임 누락이 잡히지 않아 생긴 장애의 회귀 가드."""

from pathlib import Path

import pytest

from app.ffmpeg import EncodeError, check_qsv


def test_encode_error_carries_stderr_tail_in_message():
    """log.exception 은 str(exc)만 남긴다 — ffmpeg 원인이 메시지에 섞여야 진단된다."""
    err = EncodeError("인코딩에 실패했습니다.", stderr="line1\nMFX session error: -9\nline3\n")
    assert "MFX session error: -9" in str(err)
    assert err.stderr.endswith("line3\n")  # 원문은 그대로 보존


def test_encode_error_without_stderr_is_plain():
    assert str(EncodeError("실패")) == "실패"


def test_check_qsv_raises_with_stderr_on_failure(monkeypatch):
    import subprocess
    from types import SimpleNamespace

    def fake_run(*a, **k):
        return SimpleNamespace(returncode=1, stdout="", stderr="Error creating a MFX session: -9\n")

    monkeypatch.setattr("app.ffmpeg.subprocess.run", fake_run)
    with pytest.raises(EncodeError) as ei:
        check_qsv()
    assert "MFX session" in ei.value.stderr
    assert "MFX session" in str(ei.value)


def test_check_qsv_passes_when_encoder_works(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(
        "app.ffmpeg.subprocess.run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    assert check_qsv() is None


def test_check_qsv_runs_on_this_machine():
    """실제 QSV 로 한 프레임을 인코딩한다 — 런타임 누락은 여기서 바로 드러난다.

    test_encode_integration 가 이미 QSV 를 필요로 하므로 이 스위트는 QSV 기기 전제다.
    """
    check_qsv()


def test_dockerfile_installs_mfx_gen():
    """컨테이너 QSV 는 libmfx-gen 없이 MFX 세션을 못 연다 — 오늘 장애의 근본 원인."""
    root = Path(__file__).resolve().parents[2]
    text = (root / "docker" / "Dockerfile").read_text()
    assert "libmfx-gen" in text
