"""create infra_spaces, app_spaces, deployments and seed infra

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-02
"""
from datetime import datetime, timedelta, timezone

from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

SEEDED_AT = datetime(2026, 10, 2, tzinfo=timezone.utc)

# 목록은 created_at 순서라 지금 화면 순서(퍼블릭 → 내부 → 고가용성)대로 1초씩 차이를 둔다.
# 지금까지 가짜 데이터로 보여 주던 3개를 그대로 옮긴다 (프론트가 이 id를 쓰고 있다).
# 퍼블릭 중심형은 박준서 님이 공유한 실제 값(10/1)이다. 나머지 둘은 구축되면 값을 채운다.
INFRA_SEED = [
    {
        "id": "sbh-workload-demo-vpc-public01",
        "name": "공개 웹 서비스용",
        "description": "인터넷에서 바로 접속하는 웹 서비스. 퍼블릭 서브넷 + ALB",
        "network": "public",
        "computes": ["ecs-fargate", "lambda", "ec2"],
        "status": "ready",
        "aws_account_id": None,
        "region": "ap-northeast-2",
        "vpc_id": "vpc-0c7ca2fe59980fcea",
        "public_subnet_ids": ["subnet-06316627ef6ec5610", "subnet-0c417695633bf550f"],
        "private_subnet_ids": [],
        "created_at": SEEDED_AT,
    },
    {
        "id": "sbh-workload-demo-vpc-private01",
        "name": "내부 API용",
        "description": "외부 노출 없이 내부에서만 쓰는 API. 프라이빗 서브넷",
        "network": "private",
        "computes": ["ecs-fargate", "lambda"],
        "status": "preparing",
        "aws_account_id": None,
        "region": None,
        "vpc_id": None,
        "public_subnet_ids": None,
        "private_subnet_ids": None,
        "created_at": SEEDED_AT + timedelta(seconds=1),
    },
    {
        "id": "sbh-workload-demo-vpc-ha01",
        "name": "고가용성 서비스용",
        "description": "멀티 AZ로 장애에 강한 구성",
        "network": "ha",
        "computes": ["ecs-fargate", "ec2"],
        "status": "preparing",
        "aws_account_id": None,
        "region": None,
        "vpc_id": None,
        "public_subnet_ids": None,
        "private_subnet_ids": None,
        "created_at": SEEDED_AT + timedelta(seconds=2),
    },
]


def upgrade() -> None:
    infra = op.create_table(
        "infra_spaces",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.String(length=300), nullable=False),
        sa.Column("network", sa.String(length=20), nullable=False),
        sa.Column("computes", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("aws_account_id", sa.String(length=12), nullable=True),
        sa.Column("region", sa.String(length=30), nullable=True),
        sa.Column("vpc_id", sa.String(length=32), nullable=True),
        sa.Column("public_subnet_ids", sa.JSON(), nullable=True),
        sa.Column("private_subnet_ids", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.bulk_insert(infra, INFRA_SEED)

    op.create_table(
        "app_spaces",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column(
            "repository_id", sa.String(length=32), sa.ForeignKey("repositories.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("repo_url", sa.String(length=300), nullable=False),
        sa.Column("branch", sa.String(length=255), nullable=False),
        sa.Column("infra_id", sa.String(length=64), sa.ForeignKey("infra_spaces.id"), nullable=False),
        sa.Column("latest_deployment_id", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "deployments",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("app_space_id", sa.String(length=32), sa.ForeignKey("app_spaces.id"), nullable=False),
        sa.Column("compute", sa.String(length=20), nullable=False),
        sa.Column("plan_id", sa.String(length=32), nullable=True),
        sa.Column("commit_sha", sa.String(length=40), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("step", sa.String(length=20), nullable=False),
        sa.Column("url", sa.String(length=500), nullable=True),
        sa.Column("reason", sa.String(length=1000), nullable=True),
        sa.Column("run_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "deployment_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("deployment_id", sa.String(length=32), sa.ForeignKey("deployments.id"), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("step", sa.String(length=20), nullable=False),
        sa.Column("message", sa.String(length=500), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("url", sa.String(length=500), nullable=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("deployment_id", "seq", name="uq_deployment_events_deployment_id_seq"),
    )

    op.create_table(
        "deployment_resources",
        sa.Column(
            "deployment_id", sa.String(length=32), sa.ForeignKey("deployments.id"), primary_key=True, nullable=False
        ),
        sa.Column("address", sa.String(length=500), primary_key=True, nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(length=100), nullable=False),
        sa.Column("action", sa.String(length=20), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("reason", sa.String(length=1000), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("deployment_resources")
    op.drop_table("deployment_events")
    op.drop_table("deployments")
    op.drop_table("app_spaces")
    op.drop_table("infra_spaces")
