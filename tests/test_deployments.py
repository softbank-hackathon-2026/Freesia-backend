import hashlib
import hmac
import json

import pytest

from app.config import get_settings
from app.routers import deployments
from tests.test_app_spaces import create_space

SECRET = "test-callback-secret"


@pytest.fixture
def real_mode(monkeypatch):
    """배포 레포가 연결된 상태: 가짜 진행을 끄고 콜백 서명 키를 넣는다."""
    monkeypatch.setattr(get_settings(), "deploy_simulate", False)
    monkeypatch.setattr(get_settings(), "deploy_callback_secret", SECRET)


def start(client, compute="ecs-fargate", **extra):
    space = create_space(client).json()
    return client.post(f"/api/app-spaces/{space['id']}/deployments", json={"compute": compute, **extra})


def events(client, dep_id, last_event_id=None):
    headers = {"Last-Event-ID": str(last_event_id)} if last_event_id is not None else {}
    with client.stream("GET", f"/api/deployments/{dep_id}/events", headers=headers) as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        return list(r.iter_lines())


def data(lines):
    return [json.loads(x[6:]) for x in lines if x.startswith("data: ")]


def callback(client, dep_id, body, secret=SECRET):
    raw = json.dumps(body).encode()
    sig = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return client.post(
        f"/api/deployments/{dep_id}/callback",
        content=raw,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": sig},
    )


# 가짜 진행 (배포 레포 연결 전)


def test_simulated_deployment_is_saved(client):
    r = start(client)
    assert r.status_code == 201
    dep = r.json()
    assert dep["id"].startswith("dep-")
    assert dep["status"] == "pending"
    space = client.get(f"/api/app-spaces/{dep['app_space_id']}").json()
    assert space["latest_deployment_id"] == dep["id"]
    final = client.get(f"/api/deployments/{dep['id']}").json()
    assert final["status"] == "success"
    assert final["url"]


def test_events_replay_all_steps_from_last_event_id(client):
    dep = start(client).json()
    items = data(events(client, dep["id"], last_event_id=0))
    assert [e["step"] for e in items] == ["queued", "prepare", "build", "deploy", "verify", "done"]
    assert [e["progress"] for e in items] == [0, 10, 30, 60, 90, 100]
    assert items[-1]["status"] == "success" and items[-1]["url"]


def test_reconnect_without_id_sends_only_current_state(client):
    dep = start(client).json()
    assert [e["status"] for e in data(events(client, dep["id"]))] == ["success"]


def test_event_ids_are_sequence_numbers(client):
    dep = start(client).json()
    ids = [x for x in events(client, dep["id"], last_event_id=3) if x.startswith("id: ")]
    assert ids == ["id: 4", "id: 5", "id: 6"]


def test_unsupported_compute(client):
    space = create_space(client, infra_id="sbh-workload-demo-vpc-private01").json()
    r = client.post(f"/api/app-spaces/{space['id']}/deployments", json={"compute": "ec2"})
    assert r.status_code == 400
    assert r.json()["error"] == "compute_not_supported"


def test_unknown_compute_value(client):
    assert start(client, compute="k8s").status_code == 422


def test_unknown_deployment(client):
    assert client.get("/api/deployments/nope").json()["error"] == "deployment_not_found"
    assert client.get("/api/deployments/nope/events").status_code == 404
    assert client.get("/api/deployments/nope/resources").status_code == 404


# 워크플로 콜백 (배포 레포 연결 후)


def test_second_deployment_while_in_progress(client, real_mode):
    dep = start(client).json()
    r = client.post(f"/api/app-spaces/{dep['app_space_id']}/deployments", json={"compute": "ecs-fargate"})
    assert r.status_code == 409
    assert r.json()["error"] == "deployment_in_progress"


def test_callback_flow_to_success(client, real_mode):
    dep = start(client).json()
    assert client.get(f"/api/deployments/{dep['id']}").json()["status"] == "pending"

    assert callback(client, dep["id"], {"status": "pending", "step": "prepare", "message": "준비", "run_id": 42}).status_code == 204
    assert callback(client, dep["id"], {"status": "building", "step": "build", "message": "빌드"}).status_code == 204
    assert callback(client, dep["id"], {"status": "deploying", "step": "deploy", "message": "배포"}).status_code == 204
    assert callback(client, dep["id"], {"status": "deploying", "step": "verify", "message": "확인"}).status_code == 204
    r = callback(client, dep["id"], {"status": "success", "step": "done", "message": "완료", "url": "http://app.example"})
    assert r.status_code == 204

    final = client.get(f"/api/deployments/{dep['id']}").json()
    assert (final["status"], final["url"]) == ("success", "http://app.example")
    items = data(events(client, dep["id"], last_event_id=0))
    assert [e["step"] for e in items] == ["queued", "prepare", "build", "deploy", "verify", "done"]
    assert items[-1]["url"] == "http://app.example"


def test_failure_can_report_an_earlier_job(client, real_mode):
    # 워크플로는 job 단위로 실패를 보고한다. verify 중 실패해도 step은 deploy로 온다.
    dep = start(client).json()
    callback(client, dep["id"], {"status": "deploying", "step": "verify"})
    r = callback(client, dep["id"], {"status": "failed", "step": "deploy", "reason": "앱이 응답하지 않습니다."})
    assert r.status_code == 204
    final = client.get(f"/api/deployments/{dep['id']}").json()
    assert (final["status"], final["reason"]) == ("failed", "앱이 응답하지 않습니다.")


