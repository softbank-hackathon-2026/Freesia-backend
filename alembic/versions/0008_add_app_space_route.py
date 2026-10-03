"""add app_spaces.route_path and alb_rule_priority (shared ALB routing)

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-03
"""
from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("app_spaces", sa.Column("route_path", sa.String(100), nullable=True))
    op.add_column("app_spaces", sa.Column("alb_rule_priority", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("app_spaces") as batch:
        batch.drop_column("alb_rule_priority")
        batch.drop_column("route_path")
