"""인프라 갱신 (API 명세 4절). AWS는 가짜 클라이언트로 바꿔 끼운다."""
import pytest
from botocore.exceptions import ClientError

from app import infra_sync, models
from app.config import get_settings
from tests.conftest import TestingSession
from tests.test_app_spaces import create_space

PUBLIC = "sbh-workload-demo-vpc-public01"
DB_ISOLATED = "sbh-workload-demo-vpc-dbisolated01"
MULTI_AZ = "sbh-workload-demo-vpc-multiaz01"


def subnet(sid, az, name):
    return {"SubnetId": sid, "AvailabilityZone": f"ap-northeast-2{az}", "Tags": [{"Key": "Name", "Value": name}]}


def table(tid, routes_to_igw, subnet_ids=(), main=False):
    routes = [{"DestinationCidrBlock": "10.0.0.0/16", "GatewayId": "local"}]
    if routes_to_igw:
        routes.append({"DestinationCidrBlock": "0.0.0.0/0", "GatewayId": "igw-1"})
    assocs = [{"SubnetId": s} for s in subnet_ids] + ([{"Main": True}] if main else [])
    return {"RouteTableId": tid, "Routes": routes, "Associations": assocs}


# ADR-025의 세 인프라와 같은 모양
AWS = {
    "vpc-pub": {
        "tags": {"InfraId": PUBLIC},
        "subnets": [subnet("sn-pub-a", "a", "pub_a"), subnet("sn-pub-c", "c", "pub_c")],
        "tables": [table("rt-pub", True, ["sn-pub-a", "sn-pub-c"]), table("rt-main", False, main=True)],
    },
    "vpc-db": {
        "tags": {"InfraId": DB_ISOLATED},
        "subnets": [
            subnet("sn-db-c", "c", "db_c"), subnet("sn-db-a", "a", "db_a"),
            subnet("sn-pub-a2", "a", "pub_a"), subnet("sn-pub-c2", "c", "pub_c"),
        ],
        "tables": [table("rt-pub2", True, ["sn-pub-a2", "sn-pub-c2"]), table("rt-db", False, ["sn-db-a", "sn-db-c"]),
                   table("rt-main2", False, main=True)],
    },
    "vpc-ha": {
        "tags": {"InfraId": MULTI_AZ, "DisplayName": "고가용성 서비스용", "Network": "multi-az",
                 "Computes": "ecs-fargate ec2"},
        "subnets": [
            subnet("sn-nat-a", "a", "nat_a"), subnet("sn-web-a", "a", "web_a"),
            subnet("sn-nat-c", "c", "nat_c"), subnet("sn-web-c", "c", "web_c"),
            subnet("sn-app-a", "a", "app_a"), subnet("sn-app-c", "c", "app_c"),
        ],
        # 퍼블릭은 메인 라우팅 테이블로 인터넷에 나간다 (명시 연결 없음)
        "tables": [table("rt-main3", True, main=True), table("rt-app", False, ["sn-app-a", "sn-app-c"])],
    },
}


class FakeEC2:
    def __init__(self, vpcs):
        self.vpcs = vpcs
        self.calls = 0

    def get_paginator(self, method):
        fake = self

        class Pager:
            def paginate(self, Filters):
                fake.calls += 1
                vpc_id = next((f["Values"][0] for f in Filters if f["Name"] == "vpc-id"), None)
                if method == "describe_vpcs":
                    return [{"Vpcs": [
                        {"VpcId": vid, "OwnerId": "921810471078",
                         "Tags": [{"Key": k, "Value": v} for k, v in vpc["tags"].items()]}
                        for vid, vpc in fake.vpcs.items()
                    ]}]
                if method == "describe_subnets":
                    return [{"Subnets": fake.vpcs[vpc_id]["subnets"]}]
                return [{"RouteTables": fake.vpcs[vpc_id]["tables"]}]

        return Pager()


@pytest.fixture(autouse=True)
def _no_cooldown(monkeypatch):
    monkeypatch.setattr(infra_sync, "_last_run", 0.0)


@pytest.fixture
def aws(monkeypatch):
    monkeypatch.setattr(get_settings(), "workload_aws_access_key_id", "test-id")
    monkeypatch.setattr(get_settings(), "workload_aws_secret_access_key", "test-secret")
    fake = FakeEC2({k: dict(v) for k, v in AWS.items()})
    monkeypatch.setattr(infra_sync, "workload_client", lambda service: fake)
    return fake


def sync(client):
    return client.post("/api/infra-spaces/sync")


