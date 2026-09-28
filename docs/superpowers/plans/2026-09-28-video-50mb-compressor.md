# 50MB 비디오 압축 사이트 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 원본 영상을 49.9MB 이하 `.webm`(vp9_qsv + opus)으로 서버 재인코딩해주는 익명 공개 사이트.

**Architecture:** FastAPI가 업로드를 받아 Postgres `jobs` 테이블에 넣고, 전용 워커 컨테이너가 `FOR UPDATE SKIP LOCKED`로 잡을 클레임해 ffmpeg를 실행한다. 목표 크기 맞추기는 2-pass 미지원(`vp9_qsv`)이라 추정→인코딩→실측→보정 순환(최대 3회)으로 한다. 프런트는 단일 페이지에서 업로드→진행률→다운로드를 2초 폴링으로 보여준다.

**Tech Stack:** Python 3.13 / FastAPI / SQLModel / Pydantic Settings / uv · PostgreSQL 16 · React 19 + Vite + TypeScript + Tailwind + shadcn/ui + TanStack Router/Query + bun · ffmpeg 7.1 (`vp9_qsv`, `libopus`) · Docker Compose + Traefik

**Spec:** `docs/superpowers/specs/2026-09-28-video-50mb-compressor-design.md`

## Global Constraints

프로젝트 전체에 적용. 모든 태스크의 요구사항에 암시적으로 포함된다.

- Python 3.13+, 패키지 관리 **uv**. JS/TS 패키지 관리 **bun**
- `TARGET_BYTES = 49_900_000` (49.9 decimal MB). `SEVI_TARGET_BYTES` env로 변경 가능
- 업로드 한도 `800 * 1024 * 1024` B. 최대 길이 `21600` s (6h)
- 보관: 결과 `created_at + 24h`, 원본은 인코딩 종료 즉시 삭제
- 워커 동시성 **1**. 세션당 활성 잡 1건, 업로드 **5건/시간**
- 비트레이트 하한 `80` kbps. 최대 시도 **3회**. 수렴 창 `0.92*TARGET .. TARGET`
- 해상도 bpp 목표 `0.06`. 사다리 `2160, 1440, 1080, 720, 480, 360, 240`
- 오디오: `libopus`, `-ac 2 -ar 48000`, 기본 96 kbps (예산 12% 상한, 하한 48)
- 출력: `.webm` (`-f webm`), 파일명은 전부 UUID — 사용자 파일명은 경로에 미사용
- **ffmpeg 인자 고정**: `-hwaccel`, `scale_qsv`, 명시적 `hwupload` **금지** (검증됨 — 전부 `-22` 실패). `scale=-2:H:flags=bicubic,format=nv12`만 사용
- 메타데이터 제거: `-map_metadata -1 -map_chapters -1`
- UI 언어 한국어. API 에러 `detail`도 한국어
- 커밋 메시지: Conventional Commits (`feat:`, `fix:`, `test:`, `chore:`)

---

## File Structure

```
sevi/
├── backend/
│   ├── pyproject.toml
│   ├── uv.lock
│   ├── app/
│   │   ├── __init__.py
│   │   ├── settings.py          # Settings(BaseSettings), get_settings()
│   │   ├── db.py                # engine, init_db(), get_session()
│   │   ├── models.py            # Job SQLModel, JobStatus enum
│   │   ├── paths.py             # MediaPaths — UUID 기반 파일 경로
│   │   ├── plan.py              # EncodePlan, pick_height(), plan_encode()   [순수]
│   │   ├── converge.py          # ConvergeResult, converge()                [순수]
│   │   ├── probe.py             # ProbeResult, probe(), ProbeError
│   │   ├── ffmpeg.py            # encode_once(), EncodeError, build_cmd()
│   │   ├── session.py           # issue(), verify(), SESSION_COOKIE
│   │   ├── ratelimit.py         # RateLimiter, RateLimitError
│   │   ├── main.py              # create_app(), 정적 서빙
│   │   ├── api/
│   │   │   ├── __init__.py
│   │   │   ├── deps.py          # session_dep, get_limiter
│   │   │   ├── jobs.py
│   │   │   ├── config.py
│   │   │   └── health.py
│   │   └── worker.py            # claim_next, process_job, sweep, run_forever
│   └── tests/
│       ├── conftest.py
│       ├── test_plan.py
│       ├── test_converge.py
│       ├── test_probe.py
│       ├── test_paths.py
│       ├── test_session.py
│       ├── test_ratelimit.py
│       ├── test_api_jobs.py
│       └── test_encode_integration.py
├── frontend/
│   ├── package.json
│   ├── bun.lock
│   ├── tsconfig.json
│   ├── vite.config.ts
│   ├── index.html
│   └── src/
│       ├── main.tsx
│       ├── index.css
│       ├── routes/index.tsx
│       ├── components/DropZone.tsx
│       ├── components/JobStatus.tsx
│       └── lib/api.ts
├── docker/Dockerfile
├── compose.yml
├── .env.example
├── .gitignore
└── docs/superpowers/{specs,plans}/
```

의존 순서: `settings` → `paths`/`models`/`db` → `plan`/`converge`/`probe`/`session`/`ratelimit` → `ffmpeg` → `api` → `worker` → `frontend` → `docker`.

---

### Task 1: 스캐폴드 — uv 프로젝트, 설정, DB, health

**Files:**
- Create: `backend/pyproject.toml`, `backend/app/__init__.py`, `backend/app/settings.py`, `backend/app/db.py`, `backend/app/main.py`, `backend/app/api/__init__.py`, `backend/app/api/health.py`
- Test: `backend/tests/conftest.py`, `backend/tests/test_health.py`

**Interfaces:**
- Consumes: 없음
- Produces:
  - `app.settings.Settings` (dataclass-like, `get_settings() -> Settings`)
  - `app.db.init_db() -> None`, `app.db.get_session() -> Iterator[Session]`, `app.db.engine`
  - `app.main.create_app(settings: Settings | None = None) -> FastAPI`

- [ ] **Step 1: 프로젝트 골격 생성**

`backend/pyproject.toml`:

```toml
[project]
name = "sevi-backend"
version = "0.1.0"
requires-python = ">=3.13"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.34",
    "sqlmodel>=0.0.24",
    "psycopg[binary]>=3.2",
    "pydantic-settings>=2.7",
    "python-multipart>=0.0.20",
]

[dependency-groups]
dev = ["pytest>=8.3", "pytest-asyncio>=0.25", "httpx>=0.28"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["app"]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
```

`backend/app/__init__.py`, `backend/app/api/__init__.py` — 빈 파일.

`backend/app/settings.py`:

```python
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SEVI_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://sevi:sevi@localhost:5432/sevi"
    media_dir: Path = Path("/tmp/sevi-media")
    target_bytes: int = 49_900_000
    max_upload_bytes: int = 800 * 1024 * 1024
    max_duration_s: float = 21600.0
    retention_hours: int = 24
    session_secret: str = "dev-secret-change-me"
    session_cookie: str = "sv_session"
    session_ttl_days: int = 7
    rate_limit_per_hour: int = 5
    min_video_kbps: int = 80
    max_attempts: int = 3
    encode_preset: str = "medium"
    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"
    worker_poll_s: float = 1.0
    log_level: str = "INFO"


def get_settings() -> Settings:
    return Settings()
```

`backend/app/db.py`:

```python
from collections.abc import Iterator

from sqlmodel import Session, SQLModel, create_engine

from app.settings import get_settings

engine = create_engine(get_settings().database_url, pool_pre_ping=True)


def init_db() -> None:
    from app import models  # noqa: F401  # 등록 보장

    SQLModel.metadata.create_all(engine)


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
```

`backend/app/main.py`:

```python
from fastapi import FastAPI

from app.settings import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="sevi", version="0.1.0")
    app.state.settings = settings

    from app.api.health import router as health_router

    app.include_router(health_router, prefix="/api")
    return app


app = create_app()
```

`backend/app/api/health.py`:

```python
from fastapi import APIRouter, Request
from sqlmodel import text

from app.db import engine

router = APIRouter(tags=["health"])


@router.get("/health")
def health(request: Request) -> dict:
    db_ok = True
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        db_ok = False
    return {"ok": db_ok, "service": "sevi"}
```

- [ ] **Step 2: 테스트 작성**

`backend/tests/conftest.py`:

```python
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import Settings


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(database_url="sqlite://", media_dir=tmp_path / "media", session_secret="test-secret")


@pytest.fixture
def client(settings) -> TestClient:
    app = create_app(settings)
    return TestClient(app)
```

`backend/tests/test_health.py`:

```python
def test_health_returns_ok(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["service"] == "sevi"
    assert "ok" in body
```

- [ ] **Step 3: 테스트가 실패함을 확인**

Run: `cd backend && uv sync && uv run pytest tests/test_health.py -v`
Expected: PASS (골격이 이미 있으므로). 실패하면 import 경로 수정.

- [ ] **Step 4: 커밋**

```bash
git add backend/pyproject.toml backend/app backend/tests
git commit -m "feat: scaffold FastAPI backend with settings, db, health endpoint"
```

---

### Task 2: 비트레이트 예산 + 해상도 사다리 (순수)

**Files:**
- Create: `backend/app/plan.py`
- Test: `backend/tests/test_plan.py`

