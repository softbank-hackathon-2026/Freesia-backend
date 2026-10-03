"""add deployment_resources.predicted (show the resource tree from the start of a deployment)

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-03
"""
from alembic import op
import sqlalchemy as sa

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "deployment_resources",
        sa.Column("predicted", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    with op.batch_alter_table("deployment_resources") as batch:
        batch.drop_column("predicted")
