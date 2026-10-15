import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_admin_id, get_db
from app.crud import cron_run as cron_run_crud
from app.crud import simpus_schedule as crud
from app.models.cron_config import CronSourceScope
from app.models.cron_run import CronRun, CronRunStatus
from app.models.puskesmas import Puskesmas
from app.models.scrape_job import TriggererType
from app.schemas.cron_run import CronRunOut, cron_run_to_out
from app.schemas.simpus_schedule import SimpusScheduleIn, SimpusScheduleOut
from app.tasks.simpus_import import open_simpus_run, schedule_window

router = APIRouter(tags=["simpus-schedule"])

_JKT = ZoneInfo("Asia/Jakarta")


@router.get(
    "/puskesmas/{puskesmas_id}/simpus-schedule",
    response_model=SimpusScheduleOut,
)
def get_simpus_schedule(
    puskesmas_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: uuid.UUID = Depends(get_current_admin_id),
) -> SimpusScheduleOut:
    obj = crud.get_by_puskesmas(db, puskesmas_id)
    if obj is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Jadwal SIMPUS belum diatur")
    return SimpusScheduleOut.model_validate(obj)


@router.put(
    "/puskesmas/{puskesmas_id}/simpus-schedule",
    response_model=SimpusScheduleOut,
)
def put_simpus_schedule(
    puskesmas_id: uuid.UUID,
    body: SimpusScheduleIn,
    db: Session = Depends(get_db),
    _: uuid.UUID = Depends(get_current_admin_id),
) -> SimpusScheduleOut:
    row = db.execute(
        select(
            Puskesmas.simpus_api_url,
            Puskesmas.simpus_api_cred.isnot(None),
            Puskesmas.asik_url,
            Puskesmas.asik_cred.isnot(None),
            Puskesmas.asik_default_alamat,
        ).where(Puskesmas.id == puskesmas_id)
    ).one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Puskesmas not found")
    api_url, has_token, asik_url, has_asik, asik_alamat = row
    if body.enabled and (not api_url or not has_token):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "URL dan token SIMPUS harus diisi sebelum jadwal diaktifkan",
        )
    if body.create_new:
        missing = [
            label
            for label, ok in (
                ("URL ASIK", asik_url),
                ("akun ASIK", has_asik),
                ("alamat default", asik_alamat),
            )
            if not ok
        ]
        if missing:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"{', '.join(missing)} harus diisi sebelum mendaftarkan pasien baru",
            )
    obj = crud.upsert(
        db,
        puskesmas_id=puskesmas_id,
        hour=body.hour,
        minute=body.minute,
        lookback_days=body.lookback_days,
        enabled=body.enabled,
        create_new=body.create_new,
    )
    return SimpusScheduleOut.model_validate(obj)


def _require_simpus_ready(
    db: Session, puskesmas_id: uuid.UUID, *, create_new: bool
) -> str:
    row = db.execute(
        select(
            Puskesmas.name,
            Puskesmas.simpus_api_url,
            Puskesmas.simpus_api_cred.isnot(None),
            Puskesmas.asik_url,
            Puskesmas.asik_cred.isnot(None),
            Puskesmas.asik_default_alamat,
        ).where(Puskesmas.id == puskesmas_id)
    ).one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Puskesmas not found")
    name, api_url, has_token, asik_url, has_asik, asik_alamat = row
    if not api_url or not has_token:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "URL dan token SIMPUS harus diisi sebelum jadwal dijalankan",
        )
    if create_new:
        missing = [
            label
            for label, ok in (
                ("URL ASIK", asik_url),
                ("akun ASIK", has_asik),
                ("alamat default", asik_alamat),
            )
            if not ok
        ]
        if missing:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"{', '.join(missing)} harus diisi sebelum mendaftarkan pasien baru",
            )
    return name


@router.delete(
    "/puskesmas/{puskesmas_id}/simpus-schedule",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_simpus_schedule(
    puskesmas_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: uuid.UUID = Depends(get_current_admin_id),
) -> None:
    obj = crud.get_by_puskesmas(db, puskesmas_id)
    if obj is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Jadwal SIMPUS belum diatur")
    crud.soft_delete(db, obj)
    runs = list(
        db.scalars(
            select(CronRun).where(
                CronRun.puskesmas_id == puskesmas_id,
                CronRun.source_scope == CronSourceScope.SIMPUS,
                CronRun.status.in_((CronRunStatus.PENDING, CronRunStatus.RUNNING)),
            )
        ).all()
    )
    for run in runs:
        cron_run_crud.cancel_cron_run_and_children(db, run)


@router.post(
    "/puskesmas/{puskesmas_id}/simpus-schedule/run-now",
    response_model=CronRunOut,
    status_code=status.HTTP_201_CREATED,
)
def run_simpus_schedule_now(
    puskesmas_id: uuid.UUID,
    db: Session = Depends(get_db),
    admin_id: uuid.UUID = Depends(get_current_admin_id),
) -> CronRunOut:
    obj = crud.get_by_puskesmas(db, puskesmas_id)
    if obj is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Jadwal SIMPUS belum diatur")
    name = _require_simpus_ready(db, puskesmas_id, create_new=obj.create_new)
    today = datetime.now(_JKT).date()
    date_from, date_to = schedule_window(today, obj.lookback_days)
    try:
        run = open_simpus_run(
            db,
            puskesmas_id=puskesmas_id,
            date_from=date_from,
            date_to=date_to,
            create_new=obj.create_new,
            triggered_by_id=admin_id,
            triggered_by_type=TriggererType.ADMIN,
        )
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "impor SIMPUS masih berjalan untuk puskesmas ini",
        ) from e
    except Exception as e:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "scrape broker unavailable"
        ) from e
    return cron_run_to_out(run, name)