def test_sync_fills_deployable_infra(client, aws):
    r = sync(client)
    assert r.status_code == 200
    got = {i["id"]: i for i in r.json()}
    # 예전 임시 인프라(private01, ha01)는 AWS에 없어서 빠진다
    assert set(got) == {PUBLIC, DB_ISOLATED, MULTI_AZ}
    # 태그가 있으면 태그대로
    assert (got[MULTI_AZ]["name"], got[MULTI_AZ]["network"], got[MULTI_AZ]["status"]) == (
        "고가용성 서비스용", "multi-az", "ready"
    )
    assert (got[MULTI_AZ]["computes"], got[MULTI_AZ]["deployable_computes"]) == (["ecs-fargate", "ec2"], ["ecs-fargate"])
    # 태그가 없으면 이름은 InfraId, 설명은 빈칸, 컴퓨팅은 전부, 네트워크는 서브넷으로 짐작
    assert (got[DB_ISOLATED]["name"], got[DB_ISOLATED]["description"]) == (DB_ISOLATED, "")
    assert (got[DB_ISOLATED]["network"], got[PUBLIC]["network"]) == ("db-isolated", "public")
    assert got[DB_ISOLATED]["computes"] == ["ecs-fargate", "lambda", "ec2"]
    with TestingSession() as db:
        ha = db.get(models.InfraSpace, MULTI_AZ)
        assert ha.vpc_id == "vpc-ha" and ha.aws_account_id == "921810471078"
        assert ha.public_subnet_ids == ["sn-web-a", "sn-web-c"]  # AZ마다 하나, nat 제외
        assert ha.private_subnet_ids == ["sn-app-a", "sn-app-c"]
        db_ = db.get(models.InfraSpace, DB_ISOLATED)
        assert (db_.public_subnet_ids, db_.private_subnet_ids) == (["sn-pub-a2", "sn-pub-c2"], ["sn-db-a", "sn-db-c"])
        assert db.get(models.InfraSpace, "sbh-workload-demo-vpc-ha01").status == "unavailable"


def test_sync_into_empty_db_then_deploy(client, aws):
    with TestingSession() as db:
        db.query(models.InfraSpace).delete()
        db.commit()
    assert client.get("/api/infra-spaces").json() == []
    assert len(sync(client).json()) == 3
    space = create_space(client, infra_id=DB_ISOLATED).json()
    r = client.post(f"/api/app-spaces/{space['id']}/deployments", json={"compute": "ecs-fargate"})
    assert r.status_code == 201


def test_unavailable_infra_cannot_be_used(client, aws):
    old = create_space(client, infra_id="sbh-workload-demo-vpc-ha01").json()  # 갱신 전에 만든 앱
    sync(client)
    assert create_space(client, infra_id="sbh-workload-demo-vpc-ha01", register_repo=False).json()["error"] == "infra_not_found"
    r = client.post(f"/api/app-spaces/{old['id']}/deployments", json={"compute": "ecs-fargate"})
    assert (r.status_code, r.json()["error"]) == (400, "infra_not_ready")
    assert client.get("/api/infra-spaces/sbh-workload-demo-vpc-ha01").json()["status"] == "unavailable"


def test_infra_without_vpc_is_not_deployable(client):
    """갱신 전 임시 인프라: VPC가 없어서 배포 불가로 보이고 배포도 막힌다 (10/2 alpha 앱 실패)."""
    infra = client.get("/api/infra-spaces/sbh-workload-demo-vpc-ha01").json()
    assert infra["deployable_computes"] == []
    space = create_space(client, infra_id="sbh-workload-demo-vpc-ha01").json()
    r = client.post(f"/api/app-spaces/{space['id']}/deployments", json={"compute": "ecs-fargate"})
    assert (r.status_code, r.json()["error"]) == (400, "infra_not_ready")


def test_one_public_subnet_is_preparing(client, aws):
    aws.vpcs["vpc-pub"]["subnets"] = [subnet("sn-pub-a", "a", "pub_a")]
    got = {i["id"]: i for i in sync(client).json()}
    assert (got[PUBLIC]["status"], got[PUBLIC]["deployable_computes"]) == ("preparing", [])


def test_display_tags(client, aws):
    aws.vpcs["vpc-pub"]["tags"] = {"InfraId": PUBLIC, "DisplayName": "라인 서비스용", "Computes": "ecs-fargate lambda"}
    got = {i["id"]: i for i in sync(client).json()}
    assert (got[PUBLIC]["name"], got[PUBLIC]["computes"]) == ("라인 서비스용", ["ecs-fargate", "lambda"])


def test_new_infra_without_tags_uses_its_id(client, aws):
    aws.vpcs["vpc-new"] = {"tags": {"InfraId": "sbh-new01"}, "subnets": AWS["vpc-pub"]["subnets"],
                           "tables": AWS["vpc-pub"]["tables"]}
    got = {i["id"]: i for i in sync(client).json()}
    assert (got["sbh-new01"]["name"], got["sbh-new01"]["network"]) == ("sbh-new01", "public")


def test_bad_infra_id_is_skipped(client, aws):
    aws.vpcs["vpc-bad"] = {"tags": {"InfraId": "Bad ID"}, "subnets": [], "tables": []}
    assert "Bad ID" not in [i["id"] for i in sync(client).json()]


def test_duplicate_infra_id_fails(client, aws):
    aws.vpcs["vpc-dup"] = dict(AWS["vpc-pub"])
    r = sync(client)
    assert (r.status_code, r.json()["error"]) == (502, "infra_sync_failed")


def test_cooldown(client, aws):
    sync(client)
    calls = aws.calls
    sync(client)
    assert aws.calls == calls


def test_sync_without_key(client):
    r = sync(client)
    assert (r.status_code, r.json()["error"]) == (502, "infra_sync_failed")
    assert len(client.get("/api/infra-spaces").json()) == 3  # DB는 그대로


def test_sync_aws_failure_keeps_db(client, aws, monkeypatch):
    def broken(method):
        raise ClientError({"Error": {"Code": "UnauthorizedOperation"}}, "DescribeVpcs")
    monkeypatch.setattr(aws, "get_paginator", broken)
    assert sync(client).status_code == 502
    assert len(client.get("/api/infra-spaces").json()) == 3
