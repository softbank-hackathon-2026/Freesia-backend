"""앱 Space (가짜 데이터). 로그인 없이 모두가 보는 공용 목록이다."""
from fastapi import APIRouter, HTTPException, status

from app import mock_data
from app.routers.infra_spaces import find_infra
from app.schemas import Analysis, AppSpace, AppSpaceCreate, Deployment, DeploymentCreate

router = APIRouter(prefix="/app-spaces", tags=["app-spaces"])


def find_app_space(app_space_id: str) -> AppSpace:
    space = mock_data.APP_SPACES.get(app_space_id)
    if space is None:
        raise HTTPException(404, detail={"error": "app_space_not_found", "message": "앱 Space를 찾을 수 없습니다."})
    return space


@router.post("", response_model=AppSpace, status_code=status.HTTP_201_CREATED, summary="앱 Space 생성")
def create_app_space(body: AppSpaceCreate) -> AppSpace:
    find_infra(body.infra_id)
    space = AppSpace(id=mock_data.new_id("app"), created_at=mock_data.now(), **body.model_dump())
    mock_data.APP_SPACES[space.id] = space
    return space


@router.get("", response_model=list[AppSpace], summary="앱 Space 목록")
def list_app_spaces() -> list[AppSpace]:
    return sorted(mock_data.APP_SPACES.values(), key=lambda s: s.created_at, reverse=True)


@router.get("/{app_space_id}", response_model=AppSpace, summary="앱 Space 상세")
def get_app_space(app_space_id: str) -> AppSpace:
    return find_app_space(app_space_id)


@router.post("/{app_space_id}/analysis", response_model=Analysis, summary="AI 분석 시작")
def start_analysis(app_space_id: str) -> Analysis:
    """지금은 바로 완료된 가짜 결과를 준다. 실제로는 status=running으로 시작한다."""
    find_app_space(app_space_id)
    return mock_data.sample_analysis()


@router.get("/{app_space_id}/analysis", response_model=Analysis, summary="AI 분석 결과 (트리 데이터)")
def get_analysis(app_space_id: str) -> Analysis:
    find_app_space(app_space_id)
    return mock_data.sample_analysis()


@router.post(
    "/{app_space_id}/deployments",
    response_model=Deployment,
    status_code=status.HTTP_201_CREATED,
    summary="배포 시작",
)
def create_deployment(app_space_id: str, body: DeploymentCreate) -> Deployment:
    """배포를 만들고 id를 돌려준다. 진행 상황은 GET /deployments/{id}/events (SSE)로 받는다."""
    space = find_app_space(app_space_id)
    if body.compute not in find_infra(space.infra_id).computes:
        raise HTTPException(
            400, detail={"error": "compute_not_supported", "message": "선택한 인프라에서 지원하지 않는 컴퓨팅입니다."}
        )
    dep = Deployment(
        id=mock_data.new_id("dep"),
        app_space_id=space.id,
        compute=body.compute,
        status="pending",
        created_at=mock_data.now(),
    )
    mock_data.DEPLOYMENTS[dep.id] = dep
    space.latest_deployment_id = dep.id
    return dep
