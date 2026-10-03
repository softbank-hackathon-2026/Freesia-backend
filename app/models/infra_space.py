"""인프라 담당이 미리 만들어 둔 인프라(인프라 Space). 플랫폼은 조회만 한다 (2일차 회의 2:27:19)."""
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, String, false
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
    # 공용 ALB가 있는 인프라(Multi-AZ). 있으면 Fargate 앱을 ecs-fargate/shared-alb로 그 ALB 뒤에 붙인다 (app/catalog.py)
    alb_listener_arn: Mapped[str | None] = mapped_column(String(200))  # 공용 ALB의 HTTPS(443) 리스너
    alb_security_group_id: Mapped[str | None] = mapped_column(String(32))
    alb_base_url: Mapped[str | None] = mapped_column(String(200))  # 예: https://demo.howon.me (끝 / 없음)
    app_subnet_ids: Mapped[list[str] | None] = mapped_column(JSON)  # 앱을 둘 프라이빗 서브넷 (db 서브넷 제외)
    # VPC의 DefaultInfra=true 태그. 앱을 만들 때 인프라를 고르지 않으면 여기로 간다. 목록에는 보이지 않는다
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
