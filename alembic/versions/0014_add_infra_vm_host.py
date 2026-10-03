"""add infra_spaces.vm_host (on-prem service VM host for the VM deploy workflow)

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-03
"""
from alembic import op
import sqlalchemy as sa

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("infra_spaces", sa.Column("vm_host", sa.String(253), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("infra_spaces") as batch:
        batch.drop_column("vm_host")
