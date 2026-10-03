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
        "tags": {"InfraId": MULTI_AZ},
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
    """ec2·elbv2·acm 대신. account_client가 계정마다 서비스와 상관없이 이 객체를 돌려준다."""

    def __init__(self, vpcs, owner="921810471078"):
        self.vpcs = vpcs
        self.owner = owner
        self.calls = 0
        self.lbs = []  # describe_load_balancers 결과
        self.lb_tags = {}  # ARN → 태그
        self.listeners = {}  # LB ARN → 리스너 목록
        self.certs = {}  # 인증서 ARN → 도메인

    def describe_tags(self, ResourceArns):
        return {"TagDescriptions": [
            {"ResourceArn": a, "Tags": [{"Key": k, "Value": v} for k, v in self.lb_tags.get(a, {}).items()]}
            for a in ResourceArns
        ]}

    def describe_listeners(self, LoadBalancerArn):
        return {"Listeners": self.listeners.get(LoadBalancerArn, [])}

    def describe_certificate(self, CertificateArn):
        return {"Certificate": {"DomainName": self.certs[CertificateArn]}}

    def get_paginator(self, method):
        fake = self

        class Pager:
            def paginate(self, Filters=()):
                fake.calls += 1
                if method == "describe_load_balancers":
                    return [{"LoadBalancers": fake.lbs}]
                vpc_id = next((f["Values"][0] for f in Filters if f["Name"] == "vpc-id"), None)
                if method == "describe_vpcs":
                    return [{"Vpcs": [
                        {"VpcId": vid, "OwnerId": fake.owner,
                         "Tags": [{"Key": k, "Value": v} for k, v in vpc["tags"].items()]}
                        for vid, vpc in fake.vpcs.items()
                    ]}]
                if method == "describe_subnets":
                    return [{"Subnets": fake.vpcs[vpc_id]["subnets"]}]
                return [{"RouteTables": fake.vpcs[vpc_id]["tables"]}]

        return Pager()


@pytest.fixture
def aws(monkeypatch):
    monkeypatch.setattr(get_settings(), "workload_aws_access_key_id", "test-id")
    monkeypatch.setattr(get_settings(), "workload_aws_secret_access_key", "test-secret")
    fake = FakeEC2({k: dict(v) for k, v in AWS.items()})
    fake.accounts = {"workload": fake}  # sandbox 테스트가 Sandbox 가짜를 더 넣는다
    monkeypatch.setattr(infra_sync, "account_client", lambda account, service: fake.accounts[account])
    return fake


def sync(client):
    return client.post("/api/infra-spaces/sync")


def test_sync_fills_deployable_infra(client, aws):
    r = sync(client)
    assert r.status_code == 200
    got = {i["id"]: i for i in r.json()}
    # 예전 임시 인프라(private01, ha01)는 AWS에 없어서 빠진다
    assert set(got) == {PUBLIC, DB_ISOLATED, MULTI_AZ}
    assert got[DB_ISOLATED]["name"] == "DB 격리형 서비스용"
    assert (got[MULTI_AZ]["network"], got[MULTI_AZ]["status"]) == ("multi-az", "ready")
    assert got[MULTI_AZ]["deployable_computes"] == ["ecs-fargate", "ec2"]  # Multi-AZ 표는 Fargate·EC2
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
    assert len(client.get("/api/infra-spaces").json()) == 3  # 목록 조회만으로 채워진다
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


def test_tags_override_known_names(client, aws):
    aws.vpcs["vpc-pub"]["tags"] = {"InfraId": PUBLIC, "DisplayName": "라인 서비스용", "Computes": "ecs-fargate lambda"}
    got = {i["id"]: i for i in sync(client).json()}
    assert (got[PUBLIC]["name"], got[PUBLIC]["computes"]) == ("라인 서비스용", ["ecs-fargate", "lambda"])


def test_unknown_infra_uses_its_id(client, aws):
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


# 목록을 볼 때마다 갱신 (새로고침 버튼이 GET만 불러도 최신)


def test_list_reads_aws_every_time(client, aws, monkeypatch):
    got = {i["id"]: i for i in client.get("/api/infra-spaces").json()}
    assert set(got) == {PUBLIC, DB_ISOLATED, MULTI_AZ}  # 갱신 버튼 없이도 AWS 값
    aws.vpcs["vpc-pub"]["tags"] = {"InfraId": PUBLIC, "DisplayName": "라인 서비스용"}
    monkeypatch.setattr(infra_sync, "_next_run", 0.0)  # 5초가 지났다고 친다
    got = {i["id"]: i for i in client.get("/api/infra-spaces").json()}
    assert got[PUBLIC]["name"] == "라인 서비스용"


def test_list_burst_reads_aws_once(client, aws):
    client.get("/api/infra-spaces")
    calls = aws.calls
    client.get("/api/infra-spaces")
    assert aws.calls == calls


