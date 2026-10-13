# AGENTS.md

## 프로젝트

익명 공개 비디오 압축 사이트 **sevi** — 업로드한 영상을 **49.9MB 이하** `.webm`(`vp9_qsv` + `libopus`)으로 서버 재인코딩해준다.
설계·구현 계획(글로벌 제약 원본): `docs/superpowers/specs/2026-09-28-video-50mb-compressor-design.md`, `docs/superpowers/plans/2026-09-28-video-50mb-compressor.md`.

## 구조

- `backend/` — Python 3.13 + FastAPI + SQLModel + psycopg3, 패키지 관리 **uv**. API 서버는 `app.main:app`, 잡 워커는 별도 프로세스 `python -m app.worker` (같은 이미지). 워커 동시성 **1** — QSV 엔진이 하나라서
- `frontend/` — React 19 + Vite + TS + TanStack Query, 패키지 관리 **bun**. Docker 빌드에서 `backend/app/static` 으로 복사되어 FastAPI가 `/` 에 서빙. 로컬 dev는 vite가 `/api` 를 `:8000` 으로 프록시
- `compose.yml` — postgres:16 + backend + worker. 호스트 `8135` → `8000`. TLS·외부 도메인은 앞단 Caddy 소관
- 마이그레이션 도구 없음 — `SQLModel.metadata.create_all` (`app/db.py:init_db`)

## 명령어

- 백엔드 테스트: `cd backend && uv run pytest` (반드시 `backend/` 에서 실행 — `testpaths = ["tests"]`)
  - 단일 테스트: `uv run pytest tests/test_plan.py::test_pick_height_keeps_source_when_bpp_satisfied -q`
- 프론트 dev: `cd frontend && bun run dev`
- 프론트 타입검사: `cd frontend && bun run typecheck` (`lint` 는 같은 `tsc --noEmit` 의 별칭 — ESLint는 없다)
- 배포: `cp .env.example .env` 후 값을 바꾸고 `docker compose up`. `SEVI_DB_PASSWORD`/`SEVI_SESSION_SECRET` 미설정이면 즉시 실패한다

## 테스트

- 대부분의 테스트는 in-memory sqlite (`Settings(database_url="sqlite://")`) — Postgres 없이 돈다
- **QSV 하드웨어 전제**: `test_encode_integration.py` 와 `test_qsv_runtime.py::test_check_qsv_runs_on_this_machine` 은 실제 ffmpeg로 `vp9_qsv` 를 연다. Intel QSV(`/dev/dri`) 없는 기계에서는 실패가 정상

## 트랩 — 고쳐 쓰지 말 것

- **ffmpeg 인자 불변 조건** (검증됨 — 위반 시 `-22` 실패): `-hwaccel`, `scale_qsv`, 명시적 `hwupload` 금지, 폭은 `-2`, 출력은 `format=nv12`. 스케일 `flags=`와 부가 sw 필터(`fps`, `hqdn3d`)는 프로필별로 허용 (`app/profiles.py`, 2026-09-29 실측). 메타데이터는 `-map_metadata -1 -map_chapters -1`
- **컨테이너 QSV 는 `libmfx-gen1.2` 패키지가 필수** — oneVPL 구현체가 없으면 "MFX session: -9" 로 모든 잡이 실패한다. `vainfo` 는 이 누락을 드러내지 못한다. Dockerfile 에서 제거 금지 (회귀 테스트가 지킨다)
- **uvicorn `--forwarded-allow-ips=""` 는 고의**: uvicorn 의 프록시 헤더 재작성은 X-Forwarded-For 의 **왼쪽 끝**(클라이언트 위조 가능)을 쓴다. 실제 IP 는 `app/api/deps.py:client_ip` 가 오른쪽부터 신뢰 못 하는 첫 홉을 택해 직접 파싱한다. 이 플래그를 "고치지" 말 것
- **목표 크기 맞추기는 추정→인코딩→실측→보정 순환** (`app/converge.py`, 최대 3회, 수렴창 `0.92*TARGET..TARGET`) — `vp9_qsv` 는 2-pass 미지원. bitrate·해상도 사다리(`app/plan.py`)를 바꾸면 이 순환 전체를 다시 검증할 것
- 파일명은 전부 UUID — 사용자 파일명은 경로에 절대 쓰지 않는다

## 컨벤션

- UI 문구와 API 에러 `detail` 은 한국어 유지. 코드 주석도 한국어
- 커밋: Conventional Commits (`feat:`, `fix:`, `test:`, `chore:`)
