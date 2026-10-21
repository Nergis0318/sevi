# 50MB 비디오 압축 사이트 설계

- 날짜: 2026-09-28
- 상태: 승인됨
- 경로: architectural

## 1. 목표

원본 영상을 업로드받아 **49.9MB 이하**의 `.webm`으로 서버 측에서 재인코딩해주는 공개 사이트.

- 비디오: `vp9_qsv` (Intel Quick Sync)
- 오디오: `libopus` 재인코딩
- 출력: `.webm` (Matroska/WebM)
- 배포: Docker Compose

## 2. 결정 사항

| 항목        | 결정                                                                     |
| ----------- | ------------------------------------------------------------------------ |
| 사용 모델   | 공개 사이트, 익명                                                        |
| 인증        | 계정 없음. IP 레이트리밋 + 서명 쿠키(`sv_session`)                       |
| 백엔드      | Python 3.13+ / FastAPI / SQLModel / uv                                   |
| 프런트      | React / Vite / TypeScript / Tailwind / shadcn/ui / TanStack Router / bun |
| DB          | PostgreSQL                                                               |
| 작업 큐     | Postgres 백엔드 큐 (`FOR UPDATE SKIP LOCKED`) + 전용 워커 컨테이너       |
| 배포        | Docker Compose (TLS·외부 도메인은 앞단 Caddy 소관)                       |
| 업로드 한도 | 800MB                                                                    |
| 목표 크기   | 49,900,000 B (49.9 decimal MB) — `TARGET_BYTES` env로 변경 가능          |
| 긴 영상     | 해상도 자동 다운스케일                                                   |
| 보관        | 결과 24시간 후 자동 삭제. 원본은 인코딩 종료 즉시 삭제                   |
| 워커 동시성 | 1 (QSV 엔진 1개)                                                         |

기각된 대안: Celery+Redis(인프라 과다), FastAPI 인프로세스 워커(내구성 없음).

## 3. 핵심 알고리즘: 목표 크기 맞추기

`vp9_qsv`는 ffmpeg에서 2-pass 미지원 (`delay hybrid`만). 따라서 **추정 → 인코딩 → 실측 → 보정** 순환.

### 3.1 예산 배분

```
S = TARGET_BYTES = 49_900_000
D = 길이(초)                    # ffprobe format.duration, 스트림 duration 폴백
budget_bps = S * 8 / D

audio_kbps = 0                                  # 오디오 스트림 없으면
         = min(96, budget_bps*0.12/1000)        # 있으면 스테레오 다운믹스, 하한 48
video_kbps = budget_bps*0.97 - audio_kbps*1000  # 3% 컨테이너/세그먼트 여유
```

### 3.2 해상도 사다리

`bpp = video_bps / (w * h * fps)`, 목표 0.06.

후보 높이: `소스 높이, 1080, 720, 480, 360, 240` 중 소스 이하이면서 `bpp >= 0.06`을 만족하는 최대값.
하나도 못 만족하면 240p. 가로는 `scale=-2:H`로 원본 비율 유지.

### 3.3 보정 루프

```python
v_kbps = initial_estimate()      # 3.1 값
for attempt in 1..3:
    encode(v_kbps)               # -b:v, -maxrate 1.2x, -bufsize 2x
    actual = size(output)
    if actual <= S and actual >= 0.92 * S:
        break                    # 수렴
    v_kbps = max(80, v_kbps * (S * 0.97) / actual)
```

- 3회 초과 시 마지막 결과를 반환하되 `status=done_oversize`로 표시 (경고 포함)
- 80kbps 하한 — 그 밑으로는 VP9 의미 없음

### 3.4 ffmpeg 인자

```
ffmpeg -nostdin -y -hide_banner -nostats
  -progress {job_dir}/progress.txt
  -hwaccel qsv -hwaccel_output_format qsv          # 실패 시 폴백: -hwaccel 없이 SW 디코드
  -i {input}
  -map 0:v:0 -map 0:a:0?
  -c:v vp9_qsv -preset medium
  -b:v {v}k -maxrate {v*1.2}k -bufsize {v*2}k
  -vf scale_qsv=w=-2:h={H} -pix_fmt nv12           # SW 폴백 시 scale= + hwupload
  -c:a libopus -b:a {a}k -ac 2 -ar 48000 -application audio
  -map_metadata -1 -map_chapters -1
  -movflags +faststart                                # webm에선 무시됨, 무해
  -f webm {output}
```