**Interfaces:**
- Consumes: 없음 (순수 함수)
- Produces:
  - `EncodePlan` frozen dataclass: `video_kbps: int`, `audio_kbps: int`, `height: int`, `has_audio: bool`, `fps: float`, `duration_s: float`
  - `pick_height(src_width: int, src_height: int, fps: float, video_bps: int) -> int`
  - `plan_encode(*, duration_s: float, fps: float, src_width: int, src_height: int, has_audio: bool, target_bytes: int = 49_900_000, min_video_kbps: int = 80) -> EncodePlan`

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_plan.py`:

```python
import pytest

from app.plan import EncodePlan, pick_height, plan_encode

TARGET = 49_900_000


def test_pick_height_keeps_source_when_bpp_satisfied():
    # 1920x1080 30fps, 8 Mbps -> bpp = 8e6/(1920*1080*30) = 0.128 >= 0.06
    assert pick_height(1920, 1080, 30.0, 8_000_000) == 1080


def test_pick_height_downscales_when_bpp_low():
    # 1080p에 500 kbps -> bpp = 5e5/(1920*1080*30) = 0.008 -> 사다리 하단
    assert pick_height(1920, 1080, 30.0, 500_000) == 240


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


def test_plan_audio_capped_at_12_percent():
    # 아주 짧은 영상이면 오디오가 12%를 넘지 않음
    plan = plan_encode(duration_s=5.0, fps=30.0, src_width=640, src_height=360, has_audio=True, target_bytes=TARGET)
    budget_bps = TARGET * 8 / 5.0
    assert plan.audio_kbps * 1000 <= budget_bps * 0.12


def test_plan_zero_duration_raises():
    with pytest.raises(ValueError):
        plan_encode(duration_s=0.0, fps=30.0, src_width=640, src_height=360, has_audio=False, target_bytes=TARGET)
```

- [ ] **Step 2: 실패 확인**

Run: `cd backend && uv run pytest tests/test_plan.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.plan'`

- [ ] **Step 3: 구현**

`backend/app/plan.py`:

```python
from dataclasses import dataclass

BPP_TARGET = 0.06
LADDER: tuple[int, ...] = (2160, 1440, 1080, 720, 480, 360, 240)
CONTAINER_MARGIN = 0.97
AUDIO_SHARE_CAP = 0.12
AUDIO_DEFAULT_KBPS = 96
AUDIO_MIN_KBPS = 48


@dataclass(frozen=True)
class EncodePlan:
    video_kbps: int
    audio_kbps: int
    height: int
    has_audio: bool
    fps: float
    duration_s: float


def pick_height(src_width: int, src_height: int, fps: float, video_bps: int) -> int:
    if src_width <= 0 or src_height <= 0:
        raise ValueError("소스 해상도가 유효하지 않습니다.")
    fps = fps if fps > 0 else 30.0
    candidates = sorted({h for h in LADDER if h <= src_height} | {src_height}, reverse=True)
    for h in candidates:
        w = src_width * h / src_height
        if video_bps / (w * h * fps) >= BPP_TARGET:
            return h
    return candidates[-1]


def plan_encode(
    *,
    duration_s: float,
    fps: float,
    src_width: int,
    src_height: int,
    has_audio: bool,
    target_bytes: int = 49_900_000,
    min_video_kbps: int = 80,
) -> EncodePlan:
    if duration_s <= 0:
        raise ValueError("재생 시간이 유효하지 않습니다.")
    if target_bytes <= 0:
        raise ValueError("목표 크기가 유효하지 않습니다.")

    budget_bps = target_bytes * 8 / duration_s

    audio_kbps = 0
    if has_audio:
        audio_kbps = int(min(AUDIO_DEFAULT_KBPS, budget_bps * AUDIO_SHARE_CAP / 1000))
        audio_kbps = max(audio_kbps, AUDIO_MIN_KBPS)

    video_bps = budget_bps * CONTAINER_MARGIN - audio_kbps * 1000
    video_kbps = max(int(video_bps / 1000), min_video_kbps)

    height = pick_height(src_width, src_height, fps, video_kbps * 1000)
    return EncodePlan(
        video_kbps=video_kbps,
        audio_kbps=audio_kbps,
        height=height,
        has_audio=has_audio,
        fps=fps if fps > 0 else 30.0,
        duration_s=duration_s,
    )
```

- [ ] **Step 4: 통과 확인**

Run: `cd backend && uv run pytest tests/test_plan.py -v`
Expected: PASS 10 tests

- [ ] **Step 5: 커밋**

```bash
git add backend/app/plan.py backend/tests/test_plan.py
git commit -m "feat: bitrate budget and resolution ladder for target size fitting"
```

---

### Task 3: 보정 루프 (순수, 인코더 주입)

**Files:**
- Create: `backend/app/converge.py`
- Test: `backend/tests/test_converge.py`

**Interfaces:**
- Consumes: 없음
- Produces:
  - `ConvergeResult` frozen dataclass: `video_kbps: int`, `attempts: int`, `result_bytes: int`, `converged: bool`
  - `converge(*, target_bytes: int, initial_kbps: int, encode: Callable[[int], int], max_attempts: int = 3, floor_kbps: int = 80) -> ConvergeResult`

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_converge.py`:

```python
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
        return int(kbps / 1000 * TARGET)  # 항상 10배 초과

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
```

- [ ] **Step 2: 실패 확인**

Run: `cd backend && uv run pytest tests/test_converge.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.converge'`

- [ ] **Step 3: 구현**

`backend/app/converge.py`:

```python
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

    return ConvergeResult(kbps, attempts, result, result <= target_bytes)
```

- [ ] **Step 4: 통과 확인**

Run: `cd backend && uv run pytest tests/test_converge.py -v`
Expected: PASS 9 tests

- [ ] **Step 5: 커밋**

```bash
git add backend/app/converge.py backend/tests/test_converge.py
git commit -m "feat: iterative bitrate convergence loop for size targeting"
```

---

### Task 4: ffprobe 검증 + 메타데이터 추출

**Files:**
- Create: `backend/app/probe.py`
- Test: `backend/tests/test_probe.py`

**Interfaces:**
- Consumes: 없음
- Produces:
  - `ProbeResult` frozen dataclass: `duration_s: float`, `width: int`, `height: int`, `fps: float`, `has_audio: bool`
  - `probe(path: Path, *, ffprobe_path: str = "ffprobe", max_duration_s: float = 21600.0) -> ProbeResult`
  - `class ProbeError(ValueError)`

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_probe.py`:

```python
import subprocess

import pytest

from app.probe import ProbeError, ProbeResult, probe


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


def test_probe_zero_duration_rejected(tmp_path):
    empty = tmp_path / "empty.bin"
    empty.write_bytes(b"")
    with pytest.raises(ProbeError):
        probe(empty)
```

- [ ] **Step 2: 실패 확인**

Run: `cd backend && uv run pytest tests/test_probe.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.probe'`

- [ ] **Step 3: 구현**

`backend/app/probe.py`:

```python
import json
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path


class ProbeError(ValueError):
    pass


@dataclass(frozen=True)
class ProbeResult:
    duration_s: float
    width: int
    height: int
    fps: float
    has_audio: bool


def _parse_fps(raw: str | None) -> float:
    if not raw or raw in {"0/0", "N/A"}:
        return 30.0
    try:
        value = float(Fraction(raw))
    except (ValueError, ZeroDivisionError):
        return 30.0
    return value if value > 0 else 30.0


def probe(
    path: Path,
    *,
    ffprobe_path: str = "ffprobe",
    max_duration_s: float = 21600.0,
) -> ProbeResult:
    if not path.is_file() or path.stat().st_size == 0:
        raise ProbeError("파일이 비어 있거나 존재하지 않습니다.")

    cmd = [
        ffprobe_path, "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProbeError("파일 분석에 실패했습니다.") from exc

    if proc.returncode != 0:
        raise ProbeError("지원하지 않는 파일 형식입니다.")

    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise ProbeError("파일 분석 결과를 읽을 수 없습니다.") from exc

    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise ProbeError("비디오 스트림을 찾을 수 없습니다.")

    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    if width <= 0 or height <= 0:
        raise ProbeError("비디오 해상도를 읽을 수 없습니다.")

    duration_s = 0.0
    try:
        duration_s = float(data.get("format", {}).get("duration") or 0.0)
    except (TypeError, ValueError):
        duration_s = 0.0
    if duration_s <= 0:
        try:
            duration_s = float(video.get("duration") or 0.0)
        except (TypeError, ValueError):
            duration_s = 0.0
    if duration_s <= 0:
        raise ProbeError("재생 시간을 읽을 수 없습니다.")
    if duration_s > max_duration_s:
        raise ProbeError(
            f"재생 시간이 한도({int(max_duration_s / 3600)}시간)를 초과합니다."
        )

    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    fps = _parse_fps(video.get("avg_frame_rate"))

    return ProbeResult(
        duration_s=duration_s,
        width=width,
        height=height,
        fps=fps,
        has_audio=has_audio,
    )
```

- [ ] **Step 4: 통과 확인**

Run: `cd backend && uv run pytest tests/test_probe.py -v`
Expected: PASS 7 tests

- [ ] **Step 5: 커밋**

```bash
git add backend/app/probe.py backend/tests/test_probe.py
git commit -m "feat: ffprobe validation and metadata extraction"
```

