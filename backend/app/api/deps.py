import ipaddress

from fastapi import Depends, HTTPException, Request, Response

from app.ratelimit import RateLimiter
from app.session import SESSION_COOKIE, issue, verify
from app.settings import Settings


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


def get_limiter(request: Request) -> RateLimiter:
    return request.app.state.limiter


def get_ip_limiter(request: Request) -> RateLimiter:
    return request.app.state.ip_limiter


def _is_trusted_proxy(host: str) -> bool:
    if not host:
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback


def client_ip(request: Request) -> str:
    """리밋 키용 클라이언트 IP — 클라이언트가 지정할 수 없다.

    신뢰 가능한 프록시(Caddy/Traefik)는 X-Forwarded-For 의 **끝에** 실제 IP를 둔다.
    왼쪽 끝은 클라이언트가 지정할 수 있으므로 쓰지 않는다 — uvicorn 의 --proxy-headers
    가 바로 그 값을 request.client 에 써서 리밋 키가 위조된다(Dockerfile 에서 차단).
    끝 항목을 그대로 쓴다 — 사설 IP(LAN/VPN) 클라이언트라 해도 끝이 진실이다.
    """
    peer = request.client.host if request.client else ""
    if not _is_trusted_proxy(peer):
        return peer or "unknown"
    hops = [h.strip() for h in request.headers.get("x-forwarded-for", "").split(",") if h.strip()]
    return hops[-1] if hops else (peer or "unknown")


def is_https(request: Request) -> bool:
    peer = request.client.host if request.client else ""
    if not _is_trusted_proxy(peer):
        return request.url.scheme == "https"
    proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip()
    return proto == "https" or (not proto and request.url.scheme == "https")


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
            secure=is_https(request),
            path="/",
        )
    return session_id
