"""SIMPUS jawaban pulls get their own cron run

Adds cron_source_scope/cron_step value 'simpus' and cron_runs.simpus_scrape_job_id
so a scheduled or run-now SIMPUS import shows up next to the EPUS/ASIK cron runs.
The partial unique index keeps one active SIMPUS run per puskesmas. It does not
join the EPUS or ASIK lane indexes.

Postgres enum ADD VALUE cannot run inside a transaction block.

Revision ID: 0045_simpus_cron_run
Revises: 0044_simpus_create_new
Create Date: 2026-10-15

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0045_simpus_cron_run"
down_revision: str | None = "0044_simpus_create_new"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE cron_source_scope ADD VALUE IF NOT EXISTS 'simpus'")
        op.execute("ALTER TYPE cron_step ADD VALUE IF NOT EXISTS 'simpus'")

    op.add_column(
        "cron_runs",
        sa.Column("simpus_scrape_job_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_cron_runs_simpus_scrape_job_id",
        "cron_runs",
        "scrape_jobs",
        ["simpus_scrape_job_id"],
        ["id"],
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_cron_runs_active_simpus ON cron_runs (puskesmas_id) "
        "WHERE deleted_at IS NULL AND status IN ('pending','running') "
        "AND source_scope = 'simpus'"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_cron_runs_active_simpus")
    op.drop_constraint("fk_cron_runs_simpus_scrape_job_id", "cron_runs", type_="foreignkey")
    op.drop_column("cron_runs", "simpus_scrape_job_id")
