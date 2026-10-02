"""Workload 계정 AWS 클라이언트. 배포된 앱과 인프라를 읽기만 한다 (모니터링, 인프라 갱신).

키는 WORKLOAD_AWS_* 설정으로 따로 넣는다. 기본 자격증명(ECS 작업 역할)은 Bedrock 호출에 쓴다.
"""
import boto3
from botocore.config import Config

from app.config import get_settings


class WorkloadKeyMissing(Exception):
    """Workload 키가 설정되지 않음."""


def workload_client(service: str):
    s = get_settings()
    if not (s.workload_aws_access_key_id and s.workload_aws_secret_access_key):
        raise WorkloadKeyMissing
    return boto3.client(
        service,
        region_name=s.workload_aws_region,
        aws_access_key_id=s.workload_aws_access_key_id,
        aws_secret_access_key=s.workload_aws_secret_access_key,
        config=Config(connect_timeout=5, read_timeout=10, retries={"total_max_attempts": 2}),
    )
