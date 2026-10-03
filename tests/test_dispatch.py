"""워크플로 실행(배포·내리기). GitHub은 가짜로 바꿔 끼운다."""
import hashlib
import hmac
import json
from datetime import datetime, timezone

import pytest

from app import github, models
from app.config import get_settings
from app.routers import app_spaces
from tests.conftest import FAKE_SHA as SHA, TestingSession
from tests.test_app_spaces import REPO, create_space

SECRET = "test-callback-secret"


@pytest.fixture
def real_mode(monkeypatch):
    monkeypatch.setattr(get_settings(), "deploy_simulate", False)
    monkeypatch.setattr(get_settings(), "github_deploy_token", "test-token")
    monkeypatch.setattr(get_settings(), "deploy_callback_secret", SECRET)


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


def mark_deployed(space_id, status="success"):
    """진짜 워크플로가 돈 배포 흔적(run_id)을 남긴다."""
    with TestingSession() as db:
        row = db.get(models.Deployment, db.get(models.AppSpace, space_id).latest_deployment_id)
        row.status, row.run_id = status, 123
        db.commit()


def deployed_space(client):
    space, _ = start(client)
    mark_deployed(space["id"])
    return space


def teardown(client, space):
    return client.post(f"/api/app-spaces/{space['id']}/teardown")


def teardown_callback(client, space, body, secret=SECRET):
    data = json.dumps(body).encode()
    sig = "sha256=" + hmac.new(secret.encode(), data, hashlib.sha256).hexdigest()
    return client.post(
        f"/api/app-spaces/{space['id']}/teardown/callback",
        content=data,
        headers={"X-Hub-Signature-256": sig, "Content-Type": "application/json"},
    )


def app_of(client, space):
    return client.get(f"/api/app-spaces/{space['id']}").json()


def test_teardown_dispatches_destroy(client, real_mode, gh):
    space = deployed_space(client)
    r = teardown(client, space)
    assert r.status_code == 202
    assert r.json()["status"] == "requested"
    req = dispatches(gh)[-1]
    assert req.url.path.endswith("/workflows/destroy.yml/dispatches")
    assert json.loads(req.content)["inputs"] == {
        "application_id": space["id"],
        "confirm": space["id"],
        "callback_url": f"https://sbh.howon.me/api/app-spaces/{space['id']}/teardown/callback",
    }
    got = app_of(client, space)
    assert got["teardown_status"] == "requested" and got["teardown_requested_at"]


def test_teardown_success_callback(client, real_mode, gh):
    space = deployed_space(client)
    teardown(client, space)
    assert teardown_callback(client, space, {"status": "success"}).status_code == 204
    got = app_of(client, space)
    assert (got["teardown_status"], got["teardown_reason"]) == ("success", None)
    assert got["teardown_finished_at"]
    # 내린 뒤에는 다시 배포하기 전까지 내릴 것이 없다
    r = teardown(client, space)
    assert (r.status_code, r.json()["error"]) == (409, "not_deployed")


def add_resource(space_id, address="aws_lb.app"):
    """마지막 배포에 워크플로가 보낸 것 같은 완료 자원을 하나 남긴다."""
    with TestingSession() as db:
        dep_id = db.get(models.AppSpace, space_id).latest_deployment_id
        db.add(models.DeploymentResource(
            deployment_id=dep_id, address=address, position=0, type="aws_lb", action="create", state="done",
            updated_at=datetime.now(timezone.utc),
        ))
        db.commit()
        return dep_id


def resource_states(client, dep_id):
    return [r["state"] for r in client.get(f"/api/deployments/{dep_id}/resources").json()]