def test_stale_and_finished_callbacks_are_ignored(client, real_mode):
    dep = start(client).json()
    callback(client, dep["id"], {"status": "building", "step": "build"})
    r = callback(client, dep["id"], {"status": "pending", "step": "prepare"})
    assert (r.status_code, r.json()["error"]) == (409, "stale_callback")
    callback(client, dep["id"], {"status": "failed", "step": "build", "reason": "Dockerfile이 없습니다."})
    r = callback(client, dep["id"], {"status": "deploying", "step": "deploy"})
    assert (r.status_code, r.json()["error"]) == (409, "deployment_finished")


@pytest.mark.parametrize("secret", ["wrong-secret", ""])
def test_bad_signature_is_rejected(client, real_mode, secret):
    dep = start(client).json()
    r = callback(client, dep["id"], {"status": "building", "step": "build"}, secret=secret)
    assert (r.status_code, r.json()["error"]) == (401, "invalid_signature")


def test_callbacks_are_rejected_without_server_secret(client, real_mode, monkeypatch):
    dep = start(client).json()
    monkeypatch.setattr(get_settings(), "deploy_callback_secret", "")
    r = callback(client, dep["id"], {"status": "building", "step": "build"}, secret="")
    assert r.status_code == 401


def test_signature_is_checked_before_existence(client, real_mode):
    r = callback(client, "nope", {"status": "building", "step": "build"}, secret="wrong")
    assert r.status_code == 401
    assert callback(client, "nope", {"status": "building", "step": "build"}).status_code == 404


def test_invalid_callback_body(client, real_mode):
    dep = start(client).json()
    r = callback(client, dep["id"], {"status": "building", "step": "image-build"})
    assert (r.status_code, r.json()["error"]) == (422, "validation_error")


def test_long_reason_is_cut_not_rejected(client, real_mode):
    dep = start(client).json()
    assert callback(client, dep["id"], {"status": "failed", "step": "build", "reason": "x" * 5000}).status_code == 204
    assert len(client.get(f"/api/deployments/{dep['id']}").json()["reason"]) == 1000


# 자원별 상태 (트리)


def resource(address, state, action="create", **extra):
    return {"address": address, "type": address.split(".")[0], "action": action, "state": state, **extra}


def send_resources(client, dep_id, *items):
    body = {"status": "deploying", "step": "deploy", "message": "자원", "resources": list(items)}
    assert callback(client, dep_id, body).status_code == 204


def tree(client, dep_id):
    return [(r["address"], r["action"], r["state"]) for r in client.get(f"/api/deployments/{dep_id}/resources").json()]


def test_resources_are_overwritten_by_address(client, real_mode):
    dep = start(client).json()
    send_resources(
        client,
        dep["id"],
        resource("aws_lb.app", "pending"),
        resource("aws_ecs_cluster.app", "done", action="no-op"),
        resource("aws_ecs_service.app", "pending"),
    )
    send_resources(client, dep["id"], resource("aws_lb.app", "in_progress"))
    send_resources(client, dep["id"], resource("aws_lb.app", "done"))
    send_resources(client, dep["id"], resource("aws_ecs_service.app", "failed", reason="InvalidParameterException"))
    assert tree(client, dep["id"]) == [
        ("aws_lb.app", "create", "done"),
        ("aws_ecs_cluster.app", "no-op", "done"),
        ("aws_ecs_service.app", "create", "failed"),
    ]
    failed = client.get(f"/api/deployments/{dep['id']}/resources").json()[2]
    assert failed["reason"] == "InvalidParameterException"
    assert failed["type"] == "aws_ecs_service"


def test_late_resource_state_does_not_go_back(client, real_mode):
    dep = start(client).json()
    send_resources(client, dep["id"], resource("aws_lb.app", "done"))
    send_resources(client, dep["id"], resource("aws_lb.app", "in_progress"))
    assert tree(client, dep["id"]) == [("aws_lb.app", "create", "done")]


def test_replace_runs_twice(client, real_mode):
    # replace는 지우기와 만들기가 따로 온다. action이 바뀌면 다시 진행 중으로 받는다.
    dep = start(client).json()
    send_resources(client, dep["id"], resource("aws_ecs_task_definition.app", "pending", action="replace"))
    send_resources(client, dep["id"], resource("aws_ecs_task_definition.app", "done", action="delete"))
    send_resources(client, dep["id"], resource("aws_ecs_task_definition.app", "in_progress", action="create"))
    assert tree(client, dep["id"]) == [("aws_ecs_task_definition.app", "create", "in_progress")]


def test_data_sources_are_not_in_tree(client, real_mode):
    dep = start(client).json()
    send_resources(client, dep["id"], resource("data.aws_region.current", "done", action="read"))
    assert tree(client, dep["id"]) == []


def test_resource_callbacks_show_up_as_progress(client, real_mode):
    dep = start(client).json()
    send_resources(client, dep["id"], resource("aws_lb.app", "done"))
    last = data(events(client, dep["id"], last_event_id=0))[-1]
    assert (last["step"], last["message"], last["progress"]) == ("deploy", "자원", 60)


# SSE 연결 유지


def test_heartbeat_while_waiting(client, real_mode, monkeypatch):
    monkeypatch.setattr(deployments, "HEARTBEAT_SECONDS", 0.02)
    monkeypatch.setattr(deployments, "STREAM_MAX_SECONDS", 0.2)
    dep = start(client).json()
    lines = events(client, dep["id"])
    assert ": ping" in lines
    assert data(lines)[0]["step"] == "queued"
