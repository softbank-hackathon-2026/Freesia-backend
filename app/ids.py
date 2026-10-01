"""ID와 시각 생성."""
import uuid
from datetime import datetime, timezone


def now() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    # ADR-005: 소문자·숫자·하이픈만 (AWS 태그 값으로 쓰일 수 있다)
    return f"{prefix}-{uuid.uuid4().hex[:12]}"
