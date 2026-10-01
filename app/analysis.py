"""AI 분석 실행과 저장 (API 명세 7절). AI 호출 자체는 app/ai (강효승)에 있다.

분석은 20초 안팎 걸려서 POST는 running을 바로 돌려주고, 실제 분석은 백그라운드에서 돈다.
어떤 오류가 나도, 서버가 도중에 재시작돼도 running에 멈춰 있지 않게 한다.
"""
import logging
from datetime import timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import db as db_module
from app import mock_data, models
from app.ai.analyze import FAIL_MESSAGE, run_analysis
from app.config import get_settings
from app.ids import new_id, now
from app.schemas import Analysis

logger = logging.getLogger(__name__)

# GitHub 읽기 + 모델 1·2단계(각 최대 60초)를 넘는 값. 이보다 오래 running이면 실패로 본다
RUNNING_TIMEOUT = timedelta(minutes=3)
SAMPLE_MODEL_ID = "sample"


def latest(db: Session, app_space_id: str) -> models.Analysis | None:
    stmt = (
        select(models.Analysis)
        .where(models.Analysis.app_space_id == app_space_id)
        .order_by(models.Analysis.created_at.desc())
        .limit(1)
    )
    return db.scalar(stmt)


def to_schema(row: models.Analysis) -> Analysis:
    if row.result is None:
        return Analysis(status=row.status)
    return Analysis.model_validate(row.result)


def _finish(row: models.Analysis, result: Analysis, commit_sha: str | None) -> None:
    row.status = result.status
    row.result = result.model_dump(mode="json")
    row.commit_sha = commit_sha
    row.finished_at = now()


def expire_if_stuck(db: Session, row: models.Analysis) -> None:
    """너무 오래 running이면 실패로 바꾼다. 백그라운드 작업이 서버 재시작으로 사라진 경우다."""
    created = row.created_at if row.created_at.tzinfo else row.created_at.replace(tzinfo=timezone.utc)
    if row.status == "running" and now() - created > RUNNING_TIMEOUT:
        _finish(row, Analysis(status="failed", mascot_message=FAIL_MESSAGE), None)
        db.commit()


def start(db: Session, space: models.AppSpace, infra: models.InfraSpace) -> tuple[models.Analysis, bool]:
    """새 분석을 만든다. (분석, 백그라운드 실행이 필요한지)를 돌려준다.

    AI 모델이 설정되지 않았으면(AI_MODEL_ID 비어 있음) 지금까지처럼 샘플 결과를 바로 저장한다.
    """
    model_id = get_settings().ai_model_id
    row = models.Analysis(
        id=new_id("ana"),
        app_space_id=space.id,
        infra_id=infra.id,
        status="running",
        model_id=model_id or SAMPLE_MODEL_ID,
        created_at=now(),
    )
    if not model_id:
        _finish(row, mock_data.sample_analysis(), None)
    db.add(row)
    db.commit()
    return row, bool(model_id)


def run(analysis_id: str) -> None:
    """백그라운드에서 분석하고 결과를 저장한다. 모델 호출 동안에는 DB 연결을 잡고 있지 않는다."""
    with db_module.SessionLocal() as db:
        row = db.get(models.Analysis, analysis_id)
        if row is None or row.status != "running":
            return
        space = db.get(models.AppSpace, row.app_space_id)
        infra = db.get(models.InfraSpace, row.infra_id)
        repo_url, branch, computes = space.repo_url, space.branch, list(infra.computes)

    try:
        result, commit_sha = run_analysis(repo_url, branch, computes)
    except Exception:
        # run_analysis는 실패를 failed로 돌려주지만, 예상 못 한 예외도 running으로 남기지 않는다
        logger.exception("분석 중 예상 못 한 오류: %s", analysis_id)
        result, commit_sha = Analysis(status="failed", mascot_message=FAIL_MESSAGE), None

    with db_module.SessionLocal() as db:
        row = db.get(models.Analysis, analysis_id)
        # 그사이 시간 초과로 실패 처리됐으면 덮어쓰지 않는다 (화면이 이미 실패를 보여 줬다)
        if row is None or row.status != "running":
            return
        _finish(row, result, commit_sha)
        db.commit()
