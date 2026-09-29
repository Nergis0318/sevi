"""client_ip / is_https — 리밋 키와 쿠키 Secure 플래그의 위조 방지."""

from types import SimpleNamespace

from app.api.deps import client_ip, is_https


def _req(*, peer: str | None, headers: dict | None = None, scheme: str = "http"):
    return SimpleNamespace(
        client=SimpleNamespace(host=peer) if peer is not None else None,
        headers=headers or {},
        url=SimpleNamespace(scheme=scheme),
    )


def test_untrusted_peer_ignores_forwarded_header():
    """직접 접속한 클라이언트가 XFF 를 보내도 무시된다 — 자기 IP 를 지정할 수 없다."""
    req = _req(peer="11.11.11.11", headers={"x-forwarded-for": "22.22.22.22"})
    assert client_ip(req) == "11.11.11.11"


def test_trusted_peer_uses_rightmost_untrusted_hop():
    """프록시(Caddy/Traefik)는 실제 IP를 끝에 둔다 — 왼쪽 끝(uvicorn 기본)은 클라이언트가 지정한 값."""
    req = _req(
        peer="172.18.0.4",
        headers={"x-forwarded-for": "22.22.22.22, 11.11.11.11"},
    )
    assert client_ip(req) == "11.11.11.11"


def test_trusted_peer_uses_last_hop_in_chain():
    req = _req(
        peer="172.18.0.4",
        headers={"x-forwarded-for": "22.22.22.22, 10.0.0.1, 11.11.11.11"},
    )
    assert client_ip(req) == "11.11.11.11"


def test_private_client_keeps_its_own_ip():
    """LAN/VPN 클라이언트(사설 IP)는 자기 IP를 유지한다 — 피어로 뭉개면 전원이 한 버킷."""
    req = _req(peer="127.0.0.1", headers={"x-forwarded-for": "10.0.0.1"})
    assert client_ip(req) == "10.0.0.1"


def test_appended_hop_wins_over_client_supplied_prefix():
    """프록시가 끝에 실제 IP를 붙이면 앞의 위조값은 못 쓴다 — 사설이어도 끝이 진실이다."""
    req = _req(peer="127.0.0.1", headers={"x-forwarded-for": "22.22.22.22, 10.0.0.1"})
    assert client_ip(req) == "10.0.0.1"


def test_no_header_falls_back_to_peer():
    req = _req(peer="11.11.11.11")
    assert client_ip(req) == "11.11.11.11"


def test_missing_client_is_unknown():
    assert client_ip(_req(peer=None)) == "unknown"


def test_is_https_untrusted_peer_uses_request_scheme():
    assert is_https(_req(peer="11.11.11.11", headers={"x-forwarded-proto": "https"})) is False
    assert is_https(_req(peer="11.11.11.11", scheme="https")) is True


def test_is_https_trusted_peer_reads_forwarded_proto():
    req = _req(peer="172.18.0.4", headers={"x-forwarded-proto": "https"})
    assert is_https(req) is True
    assert is_https(_req(peer="172.18.0.4", headers={"x-forwarded-proto": "http"})) is False


def test_is_https_trusted_peer_without_header_falls_back():
    assert is_https(_req(peer="172.18.0.4")) is False
    assert is_https(_req(peer="172.18.0.4", scheme="https")) is True
