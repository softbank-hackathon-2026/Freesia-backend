import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app


def test_validation_error_format(client):
    # 기존 API로는 422를 만들기 어려워 임시 라우트로 확인한다
    @app.get("/_test/validation")
    def _v(n: int) -> dict:
        return {"n": n}

    r = client.get("/_test/validation?n=abc")
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


def test_weak_jwt_secret_rejected_outside_local():
    with pytest.raises(ValueError):
        Settings(app_env="dev", jwt_secret="change-me")
    Settings(app_env="dev", jwt_secret="x" * 32)
    Settings(app_env="local", jwt_secret="change-me")
