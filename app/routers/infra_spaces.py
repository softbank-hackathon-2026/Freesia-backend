"""인프라 Space 조회 (가짜 데이터). 플랫폼은 조회만 한다 (2일차 회의 2:27:19)."""
from fastapi import APIRouter, HTTPException

from app import mock_data
from app.schemas import InfraSpace

router = APIRouter(prefix="/infra-spaces", tags=["infra-spaces"])


def find_infra(infra_id: str) -> InfraSpace:
    infra = next((i for i in mock_data.INFRA_SPACES if i.id == infra_id), None)
    if infra is None:
        raise HTTPException(404, detail={"error": "infra_not_found", "message": "인프라를 찾을 수 없습니다."})
    return infra


@router.get("", response_model=list[InfraSpace], summary="인프라 목록")
def list_infra_spaces() -> list[InfraSpace]:
    return mock_data.INFRA_SPACES


@router.get("/{infra_id}", response_model=InfraSpace, summary="인프라 상세")
def get_infra_space(infra_id: str) -> InfraSpace:
    return find_infra(infra_id)
