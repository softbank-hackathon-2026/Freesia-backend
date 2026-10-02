"""테스트 공통 준비물. 실제 Postgres 없이 돌아가도록 SQLite 메모리 DB를 쓴다."""
import os
from datetime import datetime, timedelta, timezone

# 앱을 불러오기 전에 테스트용 설정을 넣는다
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["APP_VERSION"] = "test-sha"

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app import deploy, github, models  # noqa: E402
from app.db import Base, SessionLocal, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.routers import deployments  # noqa: E402

engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
TestingSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
# 요청 밖에서 세션을 여는 코드(가짜 진행, SSE)도 테스트 DB를 쓰게 한다
SessionLocal.configure(bind=engine)

# 마이그레이션 0002가 넣는 인프라와 같은 모양 (테스트는 create_all이라 직접 넣는다)
INFRA = [
    ("sbh-workload-demo-vpc-public01", "public", ["ecs-fargate", "lambda", "ec2"], "vpc-test"),
    ("sbh-workload-demo-vpc-private01", "private", ["ecs-fargate", "lambda"], None),
    ("sbh-workload-demo-vpc-ha01", "ha", ["ecs-fargate", "ec2"], None),
]


@pytest.fixture(autouse=True)
def _db(monkeypatch):
    Base.metadata.create_all(engine)
    base = datetime(2026, 10, 2, tzinfo=timezone.utc)
    with TestingSession() as db:
        for i, (infra_id, network, computes, vpc_id) in enumerate(INFRA):
            db.add(
                models.InfraSpace(
                    id=infra_id,
                    name=infra_id,
                    description="테스트 인프라",
                    network=network,
                    computes=computes,
                    status="ready",
                    vpc_id=vpc_id,
                    created_at=base + timedelta(seconds=i),
                )
            )
        db.commit()
    # 가짜 진행과 SSE가 기다리지 않게 한다. 끝나지 않은 배포의 SSE도 0.5초 뒤에 닫힌다.
    monkeypatch.setattr(deploy, "STEP_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(deployments, "POLL_SECONDS", 0.01)
    monkeypatch.setattr(deployments, "STREAM_MAX_SECONDS", 0.5)
    yield
    Base.metadata.drop_all(engine)


FAKE_SHA = "c" * 40


@pytest.fixture(autouse=True)
def gh(monkeypatch):
    """가짜 GitHub. 테스트가 진짜 GitHub을 부르지 않게 한다.
    보낸 요청은 requests에 쌓이고, commit_status·dispatch_status로 응답 코드를 바꾼다."""
    state = {"requests": [], "commit_status": 200, "dispatch_status": 204}

    def handler(req: httpx.Request) -> httpx.Response:
        state["requests"].append(req)
        if "/commits/" in req.url.path:
            return httpx.Response(state["commit_status"], text=FAKE_SHA)
        if state["dispatch_status"] == 204:
            return httpx.Response(204)
        return httpx.Response(state["dispatch_status"], json={"message": "fake"})

    monkeypatch.setattr(github, "TRANSPORT", httpx.MockTransport(handler))
    return state


@pytest.fixture
def client():
    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
