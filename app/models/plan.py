"""구성안: 템플릿 하나와 거기에 넣을 값 (ADR-012 Option B, API 명세 8절). 워크플로가 plan_id로 받아 간다."""
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Plan(Base):
    __tablename__ = "plans"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    app_space_id: Mapped[str] = mapped_column(String(32), ForeignKey("app_spaces.id"))
    # 근거가 된 AI 분석. 분석 없이 만든 구성안이면 비어 있다
    analysis_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("analyses.id"))
    compute: Mapped[str] = mapped_column(String(20))
    # 배포 레포 templates/ 아래 폴더 이름. 예: ecs-fargate/basic
    template: Mapped[str] = mapped_column(String(100))
    # 템플릿에 넣을 값. 범위를 검사하고 빠진 값은 기본값으로 채운 뒤 저장한다
    values: Mapped[dict] = mapped_column(JSON)
    name: Mapped[str] = mapped_column(String(100))
    summary: Mapped[str] = mapped_column(String(500))
    pros: Mapped[list[str]] = mapped_column(JSON)
    cons: Mapped[list[str]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
