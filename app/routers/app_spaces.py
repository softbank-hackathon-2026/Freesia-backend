"""앱 Space. 로그인 없이 모두가 보는 공용 목록이다. AI 분석은 아직 가짜 결과를 준다."""
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import deploy, mock_data, models
from app.config import get_settings
from app.db import get_db
from app.ids import new_id, now
from app.schemas import Analysis, AppSpace, AppSpaceCreate, Deployment, DeploymentCreate

router = APIRouter(prefix="/app-spaces", tags=["app-spaces"])


def find_app_space(db: Session, app_space_id: str) -> models.AppSpace:
    space = db.get(models.AppSpace, app_space_id)
    if space is None:
        raise HTTPException(404, detail={"error": "app_space_not_found", "message": "앱 Space를 찾을 수 없습니다."})
    return space


@router.post("", response_model=AppSpace, status_code=status.HTTP_201_CREATED, summary="앱 Space 생성")
def create_app_space(body: AppSpaceCreate, db: Session = Depends(get_db)) -> models.AppSpace:
    """저장소(repo_url + branch)와 인프라로 앱을 만든다.

    명세 6절은 등록된 저장소만 받기로 했지만, 프론트 통합 화면이 아직 저장소 API에 연결되지 않아
    당분간 등록되지 않은 저장소도 받는다(repository_id가 비어 있음). 연결되면 400 repository_not_registered로 막는다.
    """
    if db.get(models.InfraSpace, body.infra_id) is None:
        raise HTTPException(400, detail={"error": "infra_not_found", "message": "없는 인프라입니다."})
    repo = db.scalar(
        select(models.Repository).where(
            models.Repository.repo_url == body.repo_url, models.Repository.branch == body.branch
        )
    )
    space = models.AppSpace(
        id=new_id("app"),
        name=body.name,
        repository_id=repo.id if repo else None,
        repo_url=body.repo_url,
        branch=body.branch,
        infra_id=body.infra_id,
        created_at=now(),
    )
    db.add(space)
    db.commit()
    return space


@router.get("", response_model=list[AppSpace], summary="앱 Space 목록")
def list_app_spaces(db: Session = Depends(get_db)) -> list[models.AppSpace]:
    return list(db.scalars(select(models.AppSpace).order_by(models.AppSpace.created_at.desc())))


@router.get("/{app_space_id}", response_model=AppSpace, summary="앱 Space 상세")
def get_app_space(app_space_id: str, db: Session = Depends(get_db)) -> models.AppSpace:
    return find_app_space(db, app_space_id)


@router.post("/{app_space_id}/analysis", response_model=Analysis, summary="AI 분석 시작")
def start_analysis(app_space_id: str, db: Session = Depends(get_db)) -> Analysis:
    """지금은 바로 완료된 가짜 결과를 준다. 실제로는 status=running으로 시작한다."""
    find_app_space(db, app_space_id)
    return mock_data.sample_analysis()


@router.get("/{app_space_id}/analysis", response_model=Analysis, summary="AI 분석 결과 (트리 데이터)")
def get_analysis(app_space_id: str, db: Session = Depends(get_db)) -> Analysis:
    find_app_space(db, app_space_id)
    return mock_data.sample_analysis()


@router.post(
    "/{app_space_id}/deployments",
    response_model=Deployment,
    status_code=status.HTTP_201_CREATED,
    summary="배포 시작",
)
def create_deployment(
    app_space_id: str, body: DeploymentCreate, background: BackgroundTasks, db: Session = Depends(get_db)
) -> models.Deployment:
    """배포를 만들고 id를 돌려준다. 진행 상황은 GET /deployments/{id}/events (SSE)로 받는다.

    배포 레포 연결 전(DEPLOY_SIMULATE=true)에는 워크플로 대신 가짜 진행을 DB에 기록한다.
    """
    space = find_app_space(db, app_space_id)
    infra = db.get(models.InfraSpace, space.infra_id)
    if body.compute not in infra.computes:
        raise HTTPException(
            400, detail={"error": "compute_not_supported", "message": "선택한 인프라에서 지원하지 않는 컴퓨팅입니다."}
        )
    in_progress = db.scalar(
        select(models.Deployment).where(
            models.Deployment.app_space_id == space.id, models.Deployment.status.not_in(deploy.FINISHED)
        )
    )
    if in_progress:
        raise HTTPException(
            409, detail={"error": "deployment_in_progress", "message": "이 앱은 이미 배포가 진행 중입니다."}
        )
    dep = models.Deployment(
        id=new_id("dep"),
        app_space_id=space.id,
        compute=body.compute,
        plan_id=body.plan_id,
        status="pending",
        step="queued",
        created_at=now(),
    )
    db.add(dep)
    db.flush()
    deploy.record_event(db, dep, "pending", "queued")
    space.latest_deployment_id = dep.id
    db.commit()
    if get_settings().deploy_simulate:
        background.add_task(deploy.simulate, dep.id)
    return dep