---

### Task 5: 미디어 경로 + jobs 모델

**Files:**
- Create: `backend/app/paths.py`, `backend/app/models.py`
- Test: `backend/tests/test_paths.py`, `backend/tests/test_models.py`

**Interfaces:**
- Consumes: 없음
- Produces:
  - `MediaPaths(root: Path)` with `job_dir(job_id: UUID) -> Path`, `source(job_id) -> Path`, `result(job_id) -> Path`, `prepare(job_id) -> Path`, `remove_job(job_id) -> None`
  - `JobStatus` str-enum: `queued`, `probing`, `encoding`, `done`, `done_oversize`, `failed`, `cancelled`
  - `Job` SQLModel table `jobs` with columns listed in spec §4.1

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_paths.py`:

```python
import uuid

from app.paths import MediaPaths


def test_paths_are_uuid_scoped(tmp_path):
    p = MediaPaths(tmp_path)
    jid = uuid.uuid4()
    assert p.job_dir(jid) == tmp_path / str(jid)
    assert p.source(jid).name == "source.bin"
    assert p.result(jid).name == "result.webm"
    assert p.source(jid).parent == p.job_dir(jid)


def test_source_name_is_not_user_controlled(tmp_path):
    p = MediaPaths(tmp_path)
    jid = uuid.uuid4()
    assert p.source(jid).suffix == ".bin"


def test_prepare_creates_dir(tmp_path):
    p = MediaPaths(tmp_path)
    jid = uuid.uuid4()
    d = p.prepare(jid)
    assert d.is_dir()
    assert p.source(jid).parent == d


def test_remove_job(tmp_path):
    p = MediaPaths(tmp_path)
    jid = uuid.uuid4()
    p.prepare(jid)
    p.source(jid).write_bytes(b"x")
    p.remove_job(jid)
    assert not p.job_dir(jid).exists()


def test_remove_job_is_idempotent(tmp_path):
    p = MediaPaths(tmp_path)
    jid = uuid.uuid4()
    p.prepare(jid)
    p.remove_job(jid)
    p.remove_job(jid)  # 두 번째 호출은 조용히 성공
```

`backend/tests/test_models.py`:

```python
import uuid
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, SQLModel, create_engine, select

from app.models import Job, JobStatus


def test_job_roundtrip():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    now = datetime.now(timezone.utc)
    job = Job(
        id=uuid.uuid4(),
        session_id="abc123",
        status=JobStatus.queued,
        source_name="holiday.mp4",
        created_at=now,
        expires_at=now + timedelta(hours=24),
    )
    with Session(engine) as s:
        s.add(job)
        s.commit()
        got = s.exec(select(Job)).one()
        assert got.status is JobStatus.queued
        assert got.session_id == "abc123"
        assert got.attempt == 0
        assert got.progress == 0.0


def test_job_status_values():
    assert JobStatus.queued.value == "queued"
    assert JobStatus.done_oversize.value == "done_oversize"
    assert JobStatus.cancelled.value == "cancelled"
```

- [ ] **Step 2: 실패 확인**

Run: `cd backend && uv run pytest tests/test_paths.py tests/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 구현**

`backend/app/paths.py`:

```python
import shutil
import uuid
from pathlib import Path


class MediaPaths:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def job_dir(self, job_id: uuid.UUID) -> Path:
        return self.root / str(job_id)

    def source(self, job_id: uuid.UUID) -> Path:
        return self.job_dir(job_id) / "source.bin"

    def result(self, job_id: uuid.UUID) -> Path:
        return self.job_dir(job_id) / "result.webm"

    def prepare(self, job_id: uuid.UUID) -> Path:
        d = self.job_dir(job_id)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def remove_job(self, job_id: uuid.UUID) -> None:
        shutil.rmtree(self.job_dir(job_id), ignore_errors=True)
```

`backend/app/models.py`:

```python
import enum
import uuid
from datetime import datetime, timezone

from sqlmodel import Field, SQLModel


class JobStatus(str, enum.Enum):
    queued = "queued"
    probing = "probing"
    encoding = "encoding"
    done = "done"
    done_oversize = "done_oversize"
    failed = "failed"
    cancelled = "cancelled"


class Job(SQLModel, table=True):
    __tablename__ = "jobs"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    session_id: str = Field(index=True)
    status: JobStatus = Field(default=JobStatus.queued, index=True)
    attempt: int = Field(default=0)
    progress: float = Field(default=0.0)
    source_name: str = Field(default="")
    source_path: str | None = Field(default=None)
    result_path: str | None = Field(default=None)
    source_bytes: int | None = Field(default=None)
    result_bytes: int | None = Field(default=None)
    duration_s: float | None = Field(default=None)
    width: int | None = Field(default=None)
    height: int | None = Field(default=None)
    error: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime
```

> **검증 메모:** `JobStatus(str, enum.Enum)`을 기본 `Field`에 두면 SQLAlchemy `Enum` 타입이 매핑되어 Python에서 `JobStatus` 인스턴스로 되돌아온다 (`job.status is JobStatus.queued` 성립). `sa_type=String`으로 바꾸면 TEXT로 저장되지만 읽을 때 `str`이 되므로 identity 검사가 깨진다 — **기본 Enum 매핑 유지**. Postgres에서 `create_all`이 `CREATE TYPE`을 발행한다. 나중에 enum 값을 추가할 때만 `ALTER TYPE` 마이그레이션이 필요하다.
> `from datetime import datetime`를 쓰면 `datetime.UTC`가 아니라 `timezone.utc`를 써야 한다 (전자는 모듈 속성이 아니라 `datetime` 클래스에 없음).

- [ ] **Step 4: 통과 확인**

Run: `cd backend && uv run pytest tests/test_paths.py tests/test_models.py -v`
Expected: PASS 7 tests

- [ ] **Step 5: 커밋**

```bash
git add backend/app/paths.py backend/app/models.py backend/tests/test_paths.py backend/tests/test_models.py
git commit -m "feat: media path layout and job model"
```

---

### Task 6: 세션 쿠키 + 레이트리밋

**Files:**
- Create: `backend/app/session.py`, `backend/app/ratelimit.py`
- Test: `backend/tests/test_session.py`, `backend/tests/test_ratelimit.py`

**Interfaces:**
- Consumes: 없음
- Produces:
  - `SESSION_COOKIE: str = "sv_session"`
  - `issue(secret: str) -> tuple[str, str]` → `(session_id, cookie_value)`
  - `verify(secret: str, cookie_value: str) -> str | None`
  - `class RateLimitError(Exception)` with `retry_after_s: int`
  - `RateLimiter(per_hour: int, clock: Callable[[], float] = time.monotonic)` with `check_upload(key: str) -> None`, `check_active(key: str) -> None`, `release_active(key: str) -> None`

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_session.py`:

```python
from app.session import SESSION_COOKIE, issue, verify

SECRET = "test-secret"


def test_cookie_name():
    assert SESSION_COOKIE == "sv_session"


def test_issue_and_verify_roundtrip():
    sid, value = issue(SECRET)
    assert verify(SECRET, value) == sid


def test_session_ids_are_unique():
    assert issue(SECRET)[0] != issue(SECRET)[0]


def test_verify_rejects_tampered_signature():
    sid, value = issue(SECRET)
    bad_sid, bad_sig = value.rsplit(".", 1)
    assert verify(SECRET, f"{bad_sid}.{'0' * len(bad_sig)}") is None


def test_verify_rejects_tampered_id():
    _, value = issue(SECRET)
    _, sig = value.rsplit(".", 1)
    assert verify(SECRET, f"deadbeef.{sig}") is None


def test_verify_rejects_wrong_secret():
    _, value = issue(SECRET)
    assert verify("other-secret", value) is None


def test_verify_rejects_garbage():
    assert verify(SECRET, "") is None
    assert verify(SECRET, "no-dot-here") is None
    assert verify(SECRET, ".") is None
```

`backend/tests/test_ratelimit.py`:

```python
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


def test_active_slot():
    clock = FakeClock()
    rl = RateLimiter(per_hour=10, clock=clock)
    rl.check_active("k")
    with pytest.raises(RateLimitError):
        rl.check_active("k")
    rl.release_active("k")
    rl.check_active("k")  # 해제 후 재사용


def test_release_is_idempotent():
    rl = RateLimiter(per_hour=10)
    rl.release_active("k")
    rl.release_active("k")
```

- [ ] **Step 2: 실패 확인**

Run: `cd backend && uv run pytest tests/test_session.py tests/test_ratelimit.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 구현**

`backend/app/session.py`:

```python
import hashlib
import hmac
import secrets

SESSION_COOKIE = "sv_session"


def _sign(secret: str, session_id: str) -> str:
    return hmac.new(secret.encode(), session_id.encode(), hashlib.sha256).hexdigest()


def issue(secret: str) -> tuple[str, str]:
    session_id = secrets.token_hex(16)
    return session_id, f"{session_id}.{_sign(secret, session_id)}"


def verify(secret: str, cookie_value: str) -> str | None:
    if not cookie_value or "." not in cookie_value:
        return None
    session_id, _, sig = cookie_value.rpartition(".")
    if not session_id or not sig:
        return None
    if not hmac.compare_digest(sig, _sign(secret, session_id)):
        return None
    return session_id
```

