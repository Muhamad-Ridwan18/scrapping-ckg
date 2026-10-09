"""SIMPUS CKG API import: puskesmas URL + token, scrape kind simpus

Revision ID: 0041_simpus_api
Revises: 0040_loop_changes_ready
Create Date: 2026-10-09

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0041_simpus_api"
down_revision: str | None = "0040_loop_changes_ready"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The partial index below compares kind = 'simpus', so the new enum label
    # must be committed before that statement. ADD VALUE cannot run in the
    # same transaction that uses the value.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE scrape_kind ADD VALUE IF NOT EXISTS 'simpus'")
    op.add_column("puskesmas", sa.Column("simpus_api_url", sa.String(length=512), nullable=True))
    op.add_column("puskesmas", sa.Column("simpus_api_cred", sa.LargeBinary(), nullable=True))
    op.execute(
        "CREATE UNIQUE INDEX uq_scrape_jobs_active_simpus "
        "ON scrape_jobs (puskesmas_id) "
        "WHERE kind = 'simpus' AND status IN ('pending','running') AND deleted_at IS NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_scrape_jobs_active_simpus")
    op.drop_column("puskesmas", "simpus_api_cred")
    op.drop_column("puskesmas", "simpus_api_url")