- 오디오 스트림이 없으면 `-map 0:a:0?`가 빈 결과를 내므로 오디오 인코딩 옵션 생략 가능 (`-an`)
- `scale_qsv` 미지원 시: `scale=w=-2:h={H},format=nv12,hwupload=extra_hw_frames=64`
- 이미 50MB 미만인 소스도 동일 경로 재인코딩 (단일 코드 경로)
- 다중 오디오 스트림은 첫 번째 것만 사용

## 4. 시스템 아키텍처

```
클라이언트 → Caddy(사용자 운영, TLS 종료) → backend (FastAPI + 빌드된 정적 프런트 서빙)
                            │
                       PostgreSQL ── jobs
                            │
                      worker (동명 이미지, 다른 CMD)
                            └─ SKIP LOCKED 클레임 → ffprobe → ffmpeg → 상태/진행률 갱신
```

### 4.1 jobs 스키마

```sql
CREATE TABLE jobs (
  id            UUID PRIMARY KEY,
  session_id    TEXT NOT NULL,
  status        TEXT NOT NULL,  -- queued|probing|encoding|done|done_oversize|failed|cancelled
  attempt       INT  NOT NULL DEFAULT 0,
  progress      REAL NOT NULL DEFAULT 0,   -- 0..1
  source_path   TEXT,
  result_path   TEXT,
  source_bytes  BIGINT,
  result_bytes  BIGINT,
  duration_s    REAL,
  width         INT,
  height        INT,
  error         TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at    TIMESTAMPTZ NOT NULL
);
CREATE INDEX jobs_claim ON jobs (status, created_at) WHERE status = 'queued';
CREATE INDEX jobs_expiry ON jobs (expires_at);
```

### 4.2 워커 루프

```
tick (1s 간격, 또는 NOTIFY):
  1. 만료 job 정리 (expires_at < now() 인 job의 파일 삭제 + 행 삭제)
  2. 활성 잡이 0개면:
       SELECT ... FROM jobs WHERE status='queued'
       ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1
  3. ffprobe 검증 → 실패 시 status=failed
  4. 소스 크기/길이 기록, 파일 경로 확정
  5. 3장 알고리즘 실행 (attempt마다 progress 갱신)
  6. 성공: status=done, result_bytes 기록, 원본 즉시 삭제
     실패: status=failed, error 기록, 원본+부산물 삭제
```

- 진행률: `ffmpeg -progress`의 `out_time_ms` / `duration_s` / `attempt`로 산출. `GET /api/jobs/{id}` 폴링(2초)
- 재시작 시 `encoding` 상태의 잡은 `failed`로 되돌리고 파일 정리 (재개 대신 재처리)
- 워커 동시성 1. QSV 엔진은 N150에 1개

## 5. API

| 메서드   | 경로                      | 설명                                                             |
| -------- | ------------------------- | ---------------------------------------------------------------- |
| `POST`   | `/api/jobs`               | multipart `file` → `{id, status}`. `sv_session` 쿠키 발급/검증   |
| `GET`    | `/api/jobs/{id}`          | `{status, attempt, progress, source_bytes, result_bytes, error}` |
| `GET`    | `/api/jobs/{id}/download` | `.webm` 스트리밍, `Content-Disposition: attachment`              |
| `DELETE` | `/api/jobs/{id}`          | 취소 + 파일 삭제                                                 |
| `GET`    | `/api/config`             | `{max_upload_bytes, target_bytes, max_duration_s}`               |
| `GET`    | `/api/health`             | QSV 가용성 + DB 연결                                             |

- `POST`는 `Content-Length`/Starlette 업로드 리밋 800MB, 앞단 Caddy의 요청 본문 상한도 동일
- 다운로드는 소유 세션만. 타인 세션은 404
- 에러 응답: `{detail: string}`. 사용자 노출 메시지는 한국어

## 6. 보안

| 위협             | 대응                                                                                                  |
| ---------------- | ----------------------------------------------------------------------------------------------------- |
| 무제한 업로드    | IP+세션당 5건/시간, 세션당 활성 1건, 전역 동시 1건                                                    |
| 경로 조작        | 파일명 전부 UUID v4. 사용자 파일명·확장자 무시                                                        |
| 메타데이터 유출  | `-map_metadata -1 -map_chapters -1`                                                                   |
| 셸 인젝션        | `subprocess` 리스트 인자. 사용자 값은 인자 값으로만                                                   |
| 타인 파일 접근   | 잡 소유권 = 서명 쿠키 `sv_session` (HMAC-SHA256 서명된 랜덤 16B, HttpOnly, SameSite=Lax, Secure, 7일) |
| 손상/비디오 아님 | ffprobe: 비디오 스트림 필수, `0 < duration <= 6h`, 컨테이너 인식 가능                                 |
| 파일 스토리지    | 앱 루트 밖 전용 볼륨. 서버 정적 서빙 경로와 분리                                                      |

