"""배포된 앱의 지표·로그 (API 명세 12절, ADR-017). 알람 생성·알림 전송은 범위 밖이다 (10/2 김동윤 님 제안)."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app import models, monitoring
from app.db import get_db
from app.routers.app_spaces import find_app_space
from app.schemas import AppLogs, AppMetrics

router = APIRouter(prefix="/app-spaces", tags=["monitoring"])

NOT_DEPLOYED = "지금 실제로 배포되어 있지 않은 앱입니다."
UNSUPPORTED = "이 컴퓨팅은 아직 모니터링을 지원하지 않습니다."


def _live(db: Session, app_space_id: str) -> tuple[models.Deployment | None, str | None, str | None]:
    """(떠 있는 배포, 상태, 문장). 떠 있으면 상태와 문장은 None."""
    space = find_app_space(db, app_space_id)
    dep = db.get(models.Deployment, space.latest_deployment_id) if space.latest_deployment_id else None
    live = monitoring.live_deployment(space, dep)
    if live is None:
        return None, "not_deployed", NOT_DEPLOYED
    if live.compute != "ecs-fargate":
        return None, "unsupported", UNSUPPORTED
    return live, None, None


@router.get("/{app_space_id}/metrics", response_model=AppMetrics, summary="앱 지표 (CPU·메모리·응답 시간)")
def get_metrics(app_space_id: str, db: Session = Depends(get_db)) -> AppMetrics:
    """최근 1분 값입니다. 상태가 `ok`가 아니면 값은 모두 null입니다. 15초 동안은 같은 값을 돌려줍니다."""
    live, status, message = _live(db, app_space_id)
    if live is None:
        return AppMetrics(status=status, message=message)
    try:
        values = monitoring.get_metrics(app_space_id)
    except monitoring.MonitoringError as e:
        return AppMetrics(status="error", message=str(e))
    if values["cpu_percent"] is None and values["memory_percent"] is None:
        return AppMetrics(status="waiting", message="아직 수집된 지표가 없습니다. 배포 후 몇 분 걸립니다.")
    return AppMetrics(status="ok", **values)


@router.get("/{app_space_id}/logs", response_model=AppLogs, summary="앱 실행 로그")
def get_logs(
    app_space_id: str, limit: int = Query(100, ge=1, le=500), db: Session = Depends(get_db)
) -> AppLogs:
    """최근 1시간에서 마지막 `limit`줄을 오래된 것부터 돌려줍니다."""
    live, status, message = _live(db, app_space_id)
    if live is None:
        return AppLogs(status=status, message=message)
    try:
        lines = monitoring.get_logs(app_space_id, limit)
    except monitoring.MonitoringError as e:
        return AppLogs(status="error", message=str(e))
    if not lines:
        return AppLogs(status="waiting", message="최근 1시간 동안 로그가 없습니다.")
    return AppLogs(status="ok", lines=lines)
