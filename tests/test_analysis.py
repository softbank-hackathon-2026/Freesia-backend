from datetime import timedelta

import pytest

from app import analysis as analysis_module
from app import models
from app.ai.analyze import FAIL_MESSAGE
from app.config import get_settings
from app.ids import now
from app.schemas import Analysis, Candidate, Evidence
from tests.conftest import TestingSession
from tests.test_app_spaces import create_space

SHA = "b" * 40


def done_result() -> Analysis:
    return Analysis(
        status="done",
        requirements=["Python 3.12"],
        evidence=[Evidence(file="Dockerfile", finding="컨테이너로 실행", certain=True)],
        candidates=[
            Candidate(compute="ecs-fargate", state="selected", reason="이유", evidence_files=["Dockerfile"]),
            Candidate(compute="lambda", state="alternative", reason="이유"),
            Candidate(compute="ec2", state="unsuitable", reason="이유"),
        ],
        mascot_message="Fargate를 추천해요",
    )


@pytest.fixture
def ai_on(monkeypatch):
    """AI 모델이 설정된 서버. run_analysis는 호출 인자를 기록하고 정해 둔 결과를 돌려준다."""
    monkeypatch.setattr(get_settings(), "ai_model_id", "test-model")
    calls = []

    def fake(repo_url, branch, computes):
        calls.append((repo_url, branch, computes))
        return done_result(), SHA

    monkeypatch.setattr(analysis_module, "run_analysis", fake)
    return calls


def url(space):
    return f"/api/app-spaces/{space['id']}/analysis"


def stored(space_id):
    with TestingSession() as db:
        return analysis_module.latest(db, space_id)


# 모델이 설정되지 않은 서버 (지금 서버): 샘플을 바로 돌려준다


def test_without_model_returns_sample_immediately(client):
    space = create_space(client).json()
    r = client.post(url(space))
    assert r.status_code == 200
    assert r.json()["status"] == "done"
    assert {c["state"] for c in r.json()["candidates"]} == {"selected", "alternative", "unsuitable"}
    assert client.get(url(space)).json() == r.json()
    assert stored(space["id"]).model_id == "sample"


def test_get_before_analysis_is_404(client):
    space = create_space(client).json()
    r = client.get(url(space))
    assert (r.status_code, r.json()["error"]) == (404, "analysis_not_found")


def test_unknown_app_space(client):
    assert client.post("/api/app-spaces/nope/analysis").json()["error"] == "app_space_not_found"


# 모델이 설정된 서버


def test_post_returns_running_then_result_is_saved(client, ai_on):
    space = create_space(client).json()
    r = client.post(url(space))
    # 응답은 running. TestClient는 응답 뒤 백그라운드 작업까지 끝낸 다음 돌아온다
    assert r.json() == {
        "status": "running", "requirements": [], "evidence": [], "candidates": [], "mascot_message": None, "template_values": {},
    }
    result = client.get(url(space)).json()
    assert result["status"] == "done"
    assert result["candidates"][0]["evidence_files"] == ["Dockerfile"]
    row = stored(space["id"])
    assert (row.commit_sha, row.model_id, row.infra_id) == (SHA, "test-model", space["infra_id"])
    assert row.finished_at is not None


def test_ai_gets_repo_and_infra_computes(client, ai_on):
    space = create_space(client).json()
    client.post(url(space))
    assert ai_on == [(space["repo_url"], "main", ["ecs-fargate", "lambda", "ec2"])]


def test_unexpected_error_is_saved_as_failed(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "ai_model_id", "test-model")

    def boom(*args):
        raise KeyError("output")

    monkeypatch.setattr(analysis_module, "run_analysis", boom)
    space = create_space(client).json()
    client.post(url(space))
    result = client.get(url(space)).json()
    assert (result["status"], result["mascot_message"]) == ("failed", FAIL_MESSAGE)


def _insert_running(space_id, infra_id, minutes_ago=0):
    with TestingSession() as db:
        db.add(
            models.Analysis(
                id=f"ana-{minutes_ago}",
                app_space_id=space_id,
                infra_id=infra_id,
                status="running",
                model_id="test-model",
                created_at=now() - timedelta(minutes=minutes_ago),
            )
        )
        db.commit()


def test_post_while_running_does_not_start_another(client, ai_on):
    space = create_space(client).json()
    _insert_running(space["id"], space["infra_id"])
    assert client.post(url(space)).json()["status"] == "running"
    assert ai_on == []


def test_stuck_running_becomes_failed(client, ai_on):
    space = create_space(client).json()
    _insert_running(space["id"], space["infra_id"], minutes_ago=5)
    result = client.get(url(space)).json()
    assert (result["status"], result["mascot_message"]) == ("failed", FAIL_MESSAGE)
    # 실패한 뒤에는 다시 분석할 수 있다
    assert client.post(url(space)).json()["status"] == "running"
    assert client.get(url(space)).json()["status"] == "done"


def test_late_result_does_not_overwrite_timeout(client, ai_on):
    space = create_space(client).json()
    _insert_running(space["id"], space["infra_id"], minutes_ago=5)
    client.get(url(space))  # 시간 초과로 failed 처리
    analysis_module.run("ana-5")  # 늦게 끝난 분석
    assert client.get(url(space)).json()["status"] == "failed"


def test_reanalysis_after_done_creates_new(client, ai_on):
    space = create_space(client).json()
    client.post(url(space))
    client.post(url(space))
    assert len(ai_on) == 2
