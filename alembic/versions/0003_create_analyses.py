"""create analyses

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-02
"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "analyses",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("app_space_id", sa.String(length=32), sa.ForeignKey("app_spaces.id"), nullable=False),
        sa.Column("infra_id", sa.String(length=64), nullable=False),
        sa.Column("commit_sha", sa.String(length=40), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("model_id", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("analyses")
