"""사용자가 만든 앱 = 등록된 저장소 하나 + 인프라 하나."""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class AppSpace(Base):
    __tablename__ = "app_spaces"

    # 배포 워크플로의 application_id, AWS ApplicationId 태그와 같은 값 (ADR-005, 24자 이하)
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    # 저장소 등록이 해제돼도 앱은 남는다. 어느 주소였는지는 repo_url·branch로 기억한다.
    repository_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("repositories.id", ondelete="SET NULL"))
    repo_url: Mapped[str] = mapped_column(String(300))
    branch: Mapped[str] = mapped_column(String(255))
    infra_id: Mapped[str] = mapped_column(String(64), ForeignKey("infra_spaces.id"))
    latest_deployment_id: Mapped[str | None] = mapped_column(String(32))
    # 배포 레포 Destroy 워크플로 실행을 요청한 시각. 끝났는지는 아직 알 수 없다(내리기 콜백 전)
    teardown_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
