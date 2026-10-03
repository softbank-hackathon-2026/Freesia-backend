"""add infra_spaces.provider (aws / onprem / gcp / azure: where the infra was read from)

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-03
"""
from alembic import op
import sqlalchemy as sa

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 지금까지의 인프라는 모두 AWS에서 읽었다
    op.add_column("infra_spaces", sa.Column("provider", sa.String(20), nullable=False, server_default="aws"))


def downgrade() -> None:
    with op.batch_alter_table("infra_spaces") as batch:
        batch.drop_column("provider")
