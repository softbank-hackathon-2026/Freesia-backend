"""온프레미스 VM 배포 (compute=vm): 배포 레포 deploy-vm.yml·destroy-vm.yml과 구성안 vm_host (10/3 박소정 님)."""
import hashlib
import hmac
import json
from datetime import datetime, timezone

import pytest

from app import catalog, models
from tests.conftest import TestingSession
from tests.test_dispatch import SECRET, dispatches, real_mode  # noqa: F401 (real_mode는 fixture)

ONPREM = "onprem-vm01"
NODE = {"runtime": "node", "app_port": 8080, "health_check_path": "/api/health", "start_command": "node server.js"}
AT = datetime(2026, 10, 3, tzinfo=timezone.utc)


@pytest.fixture
def onprem():
    """Proxmox 출처가 붙기 전이라 온프레미스 인프라를 직접 넣는다."""
    with TestingSession() as db:
        db.add(models.InfraSpace(
            id=ONPREM, provider="onprem", name="사내 서버", description="", network="public", computes=["vm"],
            status="ready", vm_host="vpn.howon.me", created_at=AT,
        ))
        db.commit()


def new_app(client, values=NODE):
    app_id = client.post("/api/app-spaces", json={
        "name": "shop-api", "repo_url": "https://github.com/softbank-hackathon-2026/shop-api", "infra_id": ONPREM,
    }).json()["id"]
    if values is not None:  # AI 분석이 vm 값을 채운 것처럼
        with TestingSession() as db:
            db.add(models.Analysis(
                id=f"an-{app_id[4:]}", app_space_id=app_id, infra_id=ONPREM, commit_sha="c" * 40, status="done",
                result={"template_values": {"vm": values}}, model_id="test", created_at=AT, finished_at=AT,
            ))
            db.commit()
    return app_id


def deploy(client, app_id):
    return client.post(f"/api/app-spaces/{app_id}/deployments", json={"compute": "vm"})


def workflow_plan(client, plan_id):
    sig = "sha256=" + hmac.new(SECRET.encode(), plan_id.encode(), hashlib.sha256).hexdigest()
    return client.get(f"/api/plans/{plan_id}", headers={"X-Hub-Signature-256": sig}).json()


def test_onprem_infra_offers_vm(client, onprem):
    infra = client.get(f"/api/infra-spaces/{ONPREM}").json()
    assert (infra["provider"], infra["deployable_computes"]) == ("onprem", ["vm"])


def test_vm_deploy_runs_deploy_vm_workflow(client, real_mode, gh, onprem):
    app_id = new_app(client)
    dep = deploy(client, app_id).json()
    assert dep["status"] == "pending"
    [req] = dispatches(gh)
    assert req.url.path.endswith("/actions/workflows/deploy-vm.yml/dispatches")
    inputs = json.loads(req.content)["inputs"]
    assert "compute" not in inputs  # deploy.yml 입력에서 compute만 뺀다
    assert (inputs["infra_id"], inputs["application_id"]) == (ONPREM, app_id)
    # 워크플로가 받아 가는 구성안: values.yaml + VM 주소만
    plan = workflow_plan(client, inputs["plan_id"])
    assert plan["infra"] == {"id": ONPREM, "vm_host": "vpn.howon.me"}
    assert plan["values"]["start_command"] == "node server.js"
    assert plan["values"]["app_port"] == 8080
    # Terraform 템플릿이 없으니 트리를 미리 채우려고 GitHub을 부르지 않는다
    assert not [r for r in gh["requests"] if "/contents/" in r.url.path]


def test_vm_without_ai_values_cannot_plan(client, real_mode, gh, onprem):
    app_id = new_app(client, values=None)
    r = client.post(f"/api/app-spaces/{app_id}/plans", json={"compute": "vm"})
    assert (r.status_code, r.json()["error"]) == (400, "plan_values_missing")
    # 구성안 없이 배포하면 실행하지 않고 실패로 남긴다
    dep = deploy(client, app_id).json()
    assert dep["status"] == "failed" and "분석" in dep["reason"]
    assert dispatches(gh) == []


def test_vm_teardown_runs_destroy_vm_with_host(client, real_mode, gh, onprem):
    app_id = new_app(client)
    dep_id = deploy(client, app_id).json()["id"]
    with TestingSession() as db:  # 실제로 배포된 것처럼
        dep = db.get(models.Deployment, dep_id)
        dep.status, dep.step, dep.run_id = "success", "done", 1
        db.commit()
    assert client.post(f"/api/app-spaces/{app_id}/teardown").status_code == 202
    req = dispatches(gh)[-1]
    assert req.url.path.endswith("/actions/workflows/destroy-vm.yml/dispatches")
    inputs = json.loads(req.content)["inputs"]
    assert (inputs["application_id"], inputs["confirm"], inputs["vm_host"]) == (app_id, app_id, "vpn.howon.me")


def test_aws_compute_not_offered_on_onprem(client, onprem):
    app_id = new_app(client)
    r = client.post(f"/api/app-spaces/{app_id}/plans", json={"compute": "ecs-fargate"})
    assert r.json()["error"] == "compute_not_supported"


def test_vm_monitoring_is_unsupported(client, onprem):
    app_id = new_app(client)
    with TestingSession() as db:
        db.add(models.Deployment(id="dep-vm1", app_space_id=app_id, compute="vm", status="success", step="done",
                                 run_id=1, created_at=AT))
        db.get(models.AppSpace, app_id).latest_deployment_id = "dep-vm1"
        db.commit()
    assert client.get(f"/api/app-spaces/{app_id}/metrics").json()["status"] == "unsupported"


# values.yaml 검사 (배포 레포 scripts/vm_plan.py check_values와 같게)


def test_vm_values_defaults_and_python():
    v = catalog.fill_values("vm", {"runtime": "python", "start_command": ".venv/bin/python app.py",
                                   "env": {"APP_ENV": "demo"}, "application_id": "evil"})
    assert (v["app_port"], v["health_check_path"], v["env"], "application_id" in v) == (8080, "/", {"APP_ENV": "demo"}, False)
    war = catalog.fill_values("vm", {"runtime": "java", "java_server": "tomcat", "runtime_version": 21})
    assert (war["runtime_version"], war["start_command"]) == ("21", "")


@pytest.mark.parametrize("values", [
    {},                                                           # runtime 없음
    {"runtime": "ruby", "start_command": "x"},
    {"runtime": "python"},                                        # 실행 명령 없음
    {"runtime": "python", "start_command": "x", "app_port": 80},  # 1024 미만
    {"runtime": "python", "start_command": "a\nb"},
    {"runtime": "python", "start_command": "x", "env": {"bad-name": "v"}},
    {"runtime": "python", "start_command": "x", "env": {"A": 'x" y'}},
    {"runtime": "java", "runtime_version": "11", "start_command": "x"},
    {"runtime": "java", "java_server": "tomcat", "tomcat_version": "9"},
    {"runtime": "python", "start_command": "x", "health_check_path": "health"},
])
def test_vm_values_rejected(values):
    with pytest.raises(ValueError):
        catalog.fill_values("vm", values)