`backend/app/ratelimit.py`:

```python
import threading
import time
from collections import deque
from collections.abc import Callable


class RateLimitError(Exception):
    def __init__(self, message: str, retry_after_s: int) -> None:
        super().__init__(message)
        self.retry_after_s = retry_after_s


# ponytail: 인메모리 리밋 — backend 단일 프로세스 전제. 스케일 아웃 시 Redis로 교체
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
        self._active: set[str] = set()
        self._lock = threading.Lock()

    def check_upload(self, key: str) -> None:
        now = self._clock()
        with self._lock:
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

    def check_active(self, key: str) -> None:
        with self._lock:
            if key in self._active:
                raise RateLimitError(
                    "이미 진행 중인 작업이 있습니다. 완료 후 다시 시도해 주세요.",
                    retry_after_s=0,
                )
            self._active.add(key)

    def release_active(self, key: str) -> None:
        with self._lock:
            self._active.discard(key)
```

- [ ] **Step 4: 통과 확인**

Run: `cd backend && uv run pytest tests/test_session.py tests/test_ratelimit.py -v`
Expected: PASS 13 tests

- [ ] **Step 5: 커밋**

```bash
git add backend/app/session.py backend/app/ratelimit.py backend/tests/test_session.py backend/tests/test_ratelimit.py
git commit -m "feat: signed session cookie and in-memory rate limiter"
```

---

### Task 7: ffmpeg 인코더 + 진행률

**Files:**
- Create: `backend/app/ffmpeg.py`
- Test: `backend/tests/test_encode_integration.py`

**Interfaces:**
- Consumes: `EncodePlan` from Task 2
- Produces:
  - `class EncodeError(RuntimeError)` with `.stderr: str`
  - `build_cmd(*, ffmpeg_path: str, src: Path, dst: Path, plan: EncodePlan, video_kbps: int) -> list[str]`
  - `encode_once(*, src: Path, dst: Path, plan: EncodePlan, video_kbps: int, ffmpeg_path: str = "ffmpeg", on_progress: Callable[[float], None] | None = None) -> int` → 출력 바이트 수

- [ ] **Step 1: 실패하는 통합 테스트 작성**

`backend/tests/test_encode_integration.py`:

```python
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
            "-shortest", "-y", str(out),
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
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", "-y", str(out),
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
```

- [ ] **Step 2: 실패 확인**

Run: `cd backend && uv run pytest tests/test_encode_integration.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.ffmpeg'`

- [ ] **Step 3: 구현**

`backend/app/ffmpeg.py`:

```python
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path

from app.plan import EncodePlan


class EncodeError(RuntimeError):
    def __init__(self, message: str, stderr: str = "") -> None:
        super().__init__(message)
        self.stderr = stderr


def build_cmd(
    *,
    ffmpeg_path: str,
    src: Path,
    dst: Path,
    plan: EncodePlan,
    video_kbps: int,
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
        "-c:v", "vp9_qsv", "-preset", "medium",
        "-b:v", f"{v}k",
        "-maxrate", f"{int(v * 1.2)}k",
        "-bufsize", f"{int(v * 2)}k",
        "-vf", f"scale=-2:{int(plan.height)}:flags=bicubic,format=nv12",
    ]
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
    ffmpeg_path: str = "ffmpeg",
    on_progress: Callable[[float], None] | None = None,
) -> int:
    if not src.is_file():
        raise EncodeError(f"입력 파일이 없습니다: {src.name}")

    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = build_cmd(ffmpeg_path=ffmpeg_path, src=src, dst=dst, plan=plan, video_kbps=video_kbps)

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

    rc = proc.wait()
    t.join(timeout=5)

    if rc != 0:
        stderr = "".join(err_lines)[-2000:]
        raise EncodeError("인코딩에 실패했습니다.", stderr=stderr)

    if not dst.is_file() or dst.stat().st_size == 0:
        raise EncodeError("인코딩 결과가 비어 있습니다.", stderr="".join(err_lines)[-2000:])

    return dst.stat().st_size
```

- [ ] **Step 4: 통과 확인**

Run: `cd backend && uv run pytest tests/test_encode_integration.py -v`
Expected: PASS 5 tests

- [ ] **Step 5: 커밋**

```bash
git add backend/app/ffmpeg.py backend/tests/test_encode_integration.py
git commit -m "feat: ffmpeg vp9_qsv/opus encoder with live progress"
```

---

### Task 8: API — 업로드/상태/다운로드/삭제/config

**Files:**
- Create: `backend/app/api/deps.py`, `backend/app/api/jobs.py`, `backend/app/api/config.py`, `backend/app/schemas.py`
- Modify: `backend/app/main.py` (라우터 등록, 정적 서빙)
- Test: `backend/tests/test_api_jobs.py`

**Interfaces:**
- Consumes: Task 1 `Settings`, `get_session`; Task 5 `Job`, `JobStatus`, `MediaPaths`; Task 6 `issue`, `verify`, `SESSION_COOKIE`, `RateLimiter`, `RateLimitError`
- Produces:
  - `app.api.deps.require_session(request, response) -> str` (Depends)
  - `JobCreated {id: str, status: str}`
  - `JobStatusOut {id: str, status: str, attempt: int, progress: float, source_name: str, source_bytes: int | None, result_bytes: int | None, duration_s: float | None, error: str | None, expires_at: str}`
  - `ConfigOut {max_upload_bytes: int, target_bytes: int, max_duration_s: float}`

- [ ] **Step 1: 스키마 + 의존성 작성**

`backend/app/schemas.py`:

```python
from pydantic import BaseModel


class JobCreated(BaseModel):
    id: str
    status: str


class JobStatusOut(BaseModel):
    id: str
    status: str
    attempt: int
    progress: float
    source_name: str
    source_bytes: int | None = None
    result_bytes: int | None = None
    duration_s: float | None = None
    error: str | None = None
    expires_at: str


class ConfigOut(BaseModel):
    max_upload_bytes: int
    target_bytes: int
    max_duration_s: float
```

`backend/app/api/deps.py`:

```python
from fastapi import Depends, HTTPException, Request, Response

from app.ratelimit import RateLimiter
from app.session import SESSION_COOKIE, issue, verify
from app.settings import Settings

_limiter = RateLimiter(per_hour=5)


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


def get_limiter() -> RateLimiter:
    return _limiter


def require_session(
    request: Request,
    response: Response,
    settings: Settings = Depends(get_settings_dep),
) -> str:
    raw = request.cookies.get(settings.session_cookie)
    session_id = verify(settings.session_secret, raw) if raw else None
    if session_id is None:
        session_id, cookie_value = issue(settings.session_secret)
        response.set_cookie(
            settings.session_cookie,
            cookie_value,
            max_age=settings.session_ttl_days * 86400,
            httponly=True,
            samesite="lax",
            secure=request.url.scheme == "https",
            path="/",
        )
    return session_id
```

- [ ] **Step 2: 잡 라우터 작성**

`backend/app/api/jobs.py`:

```python
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from sqlmodel import Session, select

from app.api.deps import get_limiter, get_settings_dep, require_session
from app.db import get_session
from app.models import Job, JobStatus
from app.paths import MediaPaths
from app.ratelimit import RateLimitError, RateLimiter
from app.schemas import JobCreated, JobStatusOut
from app.settings import Settings

router = APIRouter(tags=["jobs"])


def _paths(settings: Settings) -> MediaPaths:
    return MediaPaths(settings.media_dir)


def _to_out(job: Job) -> JobStatusOut:
    return JobStatusOut(
        id=str(job.id),
        status=job.status.value,
        attempt=job.attempt,
        progress=job.progress,
        source_name=job.source_name,
        source_bytes=job.source_bytes,
        result_bytes=job.result_bytes,
        duration_s=job.duration_s,
        error=job.error,
        expires_at=job.expires_at.isoformat(),
    )


def _get_owned(job_id: uuid.UUID, session_id: str, session: Session) -> Job:
    job = session.get(Job, job_id)
    if job is None or job.session_id != session_id:
        raise HTTPException(status_code=404, detail="작업을 찾을 수 없습니다.")
    return job


@router.post("/jobs", response_model=JobCreated, status_code=201)
async def create_job(
    file: UploadFile,
    session_id: str = Depends(require_session),
    settings: Settings = Depends(get_settings_dep),
    limiter: RateLimiter = Depends(get_limiter),
    session: Session = Depends(get_session),
) -> JobCreated:
    key = session_id
    try:
        limiter.check_upload(key)
        limiter.check_active(key)
    except RateLimitError as exc:
        raise HTTPException(
            status_code=429,
            detail=str(exc),
            headers={"Retry-After": str(exc.retry_after_s or 60)},
        ) from exc

    job_id = uuid.uuid4()
    paths = _paths(settings)
    paths.prepare(job_id)
    dest = paths.source(job_id)

    size = 0
    try:
        with dest.open("wb") as out:
            while chunk := await file.read(1 << 20):
                size += len(chunk)
                if size > settings.max_upload_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail=f"업로드 한도({settings.max_upload_bytes // (1024 * 1024)}MB)를 초과했습니다.",
                    )
                out.write(chunk)
        if size == 0:
            raise HTTPException(status_code=400, detail="빈 파일은 업로드할 수 없습니다.")
    except BaseException:
        paths.remove_job(job_id)
        limiter.release_active(key)
        raise

    now = datetime.now(timezone.utc)
    job = Job(
        id=job_id,
        session_id=session_id,
        status=JobStatus.queued,
        source_name=(file.filename or "video")[:200],
        source_path=str(dest),
        source_bytes=size,
        created_at=now,
        expires_at=now + timedelta(hours=settings.retention_hours),
    )
    session.add(job)
    session.commit()
    return JobCreated(id=str(job.id), status=job.status.value)


@router.get("/jobs/{job_id}", response_model=JobStatusOut)
def get_job(
    job_id: uuid.UUID,
    session_id: str = Depends(require_session),
    session: Session = Depends(get_session),
) -> JobStatusOut:
    return _to_out(_get_owned(job_id, session_id, session))


@router.get("/jobs/{job_id}/download")
def download_job(
    job_id: uuid.UUID,
    session_id: str = Depends(require_session),
    session: Session = Depends(get_session),
) -> FileResponse:
    job = _get_owned(job_id, session_id, session)
    if job.status not in (JobStatus.done, JobStatus.done_oversize):
        raise HTTPException(status_code=409, detail="아직 완료되지 않은 작업입니다.")
    if not job.result_path:
        raise HTTPException(status_code=404, detail="결과 파일이 없습니다.")
    return FileResponse(
        job.result_path,
        media_type="video/webm",
        filename=f"compressed_{job.id}.webm",
    )


@router.delete("/jobs/{job_id}", status_code=204)
def delete_job(
    job_id: uuid.UUID,
    session_id: str = Depends(require_session),
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> None:
    job = _get_owned(job_id, session_id, session)
    _paths(settings).remove_job(job_id)
    session.delete(job)
    session.commit()
```

