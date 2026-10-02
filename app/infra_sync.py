"""인프라 갱신: Workload 계정에서 InfraId 태그가 붙은 VPC를 읽어 infra_spaces를 채운다 (API 명세 4절).

인프라 관리자가 미리 만든 인프라를 플랫폼이 읽기만 한다. DB가 비어 있어도 갱신 한 번이면 배포에 쓸
VPC·서브넷까지 들어간다. AWS에서 사라진 인프라는 지우지 않고 unavailable로 숨긴다 (그 인프라를 쓰는 앱이 있다).
목록을 볼 때마다 갱신한다(refresh). AWS를 한 번 읽는 데 1초 안팎이라 화면이 기다릴 만하다 (10/2 서버에서 측정).
"""
import logging
import re
import time
from dataclasses import dataclass, field

from botocore.exceptions import BotoCoreError, ClientError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models
from app.aws import WorkloadKeyMissing, workload_client
from app.config import get_settings
from app.ids import now

logger = logging.getLogger(__name__)

TAG = "InfraId"
ID_PATTERN = re.compile(r"^[a-z0-9-]{1,64}$")  # ADR-005 ID 규칙
COOLDOWN_SECONDS = 5  # 한 화면이 거의 동시에 여러 번 불러도 AWS는 한 번만 읽는다
RETRY_AFTER_FAILURE_SECONDS = 60  # AWS가 안 되면 목록 조회마다 기다리지 않게 잠시 쉰다
NETWORKS = {"public", "db-isolated", "multi-az"}
DEFAULT_COMPUTES = ["ecs-fargate", "lambda", "ec2"]

# 화면 정보도 AWS에서만 가져온다: VPC의 DisplayName·Description·Network·Computes 태그 (API 명세 4절).
# 태그가 없으면 이름은 InfraId, 설명은 빈칸, 컴퓨팅은 세 가지 전부, 네트워크는 서브넷 구성으로 짐작한다.


class SyncError(Exception):
    """갱신 실패. 메시지는 화면에 그대로 보여 줄 수 있는 문장이다."""


@dataclass
class Found:
    """AWS에서 찾은 인프라 하나."""

    id: str
    account_id: str
    vpc_id: str
    tags: dict[str, str]
    public_subnet_ids: list[str] = field(default_factory=list)  # 앱을 올릴 퍼블릭 서브넷 (AZ마다 하나)
    private_subnet_ids: list[str] = field(default_factory=list)


# 서버(Task)마다 따로 기억한다. Task가 2개라 같은 순간에 AWS를 두 번 읽을 수는 있다 (읽기만 해서 괜찮다)
_next_run = 0.0  # 이 시각 전에는 AWS를 다시 읽지 않는다
_last_error: "SyncError | None" = None


def sync(db: Session) -> None:
    """AWS를 읽어 DB를 맞춘다. commit까지 한다.

    성공 뒤 5초 안에 다시 부르면 아무것도 하지 않는다. 실패 뒤 60초 안에 다시 부르면 AWS를 부르지 않고 같은 실패를 낸다.
    """
    global _next_run, _last_error
    if time.monotonic() < _next_run:
        if _last_error is not None:
            raise _last_error
        return
    try:
        found = discover()
    except SyncError as e:
        _last_error, _next_run = e, time.monotonic() + RETRY_AFTER_FAILURE_SECONDS
        raise
    apply(db, found)
    db.commit()
    _last_error, _next_run = None, time.monotonic() + COOLDOWN_SECONDS


def refresh(db: Session) -> None:
    """목록을 볼 때 부른다. AWS를 읽지 못해도 DB에 있는 목록을 그대로 보여 준다."""
    try:
        sync(db)
    except SyncError:
        db.rollback()


def discover() -> list[Found]:
    try:
        ec2 = workload_client("ec2")
        vpcs = _all(ec2, "describe_vpcs", "Vpcs", Filters=[{"Name": "tag-key", "Values": [TAG]}])
        result = []
        for vpc in vpcs:
            tags = {t["Key"]: t["Value"] for t in vpc.get("Tags", [])}
            infra_id = tags.get(TAG, "").strip()
            if not ID_PATTERN.match(infra_id):
                logger.warning("InfraId 형식이 틀린 VPC는 건너뜁니다: %s %r", vpc["VpcId"], infra_id)
                continue
            vpc_filter = [{"Name": "vpc-id", "Values": [vpc["VpcId"]]}]
            subnets = _all(ec2, "describe_subnets", "Subnets", Filters=vpc_filter)
            tables = _all(ec2, "describe_route_tables", "RouteTables", Filters=vpc_filter)
            public, private = split_subnets(subnets, tables)
            result.append(Found(infra_id, vpc["OwnerId"], vpc["VpcId"], tags, public, private))
    except WorkloadKeyMissing as e:
        raise SyncError("Workload 키가 설정되지 않아 인프라를 읽을 수 없습니다.") from e
    except (BotoCoreError, ClientError) as e:
        logger.warning("인프라 갱신 실패: %s", e)
        raise SyncError("AWS에서 인프라를 읽지 못했습니다.") from e
    ids = [f.id for f in result]
    if len(ids) != len(set(ids)):
        raise SyncError("같은 InfraId가 붙은 VPC가 여러 개라 갱신하지 않았습니다.")
    return result


