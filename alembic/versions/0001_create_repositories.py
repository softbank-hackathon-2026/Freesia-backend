"""create repositories

Revision ID: 0001
Revises:
Create Date: 2026-10-01
"""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "repositories",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("owner", sa.String(length=100), nullable=False),
        sa.Column("repo", sa.String(length=100), nullable=False),
        sa.Column("repo_url", sa.String(length=300), nullable=False),
        sa.Column("branch", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("repo_url", "branch", name="uq_repositories_repo_url_branch"),
    )


def downgrade() -> None:
    op.drop_table("repositories")
