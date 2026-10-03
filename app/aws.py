"""고객 앱 계정(Workload·Sandbox) AWS 클라이언트. 배포된 앱과 인프라를 읽기만 한다 (모니터링, 인프라 갱신).

키는 계정마다 WORKLOAD_AWS_*·SANDBOX_AWS_* 설정으로 따로 넣는다. 기본 자격증명(ECS 작업 역할)은 Bedrock 호출에 쓴다.
배포(자원 생성)는 배포 레포가 자기 시크릿으로 한다. 백엔드는 같은 계정을 읽기만 한다 (규칙 9).
"""
import boto3
from botocore.config import Config

from app.config import get_settings

WORKLOAD = "workload"
SANDBOX = "sandbox"


class WorkloadKeyMissing(Exception):
    """그 계정의 키가 설정되지 않음 (이름은 Workload만 있던 때 그대로)."""


def _keys(account: str) -> tuple[str, str, str]:
    s = get_settings()
    if account == SANDBOX:
        return s.sandbox_aws_access_key_id, s.sandbox_aws_secret_access_key, s.sandbox_aws_region
    return s.workload_aws_access_key_id, s.workload_aws_secret_access_key, s.workload_aws_region


def region(account: str) -> str:
    return _keys(account)[2]


def accounts() -> list[str]:
    """인프라를 읽을 계정. Workload는 늘 읽고, Sandbox는 키가 있을 때만 읽는다."""
    key_id, secret, _ = _keys(SANDBOX)
    return [WORKLOAD, SANDBOX] if key_id and secret else [WORKLOAD]


def account_of(aws_account_id: str | None) -> str:
    """인프라에 저장된 계정 ID로 어느 키를 쓸지. 모르는 ID(예전 행 포함)는 Workload."""
    return SANDBOX if aws_account_id and aws_account_id == get_settings().sandbox_aws_account_id else WORKLOAD


def account_client(account: str, service: str):
    key_id, secret, region_name = _keys(account)
    if not (key_id and secret):
        raise WorkloadKeyMissing
    return boto3.client(
        service,
        region_name=region_name,
        aws_access_key_id=key_id,
        aws_secret_access_key=secret,
        config=Config(connect_timeout=5, read_timeout=10, retries={"total_max_attempts": 2}),
    )


def workload_client(service: str):
    return account_client(WORKLOAD, service)
