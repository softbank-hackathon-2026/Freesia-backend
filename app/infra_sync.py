"""인프라 갱신: 인프라를 읽어 오는 곳(출처)마다 InfraId 태그가 붙은 인프라를 읽어 infra_spaces를 채운다 (API 명세 4절).

인프라 관리자가 미리 만든 인프라를 플랫폼이 읽기만 한다. DB가 비어 있어도 갱신 한 번이면 배포에 쓸
VPC·서브넷까지 들어간다. AWS에서 사라진 인프라는 지우지 않고 unavailable로 숨긴다 (그 인프라를 쓰는 앱이 있다).
목록을 볼 때마다 갱신한다(refresh). AWS를 한 번 읽는 데 1초 안팎이라 화면이 기다릴 만하다 (10/2 서버에서 측정).
Sandbox는 키가 있을 때만 읽는다. 한 출처를 못 읽으면 그 출처 인프라는 DB 값을 그대로 두고, 다른 출처는 갱신한다.

출처(Source)는 지금 AWS 계정(Workload·Sandbox)뿐이다. 인프라마다 provider(aws / onprem / gcp / azure)를 저장하는데,
태그가 아니라 어느 출처에서 읽었는지로 정한다. 새 클라우드를 붙일 때는 그 클라우드를 읽어 Found 목록을 돌려주는
함수를 만들고 sources()에 Source 하나를 더하면 된다 (목록·상세 API, 사라진 인프라 숨기기는 그대로 동작한다).
"""
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial

from botocore.exceptions import BotoCoreError, ClientError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models
from app import aws
from app.aws import WorkloadKeyMissing, account_client
from app.ids import now

logger = logging.getLogger(__name__)

TAG = "InfraId"
DEFAULT_TAG = "DefaultInfra"  # true인 VPC 한 곳이 "인프라 선택 안 함"일 때 쓰는 기본 인프라 (10/3 합의)
ID_PATTERN = re.compile(r"^[a-z0-9-]{1,64}$")  # ADR-005 ID 규칙
COOLDOWN_SECONDS = 5  # 한 화면이 거의 동시에 여러 번 불러도 AWS는 한 번만 읽는다
RETRY_AFTER_FAILURE_SECONDS = 60  # AWS가 안 되면 목록 조회마다 기다리지 않게 잠시 쉰다
NETWORKS = {"public", "db-isolated", "multi-az"}
DEFAULT_COMPUTES = ["ecs-fargate", "lambda", "ec2"]
PROVIDERS = ("aws", "onprem", "gcp", "azure")  # API 응답 provider 값. 지금 읽는 출처는 aws뿐이다

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


class SourceError(Exception):
    """출처 하나를 읽지 못함 (키 없음, API 실패 등)."""


@dataclass
class Found:
    """출처에서 찾은 인프라 하나. provider는 읽은 출처가 채운다 (읽는 함수가 정하지 않는다)."""

    id: str
    account_id: str
    vpc_id: str
    region: str
    tags: dict[str, str]
    public_subnet_ids: list[str] = field(default_factory=list)  # 앱을 올릴 퍼블릭 서브넷 (AZ마다 하나)
    private_subnet_ids: list[str] = field(default_factory=list)
    app_subnet_ids: list[str] = field(default_factory=list)  # 프라이빗 중 db가 아닌 것 (shared-alb 앱 자리)
    alb: "SharedAlb | None" = None
    provider: str = "aws"


@dataclass(frozen=True)
class Source:
    """인프라를 읽어 오는 곳 하나 (예: AWS Workload 계정).

    read: 인프라 목록을 돌려준다. 못 읽으면 SourceError.
    owns: DB의 인프라가 이 출처에서 온 것인지. 이 출처를 읽었는데 없으면 사라진 것(unavailable)으로 본다.
    required: 못 읽으면 갱신 전체를 멈출지. 아니면 그 출처만 빼고 갱신한다.
    """

    key: str
    provider: str
    required: bool
    read: Callable[[], list["Found"]]
    owns: Callable[[models.InfraSpace], bool]


