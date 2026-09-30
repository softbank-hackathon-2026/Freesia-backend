"""배포 조회와 진행 상황 SSE (가짜 데이터)."""
import asyncio

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app import mock_data
from app.schemas import Deployment, DeploymentEvent

router = APIRouter(prefix="/deployments", tags=["deployments"])

STEP_INTERVAL_SECONDS = 1.5
# CloudFront(30초)·ALB(60초)가 조용한 연결을 끊지 않도록 이 간격마다 연결 유지 신호를 보낸다
HEARTBEAT_SECONDS = 15


def find_deployment(deployment_id: str) -> Deployment:
    dep = mock_data.DEPLOYMENTS.get(deployment_id)
    if dep is None:
        raise HTTPException(404, detail={"error": "deployment_not_found", "message": "배포를 찾을 수 없습니다."})
    return dep


@router.get("/{deployment_id}", response_model=Deployment, summary="배포 상태")
def get_deployment(deployment_id: str) -> Deployment:
    return find_deployment(deployment_id)


@router.get(
    "/{deployment_id}/events",
    summary="배포 진행 상황 (SSE)",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}, "description": "DeploymentEvent가 한 줄씩 흘러나온다"}},
)
async def deployment_events(deployment_id: str) -> StreamingResponse:
    """`EventSource`로 연결하면 진행 이벤트(`DeploymentEvent` JSON)가 순서대로 온다.

    지금은 가짜 시나리오를 1.5초 간격으로 보낸다. 마지막 이벤트 status는 success 또는 failed.
    연결이 끊겨 다시 연결하면 처음부터가 아니라 현재 단계부터 이어서 보낸다.
    이벤트가 없는 동안에는 `HEARTBEAT_SECONDS`마다 SSE 주석(`: ping`)을 보내 연결을 유지한다.
    """
    dep = find_deployment(deployment_id)

    async def wait(seconds: float):
        # 기다리는 동안에도 연결 유지 신호를 보낸다
        remaining = seconds
        while remaining > 0:
            chunk = min(remaining, HEARTBEAT_SECONDS)
            await asyncio.sleep(chunk)
            remaining -= chunk
            if chunk >= HEARTBEAT_SECONDS:
                yield ": ping\n\n"

    async def stream():
        yield "retry: 3000\n\n"
        start = mock_data.PROGRESS.get(dep.id, 0)
        for i in range(start, len(mock_data.DEPLOY_STEPS)):
            status, step, message, progress = mock_data.DEPLOY_STEPS[i]
            url = f"https://{dep.app_space_id}.demo.freesia.dev" if status == "success" else None
            dep.status, dep.url = status, url
            mock_data.PROGRESS[dep.id] = i
            event = DeploymentEvent(
                status=status, step=step, message=message, progress=progress, url=url, at=mock_data.now()
            )
            yield f"id: {i}\nevent: progress\ndata: {event.model_dump_json()}\n\n"
            if status in ("success", "failed"):
                return
            async for ping in wait(STEP_INTERVAL_SECONDS):
                yield ping
            mock_data.PROGRESS[dep.id] = i + 1

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
