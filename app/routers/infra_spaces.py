"""인프라 Space 조회. 플랫폼은 조회만 한다 (2일차 회의 2:27:19)."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import models
from app.db import get_db
from app.schemas import InfraSpace

router = APIRouter(prefix="/infra-spaces", tags=["infra-spaces"])

INFRA_NOT_FOUND = {"error": "infra_not_found", "message": "인프라를 찾을 수 없습니다."}


def _to_schema(infra: models.InfraSpace, app_count: int) -> InfraSpace:
    # 계정 ID·VPC·서브넷은 배포용 내부 값이라 응답에 넣지 않는다 (API 명세 4절)
    return InfraSpace(
        id=infra.id,
        name=infra.name,
        description=infra.description,
        network=infra.network,
        computes=infra.computes,
        app_count=app_count,
    )


def _app_counts(db: Session) -> dict[str, int]:
    stmt = select(models.AppSpace.infra_id, func.count()).group_by(models.AppSpace.infra_id)
    return dict(db.execute(stmt).all())


@router.get("", response_model=list[InfraSpace], summary="인프라 목록")
def list_infra_spaces(db: Session = Depends(get_db)) -> list[InfraSpace]:
    counts = _app_counts(db)
    infras = db.scalars(select(models.InfraSpace).order_by(models.InfraSpace.created_at, models.InfraSpace.id))
    return [_to_schema(i, counts.get(i.id, 0)) for i in infras]


@router.get("/{infra_id}", response_model=InfraSpace, summary="인프라 상세")
def get_infra_space(infra_id: str, db: Session = Depends(get_db)) -> InfraSpace:
    infra = db.get(models.InfraSpace, infra_id)
    if infra is None:
        raise HTTPException(404, detail=INFRA_NOT_FOUND)
    return _to_schema(infra, _app_counts(db).get(infra.id, 0))
