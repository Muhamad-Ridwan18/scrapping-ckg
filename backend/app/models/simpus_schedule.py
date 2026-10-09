import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import SoftDeleteMixin, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from app.models.puskesmas import Puskesmas


class SimpusSchedule(UUIDPKMixin, TimestampMixin, SoftDeleteMixin, Base):
    """Daily automatic pull of GET /api/v1/ckg/jawaban.

    Separate from CronConfig: that schedule scrapes ePus and ASIK, then merges.
    This one only calls the SIMPUS API. Manual "Tarik jawaban" stays available.
    """

    __tablename__ = "simpus_schedules"

    puskesmas_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("puskesmas.id"),
        nullable=False,
    )
    hour: Mapped[int] = mapped_column(Integer, nullable=False)
    minute: Mapped[int] = mapped_column(Integer, nullable=False)
    lookback_days: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # When true, the daily pull also registers CKG umum visits that are not yet
    # in ASIK (name from SIMPUS, domicile from the puskesmas default address),
    # then fills the exam. Off until an admin opts in.
    create_new: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_fired_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    puskesmas: Mapped["Puskesmas"] = relationship()
