"""AI 분석 결과 (ADR-019·020·021, API 명세 7절). 앱마다 여러 번 할 수 있고 최신 것을 보여 준다."""
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Analysis(Base):
    __tablename__ = "analyses"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    app_space_id: Mapped[str] = mapped_column(String(32), ForeignKey("app_spaces.id"))
    # 추천 기준이 된 인프라. 앱의 인프라가 나중에 바뀌어도 어느 기준이었는지 남긴다 (ADR-020)
    infra_id: Mapped[str] = mapped_column(String(64))
    # 분석한 코드 버전. 배포 때 같은 커밋을 쓴다. 저장소를 못 읽었으면 비어 있다
    commit_sha: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20))
    # AI 응답 전체(Analysis). 형식이 아직 바뀌는 중이라 통째로 저장한다
    result: Mapped[dict | None] = mapped_column(JSON)
    # 사용한 Bedrock 모델. 모델 없이 샘플을 돌려준 경우 "sample"
    model_id: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
