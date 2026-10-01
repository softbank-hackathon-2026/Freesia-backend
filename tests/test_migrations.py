"""마이그레이션이 DATABASE_URL을 그대로 읽는지 확인한다."""
from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine

from app.config import get_settings
from app.db import Base

ROOT = Path(__file__).resolve().parent.parent


def test_migrations_match_models(tmp_path, monkeypatch):
    # 모델을 바꾸고 마이그레이션을 빠뜨리면 서버 DB와 코드가 달라진다
    url = f"sqlite:///{tmp_path}/check.db"
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    try:
        command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
    finally:
        get_settings.cache_clear()
    with create_engine(url).connect() as conn:
        assert compare_metadata(MigrationContext.configure(conn), Base.metadata) == []


def test_upgrade_accepts_percent_encoded_url(tmp_path, monkeypatch):
    # 비밀번호 예약 문자를 URL 인코딩하면 %가 들어간다 (예: @ → %40).
    # Alembic 설정은 %를 특수 기호로 읽으므로 그대로 넘기면 마이그레이션이 실패한다.
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/p%40ss.db")
    get_settings.cache_clear()
    try:
        command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
    finally:
        get_settings.cache_clear()
    assert (tmp_path / "p@ss.db").exists()