추가: 비디오 스트림만 사용, 자막/데이터 스트림 무시. 오디오 첫 스트림만.

## 7. 정리(Cleanup)

- 워커 틱마다 만료 정리 (인덱스 `jobs_expiry`)
- 원본: 인코딩 종료 즉시 삭제 (성공/실패 불문)
- 결과: `expires_at = created_at + 24h` 까지 보관 후 삭제
- 부산물(`progress.txt`, 임시 출력): 매 시도 종료 시 삭제
- 실패 잡: 레코드 24시간 보관(사용자 오류 확인용), 파일은 즉시 삭제

## 8. Docker / QSV

- 이미지: `python:3.13-slim` 기반 + `ffmpeg` + `intel-media-va-driver` (iHD)
  - 확인: ffmpeg 7.1.5-0+deb13u1, `vp9_qsv` 인코더 존재, VA-API 1.22, `iHD_drv_video.so` 로드 성공
- 서비스: `db`(postgres:16), `backend`, `worker` (backend와 동일 이미지, CMD만 다름)
- 앞단 리버스 프록시는 **사용자 운영 Caddy** (compose 에 포함하지 않음). `backend`는 `127.0.0.1:8000`으로 HTTP 노출, TLS 종료·`X-Forwarded-For`/`-Proto` 설정은 Caddy가 담당
- QSV 접근:
  ```yaml
  devices: ["/dev/dri:/dev/dri"]
  group_add: ["video", "render"]
  environment:
    LIBVA_DRIVER_NAME: iHD
  ```
- 볼륨: `media-data:/media` (백엔드·워커 공유)
- 프런트는 빌드 단계에서 `bun run build`, 결과물을 backend 이미지에 복사 후 FastAPI가 서빙 (템플릿 방식)

## 9. 프런트엔드

단일 페이지 흐름:

1. **드롭존** — 파일 선택/드래그, 800MB 초과 시 사전 거부, `/api/config` 값 표시
2. **업로드** — `XMLHttpRequest`/`fetch` 업로드 진행률
3. **인코딩** — 2초 폴링, 진행률 바 + attempt 표시
4. **완료** — 원본/결과 크기, 절감율(%), 다운로드 버튼
5. **실패** — 오류 메시지 + 재시도

상태는 TanStack Query, 라우팅은 TanStack Router, 스타일은 Tailwind + shadcn/ui.
한국어 UI.

## 10. 테스트

- **단위 (pure)**
  - 해상도 사다리 선택 (bpp 경계값)
  - 비트레이트 예산 계산 (오디오 유무, 하한)
  - 보정 루프 수렴 — 가짜 `encode()` 주입 (항상 1.1배 초과/미달하는 케이스)
- **통합**
  - `ffmpeg -f lavfi`로 만든 2초 클립 → 실제 인코딩 → 출력 ≤ S, `.webm`, 오디오 코덱 `opus`
  - 오디오 없는 클립 → 오디오 트랙 없음
  - ffprobe 거부 케이스 (이미지, 0초, 7시간)
- **API**
  - 업로드 → `queued` → 다운로드 (성공 경로)
  - 레이트리밋 429
  - 타인 세션 접근 404
  - 800MB 초과 거부
- **검증 명령**: `uv run pytest`, `bun run lint && bun run typecheck && bun run test`

## 11. 스펙 자기 검토

- 플레이스홀더 없음
- 예산 계산 ↔ ffmpeg 인자 ↔ 보정 루프 정합
- 단일 구현 계획으로 충분한 범위 (백엔드 1 + 워커 1 + 프런트 1 페이지)
- 모호성 제거: `TARGET_BYTES` = 49,900,000 (decimal), 보정 하한 80kbps, 최대 3회 시도, 실패 시 `done_oversize` 반환

## 12. 스킵 항목 (요청 시 추가)

- fps 다운스케일 (해상도만으로 비트레이트 확보)
- 진짜 2-pass 인코딩 (`vp9_qsv` 미지원)
- 목표 크기 사용자 선택 (고정 50MB)
- 로그인/이메일/사용자 이력
- 업로드 청크 전송 / 재개
