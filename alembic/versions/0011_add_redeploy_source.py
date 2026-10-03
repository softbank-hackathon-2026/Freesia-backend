"""Add nullable redeployment source provenance.

Revision ID: 0011
Revises: 0010
"""
from alembic import op
import sqlalchemy as sa

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("deployments", sa.Column("source_deployment_id", sa.String(32), nullable=True))


def downgrade() -> None:
    # Only for disposable DB round-trip checks; production rollback keeps the column.
    with op.batch_alter_table("deployments") as batch:
        batch.drop_column("source_deployment_id")
