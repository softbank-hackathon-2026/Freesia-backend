"""앱 Space. 로그인 없이 모두가 보는 공용 목록이다."""
from datetime import timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Response, status
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import analysis, catalog, deploy, github, models, signing
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
    Teardown,
    TeardownCallback,
    parse_github_url,
)
from app.routers.deployments import _raw_body

router = APIRouter(prefix="/app-spaces", tags=["app-spaces"])

# 내리기 콜백이 이 시간 안에 오지 않으면 다시 요청할 수 있다 (Destroy 워크플로 제한 시간 20분 + 대기열)
TEARDOWN_TIMEOUT = timedelta(minutes=30)
TEARDOWN_IN_PROGRESS = {"error": "teardown_in_progress", "message": "내리는 중입니다. 끝난 뒤에 다시 시도해 주세요."}


def _in_progress(db: Session, space: models.AppSpace) -> models.Deployment | None:
    return db.scalar(
        select(models.Deployment).where(
            models.Deployment.app_space_id == space.id, models.Deployment.status.not_in(deploy.FINISHED)
        )
    )


def _real_deployments(db: Session, space: models.AppSpace, success_only: bool = False) -> bool:
    """마지막으로 내린 뒤에 실제 워크플로로 배포한 적이 있는지.

    run_id는 진짜 워크플로가 첫 콜백으로 보낸다. 가짜 진행은 채우지 않는다.
    """
    query = select(models.Deployment.id).where(
        models.Deployment.app_space_id == space.id, models.Deployment.run_id.is_not(None)
    )
    if success_only:
        query = query.where(models.Deployment.status == "success")
    if space.teardown_status == "success":
        query = query.where(models.Deployment.created_at > space.teardown_requested_at)
    return db.scalar(query.limit(1)) is not None


def _tearing_down(space: models.AppSpace) -> bool:
    if space.teardown_status != "requested":
        return False
    at = space.teardown_requested_at
    at = at if at.tzinfo else at.replace(tzinfo=timezone.utc)  # SQLite는 시간대를 버린다
    return now() - at < TEARDOWN_TIMEOUT


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
    if space is None or space.deleted_at is not None:
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
    return list(
        db.scalars(
            select(models.AppSpace)
            .where(models.AppSpace.deleted_at.is_(None))
            .order_by(models.AppSpace.created_at.desc())
        )
    )


@router.get("/{app_space_id}", response_model=AppSpace, summary="앱 Space 상세")
def get_app_space(app_space_id: str, db: Session = Depends(get_db)) -> models.AppSpace:
    return find_app_space(db, app_space_id)


@router.delete("/{app_space_id}", status_code=status.HTTP_204_NO_CONTENT, summary="앱 삭제 (목록에서 숨기기)")
def delete_app_space(app_space_id: str, db: Session = Depends(get_db)) -> Response:
    """앱을 목록에서 숨긴다. DB에서 지우지 않고 배포·분석 기록은 남긴다.

    AWS에 떠 있는 앱은 먼저 내려야 한다. 숨기면 비용은 계속 나가는데 화면에서 내릴 수 없게 된다.
    """
    space = find_app_space(db, app_space_id)
    if _in_progress(db, space):
        raise HTTPException(
            409, detail={"error": "deployment_in_progress", "message": "배포가 끝난 뒤에 삭제할 수 있습니다."}
        )
    if _tearing_down(space):
        raise HTTPException(409, detail=TEARDOWN_IN_PROGRESS)
    # 성공한 실제 배포만 센다. 빌드에서 멈춘 배포는 지울 자원이 없고, 내리기도 실패해서 영영 못 지우게 된다
    if _real_deployments(db, space, success_only=True):
        raise HTTPException(
            409, detail={"error": "app_still_deployed", "message": "AWS에 배포되어 있는 앱입니다. 먼저 내려 주세요."}
        )
    space.deleted_at = now()
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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
    if _tearing_down(space):
        raise HTTPException(409, detail=TEARDOWN_IN_PROGRESS)
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
    else:
        _start_workflow(db, space, dep)
    return dep


def _start_workflow(db: Session, space: models.AppSpace, dep: models.Deployment) -> None:
    """배포 레포 deploy.yml을 실행한다 (ADR-009). 배포를 먼저 저장해 두어야 곧바로 오는 콜백을 받을 수 있다.

    실행 요청이 실패하면 배포를 바로 failed로 남긴다. 진행 상황은 이후 워크플로 콜백으로 온다.
    """
    try:
        latest = analysis.latest(db, space.id)
        # 분석한 코드와 배포하는 코드를 같게 맞춘다. 분석 커밋이 없으면 브랜치 최신 커밋
        dep.commit_sha = (latest.commit_sha if latest is not None and latest.commit_sha else None) or (
            github.latest_commit(space.repo_url, space.branch)
        )
        # 워크플로는 plan_id로 템플릿·값·VPC를 받아 간다. 구성안 없이 배포하면 기본값 구성안을 만든다
        if dep.plan_id is None:
            dep.plan_id = _new_plan(db, space, dep.compute).id
        db.commit()
        owner, repo = parse_github_url(space.repo_url)
        github.dispatch(
            "deploy.yml",
            {
                "deployment_id": dep.id,
                "application_id": space.id,
                "repo": f"{owner}/{repo}",
                "commit_sha": dep.commit_sha,
                "infra_id": space.infra_id,
                "compute": dep.compute,
                "plan_id": dep.plan_id,
                "callback_url": f"{get_settings().public_api_base}/deployments/{dep.id}/callback",
            },
        )
    except github.GitHubError as e:
        deploy.record_event(db, dep, "failed", dep.step, reason=str(e))
        db.commit()