`backend/app/api/config.py`:

```python
from fastapi import APIRouter, Depends

from app.api.deps import get_settings_dep
from app.schemas import ConfigOut
from app.settings import Settings

router = APIRouter(tags=["config"])


@router.get("/config", response_model=ConfigOut)
def get_config(settings: Settings = Depends(get_settings_dep)) -> ConfigOut:
    return ConfigOut(
        max_upload_bytes=settings.max_upload_bytes,
        target_bytes=settings.target_bytes,
        max_duration_s=settings.max_duration_s,
    )
```

- [ ] **Step 3: `main.py`에 라우터 등록**

`backend/app/main.py`를 아래와 같이 교체:

```python
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.settings import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="sevi", version="0.1.0")
    app.state.settings = settings

    from app.api.config import router as config_router
    from app.api.health import router as health_router
    from app.api.jobs import router as jobs_router

    app.include_router(health_router, prefix="/api")
    app.include_router(config_router, prefix="/api")
    app.include_router(jobs_router, prefix="/api")

    static_dir = Path(__file__).parent / "static"
    if static_dir.is_dir():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
    return app


app = create_app()
```

- [ ] **Step 4: 실패하는 API 테스트 작성**

`backend/tests/test_api_jobs.py`:

```python
import io

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine

from app.db import get_session
from app.main import create_app
from app.paths import MediaPaths
from app.settings import Settings


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        database_url="sqlite://",
        media_dir=tmp_path / "media",
        session_secret="test-secret",
        max_upload_bytes=1024 * 1024,
        rate_limit_per_hour=100,
    )


@pytest.fixture
def client(settings):
    engine = create_engine(
        settings.database_url, connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(engine)

    def _override():
        with Session(engine) as s:
            yield s

    app = create_app(settings)
    app.dependency_overrides[get_session] = _override
    with TestClient(app) as c:
        c.engine = engine
        yield c


def _upload(client, name="clip.mp4", data=b"fake-video-bytes"):
    return client.post("/api/jobs", files={"file": (name, io.BytesIO(data), "video/mp4")})


def test_config_endpoint(client):
    r = client.get("/api/config")
    assert r.status_code == 200
    body = r.json()
    assert body["target_bytes"] == 49_900_000
    assert body["max_upload_bytes"] == 1024 * 1024


def test_upload_creates_job_and_sets_cookie(client):
    r = _upload(client)
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "queued"
    assert "sv_session" in r.cookies


def test_upload_then_get_status(client):
    job_id = _upload(client).json()["id"]
    r = client.get(f"/api/jobs/{job_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "queued"
    assert body["source_name"] == "clip.mp4"
    assert body["source_bytes"] == len(b"fake-video-bytes")
    assert body["progress"] == 0.0


def test_download_requires_done_status(client):
    job_id = _upload(client).json()["id"]
    r = client.get(f"/api/jobs/{job_id}/download")
    assert r.status_code == 409


def test_foreign_session_gets_404(client):
    job_id = _upload(client).json()["id"]
    client.cookies.clear()
    r = client.get(f"/api/jobs/{job_id}")
    assert r.status_code == 404


def test_oversize_upload_rejected(settings):
    engine = create_engine(settings.database_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)

    def _override():
        with Session(engine) as s:
            yield s

    app = create_app(settings)
    app.dependency_overrides[get_session] = _override
    with TestClient(app) as c:
        big = b"x" * (settings.max_upload_bytes + 1)
        r = c.post("/api/jobs", files={"file": ("big.mp4", io.BytesIO(big), "video/mp4")})
        assert r.status_code == 413


def test_empty_upload_rejected(client):
    r = _upload(client, data=b"")
    assert r.status_code == 400


def test_delete_removes_job_and_files(client, settings):
    job_id = _upload(client).json()["id"]
    paths = MediaPaths(settings.media_dir)
    from uuid import UUID

    assert paths.source(UUID(job_id)).is_file()
    r = client.delete(f"/api/jobs/{job_id}")
    assert r.status_code == 204
    assert not paths.job_dir(UUID(job_id)).exists()
    assert client.get(f"/api/jobs/{job_id}").status_code == 404


def test_rate_limit_returns_429(settings, monkeypatch):
    settings = settings.model_copy(update={"rate_limit_per_hour": 1})
    engine = create_engine(settings.database_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)

    def _override():
        with Session(engine) as s:
            yield s

    app = create_app(settings)
    app.dependency_overrides[get_session] = _override
    with TestClient(app) as c:
        import app.api.deps as deps
        monkeypatch.setattr(deps, "_limiter", deps.RateLimiter(per_hour=1))
        assert c.post("/api/jobs", files={"file": ("a.mp4", io.BytesIO(b"x"), "video/mp4")}).status_code == 201
        assert c.post("/api/jobs", files={"file": ("b.mp4", io.BytesIO(b"x"), "video/mp4")}).status_code == 429
```

- [ ] **Step 5: 실패 확인 후 전 테스트 실행**

Run: `cd backend && uv run pytest tests/test_api_jobs.py -v`
Expected: FAIL (테스트가 코드보다 앞서 있으므로) → 구현 후 PASS
Then: `cd backend && uv run pytest -v`
Expected: PASS all

- [ ] **Step 6: 커밋**

```bash
git add backend/app tests 2>/dev/null; git add backend/app backend/tests
git commit -m "feat: job upload, status, download, delete and config API"
```

---

### Task 9: 워커 — 큐 클레임, 인코딩, 정리

**Files:**
- Create: `backend/app/worker.py`
- Test: `backend/tests/test_worker.py`

**Interfaces:**
- Consumes: Task 2 `plan_encode`, Task 3 `converge`, Task 4 `probe`, Task 5 `Job`/`JobStatus`/`MediaPaths`, Task 7 `encode_once`
- Produces:
  - `sweep(session: Session, paths: MediaPaths, *, now: datetime | None = None) -> int`
  - `claim_next(session: Session) -> Job | None`
  - `process_job(session: Session, job: Job, *, settings: Settings, paths: MediaPaths, encode_fn: Callable[..., int] | None = None) -> None`
  - `run_forever(settings: Settings | None = None) -> None` (`python -m app.worker`)

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_worker.py`:

```python
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import Job, JobStatus
from app.paths import MediaPaths
from app.plan import EncodePlan
from app.probe import ProbeResult
from app.settings import Settings
from app.worker import claim_next, process_job, sweep


@pytest.fixture
def engine():
    e = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(e)
    return e


@pytest.fixture
def session(engine):
    with Session(engine) as s:
        yield s


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(database_url="sqlite://", media_dir=tmp_path / "media", session_secret="t")


@pytest.fixture
def paths(settings) -> MediaPaths:
    return MediaPaths(settings.media_dir)


def _make_job(session: Session, paths: MediaPaths, **kw) -> Job:
    jid = uuid.uuid4()
    paths.prepare(jid)
    paths.source(jid).write_bytes(b"fake")
    now = datetime.now(timezone.utc)
    job = Job(
        id=jid,
        session_id=kw.pop("session_id", "sid"),
        status=kw.pop("status", JobStatus.queued),
        source_path=str(paths.source(jid)),
        source_bytes=4,
        created_at=kw.pop("created_at", now),
        expires_at=kw.pop("expires_at", now + timedelta(hours=24)),
        **kw,
    )
    session.add(job)
    session.commit()
    return job


def test_claim_next_picks_oldest_queued(session, paths):
    j1 = _make_job(session, paths, created_at=datetime.now(timezone.utc) - timedelta(minutes=2))
    j2 = _make_job(session, paths)
    first = claim_next(session)
    assert first is not None and first.id == j1.id
    assert first.status is JobStatus.encoding
    second = claim_next(session)
    assert second is not None and second.id == j2.id


