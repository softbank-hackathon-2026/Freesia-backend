"""마이그레이션이 DATABASE_URL을 그대로 읽는지 확인한다."""
from pathlib import Path

from alembic import command
from alembic.config import Config

from app.config import get_settings

ROOT = Path(__file__).resolve().parent.parent


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
