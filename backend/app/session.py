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
    expected = _sign(secret, session_id)
    try:
        matched = hmac.compare_digest(sig.encode("ascii"), expected.encode("ascii"))
    except UnicodeEncodeError:
        return None  # 비ASCII 위조값 — compare_digest 는 str 에 ASCII 만 허용
    if not matched:
        return None
    return session_id
