"""인프라 Space 조회와 갱신. 플랫폼은 AWS 인프라를 읽기만 한다 (2일차 회의 2:27:19)."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import catalog, infra_sync, models
from app.db import get_db
from app.schemas import InfraSpace

router = APIRouter(prefix="/infra-spaces", tags=["infra-spaces"])

INFRA_NOT_FOUND = {"error": "infra_not_found", "message": "인프라를 찾을 수 없습니다."}


def is_deployable(infra: models.InfraSpace) -> bool:
    """배포 워크플로에 넘길 VPC와 서로 다른 AZ의 퍼블릭 서브넷 2개가 있는지."""
    return infra.status == "ready" and bool(infra.vpc_id) and len(infra.public_subnet_ids or []) >= 2


def _to_schema(infra: models.InfraSpace, app_count: int) -> InfraSpace:
    # 계정 ID·VPC·서브넷은 배포용 내부 값이라 응답에 넣지 않는다 (API 명세 4절)
    return InfraSpace(
        id=infra.id,
        provider=infra.provider,
        name=infra.name,
        description=infra.description,
        network=infra.network,
        status=infra.status,
        computes=infra.computes,
        app_count=app_count,
        deployable_computes=[c for c in infra.computes if catalog.is_ready(c)] if is_deployable(infra) else [],
    )


def _app_counts(db: Session) -> dict[str, int]:
    stmt = (
        select(models.AppSpace.infra_id, func.count())
        .where(models.AppSpace.deleted_at.is_(None))
        .group_by(models.AppSpace.infra_id)
    )
    return dict(db.execute(stmt).all())


@router.get("", response_model=list[InfraSpace], summary="인프라 목록")
def list_infra_spaces(db: Session = Depends(get_db)) -> list[InfraSpace]:
    """볼 때마다 Workload 계정을 다시 읽어 최신 목록을 돌려줍니다. AWS를 읽지 못하면 저장된 목록을 그대로 돌려줍니다."""
    infra_sync.refresh(db)
    return _list(db)


def _list(db: Session) -> list[InfraSpace]:
    counts = _app_counts(db)
    infras = db.scalars(
        select(models.InfraSpace)
        # 기본 인프라는 "인프라 선택 안 함"으로만 쓴다 (10/3 합의). 상세 조회는 된다
        .where(models.InfraSpace.status != "unavailable", models.InfraSpace.is_default.is_(False))
        .order_by(models.InfraSpace.created_at, models.InfraSpace.id)
    )
    return [_to_schema(i, counts.get(i.id, 0)) for i in infras]


@router.post("/sync", response_model=list[InfraSpace], summary="인프라 갱신 (AWS에서 다시 읽기)")
def sync_infra_spaces(db: Session = Depends(get_db)) -> list[InfraSpace]:
    """Workload 계정에서 `InfraId` 태그가 붙은 VPC를 읽어 목록을 채우고, 갱신된 목록을 돌려줍니다.

    목록 조회(`GET`)도 같은 갱신을 하지만, 이 API는 AWS를 읽지 못하면 `502`로 알려 줍니다.
    DB가 비어 있어도 이것 한 번이면 배포에 쓸 VPC·서브넷까지 들어갑니다. AWS에서 사라진 인프라는 목록에서 빠집니다.
    5초 안에 다시 부르면 AWS를 부르지 않고 지금 목록을 돌려줍니다.
    """
    try:
        infra_sync.sync(db)
    except infra_sync.SyncError as e:
        db.rollback()
        raise HTTPException(502, detail={"error": "infra_sync_failed", "message": str(e)}) from e
    return _list(db)


@router.get("/{infra_id}", response_model=InfraSpace, summary="인프라 상세")
def get_infra_space(infra_id: str, db: Session = Depends(get_db)) -> InfraSpace:
    infra = db.get(models.InfraSpace, infra_id)
    if infra is None:
        raise HTTPException(404, detail=INFRA_NOT_FOUND)
    return _to_schema(infra, _app_counts(db).get(infra.id, 0))
