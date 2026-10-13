# ---- frontend build ----
FROM oven/bun:1 AS frontend
WORKDIR /build
COPY frontend/package.json frontend/bun.lock ./
RUN bun install --frozen-lockfile
COPY frontend/ ./
RUN bun run build

# ---- runtime ----
FROM python:3.13-slim

# libmfx-gen1.2 : ffmpeg 의 vp9_qsv 는 oneVPL 로 MFX 세션을 연다. 디스패처(libvpl2)만
#   있으면 구현체가 없어 "Error creating a MFX session: -9" 로 모든 잡이 실패한다.
#   vainfo 는 libva 를 직접 쓰기 때문에 이 누락을 드러내지 못한다.
RUN sed -i 's/Components: main/Components: main contrib non-free non-free-firmware/' \
        /etc/apt/sources.list.d/debian.sources \
    && apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        intel-media-va-driver-non-free \
        libmfx-gen1.2 \
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
# --forwarded-allow-ips="" : uvicorn 의 프록시 헤더 재작성을 끈다. uvicorn 은 X-Forwarded-For 의
#   **왼쪽 끝**(클라이언트가 지정 가능)을 request.client 에 쓰므로 리밋 키가 위조된다.
#   실제 IP 는 app/api/deps.py 의 client_ip 가 오른쪽부터 신뢰할 수 없는 첫 홉을 택해
#   직접 파싱한다(Caddy/Traefik 은 실제 IP 를 끝에 둔다 — Caddy 는 위조값을 버린다).
#   Secure 플래그도 deps.is_https 가 X-Forwarded-Proto 를 직접 읽는다.
CMD ["uv", "run", "--no-sync", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips="]
