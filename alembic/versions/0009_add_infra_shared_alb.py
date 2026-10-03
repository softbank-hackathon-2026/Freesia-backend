"""add infra_spaces shared ALB values and app subnets (Multi-AZ shared-alb deploy)

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-03
"""
from alembic import op
import sqlalchemy as sa

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("infra_spaces", sa.Column("alb_listener_arn", sa.String(200), nullable=True))
    op.add_column("infra_spaces", sa.Column("alb_security_group_id", sa.String(32), nullable=True))
    op.add_column("infra_spaces", sa.Column("alb_base_url", sa.String(200), nullable=True))
    op.add_column("infra_spaces", sa.Column("app_subnet_ids", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("infra_spaces") as batch:
        batch.drop_column("app_subnet_ids")
        batch.drop_column("alb_base_url")
        batch.drop_column("alb_security_group_id")
        batch.drop_column("alb_listener_arn")
