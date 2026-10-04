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
            id=ONPREM, provider="onprem", name="사내 서버", description="", network="public", computes=["onprem"],
            status="ready", vm_host="vpn.howon.me", created_at=AT,
        ))
        db.commit()


def new_app(client, values=NODE):
    app_id = client.post("/api/app-spaces", json={
        "name": "shop-api", "repo_url": "https://github.com/softbank-hackathon-2026/shop-api", "infra_id": ONPREM,
    }).json()["id"]
    if values is not None:  # AI 분석이 onprem 값을 채운 것처럼
        with TestingSession() as db:
            db.add(models.Analysis(
                id=f"an-{app_id[4:]}", app_space_id=app_id, infra_id=ONPREM, commit_sha="c" * 40, status="done",
                result={"template_values": {"onprem": values}}, model_id="test", created_at=AT, finished_at=AT,
            ))
            db.commit()
    return app_id


def deploy(client, app_id):
    return client.post(f"/api/app-spaces/{app_id}/deployments", json={"compute": "onprem"})


def workflow_plan(client, plan_id):
    sig = "sha256=" + hmac.new(SECRET.encode(), plan_id.encode(), hashlib.sha256).hexdigest()
    return client.get(f"/api/plans/{plan_id}", headers={"X-Hub-Signature-256": sig}).json()


def test_onprem_infra_offers_vm(client, onprem):
    infra = client.get(f"/api/infra-spaces/{ONPREM}").json()
    assert (infra["provider"], infra["deployable_computes"]) == ("onprem", ["onprem"])


def test_vm_deploy_runs_deploy_vm_workflow(client, real_mode, gh, onprem):
    app_id = new_app(client)
    dep = deploy(client, app_id).json()
    assert dep["status"] == "pending"
    [req] = dispatches(gh)
    assert req.url.path.endswith("/actions/workflows/deploy-vm.yml/dispatches")
    inputs = json.loads(req.content)["inputs"]
    assert inputs["compute"] == "onprem"  # 워크플로는 onprem·onprem-container만 받는다 (workload-deploy#5)
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
    r = client.post(f"/api/app-spaces/{app_id}/plans", json={"compute": "onprem"})
    assert (r.status_code, r.json()["error"]) == (400, "vm_values_missing")
    # 구성안 없이 배포하면 실행하지 않고 실패로 남긴다
    dep = deploy(client, app_id).json()
    assert dep["status"] == "failed" and "runtime" in dep["reason"]
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
        db.add(models.Deployment(id="dep-vm1", app_space_id=app_id, compute="onprem", status="success", step="done",
                                 run_id=1, created_at=AT))
        db.get(models.AppSpace, app_id).latest_deployment_id = "dep-vm1"
        db.commit()
    assert client.get(f"/api/app-spaces/{app_id}/metrics").json()["status"] == "unsupported"


# values.yaml 검사는 app/catalog.py (PR #39, 강효승 님). 칸마다 넣어 보는 AI 검사 때문에 runtime·start_command가
# 비어도 통과시키고, 구성안을 만들 때 vm_missing으로 막는다


def test_vm_values_defaults_and_missing():
    v = catalog.fill_values("onprem", {"runtime": "python", "start_command": ".venv/bin/python app.py", "application_id": "evil"})
    assert (v["app_port"], v["health_check_path"], "application_id" in v) == (8080, "/", False)
    assert catalog.vm_missing(v) == []
    assert catalog.vm_missing(catalog.fill_values("onprem", {})) == ["runtime", "start_command"]
    war = catalog.fill_values("onprem", {"runtime": "java", "java_server": "tomcat", "runtime_version": "21"})
    assert catalog.vm_missing(war) == []  # Tomcat WAR는 실행 명령이 없어도 된다


@pytest.mark.parametrize("values", [
    {"runtime": "ruby", "start_command": "x"},
    {"runtime": "python", "start_command": "x", "app_port": 80},  # 1024 미만
    {"runtime": "python", "start_command": "a\nb"},
    {"runtime": "java", "runtime_version": "11", "start_command": "x"},
    {"runtime": "python", "start_command": "x", "health_check_path": "health"},
])
def test_vm_values_rejected(values):
    with pytest.raises(ValueError):
        catalog.fill_values("onprem", values)


# onprem-container: Dockerfile이 있는 앱을 VM에서 Docker로 (같은 deploy-vm.yml, compute로 플레이북을 고름)


def container_app(client, onprem_infra, values):
    app_id = client.post("/api/app-spaces", json={
        "name": "sample-shop", "repo_url": "https://github.com/softbank-hackathon-2026/sample-shop", "infra_id": ONPREM,
    }).json()["id"]
    with TestingSession() as db:
        db.get(models.InfraSpace, ONPREM).computes = ["onprem", "onprem-container"]
        db.add(models.Analysis(
            id=f"an-{app_id[4:]}", app_space_id=app_id, infra_id=ONPREM, commit_sha="c" * 40, status="done",
            result={"template_values": {"onprem-container": values}}, model_id="test", created_at=AT, finished_at=AT,
        ))
        db.commit()
    return app_id


def test_onprem_container_is_offered(client, onprem):
    with TestingSession() as db:
        db.get(models.InfraSpace, ONPREM).computes = ["onprem", "onprem-container"]
        db.commit()
    infra = client.get(f"/api/infra-spaces/{ONPREM}").json()
    assert infra["deployable_computes"] == ["onprem", "onprem-container"]


def test_onprem_container_deploy_runs_deploy_vm_with_compute(client, real_mode, gh, onprem):
    app_id = container_app(client, onprem, {"container_port": 3000, "health_check_path": "/health"})
    r = client.post(f"/api/app-spaces/{app_id}/deployments", json={"compute": "onprem-container"})
    assert r.json()["status"] == "pending"
    [req] = dispatches(gh)
    assert req.url.path.endswith("/actions/workflows/deploy-vm.yml/dispatches")  # AWS deploy.yml이 아니다
    inputs = json.loads(req.content)["inputs"]
    assert inputs["compute"] == "onprem-container"
    plan = workflow_plan(client, inputs["plan_id"])
    assert plan["infra"] == {"id": ONPREM, "vm_host": "vpn.howon.me"}
    # 컨테이너는 runtime·start_command 없이 된다 (Dockerfile이 정함)
    assert plan["values"] == {"container_port": 3000, "health_check_path": "/health"}


def test_onprem_container_plan_without_ai_values_uses_defaults(client, real_mode, gh, onprem):
    app_id = container_app(client, onprem, {})
    r = client.post(f"/api/app-spaces/{app_id}/plans", json={"compute": "onprem-container"})
    assert r.status_code == 200
    # 포트를 모르면 비워 둔다. 배포 레포가 8080을 쓴다 (ansible/README.md)
    assert r.json()["plans"][0]["values"] == {"health_check_path": "/"}
