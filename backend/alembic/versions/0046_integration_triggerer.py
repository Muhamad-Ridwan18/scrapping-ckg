"""Add integration as a job trigger type.

Revision ID: 0046_integration_triggerer
Revises: 0045_simpus_cron_run
Create Date: 2026-11-02

"""
from collections.abc import Sequence

from alembic import op

revision: str = "0046_integration_triggerer"
down_revision: str | None = "0045_simpus_cron_run"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE triggerer_type ADD VALUE IF NOT EXISTS 'integration'")


def downgrade() -> None:
    # PostgreSQL enum values cannot be removed safely; older code ignores this value
    # unless an integration-triggered job is present.
    pass
