"""create plans

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-02
"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "plans",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("app_space_id", sa.String(length=32), sa.ForeignKey("app_spaces.id"), nullable=False),
        sa.Column("analysis_id", sa.String(length=32), sa.ForeignKey("analyses.id"), nullable=True),
        sa.Column("compute", sa.String(length=20), nullable=False),
        sa.Column("template", sa.String(length=100), nullable=False),
        sa.Column("values", sa.JSON(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("summary", sa.String(length=500), nullable=False),
        sa.Column("pros", sa.JSON(), nullable=False),
        sa.Column("cons", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("plans")