def test_teardown_success_marks_resources_deleted(client, real_mode, gh):
    space = deployed_space(client)
    old = add_resource(space["id"])
    teardown(client, space)
    assert resource_states(client, old) == ["done"]  # 끝나기 전에는 그대로
    teardown_callback(client, space, {"status": "success"})
    assert resource_states(client, old) == ["deleted"]
    # 다시 배포하면 새 배포의 트리는 새로 채워지고, 내린 배포의 기록은 deleted로 남는다
    client.post(f"/api/app-spaces/{space['id']}/deployments", json={"compute": "ecs-fargate"})
    mark_deployed(space["id"])
    new = add_resource(space["id"])
    assert (resource_states(client, new), resource_states(client, old)) == (["done"], ["deleted"])


def test_failed_teardown_keeps_resources(client, real_mode, gh):
    space = deployed_space(client)
    dep_id = add_resource(space["id"])
    teardown(client, space)
    teardown_callback(client, space, {"status": "failed", "reason": "자원을 지우는 중 실패했습니다."})
    assert resource_states(client, dep_id) == ["done"]


def test_teardown_failed_callback_can_retry(client, real_mode, gh):
    space = deployed_space(client)
    teardown(client, space)
    reason = "이 앱의 배포 기록(State)을 찾지 못했습니다."
    assert teardown_callback(client, space, {"status": "failed", "reason": reason}).status_code == 204
    got = app_of(client, space)
    assert (got["teardown_status"], got["teardown_reason"]) == ("failed", reason)
    assert teardown(client, space).status_code == 202
    assert app_of(client, space)["teardown_reason"] is None


def test_redeploy_after_teardown_can_be_torn_down(client, real_mode, gh):
    space = deployed_space(client)
    teardown(client, space)
    teardown_callback(client, space, {"status": "success"})
    client.post(f"/api/app-spaces/{space['id']}/deployments", json={"compute": "ecs-fargate"})
    mark_deployed(space["id"])
    assert teardown(client, space).status_code == 202


def test_no_deploy_or_second_teardown_while_tearing_down(client, real_mode, gh):
    space = deployed_space(client)
    teardown(client, space)
    r = client.post(f"/api/app-spaces/{space['id']}/deployments", json={"compute": "ecs-fargate"})
    assert (r.status_code, r.json()["error"]) == (409, "teardown_in_progress")
    r = teardown(client, space)
    assert (r.status_code, r.json()["error"]) == (409, "teardown_in_progress")


def test_lost_teardown_callback_times_out(client, real_mode, gh):
    space = deployed_space(client)
    teardown(client, space)
    with TestingSession() as db:
        row = db.get(models.AppSpace, space["id"])
        row.teardown_requested_at = row.teardown_requested_at - app_spaces.TEARDOWN_TIMEOUT
        db.commit()
    assert teardown(client, space).status_code == 202


def test_teardown_callback_checks_signature(client, real_mode, gh):
    space = deployed_space(client)
    teardown(client, space)
    assert teardown_callback(client, space, {"status": "success"}, secret="wrong").status_code == 401
    assert app_of(client, space)["teardown_status"] == "requested"


def test_teardown_callback_without_request(client, real_mode):
    space = deployed_space(client)
    r = teardown_callback(client, space, {"status": "success"})
    assert (r.status_code, r.json()["error"]) == (409, "teardown_not_requested")


def test_teardown_without_real_deployment(client, gh):
    space, _ = start(client)  # 가짜 진행만 함
    r = teardown(client, space)
    assert (r.status_code, r.json()["error"]) == (409, "not_deployed")
    assert dispatches(gh) == []


def test_teardown_while_deploying(client, real_mode, gh):
    space, _ = start(client)  # 진짜 모드: 콜백이 오기 전이라 pending
    mark_deployed(space["id"], status="pending")
    r = teardown(client, space)
    assert (r.status_code, r.json()["error"]) == (409, "deployment_in_progress")


def test_teardown_dispatch_failure(client, real_mode, gh):
    space = deployed_space(client)
    gh["dispatch_status"] = 403
    r = teardown(client, space)
    assert (r.status_code, r.json()["error"]) == (502, "teardown_failed")
    assert app_of(client, space)["teardown_status"] is None