def test_list_keeps_db_when_aws_fails(client, aws, monkeypatch):
    def broken(method):
        aws.calls += 1
        raise ClientError({"Error": {"Code": "UnauthorizedOperation"}}, "DescribeVpcs")
    monkeypatch.setattr(aws, "get_paginator", broken)
    r = client.get("/api/infra-spaces")
    assert r.status_code == 200 and len(r.json()) == 3  # 예전 목록 그대로
    calls = aws.calls
    assert client.get("/api/infra-spaces").status_code == 200
    assert aws.calls == calls  # 실패 뒤에는 잠시 AWS를 다시 부르지 않는다
    # 직접 갱신은 실패를 그대로 알려 준다
    assert (sync(client).status_code, aws.calls) == (502, calls)


def test_list_without_key_keeps_db(client):
    assert len(client.get("/api/infra-spaces").json()) == 3


# 공용 ALB (Multi-AZ shared-alb)

SHARED_LB = "arn:aws:elasticloadbalancing:ap-northeast-2:921810471078:loadbalancer/app/sbh-workload-demo-multiaz-alb/1"
LISTENER = "arn:aws:elasticloadbalancing:ap-northeast-2:921810471078:listener/app/sbh-workload-demo-multiaz-alb/1/443"


def add_shared_alb(aws, app_lb=True):
    aws.lbs = [{"LoadBalancerArn": SHARED_LB, "VpcId": "vpc-ha", "Type": "application", "Scheme": "internet-facing",
                "SecurityGroups": ["sg-alb"]}]
    aws.lb_tags = {SHARED_LB: {"InfraId": MULTI_AZ}}
    aws.listeners = {SHARED_LB: [
        {"ListenerArn": "arn:listener/80", "Port": 80},
        {"ListenerArn": LISTENER, "Port": 443, "Certificates": [{"CertificateArn": "arn:cert"}]},
    ]}
    aws.certs = {"arn:cert": "demo.howon.me"}
    if app_lb:  # 같은 VPC에 basic 템플릿으로 만든 앱 ALB가 있어도 공용으로 보지 않는다
        aws.lbs.append({"LoadBalancerArn": "arn:app-lb", "VpcId": "vpc-ha", "Type": "application",
                        "Scheme": "internet-facing", "SecurityGroups": ["sg-app"]})
        aws.lb_tags["arn:app-lb"] = {"InfraId": MULTI_AZ, "ApplicationId": "app-1"}
    # db 서브넷도 프라이빗이라 앱 서브넷에서 빠지는지 본다
    aws.vpcs["vpc-ha"] = {
        **aws.vpcs["vpc-ha"],
        "subnets": aws.vpcs["vpc-ha"]["subnets"] + [subnet("sn-db-a3", "a", "db_a")],
        "tables": [table("rt-main3", True, main=True), table("rt-app", False, ["sn-app-a", "sn-app-c", "sn-db-a3"])],
    }


def test_sync_finds_shared_alb_and_app_subnets(client, aws):
    add_shared_alb(aws)
    sync(client)
    with TestingSession() as db:
        ha = db.get(models.InfraSpace, MULTI_AZ)
        assert (ha.alb_listener_arn, ha.alb_security_group_id, ha.alb_base_url) == (LISTENER, "sg-alb", "https://demo.howon.me")
        assert ha.app_subnet_ids == ["sn-app-a", "sn-app-c"]
        assert "sn-db-a3" in ha.private_subnet_ids
        pub = db.get(models.InfraSpace, PUBLIC)
        assert pub.alb_listener_arn is None


def test_no_shared_alb_without_https_listener(client, aws):
    add_shared_alb(aws, app_lb=False)
    aws.listeners[SHARED_LB] = [{"ListenerArn": "arn:listener/80", "Port": 80}]
    sync(client)
    with TestingSession() as db:
        assert db.get(models.InfraSpace, MULTI_AZ).alb_listener_arn is None


# Sandbox 계정 (키가 있을 때만 읽는다)

SANDBOX_INFRA = "sbh-sandbox-vpc-default01"


@pytest.fixture
def sandbox(monkeypatch, aws):
    monkeypatch.setattr(get_settings(), "sandbox_aws_access_key_id", "sb-id")
    monkeypatch.setattr(get_settings(), "sandbox_aws_secret_access_key", "sb-secret")
    fake = FakeEC2({"vpc-sb": {**AWS["vpc-pub"], "tags": {"InfraId": SANDBOX_INFRA}}}, owner="635738234799")
    aws.accounts["sandbox"] = fake
    return fake


def infra_row(infra_id):
    with TestingSession() as db:
        return db.get(models.InfraSpace, infra_id)


