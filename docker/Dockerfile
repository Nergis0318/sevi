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
# --forwarded-allow-ips="" : uvicorn 의 프록시 헤더 재작성을 끈다. uvicorn 은 X-Forwarded-For 의
#   **왼쪽 끝**(클라이언트가 지정 가능)을 request.client 에 쓰므로 리밋 키가 위조된다.
#   실제 IP 는 app/api/deps.py 의 client_ip 가 오른쪽부터 신뢰할 수 없는 첫 홉을 택해
#   직접 파싱한다(Traefik 은 실제 IP 를 끝에 덧붙인다). Secure 플래그도 deps.is_https 가 직접 읽는다.
CMD ["uv", "run", "--no-sync", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips="]
