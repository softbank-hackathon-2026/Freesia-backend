"""배포 확인용 엔드포인트 (ADR-002, ADR-013 성공 기준). 앱에서 /api 아래에 등록된다."""
from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict:
    """앱이 살아있는지만 확인한다 (DB 확인 X)."""
    return {"status": "ok"}


@router.get("/health/db")
def health_db(db: Session = Depends(get_db)) -> dict:
    """DB 연결까지 확인한다 (ADR-013 성공 기준)."""
    db.execute(text("SELECT 1"))
    return {"status": "ok", "db": "ok"}


@router.get("/version.txt", response_class=PlainTextResponse)
def version() -> str:
    """현재 실행 중인 커밋 SHA. 빌드 시 APP_VERSION으로 주입된다."""
    return get_settings().app_version
