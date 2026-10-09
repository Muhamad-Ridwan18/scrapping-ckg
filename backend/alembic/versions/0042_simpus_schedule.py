"""Daily schedule for the SIMPUS jawaban import

Revision ID: 0042_simpus_schedule
Revises: 0041_simpus_api
Create Date: 2026-10-09

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0042_simpus_schedule"
down_revision: str | None = "0041_simpus_api"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "simpus_schedules",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("puskesmas_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("hour", sa.Integer(), nullable=False),
        sa.Column("minute", sa.Integer(), nullable=False),
        sa.Column("lookback_days", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_fired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["puskesmas_id"], ["puskesmas.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_simpus_schedules_deleted_at", "simpus_schedules", ["deleted_at"])
    op.execute(
        "CREATE UNIQUE INDEX uq_simpus_schedules_per_pk ON simpus_schedules "
        "(puskesmas_id) WHERE deleted_at IS NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_simpus_schedules_per_pk")
    op.drop_index("ix_simpus_schedules_deleted_at", table_name="simpus_schedules")
    op.drop_table("simpus_schedules")