def test_claim_next_ignores_non_queued(session, paths):
    _make_job(session, paths, status=JobStatus.done)
    assert claim_next(session) is None


def test_sweep_removes_expired(session, paths):
    old = _make_job(session, paths, expires_at=datetime.now(timezone.utc) - timedelta(hours=1))
    fresh = _make_job(session, paths)
    removed = sweep(session, paths)
    assert removed == 1
    assert session.get(Job, old.id) is None
    assert session.get(Job, fresh.id) is not None
    assert not paths.job_dir(old.id).exists()
    assert paths.job_dir(fresh.id).exists()


def test_process_job_success(session, paths, settings, monkeypatch):
    job = _make_job(session, paths)

    monkeypatch.setattr(
        "app.worker.probe",
        lambda *a, **k: ProbeResult(duration_s=2.0, width=640, height=360, fps=30.0, has_audio=True),
    )
    monkeypatch.setattr(
        "app.worker.plan_encode",
        lambda **k: EncodePlan(video_kbps=500, audio_kbps=96, height=360, has_audio=True, fps=30.0, duration_s=2.0),
    )
    monkeypatch.setattr(
        "app.worker.converge",
        lambda **k: type("R", (), {"video_kbps": 500, "attempts": 1, "result_bytes": 1234, "converged": True})(),
    )

    def fake_encode(**kwargs):
        dst = kwargs["dst"]
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(b"r" * 1234)
        return 1234

    monkeypatch.setattr("app.worker.encode_once", fake_encode)

    process_job(session, job, settings=settings, paths=paths)
    session.refresh(job)

    assert job.status is JobStatus.done
    assert job.result_bytes == 1234
    assert job.width == 640 and job.height == 360
    assert job.source_path is None  # 원본은 즉시 삭제
    assert not paths.source(job.id).exists()
    assert paths.result(job.id).exists()


def test_process_job_oversize_marks_done_oversize(session, paths, settings, monkeypatch):
    job = _make_job(session, paths)
    monkeypatch.setattr(
        "app.worker.probe",
        lambda *a, **k: ProbeResult(duration_s=2.0, width=640, height=360, fps=30.0, has_audio=False),
    )
    monkeypatch.setattr(
        "app.worker.plan_encode",
        lambda **k: EncodePlan(video_kbps=80, audio_kbps=0, height=240, has_audio=False, fps=30.0, duration_s=2.0),
    )
    monkeypatch.setattr(
        "app.worker.converge",
        lambda **k: type("R", (), {"video_kbps": 80, "attempts": 3, "result_bytes": 60_000_000, "converged": False})(),
    )
    def fake_encode_oversize(**kwargs):
        kwargs["dst"].parent.mkdir(parents=True, exist_ok=True)
        kwargs["dst"].write_bytes(b"x")
        return 60_000_000

    monkeypatch.setattr("app.worker.encode_once", fake_encode_oversize)
    process_job(session, job, settings=settings, paths=paths)
    session.refresh(job)
    assert job.status is JobStatus.done_oversize
    assert job.error is not None


def test_process_job_probe_failure(session, paths, settings, monkeypatch):
    job = _make_job(session, paths)

    def boom(*a, **k):
        from app.probe import ProbeError

        raise ProbeError("비디오 스트림을 찾을 수 없습니다.")

    monkeypatch.setattr("app.worker.probe", boom)
    process_job(session, job, settings=settings, paths=paths)
    session.refresh(job)
    assert job.status is JobStatus.failed
    assert "비디오" in (job.error or "")
    assert not paths.source(job.id).exists()


def test_process_job_marks_cancelled_if_cancelled_midflight(session, paths, settings):
    job = _make_job(session, paths, status=JobStatus.cancelled)
    process_job(session, job, settings=settings, paths=paths)
    session.refresh(job)
    assert job.status is JobStatus.cancelled
```

- [ ] **Step 2: 실패 확인**

Run: `cd backend && uv run pytest tests/test_worker.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.worker'`

- [ ] **Step 3: 구현**

`backend/app/worker.py`:

```python
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from app.converge import converge
from app.db import engine, init_db
from app.ffmpeg import encode_once
from app.models import Job, JobStatus
from app.paths import MediaPaths
from app.plan import plan_encode
from app.probe import ProbeError, probe
from app.settings import Settings, get_settings

log = logging.getLogger("sevi.worker")


def sweep(session: Session, paths: MediaPaths, *, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    expired = session.exec(select(Job).where(Job.expires_at < now)).all()
    for job in expired:
        paths.remove_job(job.id)
        session.delete(job)
    session.commit()
    return len(expired)


def claim_next(session: Session) -> Job | None:
    job = session.exec(
        select(Job).where(Job.status == JobStatus.queued).order_by(Job.created_at)
    ).first()
    if job is None:
        return None
    job.status = JobStatus.encoding
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def process_job(
    session: Session,
    job: Job,
    *,
    settings: Settings,
    paths: MediaPaths,
    encode_fn=None,
) -> None:
    if job.status is JobStatus.cancelled:
        return

    encode = encode_fn or encode_once
    src = paths.source(job.id)

    try:
        job.status = JobStatus.probing
        session.add(job)
        session.commit()

        info = probe(
            src,
            ffprobe_path=settings.ffprobe_path,
            max_duration_s=settings.max_duration_s,
        )
        job.duration_s = info.duration_s
        job.width = info.width
        job.height = info.height
        session.add(job)
        session.commit()

        plan = plan_encode(
            duration_s=info.duration_s,
            fps=info.fps,
            src_width=info.width,
            src_height=info.height,
            has_audio=info.has_audio,
            target_bytes=settings.target_bytes,
            min_video_kbps=settings.min_video_kbps,
        )

        dst = paths.result(job.id)

        def _run(kbps: int) -> int:
            job.status = JobStatus.encoding
            job.attempt += 1
            job.progress = 0.0
            session.add(job)
            session.commit()
            return encode(
                src=src,
                dst=dst,
                plan=plan,
                video_kbps=kbps,
                ffmpeg_path=settings.ffmpeg_path,
                on_progress=_make_progress_cb(session, job),
            )

        result = converge(
            target_bytes=settings.target_bytes,
            initial_kbps=plan.video_kbps,
            encode=_run,
            max_attempts=settings.max_attempts,
            floor_kbps=settings.min_video_kbps,
        )

        job.result_path = str(dst)
        job.result_bytes = result.result_bytes
        job.progress = 1.0
        if result.result_bytes <= settings.target_bytes:
            job.status = JobStatus.done
            job.error = None
        else:
            job.status = JobStatus.done_oversize
            job.error = (
                f"목표 {settings.target_bytes // 1_000_000}MB를 "
                f"{(result.result_bytes - settings.target_bytes) // 1_000_000}MB 초과했습니다."
            )

    except ProbeError as exc:
        job.status = JobStatus.failed
        job.error = str(exc)
    except Exception as exc:  # noqa: BLE001 — 워커는 어떤 실패도 잡아야 함
        log.exception("job %s failed", job.id)
        job.status = JobStatus.failed
        job.error = "인코딩 중 오류가 발생했습니다."
    finally:
        job.source_path = None
        if src.exists():
            src.unlink(missing_ok=True)
        session.add(job)
        session.commit()


def _make_progress_cb(session: Session, job: Job):
    last = {"t": 0.0}

    def cb(fraction: float) -> None:
        now = time.monotonic()
        if now - last["t"] < 1.0 and fraction < 1.0:
            return
        last["t"] = now
        job.progress = max(0.0, min(1.0, fraction))
        session.add(job)
        session.commit()

    return cb


def run_forever(settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    logging.basicConfig(level=settings.log_level)
    init_db()
    paths = MediaPaths(settings.media_dir)
    settings.media_dir.mkdir(parents=True, exist_ok=True)
    log.info("worker started; media_dir=%s", settings.media_dir)

    with Session(engine) as session:
        while True:
            try:
                removed = sweep(session, paths)
                if removed:
                    log.info("swept %d expired jobs", removed)
                job = claim_next(session)
                if job is not None:
                    log.info("processing job %s", job.id)
                    process_job(session, job, settings=settings, paths=paths)
            except Exception:  # noqa: BLE001
                log.exception("worker tick failed")
            time.sleep(settings.worker_poll_s)


if __name__ == "__main__":
    run_forever()
```

- [ ] **Step 4: 통과 확인**

Run: `cd backend && uv run pytest tests/test_worker.py -v`
Expected: PASS 7 tests
Then: `cd backend && uv run pytest -v`
Expected: PASS all

- [ ] **Step 5: 커밋**

```bash
git add backend/app/worker.py backend/tests/test_worker.py
git commit -m "feat: background worker with job claiming, encoding and expiry sweep"
```

---

### Task 10: 프런트엔드

**Files:**
- Create: `frontend/package.json`, `frontend/tsconfig.json`, `frontend/vite.config.ts`, `frontend/index.html`, `frontend/src/main.tsx`, `frontend/src/index.css`, `frontend/src/routes/index.tsx`, `frontend/src/components/DropZone.tsx`, `frontend/src/components/JobStatus.tsx`, `frontend/src/lib/api.ts`
- Test: 없음 (수동 확인 + `bun run typecheck` + `bun run build`)

