"""mark resources of already torn-down apps as deleted

내리기 콜백에서 자원을 deleted로 바꾸기 전에 내린 앱(10/2 첫 실제 테스트)의 기록을 맞춘다.
데이터만 바꾸고 칸은 바꾸지 않는다.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-02
"""
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE deployment_resources SET state = 'deleted', reason = NULL
        WHERE deployment_id IN (
            SELECT d.id FROM deployments d JOIN app_spaces a ON a.id = d.app_space_id
            WHERE a.teardown_status = 'success' AND d.created_at < a.teardown_requested_at
        )
        """
    )


def downgrade() -> None:
    # 지워진 자원을 "완료"로 되돌리지 않는다
    pass
