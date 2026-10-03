"""add infra_spaces.is_default (DefaultInfra tag: the infra used when none is chosen)

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-03
"""
from alembic import op
import sqlalchemy as sa

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("infra_spaces", sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table("infra_spaces") as batch:
        batch.drop_column("is_default")
