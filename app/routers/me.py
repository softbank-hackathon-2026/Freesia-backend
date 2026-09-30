"""내 정보."""
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.deps import get_current_user
from app.models import User

router = APIRouter(tags=["me"])


class MeResponse(BaseModel):
    id: str
    github_login: str
    name: str | None
    email: str | None
    avatar_url: str | None
    created_at: datetime


@router.get("/me", response_model=MeResponse)
def me(user: User = Depends(get_current_user)) -> User:
    return user
