"""GitHub OAuth·API 호출. 테스트에서는 이 함수들을 가짜로 바꿔 끼운다."""
from dataclasses import dataclass

import httpx

from app.config import get_settings

AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
TOKEN_URL = "https://github.com/login/oauth/access_token"
API_URL = "https://api.github.com"


@dataclass
class GitHubProfile:
    id: int
    login: str
    name: str | None
    email: str | None
    avatar_url: str | None


class GitHubError(Exception):
    pass


def build_authorize_url(state: str) -> str:
    s = get_settings()
    params = httpx.QueryParams(
        client_id=s.github_client_id,
        redirect_uri=s.github_redirect_uri,
        scope=s.github_oauth_scope,
        state=state,
        allow_signup="true",
    )
    return f"{AUTHORIZE_URL}?{params}"


def exchange_code_for_token(code: str) -> str:
    s = get_settings()
    resp = httpx.post(
        TOKEN_URL,
        data={
            "client_id": s.github_client_id,
            "client_secret": s.github_client_secret,
            "code": code,
            "redirect_uri": s.github_redirect_uri,
        },
        headers={"Accept": "application/json"},
        timeout=10,
    )
    data = resp.json() if resp.content else {}
    token = data.get("access_token")
    if resp.status_code != 200 or not token:
        raise GitHubError(data.get("error_description") or "GitHub 토큰 교환에 실패했습니다.")
    return token


def fetch_profile(token: str) -> GitHubProfile:
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    with httpx.Client(base_url=API_URL, headers=headers, timeout=10) as client:
        r = client.get("/user")
        if r.status_code != 200:
            raise GitHubError("GitHub 사용자 정보를 가져오지 못했습니다.")
        u = r.json()
        email = u.get("email")
        if not email:
            # 공개 이메일이 없으면 user:email 권한으로 기본 이메일 조회
            er = client.get("/user/emails")
            if er.status_code == 200:
                primary = next((e for e in er.json() if e.get("primary")), None)
                email = primary.get("email") if primary else None
    return GitHubProfile(
        id=u["id"],
        login=u["login"],
        name=u.get("name"),
        email=email,
        avatar_url=u.get("avatar_url"),
    )
