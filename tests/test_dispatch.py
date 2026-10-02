"""워크플로 실행(배포·내리기). GitHub은 가짜로 바꿔 끼운다."""
import json

import pytest

from app import github, models
from app.config import get_settings
from tests.conftest import FAKE_SHA as SHA, TestingSession
from tests.test_app_spaces import REPO, create_space


@pytest.fixture
def real_mode(monkeypatch):
    monkeypatch.setattr(get_settings(), "deploy_simulate", False)
    monkeypatch.setattr(get_settings(), "github_deploy_token", "test-token")


def dispatches(state):
    return [r for r in state["requests"] if r.url.path.endswith("/dispatches")]


def start(client, **extra):
    space = create_space(client).json()
    r = client.post(f"/api/app-spaces/{space['id']}/deployments", json={"compute": "ecs-fargate", **extra})
    return space, r


# 배포


def test_deploy_dispatches_workflow_with_inputs(client, real_mode, gh):
    space, r = start(client)
    assert r.status_code == 201
    dep = r.json()
    [req] = dispatches(gh)
    assert req.url.path == "/repos/softbank-hackathon-2026/workload-deploy/actions/workflows/deploy.yml/dispatches"
    assert req.headers["Authorization"] == "Bearer test-token"
    body = json.loads(req.content)
    assert body["ref"] == "main"
    inputs = body["inputs"]
    assert inputs == {
        "deployment_id": dep["id"],
        "application_id": space["id"],
        "repo": "org/todo",
        "commit_sha": SHA,
        "infra_id": space["infra_id"],
        "compute": "ecs-fargate",
        "plan_id": inputs["plan_id"],
        "callback_url": f"https://sbh.howon.me/api/deployments/{dep['id']}/callback",
    }
    # 구성안 없이 배포하면 기본값 구성안을 만들어 넘긴다
    assert inputs["plan_id"].startswith("plan-")
    with TestingSession() as db:
        saved = db.get(models.Deployment, dep["id"])
        assert (saved.commit_sha, saved.plan_id, saved.status) == (SHA, inputs["plan_id"], "pending")


def test_deploy_uses_chosen_plan(client, real_mode, gh):
    space = create_space(client).json()
    plan_id = client.post(f"/api/app-spaces/{space['id']}/plans", json={"compute": "ecs-fargate"}).json()["plans"][0]["id"]
    client.post(f"/api/app-spaces/{space['id']}/deployments", json={"compute": "ecs-fargate", "plan_id": plan_id})
    assert json.loads(dispatches(gh)[0].content)["inputs"]["plan_id"] == plan_id


def test_deploy_uses_analysed_commit(client, real_mode, gh):
    space = create_space(client).json()
    with TestingSession() as db:
        from app.ids import new_id, now

        db.add(models.Analysis(id=new_id("ana"), app_space_id=space["id"], infra_id=space["infra_id"],
                               commit_sha="d" * 40, status="done", model_id="m", created_at=now()))
        db.commit()
    client.post(f"/api/app-spaces/{space['id']}/deployments", json={"compute": "ecs-fargate"})
    assert json.loads(dispatches(gh)[0].content)["inputs"]["commit_sha"] == "d" * 40
    assert not [r for r in gh["requests"] if "/commits/" in r.url.path]  # GitHub에 커밋을 묻지 않음


def test_dispatch_failure_marks_deployment_failed(client, real_mode, gh):
    gh["dispatch_status"] = 422
    _, r = start(client)
    dep = client.get(f"/api/deployments/{r.json()['id']}").json()
    assert dep["status"] == "failed"
    assert "GitHub 422" in dep["reason"]


def test_missing_branch_marks_deployment_failed(client, real_mode, gh):
    gh["commit_status"] = 404
    _, r = start(client)
    dep = client.get(f"/api/deployments/{r.json()['id']}").json()
    assert dep["status"] == "failed"
    assert "찾을 수 없습니다" in dep["reason"]
    assert dispatches(gh) == []


def test_missing_token_marks_deployment_failed(client, real_mode, gh, monkeypatch):
    monkeypatch.setattr(get_settings(), "github_deploy_token", "")
    _, r = start(client)
    assert client.get(f"/api/deployments/{r.json()['id']}").json()["reason"] == "GitHub 실행 토큰이 설정되지 않았습니다."


def test_simulate_mode_does_not_call_github(client, gh):
    start(client)
    assert gh["requests"] == []


# 내리기


def mark_deployed(space_id):
    """진짜 워크플로가 돈 배포 흔적(run_id)을 남긴다."""
    with TestingSession() as db:
        dep = db.get(models.AppSpace, space_id).latest_deployment_id
        row = db.get(models.Deployment, dep)
        row.run_id = 123
        db.commit()


def test_teardown_dispatches_destroy(client, real_mode, gh):
    space, _ = start(client)
    deps = client.get(f"/api/app-spaces/{space['id']}").json()["latest_deployment_id"]
    with TestingSession() as db:
        row = db.get(models.Deployment, deps)
        row.status, row.run_id = "success", 123
        db.commit()
    r = client.post(f"/api/app-spaces/{space['id']}/teardown")
    assert r.status_code == 202
    assert r.json()["status"] == "requested"
    req = dispatches(gh)[-1]
    assert req.url.path.endswith("/workflows/destroy.yml/dispatches")
    assert json.loads(req.content)["inputs"] == {"application_id": space["id"], "confirm": space["id"]}
    assert client.get(f"/api/app-spaces/{space['id']}").json()["teardown_requested_at"]


def test_teardown_without_real_deployment(client, gh):
    space, _ = start(client)  # 가짜 진행만 함
    r = client.post(f"/api/app-spaces/{space['id']}/teardown")
    assert (r.status_code, r.json()["error"]) == (409, "not_deployed")
    assert dispatches(gh) == []


def test_teardown_while_deploying(client, real_mode, gh):
    space, _ = start(client)  # 진짜 모드: 콜백이 오기 전이라 pending
    mark_deployed(space["id"])
    r = client.post(f"/api/app-spaces/{space['id']}/teardown")
    assert (r.status_code, r.json()["error"]) == (409, "deployment_in_progress")


def test_teardown_dispatch_failure(client, real_mode, gh):
    space, _ = start(client)
    with TestingSession() as db:
        row = db.get(models.Deployment, db.get(models.AppSpace, space["id"]).latest_deployment_id)
        row.status, row.run_id = "success", 123
        db.commit()
    gh["dispatch_status"] = 403
    r = client.post(f"/api/app-spaces/{space['id']}/teardown")
    assert (r.status_code, r.json()["error"]) == (502, "teardown_failed")
    assert client.get(f"/api/app-spaces/{space['id']}").json()["teardown_requested_at"] is None


def test_teardown_unknown_app(client):
    assert client.post("/api/app-spaces/nope/teardown").json()["error"] == "app_space_not_found"


# GitHub 모듈


def test_latest_commit_does_not_send_token(real_mode, gh):
    assert github.latest_commit(REPO, "feat/x") == SHA
    req = gh["requests"][0]
    assert req.url.path == "/repos/org/todo/commits/feat/x"
    assert "Authorization" not in req.headers
