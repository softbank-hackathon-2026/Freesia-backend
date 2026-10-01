import hashlib
import hmac

import pytest

from app import catalog
from app.config import get_settings
from tests.test_app_spaces import PUBLIC, create_space

SECRET = "test-callback-secret"
DEFAULTS = {"container_port": 80, "cpu": 256, "memory": 512, "health_check_path": "/"}


@pytest.fixture
def secret(monkeypatch):
    monkeypatch.setattr(get_settings(), "deploy_callback_secret", SECRET)


def make_plan(client, space, compute="ecs-fargate"):
    return client.post(f"/api/app-spaces/{space['id']}/plans", json={"compute": compute})


def signed_get(client, plan_id, secret=SECRET):
    sig = "sha256=" + hmac.new(secret.encode(), plan_id.encode(), hashlib.sha256).hexdigest()
    return client.get(f"/api/plans/{plan_id}", headers={"X-Hub-Signature-256": sig})


# 템플릿 목록


def test_only_fargate_is_deployable_for_now(client):
    infra = client.get(f"/api/infra-spaces/{PUBLIC}").json()
    assert infra["computes"] == ["ecs-fargate", "lambda", "ec2"]  # AI는 셋을 비교한다
    assert infra["deployable_computes"] == ["ecs-fargate"]  # 배포는 템플릿이 있는 것만


def test_defaults_match_template():
    assert catalog.fill_values("ecs-fargate") == DEFAULTS


@pytest.mark.parametrize(
    "raw,expected",
    [
        ({"cpu": 512}, {"cpu": 512, "memory": 1024}),  # memory가 비면 그 cpu에서 가장 작은 값
        ({"cpu": 1024, "memory": 8192}, {"cpu": 1024, "memory": 8192}),
        ({"container_port": 3000, "health_check_path": "/health"}, {"container_port": 3000, "health_check_path": "/health"}),
    ],
)
def test_values_are_filled(raw, expected):
    values = catalog.fill_values("ecs-fargate", raw)
    assert {k: values[k] for k in expected} == expected


@pytest.mark.parametrize(
    "raw",
    [
        {"container_port": 0},
        {"container_port": 70000},
        {"container_port": "80"},
        {"cpu": 2048},
        {"cpu": 256, "memory": 4096},
        {"health_check_path": "health"},
        {"health_check_path": "/a b"},
    ],
)
def test_out_of_range_values_are_rejected(raw):
    with pytest.raises(ValueError):
        catalog.fill_values("ecs-fargate", raw)


def test_not_ready_template_cannot_be_filled():
    with pytest.raises(ValueError):
        catalog.fill_values("lambda")


# 구성안 API (프론트)


def test_create_and_get_plan(client):
    space = create_space(client).json()
    r = make_plan(client, space)
    assert r.status_code == 200
    body = r.json()
    assert (body["status"], body["compute"], len(body["plans"])) == ("done", "ecs-fargate", 1)
    plan = body["plans"][0]
    assert plan["id"].startswith("plan-")
    assert (plan["template"], plan["values"], plan["name"]) == ("ecs-fargate/basic", DEFAULTS, "기본형")
    assert plan["pros"] and plan["cons"]
    got = client.get(f"/api/app-spaces/{space['id']}/plans", params={"compute": "ecs-fargate"}).json()
    assert got["plans"][0]["id"] == plan["id"]


def test_plan_links_latest_done_analysis(client):
    from app import models
    from tests.conftest import TestingSession

    space = create_space(client).json()
    client.post(f"/api/app-spaces/{space['id']}/analysis")
    plan_id = make_plan(client, space).json()["plans"][0]["id"]
    with TestingSession() as db:
        assert db.get(models.Plan, plan_id).analysis_id is not None


def test_plan_for_not_ready_compute(client):
    space = create_space(client).json()
    r = make_plan(client, space, "lambda")
    assert (r.status_code, r.json()["error"]) == (400, "compute_not_ready")


def test_plan_for_unsupported_compute(client):
    space = create_space(client, infra_id="sbh-workload-demo-vpc-private01").json()
    r = make_plan(client, space, "ec2")
    assert (r.status_code, r.json()["error"]) == (400, "compute_not_supported")


def test_get_plan_before_creating(client):
    space = create_space(client).json()
    r = client.get(f"/api/app-spaces/{space['id']}/plans", params={"compute": "ecs-fargate"})
    assert (r.status_code, r.json()["error"]) == (404, "plan_not_found")


# 배포에 구성안 쓰기


def test_deploy_with_plan(client):
    space = create_space(client).json()
    plan_id = make_plan(client, space).json()["plans"][0]["id"]
    r = client.post(f"/api/app-spaces/{space['id']}/deployments", json={"compute": "ecs-fargate", "plan_id": plan_id})
    assert r.status_code == 201


def test_deploy_with_unknown_plan(client):
    space = create_space(client).json()
    r = client.post(f"/api/app-spaces/{space['id']}/deployments", json={"compute": "ecs-fargate", "plan_id": "plan-x"})
    assert (r.status_code, r.json()["error"]) == (400, "plan_not_found")


def test_deploy_with_other_apps_plan(client):
    first = create_space(client).json()
    second = create_space(client, register_repo=False).json()
    plan_id = make_plan(client, first).json()["plans"][0]["id"]
    r = client.post(f"/api/app-spaces/{second['id']}/deployments", json={"compute": "ecs-fargate", "plan_id": plan_id})
    assert (r.status_code, r.json()["error"]) == (400, "plan_mismatch")


def test_deploy_not_ready_compute(client):
    space = create_space(client).json()
    r = client.post(f"/api/app-spaces/{space['id']}/deployments", json={"compute": "lambda"})
    assert (r.status_code, r.json()["error"]) == (400, "compute_not_ready")


# 워크플로가 값을 받아 가는 API


def test_workflow_gets_template_values_and_infra(client, secret):
    space = create_space(client).json()
    plan_id = make_plan(client, space).json()["plans"][0]["id"]
    r = signed_get(client, plan_id)
    assert r.status_code == 200
    assert r.json() == {
        "id": plan_id,
        "template": "ecs-fargate/basic",
        "values": DEFAULTS,
        "infra": {"id": PUBLIC, "vpc_id": "vpc-test", "public_subnet_ids": [], "private_subnet_ids": []},
    }


def test_workflow_bad_signature(client, secret):
    space = create_space(client).json()
    plan_id = make_plan(client, space).json()["plans"][0]["id"]
    assert signed_get(client, plan_id, secret="wrong").status_code == 401
    assert client.get(f"/api/plans/{plan_id}").status_code == 401


def test_workflow_signature_checked_before_existence(client, secret):
    assert signed_get(client, "plan-x", secret="wrong").status_code == 401
    r = signed_get(client, "plan-x")
    assert (r.status_code, r.json()["error"]) == (404, "plan_not_found")


def test_workflow_rejected_without_server_secret(client):
    space = create_space(client).json()
    plan_id = make_plan(client, space).json()["plans"][0]["id"]
    assert signed_get(client, plan_id, secret="anything").status_code == 401
