import json

import pytest

from app import mock_data
from app.routers import deployments


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    mock_data.APP_SPACES.clear()
    mock_data.DEPLOYMENTS.clear()
    mock_data.PROGRESS.clear()
    monkeypatch.setattr(deployments, "STEP_INTERVAL_SECONDS", 0)


def _create_space(client, infra_id="sbh-workload-demo-vpc-public01"):
    return client.post(
        "/api/app-spaces",
        json={"name": "todo", "repo_url": "https://github.com/org/todo", "infra_id": infra_id},
    )


def test_infra_list_and_detail(client):
    items = client.get("/api/infra-spaces").json()
    assert len(items) == 3
    assert client.get(f"/api/infra-spaces/{items[0]['id']}").status_code == 200
    assert client.get("/api/infra-spaces/nope").json()["error"] == "infra_not_found"


def test_app_space_flow(client):
    r = _create_space(client)
    assert r.status_code == 201
    space = r.json()
    assert space["id"].startswith("app-")
    assert client.get("/api/app-spaces").json()[0]["id"] == space["id"]
    analysis = client.get(f"/api/app-spaces/{space['id']}/analysis").json()
    assert analysis["status"] == "done"
    assert {c["state"] for c in analysis["candidates"]} == {"selected", "alternative", "unsuitable"}


def test_create_space_with_unknown_infra(client):
    assert _create_space(client, infra_id="nope").status_code == 404


def test_deployment_rejects_unsupported_compute(client):
    space = _create_space(client, infra_id="sbh-workload-demo-vpc-private01").json()
    r = client.post(f"/api/app-spaces/{space['id']}/deployments", json={"compute": "ec2"})
    assert r.status_code == 400


def test_deployment_events_stream(client):
    space = _create_space(client).json()
    dep = client.post(f"/api/app-spaces/{space['id']}/deployments", json={"compute": "ecs-fargate"}).json()
    with client.stream("GET", f"/api/deployments/{dep['id']}/events") as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        events = [json.loads(line[6:]) for line in r.iter_lines() if line.startswith("data: ")]
    assert [e["status"] for e in events][0] == "pending"
    assert events[-1]["status"] == "success"
    assert events[-1]["url"]
    final = client.get(f"/api/deployments/{dep['id']}").json()
    assert final["status"] == "success"


def _events(client, dep_id):
    with client.stream("GET", f"/api/deployments/{dep_id}/events") as r:
        return [line for line in r.iter_lines()]


def test_reconnect_resumes_instead_of_restarting(client):
    space = _create_space(client).json()
    dep = client.post(f"/api/app-spaces/{space['id']}/deployments", json={"compute": "ecs-fargate"}).json()
    _events(client, dep["id"])  # 끝까지 받음
    again = [json.loads(x[6:]) for x in _events(client, dep["id"]) if x.startswith("data: ")]
    assert [e["status"] for e in again] == ["success"]  # 처음부터 다시 보내지 않음


def test_heartbeat_while_waiting(client, monkeypatch):
    monkeypatch.setattr(deployments, "STEP_INTERVAL_SECONDS", 0.05)
    monkeypatch.setattr(deployments, "HEARTBEAT_SECONDS", 0.02)
    space = _create_space(client).json()
    dep = client.post(f"/api/app-spaces/{space['id']}/deployments", json={"compute": "ecs-fargate"}).json()
    lines = _events(client, dep["id"])
    assert ": ping" in lines


def test_old_paths_are_not_served(client):
    assert client.get("/health").status_code == 404
    assert client.get("/api/docs").status_code == 200
