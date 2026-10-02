import hashlib
import hmac

import pytest

from app import catalog
from app.config import get_settings
from tests.test_app_spaces import PUBLIC, create_space

SECRET = "test-callback-secret"
DEFAULTS = {"container_port": 80, "cpu": 256, "memory": 512, "health_check_path": "/"}
# AI가 포트를 확인하지 못한 구성안. 포트는 배포 워크플로가 Dockerfile EXPOSE로 정한다
PLAN_DEFAULTS = {"cpu": 256, "memory": 512, "health_check_path": "/"}


@pytest.fixture
def secret(monkeypatch):
    monkeypatch.setattr(get_settings(), "deploy_callback_secret", SECRET)


def make_plan(client, space, compute="ecs-fargate"):
    return client.post(f"/api/app-spaces/{space['id']}/plans", json={"compute": compute})


def signed_get(client, plan_id, secret=SECRET):
    sig = "sha256=" + hmac.new(secret.encode(), plan_id.encode(), hashlib.sha256).hexdigest()
    return client.get(f"/api/plans/{plan_id}", headers={"X-Hub-Signature-256": sig})


# 템플릿 목록


def test_all_three_computes_are_deployable(client):
    infra = client.get(f"/api/infra-spaces/{PUBLIC}").json()
    assert infra["computes"] == ["ecs-fargate", "lambda", "ec2"]  # AI는 셋을 비교한다
    assert infra["deployable_computes"] == ["ecs-fargate", "lambda", "ec2"]  # 10/2 Lambda·EC2 템플릿 추가


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


@pytest.fixture
def lambda_not_ready(monkeypatch):
    """템플릿이 아직 없는 컴퓨팅. 지금은 셋 다 준비돼서 Lambda를 잠시 준비 중으로 돌린다."""
    monkeypatch.setitem(catalog.TEMPLATES, "lambda", catalog.Template(compute="lambda", name=None, ready=False))


def test_not_ready_template_cannot_be_filled(lambda_not_ready):
    with pytest.raises(ValueError):
        catalog.fill_values("lambda")


# Lambda·EC2 (10/2 박소정 님 템플릿, 배포 레포 templates/<이름>/variables.tf)


def test_lambda_and_ec2_defaults():
    assert catalog.fill_values("lambda") == {
        "container_port": 8080, "memory": 512, "timeout": 30, "health_check_path": "/",
    }
    assert catalog.fill_values("ec2") == {"container_port": 80, "instance_type": "t3.micro", "health_check_path": "/"}
    assert (catalog.TEMPLATES["lambda"].name, catalog.TEMPLATES["ec2"].name) == ("lambda/basic", "ec2/basic")


@pytest.mark.parametrize(
    "compute,raw",
    [
        ("lambda", {"container_port": 80}),  # Lambda는 1024 미만 포트를 못 연다
        ("lambda", {"memory": 64}),
        ("lambda", {"memory": 10241}),
        ("lambda", {"timeout": 0}),
        ("lambda", {"timeout": 901}),
        ("lambda", {"health_check_path": "health"}),
        ("ec2", {"instance_type": "t3.large"}),
        ("ec2", {"container_port": 70000}),
        ("ec2", {"cpu": 256, "instance_type": True}),
    ],
)
def test_lambda_and_ec2_out_of_range(compute, raw):
    with pytest.raises(ValueError):
        catalog.fill_values(compute, raw)


@pytest.mark.parametrize(
    "compute,raw",
    [
        ("lambda", {"container_port": 3000, "memory": 1024, "timeout": 900, "health_check_path": "/health"}),
        ("ec2", {"container_port": 3000, "instance_type": "t3.medium", "health_check_path": "/health"}),
    ],
)
def test_lambda_and_ec2_values_kept(compute, raw):
    assert catalog.fill_values(compute, raw) == raw


@pytest.mark.parametrize("compute,template", [("lambda", "lambda/basic"), ("ec2", "ec2/basic")])
def test_plan_and_deploy_lambda_and_ec2(client, compute, template):
    space = create_space(client).json()
    plan = make_plan(client, space, compute).json()["plans"][0]
    assert plan["template"] == template
    assert "container_port" not in plan["values"]  # AI가 확인 못 한 포트는 비워서 워크플로가 EXPOSE를 쓴다
    r = client.post(f"/api/app-spaces/{space['id']}/deployments", json={"compute": compute, "plan_id": plan["id"]})
    assert r.status_code == 201


# 구성안 API (프론트)


def test_create_and_get_plan(client):
    space = create_space(client).json()
    r = make_plan(client, space)
    assert r.status_code == 200
    body = r.json()
    assert (body["status"], body["compute"], len(body["plans"])) == ("done", "ecs-fargate", 1)
    plan = body["plans"][0]
    assert plan["id"].startswith("plan-")
    assert (plan["template"], plan["values"], plan["name"]) == ("ecs-fargate/basic", PLAN_DEFAULTS, "기본형")
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


def test_plan_for_not_ready_compute(client, lambda_not_ready):
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


def test_deploy_not_ready_compute(client, lambda_not_ready):
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
        "values": PLAN_DEFAULTS,
        "infra": {"id": PUBLIC, "vpc_id": "vpc-test", "public_subnet_ids": ["subnet-a", "subnet-c"], "private_subnet_ids": []},
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


# AI가 분석 때 채운 템플릿 값 (ADR-012)

AI_VALUES = {"container_port": 3000, "cpu": 256, "memory": 512, "health_check_path": "/health"}


@pytest.mark.parametrize(
    "template_values,expected",
    [
        ({"ecs-fargate": AI_VALUES}, AI_VALUES),
        ({"ecs-fargate": {**AI_VALUES, "memory": 4096}}, PLAN_DEFAULTS),  # 검사 실패면 기본값 (분석 때 이미 걸러져 보통은 오지 않음)
        ({}, PLAN_DEFAULTS),  # 템플릿 값이 없는 옛 분석
        ({"ecs-fargate": {"cpu": 512}}, {"cpu": 512, "memory": 1024, "health_check_path": "/"}),  # 포트만 모름
        ({"ecs-fargate": {"container_port": 80}}, DEFAULTS),  # AI가 확인한 80은 그대로 넘긴다
    ],
)
def test_plan_uses_ai_template_values(client, monkeypatch, template_values, expected):
    from app import analysis as analysis_module
    from tests.test_analysis import done_result

    monkeypatch.setattr(get_settings(), "ai_model_id", "test-model")
    result = done_result().model_copy(update={"template_values": template_values})
    monkeypatch.setattr(analysis_module, "run_analysis", lambda *a: (result, "a" * 40))
    space = create_space(client).json()
    client.post(f"/api/app-spaces/{space['id']}/analysis")
    assert make_plan(client, space).json()["plans"][0]["values"] == expected
