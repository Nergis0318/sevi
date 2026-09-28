from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class ConvergeResult:
    video_kbps: int
    attempts: int
    result_bytes: int
    converged: bool


def converge(
    *,
    target_bytes: int,
    initial_kbps: int,
    encode: Callable[[int], int],
    max_attempts: int = 3,
    floor_kbps: int = 80,
) -> ConvergeResult:
    if target_bytes <= 0:
        raise ValueError("목표 크기가 유효하지 않습니다.")
    if max_attempts < 1:
        raise ValueError("시도 횟수가 유효하지 않습니다.")

    kbps = max(initial_kbps, floor_kbps)
    result = 0
    attempts = 0

    for attempts in range(1, max_attempts + 1):
        result = encode(kbps)
        if 0.92 * target_bytes <= result <= target_bytes:
            return ConvergeResult(kbps, attempts, result, True)
        if attempts == max_attempts:
            break
        scale = (target_bytes * 0.97) / max(result, 1)
        kbps = max(floor_kbps, int(kbps * scale))

    return ConvergeResult(kbps, attempts, result, False)
