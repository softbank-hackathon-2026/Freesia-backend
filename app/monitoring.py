"""배포된 앱의 지표·로그 조회 (API 명세 12절, ADR-017). Workload 계정 CloudWatch를 읽기만 한다.

AWS 자원 이름은 배포 레포 템플릿(ecs-fargate·lambda·ec2/basic)이 앱 ID로 정한다. 그래서 앱 ID와 컴퓨팅만 있으면 찾을 수 있다.
컴퓨팅마다 지표 종류가 다르다. 응답 칸은 같게 두고, 그 컴퓨팅에 없는 값은 None이다 (10/2 김동윤 님과 맞춤).
- ecs-fargate: CPU·메모리(%), 로드밸런서 응답 시간·요청 수·5xx 수, 로그
- lambda: 처리 시간(평균)·호출 수·오류 수, 로그
- ec2: CPU(%)만. 메모리는 CloudWatch 에이전트가 없어서, 로그는 템플릿이 CloudWatch로 보내지 않아서 없다
키는 WORKLOAD_AWS_* 설정으로 클라이언트를 따로 만든다. 기본 자격증명(ECS 작업 역할)은 Bedrock 호출에 쓴다.
"""
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from botocore.exceptions import BotoCoreError, ClientError

from app import models
from app.aws import WorkloadKeyMissing, workload_client

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


def lambda_function(app_id: str) -> str:
    """templates/lambda/basic/main.tf의 함수 이름. 로그 그룹은 /aws/lambda/<이름>."""
    return f"{PREFIX}-fn-{app_id}"


def ec2_instance_name(app_id: str) -> str:
    """templates/ec2/basic/main.tf의 Name 태그."""
    return f"{PREFIX}-ec2-{app_id}"


def log_group(app_id: str, compute: str) -> str | None:
    """앱 로그가 있는 CloudWatch 로그 그룹. 로그를 모으지 않는 컴퓨팅이면 None."""
    if compute == "ecs-fargate":
        return names(app_id).log_group
    if compute == "lambda":
        return f"/aws/lambda/{lambda_function(app_id)}"
    return None


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
    try:
        return workload_client(service)
    except WorkloadKeyMissing as e:
        raise MonitoringError("모니터링 키가 설정되지 않았습니다.") from e


_cache: dict[tuple, tuple[float, object]] = {}


def _cached(key: tuple, fetch):
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
        return hit[1]
    value = fetch()
    _cache[key] = (time.monotonic(), value)
    return value


def get_metrics(app_id: str, compute: str) -> dict:
    """최근 1분 값. 칸은 컴퓨팅과 상관없이 같고, 없는 값은 None (모듈 설명 참고)."""
    return _cached(("metrics", app_id, compute), lambda: _fetch_metrics(app_id, compute))


def get_logs(app_id: str, compute: str, limit: int) -> list[dict]:
    """최근 로그 줄. 오래된 것부터. 로그를 모으지 않는 컴퓨팅은 부르지 않는다 (log_group이 None)."""
    group = log_group(app_id, compute)
    return _cached(("logs", app_id, compute, limit), lambda: _fetch_logs(group, limit))


def _fetch_metrics(app_id: str, compute: str) -> dict:
    try:
        queries = _metric_queries(app_id, compute)
        if not queries:  # EC2 서버가 아직 없거나 바뀌는 중
            return _metrics_result({})
        end = datetime.now(timezone.utc)
        r = _client("cloudwatch").get_metric_data(
            MetricDataQueries=queries, StartTime=end - METRICS_WINDOW, EndTime=end, ScanBy="TimestampDescending"
        )
    except (BotoCoreError, ClientError) as e:
        logger.warning("지표 조회 실패 (%s, %s): %s", app_id, compute, e)
        raise MonitoringError("지표를 가져오지 못했습니다.") from e
    latest = {}
    for result in r.get("MetricDataResults", []):
        if result.get("Values"):  # TimestampDescending이라 첫 값이 가장 최근
            latest[result["Id"]] = (result["Values"][0], result["Timestamps"][0])
    return _metrics_result(latest)


