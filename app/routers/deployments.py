"""배포 조회, 진행 상황 SSE, 워크플로 콜백, 트리용 자원 목록 (API 명세 9절, ADR-009)."""
import asyncio

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import StreamingResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import db as db_module
from app import deploy, models, signing
from app.db import get_db
from app.schemas import Deployment, DeploymentCallback, DeploymentEvent, DeploymentResource

router = APIRouter(prefix="/deployments", tags=["deployments"])

# SSE: 이 간격으로 DB에서 새 이벤트를 확인한다
POLL_SECONDS = 1.0
# CloudFront(30초)·ALB(60초)가 조용한 연결을 끊지 않도록 이 간격마다 연결 유지 신호를 보낸다
HEARTBEAT_SECONDS = 15
# 연결 하나를 이 시간 넘게 붙잡지 않는다. 브라우저 EventSource가 Last-Event-ID로 다시 연결한다.
STREAM_MAX_SECONDS = 25 * 60


def find_deployment(db: Session, deployment_id: str) -> models.Deployment:
    dep = db.get(models.Deployment, deployment_id)
    if dep is None:
        raise HTTPException(404, detail={"error": "deployment_not_found", "message": "배포를 찾을 수 없습니다."})
    return dep


@router.get("/{deployment_id}", response_model=Deployment, summary="배포 상태")
def get_deployment(deployment_id: str, db: Session = Depends(get_db)) -> models.Deployment:
    return find_deployment(db, deployment_id)


@router.get("/{deployment_id}/resources", response_model=list[DeploymentResource], summary="자원별 상태 (트리)")
def list_resources(deployment_id: str, db: Session = Depends(get_db)) -> list[models.DeploymentResource]:
    """워크플로가 apply 전에 보낸 자원 목록과 자원마다의 최신 상태. 받은 순서대로 돌려준다."""
    find_deployment(db, deployment_id)
    stmt = (
        select(models.DeploymentResource)
        .where(models.DeploymentResource.deployment_id == deployment_id)
        .order_by(models.DeploymentResource.position)
    )
    return list(db.scalars(stmt))


def _load_events(deployment_id: str, after_seq: int | None) -> tuple[list[tuple[int, DeploymentEvent]], bool]:
    """after_seq 다음 이벤트들과 배포가 끝났는지. after_seq가 None이면 마지막 이벤트 하나만."""
    with db_module.SessionLocal() as db:
        # 배포 상태를 먼저 읽는다. 끝난 배포라면 마지막 이벤트까지 이미 저장돼 있어 아래 조회에 포함된다.
        dep = db.get(models.Deployment, deployment_id)
        finished = dep is None or dep.status in deploy.FINISHED
        stmt = select(models.DeploymentEvent).where(models.DeploymentEvent.deployment_id == deployment_id)
        if after_seq is None:
            stmt = stmt.order_by(models.DeploymentEvent.seq.desc()).limit(1)
        else:
            stmt = stmt.where(models.DeploymentEvent.seq > after_seq).order_by(models.DeploymentEvent.seq)
        return [(e.seq, DeploymentEvent.model_validate(e)) for e in db.scalars(stmt)], finished


@router.get(
    "/{deployment_id}/events",
    summary="배포 진행 상황 (SSE)",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}, "description": "DeploymentEvent가 한 줄씩 흘러나온다"}},
)
def deployment_events(
    deployment_id: str,
    last_event_id: str | None = Header(None, alias="Last-Event-ID"),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """`EventSource`로 연결하면 진행 이벤트(`DeploymentEvent` JSON)가 순서대로 온다.

    처음 연결하면 현재 단계부터, `Last-Event-ID`로 다시 연결하면 그 다음 이벤트부터 보낸다.
    마지막 이벤트 status는 success 또는 failed이고, 보낸 뒤 연결을 닫는다.
    이벤트가 없는 동안에는 `HEARTBEAT_SECONDS`마다 SSE 주석(`: ping`)을 보내 연결을 유지한다.
    """
    find_deployment(db, deployment_id)
    after_seq = int(last_event_id) if last_event_id and last_event_id.isdigit() else None

    async def stream():
        nonlocal after_seq
        yield "retry: 3000\n\n"
        loop = asyncio.get_running_loop()
        deadline = loop.time() + STREAM_MAX_SECONDS
        quiet = 0.0
        while True:
            events, finished = await asyncio.to_thread(_load_events, deployment_id, after_seq)
            for seq, event in events:
                yield f"id: {seq}\nevent: progress\ndata: {event.model_dump_json()}\n\n"
                after_seq, quiet = seq, 0.0
            if after_seq is None:
                after_seq = 0
            if finished or loop.time() >= deadline:
                return
            await asyncio.sleep(POLL_SECONDS)
            quiet += POLL_SECONDS
            if quiet >= HEARTBEAT_SECONDS:
                yield ": ping\n\n"
                quiet = 0.0

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _raw_body(request: Request) -> bytes:
    return await request.body()


@router.post(
    "/{deployment_id}/callback",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="워크플로 진행 보고 (프론트는 부르지 않음)",
    openapi_extra={
        "requestBody": {"content": {"application/json": {"schema": DeploymentCallback.model_json_schema()}}}
    },
)
def deployment_callback(
    deployment_id: str,
    body: bytes = Depends(_raw_body),
    signature: str | None = Header(None, alias="X-Hub-Signature-256"),
    db: Session = Depends(get_db),
) -> Response:
    """배포 워크플로(workload-deploy)가 단계마다 보낸다. 본문은 `DeploymentCallback`.

    헤더 `X-Hub-Signature-256: sha256=<본문의 HMAC-SHA256 hex>`가 맞아야 받는다 (GitHub Webhook과 같은 방식).
    `409`는 "무시했다"는 뜻이라 워크플로가 다시 보내지 않아도 된다.
    """
    # 서명이 맞기 전에는 배포가 있는지도 알려 주지 않는다
    signing.verify(body, signature)
    try:
        cb = DeploymentCallback.model_validate_json(body)
    except ValidationError as e:
        raise RequestValidationError(e.errors()) from e

    dep = find_deployment(db, deployment_id)
    if dep.status in deploy.FINISHED:
        raise HTTPException(409, detail={"error": "deployment_finished", "message": "이미 끝난 배포입니다."})
    if deploy.is_stale(dep, cb.status, cb.step):
        raise HTTPException(409, detail={"error": "stale_callback", "message": "이전 단계로 되돌아가는 보고입니다."})

    if cb.run_id and dep.run_id is None:
        dep.run_id = cb.run_id
    deploy.record_event(db, dep, cb.status, cb.step, message=cb.message, url=cb.url, reason=cb.reason)
    if cb.resources:
        deploy.upsert_resources(db, dep, cb.resources)
        # 자원 여러 개가 한 번에 오면 Terraform 계획 전체다 (apply 중에는 하나씩 온다). 계획에 없는 예상 자원은 지운다
        if len(cb.resources) > 1:
            deploy.drop_predicted(db, dep)
    if cb.status in deploy.FINISHED:
        # 빌드에서 실패해 계획이 안 왔거나, 계획 콜백이 유실된 경우 남은 예상 자원을 정리한다
        deploy.drop_predicted(db, dep)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