def test_teardown_unknown_app(client):
    assert client.post("/api/app-spaces/nope/teardown").json()["error"] == "app_space_not_found"


# GitHub 모듈


def test_latest_commit_does_not_send_token(real_mode, gh):
    assert github.latest_commit(REPO, "feat/x") == SHA
    req = gh["requests"][0]
    assert req.url.path == "/repos/org/todo/commits/feat/x"
    assert "Authorization" not in req.headers


# 앱 삭제 (목록에서 숨기기)


def delete(client, space):
    return client.delete(f"/api/app-spaces/{space['id']}")


def test_delete_hides_app(client):
    space, _ = start(client)  # 가짜 진행만 한 앱
    infra = space["infra_id"]
    before = client.get(f"/api/infra-spaces/{infra}").json()["app_count"]
    assert delete(client, space).status_code == 204
    assert space["id"] not in [a["id"] for a in client.get("/api/app-spaces").json()]
    assert client.get(f"/api/app-spaces/{space['id']}").json()["error"] == "app_space_not_found"
    assert client.get(f"/api/app-spaces/{space['id']}/metrics").status_code == 404
    assert client.get(f"/api/infra-spaces/{infra}").json()["app_count"] == before - 1
    assert delete(client, space).status_code == 404
    with TestingSession() as db:  # 기록은 남는다
        assert db.get(models.AppSpace, space["id"]).deleted_at is not None


def test_cannot_delete_live_app(client, real_mode, gh):
    space = deployed_space(client)
    r = delete(client, space)
    assert (r.status_code, r.json()["error"]) == (409, "app_still_deployed")
    teardown(client, space)
    assert (delete(client, space).status_code, delete(client, space).json()["error"]) == (409, "teardown_in_progress")
    teardown_callback(client, space, {"status": "success"})
    assert delete(client, space).status_code == 204


def test_cannot_delete_after_failed_teardown(client, real_mode, gh):
    space = deployed_space(client)
    teardown(client, space)
    teardown_callback(client, space, {"status": "failed", "reason": "x"})
    assert delete(client, space).json()["error"] == "app_still_deployed"


def test_delete_app_whose_real_deploy_failed_before_terraform(client, real_mode, gh):
    space, _ = start(client)
    mark_deployed(space["id"], status="failed")  # 워크플로는 돌았지만 빌드·변수 준비에서 실패
    add_resource(space["id"])  # 자원 목록만 받고(pending) 만들지는 않음
    with TestingSession() as db:
        db.query(models.DeploymentResource).update({"state": "pending"})
        db.commit()
    assert delete(client, space).status_code == 204


def test_cannot_delete_app_whose_deploy_failed_midway(client, real_mode, gh):
    space, _ = start(client)
    mark_deployed(space["id"], status="failed")  # Terraform이 자원을 만들다가 실패
    add_resource(space["id"])  # done 자원이 남음
    assert delete(client, space).json()["error"] == "app_still_deployed"


def test_cannot_delete_while_deploying(client, real_mode, gh):
    space, _ = start(client)
    r = delete(client, space)
    assert (r.status_code, r.json()["error"]) == (409, "deployment_in_progress")


def test_vm_deploy_without_runtime_fails_instead_of_staying_pending(client, real_mode, gh):
    """구성안 없이 onprem을 배포했는데 분석이 언어·실행 명령을 못 찾았으면 워크플로를 부르지 않고 배포를 실패로 닫는다.
    pending으로 남으면 이 앱의 다음 배포가 deployment_in_progress로 계속 막힌다."""
    space = create_space(client).json()
    with TestingSession() as db:
        dep = models.Deployment(id="dep-vm", app_space_id=space["id"], compute="onprem", status="pending", step="queued",
                                created_at=datetime.now(timezone.utc))
        db.add(dep)
        db.commit()
        app_spaces._start_workflow(db, db.get(models.AppSpace, space["id"]), dep)
        assert db.get(models.Deployment, "dep-vm").status == "failed"
    assert dispatches(gh) == []
