"""배포 기록, 진행 단계, 자원별 상태 (ADR-009, API 명세 9절)."""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Deployment(Base):
    __tablename__ = "deployments"

    # AWS DeploymentId 태그와 같은 값 (ADR-005)
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    app_space_id: Mapped[str] = mapped_column(String(32), ForeignKey("app_spaces.id"))
    compute: Mapped[str] = mapped_column(String(20))
    # plans 테이블이 생기면 FK를 건다 (API 명세 8절)
    plan_id: Mapped[str | None] = mapped_column(String(32))
    commit_sha: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20))
    step: Mapped[str] = mapped_column(String(20))
    url: Mapped[str | None] = mapped_column(String(500))
    reason: Mapped[str | None] = mapped_column(String(1000))
    # GitHub Actions 실행 ID. 첫 콜백(prepare)으로 받는다
    run_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DeploymentEvent(Base):
    """SSE로 보내는 진행 이벤트. 다시 연결하면 seq 다음부터 보낸다."""

    __tablename__ = "deployment_events"
    __table_args__ = (UniqueConstraint("deployment_id", "seq", name="uq_deployment_events_deployment_id_seq"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    deployment_id: Mapped[str] = mapped_column(String(32), ForeignKey("deployments.id"))
    seq: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20))
    step: Mapped[str] = mapped_column(String(20))
    message: Mapped[str] = mapped_column(String(500))
    progress: Mapped[int] = mapped_column(Integer)
    url: Mapped[str | None] = mapped_column(String(500))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DeploymentResource(Base):
    """트리용 자원별 상태. 워크플로가 보낸 address 기준으로 덮어쓴다."""

    __tablename__ = "deployment_resources"

    deployment_id: Mapped[str] = mapped_column(String(32), ForeignKey("deployments.id"), primary_key=True)
    # Terraform 자원 주소. 예: aws_lb.app
    address: Mapped[str] = mapped_column(String(500), primary_key=True)
    # 처음 받은 순서 (화면에 같은 순서로 보여 주기 위함)
    position: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(20))
    state: Mapped[str] = mapped_column(String(20))
    reason: Mapped[str | None] = mapped_column(String(1000))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
