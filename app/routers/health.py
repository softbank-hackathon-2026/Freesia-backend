"""배포 확인용 엔드포인트 (ADR-002 성공 기준)."""
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
    """DB 연결까지 확인한다. /health에 포함할지는 박소정과 합의 필요 (ADR-007 후속)."""
    db.execute(text("SELECT 1"))
    return {"status": "ok", "db": "ok"}


@router.get("/version.txt", response_class=PlainTextResponse)
def version() -> str:
    """현재 실행 중인 커밋 SHA. 빌드 시 APP_VERSION으로 주입된다."""
    return get_settings().app_version
