import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_admin_id, get_db
from app.crud import simpus_schedule as crud
from app.models.puskesmas import Puskesmas
from app.schemas.simpus_schedule import SimpusScheduleIn, SimpusScheduleOut

router = APIRouter(tags=["simpus-schedule"])


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
    if body.create_new and (not asik_url or not has_asik or not asik_alamat):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "URL ASIK, akun ASIK, dan alamat default harus diisi sebelum mendaftarkan pasien baru",
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
