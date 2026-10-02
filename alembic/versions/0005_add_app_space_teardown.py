"""add app_spaces teardown columns

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-02
"""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("app_spaces", sa.Column("teardown_status", sa.String(20), nullable=True))
    op.add_column("app_spaces", sa.Column("teardown_requested_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("app_spaces", sa.Column("teardown_finished_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("app_spaces", sa.Column("teardown_reason", sa.String(1000), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("app_spaces") as batch:
        batch.drop_column("teardown_reason")
        batch.drop_column("teardown_finished_at")
        batch.drop_column("teardown_requested_at")
        batch.drop_column("teardown_status")
