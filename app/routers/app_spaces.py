"""앱 Space. 로그인 없이 모두가 보는 공용 목록이다."""
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import analysis, catalog, deploy, models
from app.config import get_settings
from app.db import get_db
from app.ids import new_id, now
from app.schemas import (
    Analysis,
    AppSpace,
    AppSpaceCreate,
    Compute,
    Deployment,
    DeploymentCreate,
    PlanCreate,
    PlanSet,
)

router = APIRouter(prefix="/app-spaces", tags=["app-spaces"])


def check_compute(infra: models.InfraSpace, compute: str) -> None:
    """인프라가 지원하고, 배포 템플릿도 준비된 컴퓨팅인지 (ADR-012, catalog.py)."""
    if compute not in infra.computes:
        raise HTTPException(
            400, detail={"error": "compute_not_supported", "message": "선택한 인프라에서 지원하지 않는 컴퓨팅입니다."}
        )
    if not catalog.is_ready(compute):
        raise HTTPException(
            400, detail={"error": "compute_not_ready", "message": "이 컴퓨팅은 배포 템플릿을 준비 중입니다."}
        )


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
def start_analysis(app_space_id: str, background: BackgroundTasks, db: Session = Depends(get_db)) -> Analysis:
    """분석을 시작하고 바로 `running`을 돌려준다. 결과는 GET으로 다시 확인한다 (예: 2초 간격).

    진행 중인 분석이 있으면 새로 시작하지 않고 그 분석을 돌려준다. 끝난 뒤에 부르면 새로 분석한다.
    AI 모델이 설정되지 않은 서버(AI_MODEL_ID 비어 있음)는 샘플 결과를 바로 `done`으로 돌려준다.
    """
    space = find_app_space(db, app_space_id)
    current = analysis.latest(db, space.id)
    if current is not None:
        analysis.expire_if_stuck(db, current)
        if current.status == "running":
            return analysis.to_schema(current)
    row, needs_run = analysis.start(db, space, db.get(models.InfraSpace, space.infra_id))
    if needs_run:
        background.add_task(analysis.run, row.id)
    return analysis.to_schema(row)


@router.get("/{app_space_id}/analysis", response_model=Analysis, summary="AI 분석 결과 (트리 데이터)")
def get_analysis(app_space_id: str, db: Session = Depends(get_db)) -> Analysis:
    """가장 최근 분석. `running`이면 아직 진행 중이고, 3분이 넘도록 끝나지 않으면 `failed`로 바뀐다."""
    find_app_space(db, app_space_id)
    current = analysis.latest(db, app_space_id)
    if current is None:
        raise HTTPException(404, detail={"error": "analysis_not_found", "message": "아직 분석하지 않은 앱입니다."})
    analysis.expire_if_stuck(db, current)
    return analysis.to_schema(current)


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
    check_compute(db.get(models.InfraSpace, space.infra_id), body.compute)
    if body.plan_id is not None:
        plan = db.get(models.Plan, body.plan_id)
        if plan is None:
            raise HTTPException(400, detail={"error": "plan_not_found", "message": "없는 구성안입니다."})
        if plan.app_space_id != space.id or plan.compute != body.compute:
            raise HTTPException(
                400, detail={"error": "plan_mismatch", "message": "이 앱과 컴퓨팅의 구성안이 아닙니다."}
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


def _plan_set(compute: str, plans: list[models.Plan]) -> PlanSet:
    return PlanSet(compute=compute, plans=plans)


@router.post("/{app_space_id}/plans", response_model=PlanSet, summary="구성안 만들기")
def create_plan(app_space_id: str, body: PlanCreate, db: Session = Depends(get_db)) -> PlanSet:
    """고른 컴퓨팅의 템플릿과 넣을 값(구성안)을 만든다. 구성안은 1개다 (ADR-012).

    지금은 템플릿 기본값으로 채운다. AI가 값을 채우게 되면 같은 응답 모양으로 값만 바뀐다.
    """
    space = find_app_space(db, app_space_id)
    check_compute(db.get(models.InfraSpace, space.infra_id), body.compute)
    template = catalog.TEMPLATES[body.compute]
    latest = analysis.latest(db, space.id)
    plan = models.Plan(
        id=new_id("plan"),
        app_space_id=space.id,
        analysis_id=latest.id if latest is not None and latest.status == "done" else None,
        compute=body.compute,
        template=template.name,
        values=catalog.fill_values(body.compute),
        name=template.plan_name,
        summary=template.summary,
        pros=template.pros,
        cons=template.cons,
        created_at=now(),
    )
    db.add(plan)
    db.commit()
    return _plan_set(body.compute, [plan])


@router.get("/{app_space_id}/plans", response_model=PlanSet, summary="구성안 보기")
def get_plans(app_space_id: str, compute: Compute, db: Session = Depends(get_db)) -> PlanSet:
    """그 컴퓨팅으로 가장 최근에 만든 구성안. 만든 적이 없으면 404."""
    find_app_space(db, app_space_id)
    plan = db.scalar(
        select(models.Plan)
        .where(models.Plan.app_space_id == app_space_id, models.Plan.compute == compute)
        .order_by(models.Plan.created_at.desc())
        .limit(1)
    )
    if plan is None:
        raise HTTPException(404, detail={"error": "plan_not_found", "message": "아직 만든 구성안이 없습니다."})
    return _plan_set(compute, [plan])
