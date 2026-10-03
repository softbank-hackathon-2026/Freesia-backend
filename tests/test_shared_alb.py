"""공용 ALB 인프라(Multi-AZ)의 Fargate 앱 → ecs-fargate/shared-alb 구성안과 워크플로 값."""
import pytest

from app import models
from tests.conftest import TestingSession
from tests.test_plans import SECRET, secret, signed_get  # noqa: F401  (secret 픽스처)

HA = "sbh-workload-demo-vpc-ha01"
PUBLIC = "sbh-workload-demo-vpc-public01"
LISTENER = "arn:aws:elasticloadbalancing:ap-northeast-2:921810471078:listener/app/multiaz/1/443"


@pytest.fixture
def shared_infra():
    with TestingSession() as db:
        ha = db.get(models.InfraSpace, HA)
        ha.aws_account_id, ha.vpc_id = "921810471078", "vpc-ha"
        ha.public_subnet_ids, ha.private_subnet_ids = ["sn-web-a", "sn-web-c"], ["sn-app-a", "sn-app-c", "sn-db-a"]
        ha.app_subnet_ids = ["sn-app-a", "sn-app-c"]
        ha.alb_listener_arn, ha.alb_security_group_id, ha.alb_base_url = LISTENER, "sg-alb", "https://demo.howon.me"
        db.commit()


def make_app(client, repo, route=None, infra_id=HA):
    body = {"name": repo, "repo_url": f"https://github.com/org/{repo}", "branch": "main", "infra_id": infra_id}
    if route:
        body["route_path"] = route
    return client.post("/api/app-spaces", json=body).json()


def plan(client, app, compute="ecs-fargate"):
    return client.post(f"/api/app-spaces/{app['id']}/plans", json={"compute": compute})


def test_front_and_back_share_the_alb(client, shared_infra):
    web, api = make_app(client, "sample-shop"), make_app(client, "shop-api")
    w, a = plan(client, web).json()["plans"][0], plan(client, api).json()["plans"][0]
    assert w["template"] == a["template"] == "ecs-fargate/shared-alb"
    assert (w["values"]["path_pattern"], w["values"]["rule_priority"], w["values"]["health_check_path"]) == ("/*", 1000, "/")
    # 경로를 안 고르면 -api 저장소는 /api. 헬스체크도 그 경로 안으로 맞춘다
    assert (a["values"]["path_pattern"], a["values"]["rule_priority"], a["values"]["health_check_path"]) == ("/api/*", 100, "/api/")
    assert client.get(f"/api/app-spaces/{api['id']}").json()["route_path"] == "/api"


def test_chosen_route_wins(client, shared_infra):
    app = make_app(client, "shop-api", route="/v1")
    values = plan(client, app).json()["plans"][0]["values"]
    assert (values["path_pattern"], values["rule_priority"]) == ("/v1/*", 100)


def test_replan_keeps_rule_number(client, shared_infra):
    app = make_app(client, "shop-api")
    assert plan(client, app).json()["plans"][0]["values"]["rule_priority"] == 100
    assert plan(client, app).json()["plans"][0]["values"]["rule_priority"] == 100


def test_second_root_app_conflicts(client, shared_infra):
    plan(client, make_app(client, "sample-shop"))
    r = plan(client, make_app(client, "other-shop"))  # 둘 다 / 로 정해진다
    assert (r.status_code, r.json()["error"]) == (409, "route_path_taken")


def test_ec2_on_shared_infra_stays_basic(client, shared_infra):
    values = plan(client, make_app(client, "sample-shop"), "ec2").json()["plans"][0]
    assert values["template"] == "ec2/basic" and "path_pattern" not in values["values"]


def test_infra_without_alb_stays_basic(client):
    p = plan(client, make_app(client, "sample-shop", infra_id=PUBLIC)).json()["plans"][0]
    assert p["template"] == "ecs-fargate/basic" and "rule_priority" not in p["values"]


# 워크플로가 받아 가는 값


def test_workflow_gets_alb_and_app_subnets(client, shared_infra, secret):  # noqa: F811
    plan_id = plan(client, make_app(client, "shop-api")).json()["plans"][0]["id"]
    infra = signed_get(client, plan_id).json()["infra"]
    assert infra == {
        "id": HA, "vpc_id": "vpc-ha", "aws_account_id": "921810471078",
        "public_subnet_ids": ["sn-web-a", "sn-web-c"], "private_subnet_ids": ["sn-app-a", "sn-app-c"],  # db 빠짐
        "alb_listener_arn": LISTENER, "alb_security_group_id": "sg-alb", "alb_base_url": "https://demo.howon.me",
    }


def test_workflow_basic_plan_keeps_private_and_omits_empty(client, shared_infra, secret):  # noqa: F811
    plan_id = plan(client, make_app(client, "sample-shop"), "ec2").json()["plans"][0]["id"]
    infra = signed_get(client, plan_id).json()["infra"]
    assert infra["private_subnet_ids"] == ["sn-app-a", "sn-app-c", "sn-db-a"]
    plan_id = plan(client, make_app(client, "sample-shop", infra_id=PUBLIC)).json()["plans"][0]["id"]
    assert "aws_account_id" not in signed_get(client, plan_id).json()["infra"]  # 계정을 모르면 칸을 뺀다