@dataclass
class SharedAlb:
    """인프라에 미리 만들어 둔 공용 ALB (Multi-AZ). 앱마다 만드는 ALB와는 ApplicationId 태그로 구분한다."""

    listener_arn: str
    security_group_id: str
    base_url: str


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
        found, read = discover()
    except SyncError as e:
        _last_error, _next_run = e, time.monotonic() + RETRY_AFTER_FAILURE_SECONDS
        raise
    apply(db, found, read)
    db.commit()
    _last_error, _next_run = None, time.monotonic() + COOLDOWN_SECONDS


def refresh(db: Session) -> None:
    """목록을 볼 때 부른다. AWS를 읽지 못해도 DB에 있는 목록을 그대로 보여 준다."""
    try:
        sync(db)
    except SyncError:
        db.rollback()


def sources() -> list[Source]:
    """지금 읽을 출처 목록. AWS는 키가 있는 계정마다 하나 (Workload는 필수, Sandbox는 선택)."""
    return [
        Source(
            key=f"aws:{account}",
            provider="aws",
            required=account == aws.WORKLOAD,
            read=partial(_read_aws, account),
            owns=partial(_owned_by_aws_account, account),
        )
        for account in aws.accounts()
    ]


def _owned_by_aws_account(account: str, row: models.InfraSpace) -> bool:
    return row.provider == "aws" and aws.account_of(row.aws_account_id) == account


def _read_aws(account: str) -> list["Found"]:
    try:
        return _discover_account(account)
    except WorkloadKeyMissing as e:
        raise SourceError(f"{account} 키가 설정되지 않아 인프라를 읽을 수 없습니다.") from e
    except (BotoCoreError, ClientError) as e:
        raise SourceError(f"AWS({account})에서 인프라를 읽지 못했습니다.") from e


def discover() -> tuple[list[Found], list[Source]]:
    """(찾은 인프라, 읽은 출처). 필수 출처를 못 읽으면 SyncError, 선택 출처는 경고만 남기고 빼고 돌려준다."""
    result, read = [], []
    for source in sources():
        try:
            found = source.read()
        except SourceError as e:
            logger.warning("인프라 갱신 실패 (%s): %s", source.key, e.__cause__ or e)
            if source.required:
                raise SyncError(str(e)) from e
            continue
        for f in found:
            f.provider = source.provider
        result += found
        read.append(source)
    ids = [f.id for f in result]
    if len(ids) != len(set(ids)):
        raise SyncError("같은 InfraId가 붙은 인프라가 여러 개라 갱신하지 않았습니다.")
    return result, read


def _discover_account(account: str) -> list[Found]:
    ec2 = account_client(account, "ec2")
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
        names = {s["SubnetId"]: _name(s) for s in subnets}
        apps = [sid for sid in private if "db" not in names.get(sid, "").lower()]
        alb = find_shared_alb(account, infra_id, vpc["VpcId"])
        result.append(Found(infra_id, vpc["OwnerId"], vpc["VpcId"], aws.region(account), tags, public, private, apps, alb))
    return result


def find_shared_alb(account: str, infra_id: str, vpc_id: str) -> SharedAlb | None:
    """VPC 안에서 그 인프라의 공용 ALB를 찾는다. 없으면 None (그 인프라는 앱마다 ALB를 만드는 basic 템플릿을 쓴다).

    공용 ALB: 인터넷용 ALB이면서 InfraId 태그가 이 인프라이고, ApplicationId 태그가 없는 것(앱 ALB는 이 태그가 있다).
    HTTPS(443) 리스너와 그 인증서 도메인으로 주소(https://도메인)를 만든다. 하나라도 없으면 공용 ALB로 쓰지 않는다.
    """
    elb = account_client(account, "elbv2")
    lbs = [
        lb for lb in _all(elb, "describe_load_balancers", "LoadBalancers")
        if lb.get("VpcId") == vpc_id and lb.get("Type") == "application" and lb.get("Scheme") == "internet-facing"
    ]
    if not lbs:
        return None
    arns = [lb["LoadBalancerArn"] for lb in lbs]
    tags = {
        d["ResourceArn"]: {t["Key"]: t["Value"] for t in d.get("Tags", [])}
        for i in range(0, len(arns), 20)  # describe_tags는 한 번에 20개까지
        for d in elb.describe_tags(ResourceArns=arns[i : i + 20])["TagDescriptions"]
    }
    shared = [lb for lb in lbs if tags.get(lb["LoadBalancerArn"], {}).get(TAG) == infra_id
              and "ApplicationId" not in tags.get(lb["LoadBalancerArn"], {})]
    if len(shared) != 1:
        if shared:
            logger.warning("공용 ALB가 여러 개라 쓰지 않습니다: %s", infra_id)
        return None
    lb = shared[0]
    https = [ls for ls in elb.describe_listeners(LoadBalancerArn=lb["LoadBalancerArn"])["Listeners"] if ls.get("Port") == 443]
    if not https or not https[0].get("Certificates") or not lb.get("SecurityGroups"):
        return None
    cert = account_client(account, "acm").describe_certificate(CertificateArn=https[0]["Certificates"][0]["CertificateArn"])
    domain = cert["Certificate"].get("DomainName", "")
    if not domain or "*" in domain:
        return None
    return SharedAlb(https[0]["ListenerArn"], lb["SecurityGroups"][0], f"https://{domain}")