def test_sandbox_infra_is_listed_with_its_account(client, sandbox):
    got = {i["id"]: i for i in sync(client).json()}
    assert set(got) == {PUBLIC, DB_ISOLATED, MULTI_AZ, SANDBOX_INFRA}
    assert got[SANDBOX_INFRA]["status"] == "ready"
    row = infra_row(SANDBOX_INFRA)
    # 구성안이 이 계정 ID를 넘겨서 배포 레포가 Sandbox로 배포한다
    assert (row.aws_account_id, row.region, row.vpc_id) == ("635738234799", "ap-northeast-2", "vpc-sb")
    assert infra_row(PUBLIC).aws_account_id == "921810471078"


def test_sandbox_not_read_without_key(client, aws):
    fake = FakeEC2({"vpc-sb": {**AWS["vpc-pub"], "tags": {"InfraId": SANDBOX_INFRA}}}, owner="635738234799")
    aws.accounts["sandbox"] = fake
    assert SANDBOX_INFRA not in {i["id"] for i in sync(client).json()}
    assert fake.calls == 0


def test_sandbox_failure_keeps_sandbox_and_updates_workload(client, aws, sandbox, monkeypatch):
    sync(client)

    def broken(method):
        raise ClientError({"Error": {"Code": "UnauthorizedOperation"}}, "DescribeVpcs")
    monkeypatch.setattr(sandbox, "get_paginator", broken)
    aws.vpcs["vpc-pub"]["tags"] = {"InfraId": PUBLIC, "DisplayName": "새 이름"}
    monkeypatch.setattr(infra_sync, "_next_run", 0.0)
    got = {i["id"]: i for i in sync(client).json()}
    assert got[PUBLIC]["name"] == "새 이름"  # Workload는 갱신
    assert got[SANDBOX_INFRA]["status"] == "ready"  # 못 읽은 Sandbox는 그대로


def test_workload_failure_keeps_everything(client, aws, sandbox, monkeypatch):
    sync(client)

    def broken(method):
        raise ClientError({"Error": {"Code": "UnauthorizedOperation"}}, "DescribeVpcs")
    monkeypatch.setattr(aws, "get_paginator", broken)
    monkeypatch.setattr(infra_sync, "_next_run", 0.0)
    assert sync(client).status_code == 502
    assert len(client.get("/api/infra-spaces").json()) == 4


def test_vanished_sandbox_infra_is_hidden(client, aws, sandbox):
    sync(client)
    sandbox.vpcs.clear()
    infra_sync._next_run = 0.0
    assert SANDBOX_INFRA not in {i["id"] for i in sync(client).json() if i["status"] != "unavailable"}
    assert infra_row(SANDBOX_INFRA).status == "unavailable"


# 기본 인프라 (DefaultInfra 태그): 인프라를 고르지 않은 앱이 간다. 목록에는 안 보인다


def make_default(sandbox):
    sandbox.vpcs["vpc-sb"]["tags"] = {"InfraId": SANDBOX_INFRA, "DefaultInfra": "true"}


def new_app(client, **body):
    return client.post("/api/app-spaces", json={"name": "todo", "repo_url": "https://github.com/org/todo", **body})


def test_default_infra_hidden_from_list_but_readable(client, sandbox):
    make_default(sandbox)
    ids = {i["id"] for i in sync(client).json()}
    assert SANDBOX_INFRA not in ids and PUBLIC in ids
    assert SANDBOX_INFRA not in {i["id"] for i in client.get("/api/infra-spaces").json()}
    assert client.get(f"/api/infra-spaces/{SANDBOX_INFRA}").json()["status"] == "ready"


def test_app_without_infra_goes_to_default(client, sandbox):
    make_default(sandbox)
    sync(client)
    r = new_app(client)
    assert r.status_code == 201
    assert r.json()["infra_id"] == SANDBOX_INFRA
    # 직접 고르면 그대로
    assert new_app(client, infra_id=PUBLIC).json()["infra_id"] == PUBLIC


def test_app_without_infra_reads_aws_if_needed(client, sandbox):
    make_default(sandbox)  # 아직 갱신 전: 앱 만들 때 한 번 읽어 온다
    assert new_app(client).json()["infra_id"] == SANDBOX_INFRA


def test_no_default_infra(client, aws):
    sync(client)
    r = new_app(client)
    assert r.status_code == 400
    assert r.json()["error"] == "no_default_infra"


def test_two_default_infras_means_none(client, aws, sandbox):
    make_default(sandbox)
    aws.vpcs["vpc-pub"]["tags"] = {"InfraId": PUBLIC, "DefaultInfra": "true"}
    sync(client)
    assert new_app(client).json()["error"] == "no_default_infra"


def test_removing_tag_unhides_infra(client, sandbox):
    make_default(sandbox)
    sync(client)
    sandbox.vpcs["vpc-sb"]["tags"] = {"InfraId": SANDBOX_INFRA}
    infra_sync._next_run = 0.0
    assert SANDBOX_INFRA in {i["id"] for i in sync(client).json()}
