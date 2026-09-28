import pytest

from app.ratelimit import RateLimiter, RateLimitError


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def test_allows_within_limit():
    clock = FakeClock()
    rl = RateLimiter(per_hour=3, clock=clock)
    for _ in range(3):
        rl.check_upload("ip1|sid1")


def test_blocks_over_limit():
    clock = FakeClock()
    rl = RateLimiter(per_hour=2, clock=clock)
    rl.check_upload("k")
    rl.check_upload("k")
    with pytest.raises(RateLimitError) as ei:
        rl.check_upload("k")
    assert ei.value.retry_after_s > 0


def test_window_slides():
    clock = FakeClock()
    rl = RateLimiter(per_hour=1, clock=clock)
    rl.check_upload("k")
    clock.t += 3601
    rl.check_upload("k")  # 창 밖이라 통과


def test_keys_are_independent():
    clock = FakeClock()
    rl = RateLimiter(per_hour=1, clock=clock)
    rl.check_upload("a")
    rl.check_upload("b")  # 다른 키는 영향 없음
