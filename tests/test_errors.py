from fastapi.testclient import TestClient

from app.main import app


def test_validation_error_format(client):
    r = client.post("/app-spaces", json={})
    assert r.status_code == 422
    body = r.json()
    assert body["error"] == "validation_error"
    assert "message" in body


def test_unexpected_error_format():
    @app.get("/_test/boom")
    def _boom() -> dict:
        raise RuntimeError("secret detail")

    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get("/_test/boom")
    assert r.status_code == 500
    assert r.json() == {"error": "internal_error", "message": "서버 오류가 발생했습니다."}

