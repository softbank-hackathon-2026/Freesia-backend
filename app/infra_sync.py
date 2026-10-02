"""인프라 갱신: Workload 계정에서 InfraId 태그가 붙은 VPC를 읽어 infra_spaces를 채운다 (API 명세 4절).

인프라 관리자가 미리 만든 인프라를 플랫폼이 읽기만 한다. DB가 비어 있어도 갱신 한 번이면 배포에 쓸
VPC·서브넷까지 들어간다. AWS에서 사라진 인프라는 지우지 않고 unavailable로 숨긴다 (그 인프라를 쓰는 앱이 있다).
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
COOLDOWN_SECONDS = 30  # 버튼을 연달아 눌러도 AWS를 한 번만 부른다
NETWORKS = {"public", "db-isolated", "multi-az"}
DEFAULT_COMPUTES = ["ecs-fargate", "lambda", "ec2"]

# 화면 이름은 AWS에 없어서 여기 둔다. ADR-026에서 이름이 정해지면 바꾼다. VPC에 DisplayName·Description·
# Network·Computes 태그가 있으면 태그가 우선이다. 표에도 태그에도 없으면 InfraId를 이름으로 쓴다.
KNOWN = {
    "sbh-workload-demo-vpc-public01": {
        "name": "공개 웹 서비스용",
        "description": "인터넷에서 바로 접속하는 웹 서비스. 퍼블릭 서브넷만 있는 가장 단순한 구성",
        "network": "public",
        "computes": ["ecs-fargate", "lambda", "ec2"],
    },
    "sbh-workload-demo-vpc-dbisolated01": {
        "name": "DB 격리형 서비스용",
        "description": "웹은 퍼블릭, DB는 인터넷이 닿지 않는 프라이빗 서브넷에 두는 구성",
        "network": "db-isolated",
        "computes": ["ecs-fargate", "lambda"],
    },
    "sbh-workload-demo-vpc-multiaz01": {
        "name": "고가용성 서비스용",
        "description": "여러 가용영역에 나눠 두어 한 곳에 장애가 나도 살아 있는 구성",
        "network": "multi-az",
        "computes": ["ecs-fargate", "ec2"],
    },
}


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


_last_run = 0.0


def sync(db: Session) -> None:
    """AWS를 읽어 DB를 맞춘다. 30초 안에 다시 부르면 AWS를 부르지 않는다. commit까지 한다."""
    global _last_run
    if time.monotonic() - _last_run < COOLDOWN_SECONDS:
        return
    found = discover()
    apply(db, found)
    db.commit()
    _last_run = time.monotonic()


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
    """화면 정보. 태그 > 코드 표 > 기본값."""
    known = KNOWN.get(f.id, {})
    computes = [c for c in re.split(r"[\s:/]+", f.tags.get("Computes", "")) if c in DEFAULT_COMPUTES]
    network = f.tags.get("Network", "")
    return {
        "name": (f.tags.get("DisplayName") or known.get("name") or f.id)[:100],
        "description": (f.tags.get("Description") or known.get("description") or "")[:300],
        "network": network if network in NETWORKS else known.get("network") or _guess_network(f),
        "computes": computes or known.get("computes") or DEFAULT_COMPUTES,
    }


def _guess_network(f: Found) -> str:
    if not f.private_subnet_ids:
        return "public"
    return "db-isolated"
