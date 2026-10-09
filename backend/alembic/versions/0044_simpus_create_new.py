"""SIMPUS schedule can register patients who are not yet in ASIK

Revision ID: 0044_simpus_create_new
Revises: 0043_patient_from_simpus
Create Date: 2026-10-09

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0044_simpus_create_new"
down_revision: str | None = "0043_patient_from_simpus"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "simpus_schedules",
        sa.Column(
            "create_new",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("simpus_schedules", "create_new")