def _name(subnet: dict) -> str:
    return next((t["Value"] for t in subnet.get("Tags", []) if t["Key"] == "Name"), "")


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

    public, private = {}, []
    for s in sorted(subnets, key=lambda s: ("nat" in _name(s).lower(), _name(s), s["SubnetId"])):
        if not is_public(s):
            private.append(s)
        elif s["AvailabilityZone"] not in public:
            public[s["AvailabilityZone"]] = s
    return (
        [public[az]["SubnetId"] for az in sorted(public)],
        [s["SubnetId"] for s in sorted(private, key=lambda s: (s["AvailabilityZone"], _name(s), s["SubnetId"]))],
    )


def apply(db: Session, found: list[Found], read: list[Source]) -> None:
    """찾은 인프라는 넣거나 고치고, 읽은 출처(read)에서 사라진 인프라는 unavailable로 숨긴다.

    못 읽은 출처의 인프라는 그대로 둔다 (Sandbox 키가 잠깐 안 돼도 Sandbox 인프라가 목록에서 사라지지 않게).
    """
    seen = set()
    for f in found:
        seen.add(f.id)
        info = describe(f)
        row = db.get(models.InfraSpace, f.id)
        if row is None:
            row = models.InfraSpace(id=f.id, created_at=now())
            db.add(row)
        row.provider = f.provider
        row.name, row.description = info["name"], info["description"]
        row.network, row.computes = info["network"], info["computes"]
        row.aws_account_id, row.region, row.vpc_id = f.account_id, f.region, f.vpc_id
        row.public_subnet_ids, row.private_subnet_ids = f.public_subnet_ids, f.private_subnet_ids
        row.app_subnet_ids = f.app_subnet_ids
        row.alb_listener_arn = f.alb.listener_arn if f.alb else None
        row.alb_security_group_id = f.alb.security_group_id if f.alb else None
        row.alb_base_url = f.alb.base_url if f.alb else None
        row.is_default = f.tags.get(DEFAULT_TAG, "").strip().lower() == "true"
        # 로드밸런서는 서로 다른 AZ의 퍼블릭 서브넷 2개 이상이 있어야 만들 수 있다
        row.status = "ready" if len(f.public_subnet_ids) >= 2 else "preparing"
    for row in db.scalars(select(models.InfraSpace).where(models.InfraSpace.id.not_in(seen))):
        if any(source.owns(row) for source in read):
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


def default_infra(db: Session) -> "models.InfraSpace | None":
    """기본 인프라. DefaultInfra=true인 인프라가 딱 하나일 때만 돌려준다 (없거나 여럿이면 None, 엉뚱한 곳 배포 방지)."""
    rows = list(db.scalars(
        select(models.InfraSpace).where(models.InfraSpace.is_default.is_(True), models.InfraSpace.status != "unavailable")
    ))
    if len(rows) > 1:
        logger.warning("DefaultInfra 태그가 여러 인프라에 있어 기본 인프라를 쓰지 않습니다: %s", [r.id for r in rows])
    return rows[0] if len(rows) == 1 else None
