"""배포된 앱의 지표·로그 조회 (API 명세 12절, ADR-017). Workload 계정 CloudWatch를 읽기만 한다.

AWS 자원 이름은 배포 레포 템플릿(ecs-fargate/basic)이 앱 ID로 정한다. 그래서 앱 ID만 있으면 찾을 수 있다.
키는 WORKLOAD_AWS_* 설정으로 클라이언트를 따로 만든다. 기본 자격증명(ECS 작업 역할)은 Bedrock 호출에 쓴다.
"""
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app import models
from app.config import get_settings

logger = logging.getLogger(__name__)

PREFIX = "sbh-workload-demo"
METRICS_WINDOW = timedelta(minutes=10)  # 이 안의 가장 최근 1분 값을 보여 준다
LOGS_WINDOW = timedelta(hours=1)
CACHE_SECONDS = 15  # 화면이 몇 초마다 다시 불러도 CloudWatch를 매번 부르지 않게


class MonitoringError(Exception):
    """AWS 호출 실패. 메시지는 화면에 그대로 보여 줄 수 있는 문장이다."""


@dataclass(frozen=True)
class Names:
    cluster: str
    service: str
    log_group: str
    load_balancer: str


def names(app_id: str) -> Names:
    """배포 레포 templates/ecs-fargate/basic/main.tf의 이름 규칙과 같다."""
    return Names(
        cluster=f"{PREFIX}-ecs-{app_id}",
        service=f"{PREFIX}-svc-{app_id}",
        log_group=f"/ecs/{PREFIX}-{app_id}",
        load_balancer=f"sbh-{app_id}-alb",
    )


def live_deployment(space: models.AppSpace, dep: models.Deployment | None) -> models.Deployment | None:
    """지금 AWS에 떠 있는 배포. 실제 워크플로로 성공했고, 그 뒤에 내리지 않았어야 한다."""
    if dep is None or dep.status != "success" or dep.run_id is None:
        return None
    if space.teardown_status in ("requested", "success") and space.teardown_requested_at:
        if _aware(space.teardown_requested_at) > _aware(dep.created_at):
            return None
    return dep


def _aware(at: datetime) -> datetime:
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)  # SQLite는 시간대를 버린다


def _client(service: str):
    s = get_settings()
    if not (s.workload_aws_access_key_id and s.workload_aws_secret_access_key):
        raise MonitoringError("모니터링 키가 설정되지 않았습니다.")
    return boto3.client(
        service,
        region_name=s.workload_aws_region,
        aws_access_key_id=s.workload_aws_access_key_id,
        aws_secret_access_key=s.workload_aws_secret_access_key,
        config=Config(connect_timeout=5, read_timeout=10, retries={"total_max_attempts": 2}),
    )


_cache: dict[tuple, tuple[float, object]] = {}


def _cached(key: tuple, fetch):
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
        return hit[1]
    value = fetch()
    _cache[key] = (time.monotonic(), value)
    return value


def get_metrics(app_id: str) -> dict:
    """최근 1분 CPU·메모리(%)와 응답 시간(ms)·요청 수. 아직 값이 없으면 None."""
    return _cached(("metrics", app_id), lambda: _fetch_metrics(names(app_id)))


def get_logs(app_id: str, limit: int) -> list[dict]:
    """최근 로그 줄. 오래된 것부터."""
    return _cached(("logs", app_id, limit), lambda: _fetch_logs(names(app_id), limit))


def _fetch_metrics(n: Names) -> dict:
    try:
        lb_dimension = _load_balancer_dimension(n.load_balancer)
        ecs = [{"Name": "ClusterName", "Value": n.cluster}, {"Name": "ServiceName", "Value": n.service}]
        queries = [
            _query("cpu", "AWS/ECS", "CPUUtilization", ecs, "Average"),
            _query("memory", "AWS/ECS", "MemoryUtilization", ecs, "Average"),
        ]
        if lb_dimension:
            lb = [{"Name": "LoadBalancer", "Value": lb_dimension}]
            queries += [
                _query("latency", "AWS/ApplicationELB", "TargetResponseTime", lb, "Average"),
                _query("requests", "AWS/ApplicationELB", "RequestCount", lb, "Sum"),
            ]
        end = datetime.now(timezone.utc)
        r = _client("cloudwatch").get_metric_data(
            MetricDataQueries=queries, StartTime=end - METRICS_WINDOW, EndTime=end, ScanBy="TimestampDescending"
        )
    except (BotoCoreError, ClientError) as e:
        logger.warning("지표 조회 실패 (%s): %s", n.service, e)
        raise MonitoringError("지표를 가져오지 못했습니다.") from e
    latest = {}
    for result in r.get("MetricDataResults", []):
        if result.get("Values"):  # TimestampDescending이라 첫 값이 가장 최근
            latest[result["Id"]] = (result["Values"][0], result["Timestamps"][0])
    value = lambda key: latest[key][0] if key in latest else None  # noqa: E731
    latency = value("latency")
    return {
        "cpu_percent": _round(value("cpu")),
        "memory_percent": _round(value("memory")),
        "response_time_ms": _round(latency * 1000) if latency is not None else None,
        "request_count": int(value("requests")) if value("requests") is not None else None,
        "measured_at": max((t for _, t in latest.values()), default=None),
    }


def _query(qid: str, namespace: str, metric: str, dimensions: list[dict], stat: str) -> dict:
    return {
        "Id": qid,
        "MetricStat": {
            "Metric": {"Namespace": namespace, "MetricName": metric, "Dimensions": dimensions},
            "Period": 60,
            "Stat": stat,
        },
    }


def _round(v: float | None) -> float | None:
    return round(v, 1) if v is not None else None


def _load_balancer_dimension(name: str) -> str | None:
    """ALB 지표의 차원 값 `app/<이름>/<id>`. ALB가 없으면 None (응답 시간·요청 수만 빠진다)."""
    try:
        lbs = _client("elbv2").describe_load_balancers(Names=[name])["LoadBalancers"]
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "LoadBalancerNotFound":
            return None
        raise
    return lbs[0]["LoadBalancerArn"].split(":loadbalancer/", 1)[1] if lbs else None


def _fetch_logs(n: Names, limit: int) -> list[dict]:
    logs = _client("logs")
    since = int((datetime.now(timezone.utc) - LOGS_WINDOW).timestamp() * 1000)
    try:
        streams = logs.describe_log_streams(
            logGroupName=n.log_group, orderBy="LastEventTime", descending=True, limit=3
        )["logStreams"]
        events = []
        for stream in streams:  # Task가 바뀌면 스트림도 바뀐다. 최근 스트림 몇 개를 합친다
            r = logs.get_log_events(
                logGroupName=n.log_group, logStreamName=stream["logStreamName"],
                startTime=since, limit=limit, startFromHead=False,
            )
            events += r.get("events", [])
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "ResourceNotFoundException":
            return []
        logger.warning("로그 조회 실패 (%s): %s", n.log_group, e)
        raise MonitoringError("로그를 가져오지 못했습니다.") from e
    except BotoCoreError as e:
        logger.warning("로그 조회 실패 (%s): %s", n.log_group, e)
        raise MonitoringError("로그를 가져오지 못했습니다.") from e
    events.sort(key=lambda e: e["timestamp"])
    return [
        {"at": datetime.fromtimestamp(e["timestamp"] / 1000, timezone.utc), "message": e["message"].rstrip("\n")}
        for e in events[-limit:]
    ]
