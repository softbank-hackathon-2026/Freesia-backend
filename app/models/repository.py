"""통합 메뉴에서 등록한 GitHub 저장소. 주소만 저장하고 코드는 저장하지 않는다."""
from datetime import datetime

from sqlalchemy import DateTime, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Repository(Base):
    __tablename__ = "repositories"
    __table_args__ = (UniqueConstraint("repo_url", "branch", name="uq_repositories_repo_url_branch"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    owner: Mapped[str] = mapped_column(String(100))
    repo: Mapped[str] = mapped_column(String(100))
    repo_url: Mapped[str] = mapped_column(String(300))
    branch: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    @property
    def name(self) -> str:
        return f"{self.owner}/{self.repo}"