def _metric_queries(app_id: str, compute: str) -> list[dict]:
    """컴퓨팅별 CloudWatch 질의. Id가 응답 칸을 정한다 (_metrics_result)."""
    if compute == "lambda":
        fn = [{"Name": "FunctionName", "Value": lambda_function(app_id)}]
        return [
            _query("duration_ms", "AWS/Lambda", "Duration", fn, "Average"),
            _query("requests", "AWS/Lambda", "Invocations", fn, "Sum"),
            _query("errors", "AWS/Lambda", "Errors", fn, "Sum"),
        ]
    if compute == "ec2":
        instance_id = _ec2_instance_id(ec2_instance_name(app_id))
        if instance_id is None:
            return []
        return [_query("cpu", "AWS/EC2", "CPUUtilization", [{"Name": "InstanceId", "Value": instance_id}], "Average")]
    n = names(app_id)
    ecs = [{"Name": "ClusterName", "Value": n.cluster}, {"Name": "ServiceName", "Value": n.service}]
    queries = [
        _query("cpu", "AWS/ECS", "CPUUtilization", ecs, "Average"),
        _query("memory", "AWS/ECS", "MemoryUtilization", ecs, "Average"),
    ]
    lb_dimension = _load_balancer_dimension(n.load_balancer)
    if lb_dimension:
        lb = [{"Name": "LoadBalancer", "Value": lb_dimension}]
        queries += [
            _query("latency", "AWS/ApplicationELB", "TargetResponseTime", lb, "Average"),
            _query("requests", "AWS/ApplicationELB", "RequestCount", lb, "Sum"),
            _query("errors", "AWS/ApplicationELB", "HTTPCode_Target_5XX_Count", lb, "Sum"),
        ]
    return queries


def _metrics_result(latest: dict[str, tuple[float, datetime]]) -> dict:
    value = lambda key: latest[key][0] if key in latest else None  # noqa: E731
    latency, duration = value("latency"), value("duration_ms")  # ALB는 초, Lambda는 밀리초
    response_ms = latency * 1000 if latency is not None else duration
    return {
        "cpu_percent": _round(value("cpu")),
        "memory_percent": _round(value("memory")),
        "response_time_ms": _round(response_ms),
        "request_count": _int(value("requests")),
        "error_count": _int(value("errors")),
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


def _int(v: float | None) -> int | None:
    return int(v) if v is not None else None


def _ec2_instance_id(name: str) -> str | None:
    """Name 태그로 실행 중인 서버를 찾는다. 재배포하면 서버가 바뀌므로 가장 최근에 뜬 것을 쓴다."""
    r = _client("ec2").describe_instances(Filters=[
        {"Name": "tag:Name", "Values": [name]},
        {"Name": "instance-state-name", "Values": ["running"]},
    ])
    instances = [i for res in r.get("Reservations", []) for i in res.get("Instances", [])]
    if not instances:
        return None
    return max(instances, key=lambda i: i["LaunchTime"])["InstanceId"]


def _load_balancer_dimension(name: str) -> str | None:
    """ALB 지표의 차원 값 `app/<이름>/<id>`. ALB가 없으면 None (응답 시간·요청 수만 빠진다)."""
    try:
        lbs = _client("elbv2").describe_load_balancers(Names=[name])["LoadBalancers"]
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "LoadBalancerNotFound":
            return None
        raise
    return lbs[0]["LoadBalancerArn"].split(":loadbalancer/", 1)[1] if lbs else None


def _fetch_logs(group: str, limit: int) -> list[dict]:
    logs = _client("logs")
    since = int((datetime.now(timezone.utc) - LOGS_WINDOW).timestamp() * 1000)
    try:
        streams = logs.describe_log_streams(
            logGroupName=group, orderBy="LastEventTime", descending=True, limit=3
        )["logStreams"]
        events = []
        for stream in streams:  # Task·Lambda 실행 환경이 바뀌면 스트림도 바뀐다. 최근 스트림 몇 개를 합친다
            r = logs.get_log_events(
                logGroupName=group, logStreamName=stream["logStreamName"],
                startTime=since, limit=limit, startFromHead=False,
            )
            events += r.get("events", [])
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "ResourceNotFoundException":
            return []
        logger.warning("로그 조회 실패 (%s): %s", group, e)
        raise MonitoringError("로그를 가져오지 못했습니다.") from e
    except BotoCoreError as e:
        logger.warning("로그 조회 실패 (%s): %s", group, e)
        raise MonitoringError("로그를 가져오지 못했습니다.") from e
    events.sort(key=lambda e: e["timestamp"])
    return [
        {"at": datetime.fromtimestamp(e["timestamp"] / 1000, timezone.utc), "message": e["message"].rstrip("\n")}
        for e in events[-limit:]
    ]
