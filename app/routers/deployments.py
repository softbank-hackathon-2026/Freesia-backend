"""배포 조회와 진행 상황 SSE (가짜 데이터)."""
import asyncio

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app import mock_data
from app.schemas import Deployment, DeploymentEvent

router = APIRouter(prefix="/deployments", tags=["deployments"])

STEP_INTERVAL_SECONDS = 1.5


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
    """
    dep = find_deployment(deployment_id)

    async def stream():
        for status, step, message, progress in mock_data.DEPLOY_STEPS:
            url = f"https://{dep.app_space_id}.demo.freesia.dev" if status == "success" else None
            dep.status, dep.url = status, url
            event = DeploymentEvent(
                status=status, step=step, message=message, progress=progress, url=url, at=mock_data.now()
            )
            yield f"event: progress\ndata: {event.model_dump_json()}\n\n"
            if status != "success":
                await asyncio.sleep(STEP_INTERVAL_SECONDS)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})
