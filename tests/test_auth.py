from urllib.parse import parse_qs, urlparse

from app.models import User
from app.security import decrypt_token
from app.services import github
from tests.conftest import TestingSession


def _fake_github(monkeypatch, gh_id=12345):
    monkeypatch.setattr(github, "exchange_code_for_token", lambda code: "gho_fake_token")
    monkeypatch.setattr(
        github,
        "fetch_profile",
        lambda token: github.GitHubProfile(
            id=gh_id, login="freesia-dev", name="Freesia", email="dev@example.com", avatar_url=None
        ),
    )


def _login(client):
    r = client.get("/auth/github", follow_redirects=False)
    assert r.status_code == 302
    state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
    return client.get(f"/auth/github/callback?code=abc&state={state}", follow_redirects=False)


def test_login_redirects_to_github(client):
    r = client.get("/auth/github", follow_redirects=False)
    assert r.status_code == 302
    loc = urlparse(r.headers["location"])
    assert loc.netloc == "github.com"
    q = parse_qs(loc.query)
    assert q["scope"] == ["read:user user:email"]  # 최소 권한
    assert "state" in q


def test_callback_rejects_bad_state(client):
    client.get("/auth/github", follow_redirects=False)
    r = client.get("/auth/github/callback?code=abc&state=wrong", follow_redirects=False)
    assert r.status_code == 400


def test_login_flow_creates_user_and_me(client, monkeypatch):
    _fake_github(monkeypatch)
    r = _login(client)
    assert r.status_code == 302
    assert r.headers["location"] == "http://frontend.test"

    me = client.get("/me")
    assert me.status_code == 200
    body = me.json()
    assert body["github_login"] == "freesia-dev"
    assert body["email"] == "dev@example.com"
    assert "github_token_encrypted" not in body

    db = TestingSession()
    user = db.query(User).one()
    assert user.github_token_encrypted != "gho_fake_token"  # 평문 저장 금지
    assert decrypt_token(user.github_token_encrypted) == "gho_fake_token"
    db.close()


def test_relogin_does_not_duplicate_user(client, monkeypatch):
    _fake_github(monkeypatch)
    _login(client)
    _login(client)
    db = TestingSession()
    assert db.query(User).count() == 1
    db.close()


def test_me_requires_login(client):
    assert client.get("/me").status_code == 401


def test_logout(client, monkeypatch):
    _fake_github(monkeypatch)
    _login(client)
    assert client.post("/auth/logout").status_code == 200
    assert client.get("/me").status_code == 401


def test_error_format(client):
    body = client.get("/me").json()
    assert body == {"error": "unauthorized", "message": "로그인이 필요합니다."}


def test_callback_cancelled(client):
    client.get("/auth/github", follow_redirects=False)
    r = client.get("/auth/github/callback?error=access_denied&state=x", follow_redirects=False)
    assert r.status_code == 400
    assert r.json()["error"] == "login_cancelled"
