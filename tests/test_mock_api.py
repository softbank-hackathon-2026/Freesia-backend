import json

import pytest

from app import mock_data
from app.routers import deployments


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    mock_data.APP_SPACES.clear()
    mock_data.DEPLOYMENTS.clear()
    monkeypatch.setattr(deployments, "STEP_INTERVAL_SECONDS", 0)


def _create_space(client, infra_id="sbh-workload-demo-vpc-public01"):
    return client.post(
        "/app-spaces",
        json={"name": "todo", "repo_url": "https://github.com/org/todo", "infra_id": infra_id},
    )


def test_infra_list_and_detail(client):
    items = client.get("/infra-spaces").json()
    assert len(items) == 3
    assert client.get(f"/infra-spaces/{items[0]['id']}").status_code == 200
    assert client.get("/infra-spaces/nope").json()["error"] == "infra_not_found"


def test_app_space_flow(client):
    r = _create_space(client)
    assert r.status_code == 201
    space = r.json()
    assert space["id"].startswith("app-")
    assert client.get("/app-spaces").json()[0]["id"] == space["id"]
    analysis = client.get(f"/app-spaces/{space['id']}/analysis").json()
    assert analysis["status"] == "done"
    assert {c["state"] for c in analysis["candidates"]} == {"selected", "alternative", "unsuitable"}


def test_create_space_with_unknown_infra(client):
    assert _create_space(client, infra_id="nope").status_code == 404


def test_deployment_rejects_unsupported_compute(client):
    space = _create_space(client, infra_id="sbh-workload-demo-vpc-private01").json()
    r = client.post(f"/app-spaces/{space['id']}/deployments", json={"compute": "ec2"})
    assert r.status_code == 400


def test_deployment_events_stream(client):
    space = _create_space(client).json()
    dep = client.post(f"/app-spaces/{space['id']}/deployments", json={"compute": "ecs-fargate"}).json()
    with client.stream("GET", f"/deployments/{dep['id']}/events") as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        events = [json.loads(line[6:]) for line in r.iter_lines() if line.startswith("data: ")]
    assert [e["status"] for e in events][0] == "pending"
    assert events[-1]["status"] == "success"
    assert events[-1]["url"]
    final = client.get(f"/deployments/{dep['id']}").json()
    assert final["status"] == "success"
