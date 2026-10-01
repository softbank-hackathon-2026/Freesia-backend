"""배포 진행 기록. 워크플로 콜백과 가짜 진행(시뮬레이션)이 같은 함수로 DB에 남긴다 (ADR-009)."""
import time

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import db as db_module
from app import models
from app.ids import now
from app.schemas import CallbackResource

# API 명세 9-3절. 단계는 이 순서로만 진행한다.
STEP_ORDER = ["queued", "prepare", "build", "deploy", "verify", "done"]
STEP_PROGRESS = {"queued": 0, "prepare": 10, "build": 30, "deploy": 60, "verify": 90, "done": 100}
STEP_MESSAGES = {
    "queued": "배포 요청을 받았어요",
    "prepare": "Terraform 코드를 준비하는 중",
    "build": "컨테이너 이미지를 빌드하는 중",
    "deploy": "AWS에 자원을 만들고 앱을 배포하는 중",
    "verify": "앱이 정상 응답하는지 확인하는 중",
    "done": "배포 완료!",
}
FINISHED = ("success", "failed")

# 자원 상태가 되돌아가지 않게 비교할 순서
RESOURCE_STATE_RANK = {"pending": 0, "in_progress": 1, "done": 2, "failed": 2}

# 가짜 진행: 워크플로 콜백 5번과 같은 순서 (ADR-009)
SIMULATED_STEPS = [
    ("pending", "prepare"),
    ("building", "build"),
    ("deploying", "deploy"),
    ("deploying", "verify"),
    ("success", "done"),
]
STEP_INTERVAL_SECONDS = 1.5


def _cut(text: str | None, limit: int) -> str | None:
    return text[:limit] if text else text


def record_event(
    db: Session,
    dep: models.Deployment,
    status: str,
    step: str,
    message: str | None = None,
    url: str | None = None,
    reason: str | None = None,
) -> None:
    """배포 상태를 바꾸고 진행 이벤트를 한 건 남긴다. commit은 부르는 쪽에서 한다."""
    dep.status, dep.step = status, step
    if url:
        dep.url = _cut(url, 500)
    if reason:
        dep.reason = _cut(reason, 1000)
    if status in FINISHED:
        dep.finished_at = now()
    last_seq = db.scalar(
        select(func.max(models.DeploymentEvent.seq)).where(models.DeploymentEvent.deployment_id == dep.id)
    )
    db.add(
        models.DeploymentEvent(
            deployment_id=dep.id,
            seq=(last_seq or 0) + 1,
            status=status,
            step=step,
            message=_cut(message or reason or STEP_MESSAGES[step], 500),
            progress=STEP_PROGRESS[step],
            url=dep.url if status == "success" else None,
            at=now(),
        )
    )


def is_stale(dep: models.Deployment, status: str, step: str) -> bool:
    """이전 단계로 되돌아가는 콜백인가. 같은 단계는 허용한다(자원별 콜백이 여러 번 온다).

    실패는 단계와 상관없이 받는다. 워크플로는 job 단위로 실패를 보고해서,
    verify 중에 실패해도 최종 콜백의 step이 deploy로 올 수 있다.
    """
    return status != "failed" and STEP_ORDER.index(step) < STEP_ORDER.index(dep.step)


def upsert_resources(db: Session, dep: models.Deployment, resources: list[CallbackResource]) -> None:
    """자원 상태를 address 기준으로 덮어쓴다. 조회용 data 자원은 트리에 넣지 않는다."""
    existing = {
        r.address: r
        for r in db.scalars(
            select(models.DeploymentResource).where(models.DeploymentResource.deployment_id == dep.id)
        )
    }
    for item in resources:
        if item.address.startswith("data.") or item.action == "read":
            continue
        row = existing.get(item.address)
        if row is None:
            row = models.DeploymentResource(deployment_id=dep.id, address=item.address, position=len(existing))
            db.add(row)
            existing[item.address] = row
        # 순서가 뒤바뀌어 온 이전 상태는 무시한다. 다만 replace처럼 action이 바뀌면 새 작업이라 받는다.
        elif (
            item.action == row.action
            and RESOURCE_STATE_RANK[item.state] < RESOURCE_STATE_RANK[row.state]
        ):
            continue
        row.type, row.action, row.state = item.type, item.action, item.state
        row.reason = _cut(item.reason, 1000) if item.state == "failed" else None
        row.updated_at = now()


def simulate(deployment_id: str) -> None:
    """워크플로 대신 가짜 진행을 기록한다. 배포를 만든 뒤 백그라운드에서 돈다."""
    for status, step in SIMULATED_STEPS:
        time.sleep(STEP_INTERVAL_SECONDS)
        with db_module.SessionLocal() as db:
            dep = db.get(models.Deployment, deployment_id)
            if dep is None or dep.status in FINISHED:
                return
            url = f"https://{dep.app_space_id}.demo.freesia.dev" if status == "success" else None
            record_event(db, dep, status, step, url=url)
            db.commit()