**Interfaces:**
- Consumes: `POST /api/jobs`, `GET /api/jobs/{id}`, `GET /api/jobs/{id}/download`, `GET /api/config`
- Produces: 정적 빌드 산출물 `frontend/dist/` (Task 11 Docker가 복사)

- [ ] **Step 1: 프로젝트 파일 작성**

`frontend/package.json`:

```json
{
  "name": "sevi-frontend",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "vite build",
    "typecheck": "tsc --noEmit",
    "lint": "tsc --noEmit"
  },
  "dependencies": {
    "@tanstack/react-query": "^5.66",
    "react": "^19.0",
    "react-dom": "^19.0"
  },
  "devDependencies": {
    "@types/react": "^19.0",
    "@types/react-dom": "^19.0",
    "@vitejs/plugin-react": "^4.3",
    "typescript": "^5.7",
    "vite": "^6.0"
  }
}
```

`frontend/tsconfig.json`:

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "moduleResolution": "bundler",
    "jsx": "react-jsx",
    "strict": true,
    "noEmit": true,
    "skipLibCheck": true,
    "isolatedModules": true,
    "resolveJsonModule": true
  },
  "include": ["src"]
}
```

`frontend/vite.config.ts`:

```ts
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: { "/api": "http://localhost:8000" },
  },
});
```

`frontend/index.html`:

```html
<!doctype html>
<html lang="ko">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>sevi — 50MB 비디오 압축</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

`frontend/src/index.css`:

```css
:root {
  color-scheme: light dark;
  font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
  line-height: 1.5;
}
body { margin: 0; }
#root { max-width: 720px; margin: 0 auto; padding: 2rem 1rem; }
.drop {
  border: 2px dashed #8884; border-radius: 12px; padding: 3rem 1rem;
  text-align: center; cursor: pointer; transition: border-color .15s, background .15s;
}
.drop.over { border-color: #4f8; background: #4f81; }
.bar { height: 10px; background: #8883; border-radius: 999px; overflow: hidden; }
.bar > i { display: block; height: 100%; background: #4f8; transition: width .3s; }
.card { border: 1px solid #8883; border-radius: 12px; padding: 1.25rem; margin-top: 1rem; }
.row { display: flex; justify-content: space-between; padding: .25rem 0; }
.muted { opacity: .65; font-size: .9em; }
.err { color: #e55; }
button {
  font: inherit; padding: .65rem 1.25rem; border-radius: 10px; border: 0;
  background: #4f8; color: #062; cursor: pointer; font-weight: 600;
}
button:disabled { opacity: .5; cursor: not-allowed; }
```

- [ ] **Step 2: API 클라이언트 작성**

`frontend/src/lib/api.ts`:

```ts
export type ConfigOut = {
  max_upload_bytes: number;
  target_bytes: number;
  max_duration_s: number;
};

export type JobStatusOut = {
  id: string;
  status: "queued" | "probing" | "encoding" | "done" | "done_oversize" | "failed" | "cancelled";
  attempt: number;
  progress: number;
  source_name: string;
  source_bytes: number | null;
  result_bytes: number | null;
  duration_s: number | null;
  error: string | null;
  expires_at: string;
};

export type JobCreated = { id: string; status: string };

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = `요청에 실패했습니다 (${res.status})`;
    try {
      const body = await res.json();
      if (body?.detail) detail = String(body.detail);
    } catch {
      /* keep default */
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export function getConfig(): Promise<ConfigOut> {
  return fetch("/api/config").then(json<ConfigOut>);
}

export function getJob(id: string): Promise<JobStatusOut> {
  return fetch(`/api/jobs/${id}`).then(json<JobStatusOut>);
}

export function deleteJob(id: string): Promise<void> {
  return fetch(`/api/jobs/${id}`, { method: "DELETE" }).then((r) => {
    if (!r.ok && r.status !== 204) throw new Error("삭제에 실패했습니다.");
  });
}

export function downloadUrl(id: string): string {
  return `/api/jobs/${id}/download`;
}

export function uploadJob(
  file: File,
  onProgress: (fraction: number) => void,
): Promise<JobCreated> {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    form.append("file", file);
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/jobs");
    xhr.responseType = "json";
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress(e.loaded / e.total);
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(xhr.response as JobCreated);
      } else {
        const detail = (xhr.response as { detail?: string } | null)?.detail;
        reject(new Error(detail || `업로드에 실패했습니다 (${xhr.status})`));
      }
    };
    xhr.onerror = () => reject(new Error("네트워크 오류가 발생했습니다."));
    xhr.send(form);
  });
}
```

- [ ] **Step 3: 컴포넌트 작성**

`frontend/src/components/DropZone.tsx`:

```tsx
import { useCallback, useRef, useState } from "react";

export function DropZone({
  disabled,
  onFile,
}: {
  disabled: boolean;
  onFile: (file: File) => void;
}) {
  const [over, setOver] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  const take = useCallback(
    (files: FileList | null) => {
      const f = files?.[0];
      if (f) onFile(f);
    },
    [onFile],
  );

  return (
    <div
      className={`drop${over ? " over" : ""}`}
      onClick={() => !disabled && input.current?.click()}
      onDragOver={(e) => {
        e.preventDefault();
        if (!disabled) setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setOver(false);
        if (!disabled) take(e.dataTransfer.files);
      }}
    >
      <p>
        <strong>영상을 여기에 끌어다 놓거나</strong> 클릭해서 선택하세요
      </p>
      <p className="muted">자동으로 49.9MB 이하 .webm 으로 압축됩니다</p>
      <input
        ref={input}
        type="file"
        accept="video/*"
        hidden
        onChange={(e) => take(e.target.files)}
      />
    </div>
  );
}
```

`frontend/src/components/JobStatus.tsx`:

```tsx
import { downloadUrl, type JobStatusOut } from "../lib/api";

const LABEL: Record<JobStatusOut["status"], string> = {
  queued: "대기 중",
  probing: "파일 분석 중",
  encoding: "인코딩 중",
  done: "완료",
  done_oversize: "완료 (목표 초과)",
  failed: "실패",
  cancelled: "취소됨",
};

function mb(bytes: number | null): string {
  if (bytes == null) return "-";
  return `${(bytes / 1_000_000).toFixed(1)}MB`;
}

export function JobStatus({ job }: { job: JobStatusOut }) {
  const pct = Math.round(job.progress * 100);
  const saved =
    job.source_bytes && job.result_bytes != null
      ? Math.max(0, 1 - job.result_bytes / job.source_bytes)
      : null;

  return (
    <div className="card">
      <div className="row">
        <span>{LABEL[job.status]}</span>
        <span className="muted">
          시도 {job.attempt} · {pct}%
        </span>
      </div>
      <div className="bar">
        <i style={{ width: `${pct}%` }} />
      </div>

      {job.result_bytes != null && (
        <div className="row">
          <span className="muted">원본 → 결과</span>
          <span>
            {mb(job.source_bytes)} → {mb(job.result_bytes)}
            {saved != null && ` (${(saved * 100).toFixed(0)}% 절감)`}
          </span>
        </div>
      )}

      {job.error && <p className="err">{job.error}</p>}

      {(job.status === "done" || job.status === "done_oversize") && (
        <p>
          <a href={downloadUrl(job.id)}>
            <button type="button">다운로드</button>
          </a>
        </p>
      )}
    </div>
  );
}
```

- [ ] **Step 4: 메인 페이지 작성**

`frontend/src/main.tsx`:

```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import { App } from "./routes/index";

const qc = new QueryClient();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={qc}>
      <App />
    </QueryClientProvider>
  </StrictMode>,
);
```

`frontend/src/routes/index.tsx`:

```tsx
import { useQuery } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import { DropZone } from "../components/DropZone";
import { JobStatus } from "../components/JobStatus";
import {
  deleteJob,
  getConfig,
  getJob,
  uploadJob,
  type JobStatusOut,
} from "../lib/api";

function mb(bytes: number): string {
  return `${(bytes / 1_000_000).toFixed(0)}MB`;
}

export function App() {
  const { data: config } = useQuery({ queryKey: ["config"], queryFn: getConfig });
  const [phase, setPhase] = useState<"idle" | "upload" | "job">("idle");
  const [uploadPct, setUploadPct] = useState(0);
  const [jobId, setJobId] = useState<string | null>(null);
  const [job, setJob] = useState<JobStatusOut | null>(null);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<number | null>(null);

  const stopPolling = () => {
    if (timer.current != null) {
      window.clearInterval(timer.current);
      timer.current = null;
    }
  };

  useEffect(() => stopPolling, []);

  useEffect(() => {
    if (!jobId) return;
    stopPolling();
    timer.current = window.setInterval(async () => {
      try {
        const j = await getJob(jobId);
        setJob(j);
        if (j.status === "done" || j.status === "done_oversize" || j.status === "failed") {
          stopPolling();
        }
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
        stopPolling();
      }
    }, 2000);
    return stopPolling;
  }, [jobId]);

  const onFile = useCallback(
    async (file: File) => {
      setError(null);
      if (config && file.size > config.max_upload_bytes) {
        setError(`업로드 한도(${mb(config.max_upload_bytes)})를 초과했습니다.`);
        return;
      }
      setPhase("upload");
      setUploadPct(0);
      setJob(null);
      setJobId(null);
      try {
        const created = await uploadJob(file, setUploadPct);
        setJobId(created.id);
        setPhase("job");
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
        setPhase("idle");
      }
    },
    [config],
  );

  const reset = () => {
    stopPolling();
    setPhase("idle");
    setJob(null);
    setJobId(null);
    setError(null);
    setUploadPct(0);
  };

  const remove = async () => {
    if (jobId) await deleteJob(jobId).catch(() => undefined);
    reset();
  };

  return (
    <>
      <h1>sevi</h1>
      <p className="muted">
        영상을 49.9MB 이하 .webm (VP9 + Opus) 으로 압축합니다
        {config && ` · 최대 업로드 ${mb(config.max_upload_bytes)}`}
      </p>

      {phase === "idle" && <DropZone disabled={false} onFile={onFile} />}

      {phase === "upload" && (
        <div className="card">
          <div className="row">
            <span>업로드 중</span>
            <span>{Math.round(uploadPct * 100)}%</span>
          </div>
          <div className="bar">
            <i style={{ width: `${uploadPct * 100}%` }} />
          </div>
        </div>
      )}

      {phase === "job" && job && <JobStatus job={job} />}
      {phase === "job" && !job && (
        <div className="card">
          <p className="muted">작업 정보를 불러오는 중…</p>
        </div>
      )}

      {error && <p className="err">{error}</p>}

      {phase !== "idle" && (
        <p>
          <button type="button" onClick={remove}>
            새로 시작
          </button>
        </p>
      )}
    </>
  );
}
```

