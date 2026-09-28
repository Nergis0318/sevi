# ---- frontend build ----
FROM oven/bun:1 AS frontend
WORKDIR /build
COPY frontend/package.json frontend/bun.lock ./
RUN bun install --frozen-lockfile
COPY frontend/ ./
RUN bun run build

# ---- runtime ----
FROM python:3.13-slim

RUN sed -i 's/Components: main/Components: main contrib non-free non-free-firmware/' \
        /etc/apt/sources.list.d/debian.sources \
    && apt-get update && apt-get install -y --no-install-recommends \
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
# --proxy-headers / --forwarded-allow-ips 필수: Traefik 뒤에서 X-Forwarded-For(클라이언트 IP,
# IP+세션 리밋 키)와 X-Forwarded-Proto(https 감지 → Secure 쿠키)를 반영하려면 없으면 안 된다
CMD ["uv", "run", "--no-sync", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
