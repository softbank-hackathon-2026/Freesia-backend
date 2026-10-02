"""add app_spaces.deleted_at (app delete hides the app)

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-02
"""
from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("app_spaces", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("app_spaces") as batch:
        batch.drop_column("deleted_at")
