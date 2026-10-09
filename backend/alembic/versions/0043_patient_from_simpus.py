"""Mark patients whose ASIK blob came from the SIMPUS jawaban import

Revision ID: 0043_patient_from_simpus
Revises: 0042_simpus_schedule
Create Date: 2026-10-09

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0043_patient_from_simpus"
down_revision: str | None = "0042_simpus_schedule"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "patients",
        sa.Column(
            "from_simpus",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("patients", "from_simpus")
