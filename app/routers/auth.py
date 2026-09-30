"""GitHub 로그인 (OAuth App, 최소 권한 read:user user:email)."""
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import User
from app.security import create_access_token, encrypt_token
from app.services import github

router = APIRouter(prefix="/auth", tags=["auth"])

STATE_COOKIE = "oauth_state"


@router.get("/github")
def github_login() -> RedirectResponse:
    """GitHub 로그인 페이지로 보낸다. CSRF 방지를 위해 state를 쿠키에 저장한다."""
    s = get_settings()
    state = secrets.token_urlsafe(24)
    resp = RedirectResponse(github.build_authorize_url(state), status_code=302)
    resp.set_cookie(
        STATE_COOKIE, state, max_age=600, httponly=True, secure=s.cookie_secure, samesite="lax"
    )
    return resp


@router.get("/github/callback")
def github_callback(
    request: Request, code: str | None = None, state: str | None = None, db: Session = Depends(get_db)
) -> RedirectResponse:
    """GitHub에서 돌아오면 사용자 저장 후 로그인 쿠키를 발급하고 프론트로 보낸다."""
    s = get_settings()
    saved_state = request.cookies.get(STATE_COOKIE)
    if not code or not state or not saved_state or not secrets.compare_digest(state, saved_state):
        raise HTTPException(400, detail={"error": "invalid_state", "message": "로그인 요청이 올바르지 않습니다."})

    try:
        token = github.exchange_code_for_token(code)
        profile = github.fetch_profile(token)
    except github.GitHubError as e:
        raise HTTPException(502, detail={"error": "github_error", "message": str(e)}) from e

    user = db.scalar(select(User).where(User.github_id == profile.id))
    if user is None:
        user = User(github_id=profile.id, github_login=profile.login)
        db.add(user)
    user.github_login = profile.login
    user.name = profile.name
    user.email = profile.email
    user.avatar_url = profile.avatar_url
    user.github_token_encrypted = encrypt_token(token)
    db.commit()

    resp = RedirectResponse(s.frontend_url, status_code=302)
    resp.delete_cookie(STATE_COOKIE)
    resp.set_cookie(
        s.auth_cookie_name,
        create_access_token(user.id),
        max_age=s.jwt_expires_minutes * 60,
        httponly=True,
        secure=s.cookie_secure,
        samesite="lax",
    )
    return resp


@router.post("/logout")
def logout() -> JSONResponse:
    resp = JSONResponse({"status": "ok"})
    resp.delete_cookie(get_settings().auth_cookie_name)
    return resp
