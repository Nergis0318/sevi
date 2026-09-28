import threading
import time
from collections import deque
from collections.abc import Callable


class RateLimitError(Exception):
    def __init__(self, message: str, retry_after_s: int) -> None:
        super().__init__(message)
        self.retry_after_s = retry_after_s


# ponytail: 인메모리 리밋 — backend 단일 프로세스 전제. 스케일 아웃 시 Redis로 교체
# 활동 슬롯은 여기서 다루지 않는다 — 워커가 별도 프로세스라 해제할 수 없으므로
# DB 의존하는 app.models.count_active 가 유일한 진실 원천이다.
class RateLimiter:
    def __init__(
        self,
        *,
        per_hour: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._per_hour = per_hour
        self._window = 3600.0
        self._clock = clock
        self._uploads: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def _evict(self, now: float) -> None:
        """창을 지난 키와 빈 deque 를 제거 — 세션 무한 생성에 따른 메모리 증식 방지."""
        stale = [
            k
            for k, q in self._uploads.items()
            if not q or now - q[-1] > self._window
        ]
        for k in stale:
            del self._uploads[k]

    def check_upload(self, key: str) -> None:
        now = self._clock()
        with self._lock:
            self._evict(now)
            q = self._uploads.setdefault(key, deque())
            while q and now - q[0] > self._window:
                q.popleft()
            if len(q) >= self._per_hour:
                oldest = q[0]
                wait = int(self._window - (now - oldest)) + 1
                raise RateLimitError(
                    f"업로드 한도를 초과했습니다. {wait}초 후 다시 시도해 주세요.",
                    retry_after_s=wait,
                )
            q.append(now)
