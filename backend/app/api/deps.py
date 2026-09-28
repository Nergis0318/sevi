from fastapi import Depends, HTTPException, Request, Response

from app.ratelimit import RateLimiter
from app.session import SESSION_COOKIE, issue, verify
from app.settings import Settings


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


def get_limiter(request: Request) -> RateLimiter:
    return request.app.state.limiter


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


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
