from unittest.mock import MagicMock


def test_health_returns_ok(client, monkeypatch):
    monkeypatch.setattr("app.api.health.engine", MagicMock())
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["service"] == "sevi"
    assert isinstance(body["ok"], bool)
    assert body["ok"] is True


def test_health_returns_ok_false_when_db_probe_fails(client, monkeypatch):
    broken = MagicMock()
    broken.connect.side_effect = RuntimeError("db down")
    monkeypatch.setattr("app.api.health.engine", broken)
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["service"] == "sevi"
    assert body["ok"] is False