- [ ] **Step 5: 빌드 검증**

Run: `cd frontend && bun install && bun run typecheck && bun run build`
Expected: typecheck 통과, `dist/index.html` 생성

- [ ] **Step 6: 커밋**

```bash
git add frontend
git commit -m "feat: frontend dropzone, progress and download UI"
```

---

### Task 11: Docker Compose + QSV 셋업

**Files:**
- Create: `docker/Dockerfile`, `compose.yml`, `.env.example`, `.gitignore`
- Test: 없음 (`docker compose config` 검증 + 수동 기동)

**Interfaces:**
- Consumes: Task 1–10 전체, `frontend/dist/`
- Produces: `docker compose up`으로 동작하는 배포

- [ ] **Step 1: Dockerfile 작성**

`docker/Dockerfile`:

```dockerfile
# ---- frontend build ----
FROM oven/bun:1 AS frontend
WORKDIR /build
COPY frontend/package.json frontend/bun.lock ./
RUN bun install --frozen-lockfile
COPY frontend/ ./
RUN bun run build

# ---- runtime ----
FROM python:3.13-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        intel-media-va-driver-non-free \
        libva-drm2 \
        libva2 \
        vainfo \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /usr/local/bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    LIBVA_DRIVER_NAME=iHD

COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev

COPY backend/app ./app
COPY --from=frontend /build/dist ./app/static

EXPOSE 8000
CMD ["uv", "run", "--no-sync", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 2: Compose 작성**

`compose.yml`:

```yaml
name: sevi

services:
  traefik:
    image: traefik:v3.3
    command:
      - --providers.docker=true
      - --providers.docker.exposedbydefault=false
      - --entrypoints.web.address=:80
    ports:
      - "80:80"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro

  db:
    image: postgres:16
    environment:
      POSTGRES_USER: sevi
      POSTGRES_PASSWORD: ${SEVI_DB_PASSWORD:-sevi}
      POSTGRES_DB: sevi
    volumes:
      - db-data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U sevi -d sevi"]
      interval: 5s
      timeout: 3s
      retries: 10

  backend:
    build:
      context: .
      dockerfile: docker/Dockerfile
    environment: &appenv
      SEVI_DATABASE_URL: postgresql+psycopg://sevi:${SEVI_DB_PASSWORD:-sevi}@db:5432/sevi
      SEVI_MEDIA_DIR: /media
      SEVI_SESSION_SECRET: ${SEVI_SESSION_SECRET:-change-me-in-production}
      SEVI_TARGET_BYTES: "49900000"
      SEVI_MAX_UPLOAD_BYTES: "838860800"
      LIBVA_DRIVER_NAME: iHD
    devices:
      - /dev/dri:/dev/dri
    group_add:
      - "video"
      - "render"
    volumes:
      - media-data:/media
    depends_on:
      db:
        condition: service_healthy
    labels:
      - traefik.enable=true
      - traefik.http.routers.sevi.rule=PathPrefix(`/`)
      - traefik.http.services.sevi.loadbalancer.server.port=8000

  worker:
    build:
      context: .
      dockerfile: docker/Dockerfile
    command: ["uv", "run", "--no-sync", "python", "-m", "app.worker"]
    environment: *appenv
    devices:
      - /dev/dri:/dev/dri
    group_add:
      - "video"
      - "render"
    volumes:
      - media-data:/media
    depends_on:
      db:
        condition: service_healthy

volumes:
  db-data:
  media-data:
```

- [ ] **Step 3: 환경/무시 파일 작성**

`.env.example`:

```env
SEVI_DB_PASSWORD=change-me
SEVI_SESSION_SECRET=change-me-to-a-long-random-string
```

`.gitignore`:

```
.venv/
__pycache__/
*.pyc
.pytest_cache/
node_modules/
frontend/dist/
.env
*.webm
*.mp4
.DS_Store
```

- [ ] **Step 4: Compose 설정 검증**

Run: `docker compose config -q`
Expected: 오류 없음

Run: `cd backend && uv run pytest -v`
Expected: PASS all (Task 1–9 테스트 재확인)

- [ ] **Step 5: QSV 가용성 확인**

Run: `docker compose run --rm --no-deps backend vainfo 2>&1 | head -20`
Expected: `VAEntrypointEncSlice` 목록에 `VAProfileVP9Profile0` 또는 유사 노출

Run: `docker compose run --rm --no-deps backend ffmpeg -hide_banner -encoders 2>/dev/null | grep vp9_qsv`
Expected: `vp9_qsv` 한 줄 출력

- [ ] **Step 6: 커밋**

```bash
git add docker/Dockerfile compose.yml .env.example .gitignore
git commit -m "feat: docker compose deployment with QSV passthrough"
```

---

## Self-Review

### 1. Spec coverage

| 스펙 § | 요구사항 | 태스크 |
|---|---|---|
| 2 | 익명 + 쿠키 + IP 레이트리밋 | T6, T8 |
| 2 | Postgres 큐 + 워커 | T5, T9 |
| 2 | 800MB / 6h / 24h / 동시성 1 | T1, T8, T9 |
| 3.1 | 예산 배분, 오디오 12% 상한·48 하한 | T2 |
| 3.2 | 해상도 사다리, bpp 0.06 | T2 |
| 3.3 | 보정 루프 3회, 80kbps 하한, 0.92 창 | T3 |
| 3.4 | ffmpeg 인자, map_metadata, webm | T7 |
| 4.1 | jobs 스키마 | T5 |
| 4.2 | 워커 루프, 재시작 시 실패 처리 | T9 |
| 5 | API 6개 엔드포인트 | T8 |
| 6 | 보안 표 전체 | T5(경로), T6(쿠키/리밋), T7(메타), T8(업로드/소유권) |
| 7 | 정리: 만료 스위프, 원본 즉시 삭제 | T9 |
| 8 | Docker + QSV 디바이스 | T11 |
| 9 | 프런트 5단계 흐름 | T10 |
| 10 | 테스트 4종 | T2, T3, T4, T7, T8, T9 |

누락 없음.

### 2. Placeholder scan

- TBD/TODO/"구현 later" 없음
- 모든 코드 스텝에 실제 코드 포함
- "Similar to Task N" 참조 없음
- 에러 핸들링·검증이 구체 메시지로 명시됨

### 3. Type consistency

- `EncodePlan(video_kbps, audio_kbps, height, has_audio, fps, duration_s)` — T2 정의, T3/T7/T9 사용 일치
- `ConvergeResult(video_kbps, attempts, result_bytes, converged)` — T3 정의, T9 사용 일치
- `ProbeResult(duration_s, width, height, fps, has_audio)` — T4 정의, T9 사용 일치
- `MediaPaths.job_dir/source/result/prepare/remove_job` — T5 정의, T8/T9 사용 일치
- `encode_once(src, dst, plan, video_kbps, ffmpeg_path, on_progress)` — T7 정의, T9 `_run` 호출과 일치
- `issue(secret) -> (sid, cookie)`, `verify(secret, value) -> sid | None` — T6 정의, T8 `require_session`과 일치
- `RateLimiter.check_upload/check_active/release_active` — T6 정의, T8 사용 일치
- `JobStatus` 멤버 `queued/probing/encoding/done/done_oversize/failed/cancelled` — T5 정의, T8/T9/T10 라벨과 일치
- `worker.py`는 `probe`/`plan_encode`/`converge`/`encode_once`를 **모듈 레벨 직접 import** — T9의 `monkeypatch.setattr("app.worker.probe", ...)` 타깃과 일치. 모듈 별칭(`_probe.probe`)을 쓰면 패치가 안 먹으므로 금지
- `created_at` 기본값은 `datetime.now(timezone.utc)` — `datetime.UTC`는 `from datetime import datetime`와 함께 쓸 수 없음

일치 확인 완료.
