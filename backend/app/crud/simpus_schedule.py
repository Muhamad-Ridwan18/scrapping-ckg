import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crud.cron_config import compute_next_run_at
from app.models.simpus_schedule import SimpusSchedule


def get_by_puskesmas(db: Session, puskesmas_id: uuid.UUID) -> SimpusSchedule | None:
    return db.scalar(
        select(SimpusSchedule).where(SimpusSchedule.puskesmas_id == puskesmas_id)
    )


def upsert(
    db: Session,
    *,
    puskesmas_id: uuid.UUID,
    hour: int,
    minute: int,
    lookback_days: int,
    enabled: bool,
    create_new: bool,
) -> SimpusSchedule:
    obj = get_by_puskesmas(db, puskesmas_id)
    if obj is None:
        obj = SimpusSchedule(
            puskesmas_id=puskesmas_id,
            hour=hour,
            minute=minute,
            lookback_days=lookback_days,
            enabled=enabled,
            create_new=create_new,
            next_run_at=compute_next_run_at(hour, minute),
        )
        db.add(obj)
    else:
        obj.hour = hour
        obj.minute = minute
        obj.lookback_days = lookback_days
        obj.enabled = enabled
        obj.create_new = create_new
        obj.next_run_at = compute_next_run_at(hour, minute)
    db.commit()
    db.refresh(obj)
    return obj