def _all(client, method: str, key: str, **kwargs) -> list[dict]:
    items = []
    for page in client.get_paginator(method).paginate(**kwargs):
        items += page[key]
    return items


def split_subnets(subnets: list[dict], tables: list[dict]) -> tuple[list[str], list[str]]:
    """(앱용 퍼블릭, 프라이빗). 인터넷 게이트웨이로 가는 경로가 있으면 퍼블릭이다.

    로드밸런서는 한 AZ에 서브넷 하나만 받으므로 퍼블릭은 AZ마다 하나만 고른다.
    이름에 nat이 들어간 서브넷(Multi-AZ의 NAT용)은 같은 AZ에 다른 퍼블릭이 있으면 고르지 않는다.
    """
    main = next((t for t in tables if any(a.get("Main") for a in t.get("Associations", []))), None)
    by_subnet = {a["SubnetId"]: t for t in tables for a in t.get("Associations", []) if a.get("SubnetId")}

    def is_public(subnet: dict) -> bool:
        table = by_subnet.get(subnet["SubnetId"], main)
        return bool(table) and any(r.get("GatewayId", "").startswith("igw-") for r in table.get("Routes", []))

    def name(subnet: dict) -> str:
        return next((t["Value"] for t in subnet.get("Tags", []) if t["Key"] == "Name"), "")

    public, private = {}, []
    for s in sorted(subnets, key=lambda s: ("nat" in name(s).lower(), name(s), s["SubnetId"])):
        if not is_public(s):
            private.append(s)
        elif s["AvailabilityZone"] not in public:
            public[s["AvailabilityZone"]] = s
    return (
        [public[az]["SubnetId"] for az in sorted(public)],
        [s["SubnetId"] for s in sorted(private, key=lambda s: (s["AvailabilityZone"], name(s), s["SubnetId"]))],
    )


def apply(db: Session, found: list[Found]) -> None:
    """찾은 인프라는 넣거나 고치고, DB에만 있는 인프라는 unavailable로 숨긴다."""
    seen = set()
    for f in found:
        seen.add(f.id)
        info = describe(f)
        row = db.get(models.InfraSpace, f.id)
        if row is None:
            row = models.InfraSpace(id=f.id, created_at=now())
            db.add(row)
        row.name, row.description = info["name"], info["description"]
        row.network, row.computes = info["network"], info["computes"]
        row.aws_account_id, row.region, row.vpc_id = f.account_id, get_settings().workload_aws_region, f.vpc_id
        row.public_subnet_ids, row.private_subnet_ids = f.public_subnet_ids, f.private_subnet_ids
        # 로드밸런서는 서로 다른 AZ의 퍼블릭 서브넷 2개 이상이 있어야 만들 수 있다
        row.status = "ready" if len(f.public_subnet_ids) >= 2 else "preparing"
    for row in db.scalars(select(models.InfraSpace).where(models.InfraSpace.id.not_in(seen))):
        row.status = "unavailable"


def describe(f: Found) -> dict:
    """화면 정보. VPC 태그, 없으면 기본값."""
    computes = [c for c in re.split(r"[\s:/]+", f.tags.get("Computes", "")) if c in DEFAULT_COMPUTES]
    network = f.tags.get("Network", "").strip()
    return {
        "name": (f.tags.get("DisplayName", "").strip() or f.id)[:100],
        "description": f.tags.get("Description", "").strip()[:300],
        "network": network if network in NETWORKS else _guess_network(f),
        "computes": computes or DEFAULT_COMPUTES,
    }


def _guess_network(f: Found) -> str:
    """Network 태그가 없을 때. Multi-AZ와 DB 격리형은 서브넷만으로 구분하기 어려워 태그를 권한다."""
    if not f.private_subnet_ids:
        return "public"
    return "db-isolated"