@router.post(
    "/{app_space_id}/teardown",
    response_model=Teardown,
    status_code=status.HTTP_202_ACCEPTED,
    summary="배포된 앱 내리기",
)
def teardown_app_space(app_space_id: str, db: Session = Depends(get_db)) -> Teardown:
    """배포 레포 Destroy 워크플로로 그 앱의 AWS 자원(ECS 서비스, 로드밸런서, 로그 그룹 등)을 지운다.

    실제로 배포된 적 있는 앱만 내릴 수 있다. 가짜 진행만 한 앱은 지울 자원이 없다.
    앱 기록은 남고 다시 배포할 수 있다. 결과는 Destroy 워크플로가 내리기 콜백으로 알려 주고,
    앱의 `teardown_status`가 `requested` → `success` / `failed`로 바뀐다.
    """
    space = find_app_space(db, app_space_id)
    if _tearing_down(space):
        raise HTTPException(409, detail=TEARDOWN_IN_PROGRESS)
    if _in_progress(db, space):
        raise HTTPException(
            409, detail={"error": "deployment_in_progress", "message": "배포가 끝난 뒤에 내릴 수 있습니다."}
        )
    if not _real_deployments(db, space):
        raise HTTPException(409, detail={"error": "not_deployed", "message": "실제로 배포된 자원이 없습니다."})
    callback_url = f"{get_settings().public_api_base}/app-spaces/{space.id}/teardown/callback"
    try:
        github.dispatch("destroy.yml", {"application_id": space.id, "confirm": space.id, "callback_url": callback_url})
    except github.GitHubError as e:
        raise HTTPException(502, detail={"error": "teardown_failed", "message": str(e)}) from e
    space.teardown_status = "requested"
    space.teardown_requested_at = now()
    space.teardown_finished_at = None
    space.teardown_reason = None
    db.commit()
    return Teardown(app_space_id=space.id, requested_at=space.teardown_requested_at)


@router.post(
    "/{app_space_id}/teardown/callback",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="내리기 결과 보고 (프론트는 부르지 않음)",
    openapi_extra={"requestBody": {"content": {"application/json": {"schema": TeardownCallback.model_json_schema()}}}},
)
def teardown_callback(
    app_space_id: str,
    body: bytes = Depends(_raw_body),
    signature: str | None = Header(None, alias="X-Hub-Signature-256"),
    db: Session = Depends(get_db),
) -> Response:
    """Destroy 워크플로가 끝날 때 한 번 보낸다. 본문은 `TeardownCallback`. 서명은 배포 콜백과 같다.

    `409`는 "무시했다"는 뜻이라 워크플로가 다시 보내지 않아도 된다.
    """
    # 서명이 맞기 전에는 앱이 있는지도 알려 주지 않는다
    signing.verify(body, signature)
    try:
        cb = TeardownCallback.model_validate_json(body)
    except ValidationError as e:
        raise RequestValidationError(e.errors()) from e

    space = find_app_space(db, app_space_id)
    if space.teardown_status != "requested":
        raise HTTPException(409, detail={"error": "teardown_not_requested", "message": "요청 중인 내리기가 없습니다."})
    space.teardown_status = cb.status
    space.teardown_finished_at = now()
    space.teardown_reason = deploy._cut(cb.reason, 1000) if cb.status == "failed" and cb.reason else None
    if cb.status == "success":
        deploy.mark_resources_deleted(db, space.id, before=space.teardown_requested_at)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _plan_set(compute: str, plans: list[models.Plan]) -> PlanSet:
    return PlanSet(compute=compute, plans=plans)


@router.post("/{app_space_id}/plans", response_model=PlanSet, summary="구성안 만들기")
def create_plan(app_space_id: str, body: PlanCreate, db: Session = Depends(get_db)) -> PlanSet:
    """고른 컴퓨팅의 템플릿과 넣을 값(구성안)을 만든다. 구성안은 1개다 (ADR-012).

    지금은 템플릿 기본값으로 채운다. AI가 값을 채우게 되면 같은 응답 모양으로 값만 바뀐다.
    """
    space = find_app_space(db, app_space_id)
    check_compute(db.get(models.InfraSpace, space.infra_id), body.compute)
    plan = _new_plan(db, space, body.compute)
    db.commit()
    return _plan_set(body.compute, [plan])


def _new_plan(db: Session, space: models.AppSpace, compute: str) -> models.Plan:
    """AI가 분석 때 채운 템플릿 값으로 구성안을 만든다. 분석이 없거나 값이 틀리면 템플릿 기본값. commit은 부르는 쪽.

    포트는 AI가 확인하지 못했으면 비워 둔다. 배포 워크플로가 Dockerfile EXPOSE를 쓰고, 없으면 템플릿 기본값을 쓴다
    (배포 레포 scripts/plan.py). 기본값 80을 넣으면 EXPOSE 3000인 앱이 헬스체크에서 실패한다.
    """
    template = catalog.TEMPLATES[compute]
    latest = analysis.latest(db, space.id)
    done = latest if latest is not None and latest.status == "done" else None
    ai_values = ((done.result or {}).get("template_values") or {}).get(compute) if done else None
    try:
        values = catalog.fill_values(compute, ai_values)
    except ValueError:
        ai_values, values = None, catalog.fill_values(compute)
    if "container_port" not in (ai_values or {}):
        values.pop("container_port", None)
    plan = models.Plan(
        id=new_id("plan"),
        app_space_id=space.id,
        analysis_id=done.id if done else None,
        compute=compute,
        template=template.name,
        values=values,
        name=template.plan_name,
        summary=template.summary,
        pros=template.pros,
        cons=template.cons,
        created_at=now(),
    )
    db.add(plan)
    db.flush()
    return plan


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
