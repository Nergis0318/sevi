from app.converge import ConvergeResult, converge

TARGET = 49_900_000


def test_converges_first_try():
    calls = []

    def encode(kbps: int) -> int:
        calls.append(kbps)
        return int(TARGET * 0.96)

    res = converge(target_bytes=TARGET, initial_kbps=1000, encode=encode)
    assert res.converged is True
    assert res.attempts == 1
    assert len(calls) == 1


def test_scales_down_when_oversize():
    calls = []

    def encode(kbps: int) -> int:
        calls.append(kbps)
        return TARGET * 10  # 항상 10배 초과 — 결코 수렴하지 않음

    res = converge(target_bytes=TARGET, initial_kbps=1000, encode=encode, max_attempts=3)
    assert res.attempts == 3
    assert calls[1] < calls[0]
    assert res.converged is False
    assert res.result_bytes > TARGET


def test_scales_up_when_undersize():
    calls = []

    def encode(kbps: int) -> int:
        calls.append(kbps)
        return int(TARGET * 0.5)

    res = converge(target_bytes=TARGET, initial_kbps=1000, encode=encode, max_attempts=3)
    assert calls[1] > calls[0]
    assert res.attempts == 3


def test_undersize_is_still_success():
    def encode(kbps: int) -> int:
        return int(TARGET * 0.5)

    res = converge(target_bytes=TARGET, initial_kbps=1000, encode=encode, max_attempts=1)
    assert res.converged is False
    assert res.result_bytes <= TARGET


def test_floor_respected():
    calls = []

    def encode(kbps: int) -> int:
        calls.append(kbps)
        return TARGET * 100

    res = converge(target_bytes=TARGET, initial_kbps=100, encode=encode, max_attempts=3, floor_kbps=80)
    assert all(k >= 80 for k in calls)
    assert res.video_kbps >= 80


def test_target_exact_boundary_accepted():
    def encode(kbps: int) -> int:
        return TARGET

    res = converge(target_bytes=TARGET, initial_kbps=500, encode=encode)
    assert res.converged is True


def test_lower_boundary_accepted():
    def encode(kbps: int) -> int:
        return int(TARGET * 0.92)

    res = converge(target_bytes=TARGET, initial_kbps=500, encode=encode)
    assert res.converged is True


def test_below_lower_boundary_retries():
    calls = []

    def encode(kbps: int) -> int:
        calls.append(kbps)
        return int(TARGET * 0.91)

    res = converge(target_bytes=TARGET, initial_kbps=500, encode=encode, max_attempts=2)
    assert res.attempts == 2
    assert calls[1] > calls[0]


def test_result_type():
    def encode(kbps: int) -> int:
        return TARGET

    res = converge(target_bytes=TARGET, initial_kbps=1, encode=encode)
    assert isinstance(res, ConvergeResult)
