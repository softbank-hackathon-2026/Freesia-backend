"""인프라 담당이 미리 만들어 둔 인프라(인프라 Space). 플랫폼은 조회만 한다 (2일차 회의 2:27:19)."""
from datetime import datetime

from sqlalchemy import JSON, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class InfraSpace(Base):
    __tablename__ = "infra_spaces"

    # AWS 자원의 InfraId 태그와 같은 값 ("AWS 리소스 네이밍 및 태깅 규칙")
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(String(300))
    network: Mapped[str] = mapped_column(String(20))
    computes: Mapped[list[str]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20))
    # 아래는 배포 때 워크플로에 넘기는 내부 값이다. API 응답에는 넣지 않는다.
    aws_account_id: Mapped[str | None] = mapped_column(String(12))
    region: Mapped[str | None] = mapped_column(String(30))
    vpc_id: Mapped[str | None] = mapped_column(String(32))
    public_subnet_ids: Mapped[list[str] | None] = mapped_column(JSON)
    private_subnet_ids: Mapped[list[str] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
