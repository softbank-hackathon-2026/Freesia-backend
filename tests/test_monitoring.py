"""앱 지표·로그 (API 명세 12절). AWS는 가짜 클라이언트로 바꿔 끼운다."""
from datetime import datetime, timezone

import pytest
from botocore.exceptions import ClientError

from app import models, monitoring
from app.config import get_settings
from tests.conftest import TestingSession
from tests.test_app_spaces import create_space

AT = datetime(2026, 10, 2, 10, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _fresh_cache():
    monitoring._cache.clear()


@pytest.fixture
def keys(monkeypatch):
    monkeypatch.setattr(get_settings(), "workload_aws_access_key_id", "test-id")
    monkeypatch.setattr(get_settings(), "workload_aws_secret_access_key", "test-secret")


def deployed(client, status="success", run_id=123, compute="ecs-fargate"):
    """실제 워크플로로 배포한 것처럼 DB에 남긴다."""
    space = create_space(client).json()
    with TestingSession() as db:
        dep = models.Deployment(
            id=f"dep-{space['id'][4:]}", app_space_id=space["id"], compute=compute, status=status,
            step="done", run_id=run_id, created_at=AT,
        )
        db.add(dep)
        db.get(models.AppSpace, space["id"]).latest_deployment_id = dep.id
        db.commit()
    return space


def metrics(client, space):
    return client.get(f"/api/app-spaces/{space['id']}/metrics").json()


def logs(client, space, **params):
    return client.get(f"/api/app-spaces/{space['id']}/logs", params=params).json()


class FakeAWS:
    """boto3 클라이언트 대신. 서비스 이름별로 같은 객체를 돌려준다."""

    def __init__(self, metric_values=None, lb=True, streams=None, missing_group=False):
        self.metric_values = metric_values or {}
        self.lb = lb
        self.streams = streams or {}
        self.missing_group = missing_group
        self.metric_queries = None

    def describe_load_balancers(self, Names):
        if not self.lb:
            raise ClientError({"Error": {"Code": "LoadBalancerNotFound"}}, "DescribeLoadBalancers")
        return {"LoadBalancers": [{"LoadBalancerArn": f"arn:aws:elasticloadbalancing:x:1:loadbalancer/app/{Names[0]}/abc"}]}

    def get_metric_data(self, MetricDataQueries, **_):
        self.metric_queries = MetricDataQueries
        return {"MetricDataResults": [
            {"Id": q["Id"], "Values": [self.metric_values[q["Id"]]] if q["Id"] in self.metric_values else [],
             "Timestamps": [AT] if q["Id"] in self.metric_values else []}
            for q in MetricDataQueries
        ]}

    def describe_log_streams(self, logGroupName, **_):
        if self.missing_group:
            raise ClientError({"Error": {"Code": "ResourceNotFoundException"}}, "DescribeLogStreams")
        return {"logStreams": [{"logStreamName": name} for name in self.streams]}

    def get_log_events(self, logStreamName, limit, **_):
        return {"events": self.streams[logStreamName][-limit:]}


@pytest.fixture
def aws(monkeypatch, keys):
    fake = FakeAWS()
    monkeypatch.setattr(monitoring, "_client", lambda service: fake)
    return fake


def event(ms, message):
    return {"timestamp": ms, "message": message + "\n"}


# 이름 규칙


def test_names_follow_template():
    n = monitoring.names("app-8d05b4eba4a3")
    assert n == monitoring.Names(
        cluster="sbh-workload-demo-ecs-app-8d05b4eba4a3",
        service="sbh-workload-demo-svc-app-8d05b4eba4a3",
        log_group="/ecs/sbh-workload-demo-app-8d05b4eba4a3",
        load_balancer="sbh-app-8d05b4eba4a3-alb",
    )


# 지표


def test_metrics(client, aws):
    aws.metric_values = {"cpu": 24.13, "memory": 38.0, "latency": 0.0125, "requests": 42.0}
    space = deployed(client)
    assert metrics(client, space) == {
        "status": "ok", "message": None, "cpu_percent": 24.1, "memory_percent": 38.0,
        "response_time_ms": 12.5, "request_count": 42, "measured_at": "2026-10-02T10:00:00Z",
    }
    dims = {q["Id"]: q["MetricStat"]["Metric"]["Dimensions"] for q in aws.metric_queries}
    assert dims["cpu"] == [
        {"Name": "ClusterName", "Value": f"sbh-workload-demo-ecs-{space['id']}"},
        {"Name": "ServiceName", "Value": f"sbh-workload-demo-svc-{space['id']}"},
    ]
    assert dims["latency"] == [{"Name": "LoadBalancer", "Value": f"app/sbh-{space['id']}-alb/abc"}]


def test_metrics_without_load_balancer(client, aws):
    aws.lb, aws.metric_values = False, {"cpu": 5.0, "memory": 10.0}
    got = metrics(client, deployed(client))
    assert (got["status"], got["cpu_percent"], got["response_time_ms"]) == ("ok", 5.0, None)
    assert {q["Id"] for q in aws.metric_queries} == {"cpu", "memory"}


def test_metrics_waiting_for_first_values(client, aws):
    assert metrics(client, deployed(client))["status"] == "waiting"


def test_metrics_are_cached(client, aws):
    aws.metric_values = {"cpu": 1.0, "memory": 1.0}
    space = deployed(client)
    metrics(client, space)
    aws.metric_values = {"cpu": 99.0, "memory": 99.0}
    assert metrics(client, space)["cpu_percent"] == 1.0


def test_metrics_aws_failure(client, aws, monkeypatch):
    def broken(**_):
        raise ClientError({"Error": {"Code": "AccessDenied"}}, "GetMetricData")
    monkeypatch.setattr(aws, "get_metric_data", broken)
    got = metrics(client, deployed(client))
    assert (got["status"], got["message"], got["cpu_percent"]) == ("error", "지표를 가져오지 못했습니다.", None)


def test_metrics_without_keys(client):
    got = metrics(client, deployed(client))
    assert (got["status"], got["message"]) == ("error", "모니터링 키가 설정되지 않았습니다.")


# 떠 있는 배포가 없을 때


@pytest.mark.parametrize("status,run_id", [("success", None), ("failed", 123), ("deploying", 123)])
def test_not_deployed(client, aws, status, run_id):
    space = deployed(client, status=status, run_id=run_id)  # run_id 없음 = 가짜 진행
    assert metrics(client, space)["status"] == "not_deployed"
    assert logs(client, space)["status"] == "not_deployed"
    assert aws.metric_queries is None  # AWS를 부르지 않음


def test_never_deployed(client, aws):
    space = create_space(client).json()
    assert metrics(client, space) == {
        "status": "not_deployed", "message": "지금 실제로 배포되어 있지 않은 앱입니다.", "cpu_percent": None,
        "memory_percent": None, "response_time_ms": None, "request_count": None, "measured_at": None,
    }


@pytest.mark.parametrize("teardown", ["requested", "success"])
def test_torn_down(client, aws, teardown):
    space = deployed(client)
    with TestingSession() as db:
        row = db.get(models.AppSpace, space["id"])
        row.teardown_status, row.teardown_requested_at = teardown, datetime(2026, 10, 2, 11, tzinfo=timezone.utc)
        db.commit()
    assert metrics(client, space)["status"] == "not_deployed"


def test_teardown_failed_still_live(client, aws):
    aws.metric_values = {"cpu": 1.0, "memory": 1.0}
    space = deployed(client)
    with TestingSession() as db:
        row = db.get(models.AppSpace, space["id"])
        row.teardown_status, row.teardown_requested_at = "failed", datetime(2026, 10, 2, 11, tzinfo=timezone.utc)
        db.commit()
    assert metrics(client, space)["status"] == "ok"


def test_unknown_app(client):
    assert client.get("/api/app-spaces/nope/metrics").json()["error"] == "app_space_not_found"


# 로그


def test_logs_merge_recent_streams(client, aws):
    aws.streams = {
        "app/app/new": [event(1_759_399_203_000, "GET /health 200")],
        "app/app/old": [event(1_759_399_201_000, "listening on 3000"), event(1_759_399_202_000, "GET / 200")],
    }
    got = logs(client, deployed(client))
    assert got["status"] == "ok"
    assert [line["message"] for line in got["lines"]] == ["listening on 3000", "GET / 200", "GET /health 200"]
    assert got["lines"][0]["at"] == "2025-10-02T10:00:01Z"


def test_logs_limit(client, aws):
    aws.streams = {"s": [event(1_759_399_200_000 + i, f"line {i}") for i in range(10)]}
    got = logs(client, deployed(client), limit=3)
    assert [line["message"] for line in got["lines"]] == ["line 7", "line 8", "line 9"]


def test_logs_empty_or_missing_group(client, aws):
    space = deployed(client)
    assert logs(client, space)["status"] == "waiting"
    aws.missing_group = True
    monitoring._cache.clear()
    assert logs(client, space)["status"] == "waiting"


def test_logs_limit_range(client):
    space = create_space(client).json()
    assert client.get(f"/api/app-spaces/{space['id']}/logs", params={"limit": 0}).status_code == 422
