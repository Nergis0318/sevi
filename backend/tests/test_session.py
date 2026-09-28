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
