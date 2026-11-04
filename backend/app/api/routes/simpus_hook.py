"""Inbound hook from kliksimpus — trigger a SIMPUS jawaban import.

SIMPUS POSTs here when a petugas clicks "Kirim ke CKG". Auth is a shared
Bearer (default in settings.SIMPUS_HOOK_BEARER; override via env). Public
prod URL:

  https://api-ckg.kliksimpus.com/hooks/simpus

Local SIMPUS points its Setting → Alamat CKG at this process, e.g.
http://127.0.0.1:8001/hooks/simpus
"""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, date, datetime
from typing import Literal
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, load_only

from app.api.deps import get_db
from app.celery_app import celery_app
from app.config import settings
from app.crud import scrape_job as scrape_job_crud
from app.models.patient import Patient
from app.models.puskesmas import Puskesmas
from app.models.scrape_job import ScrapeJob, ScrapeKind, ScrapeStatus, TriggererType
from app.models.sync_job import SyncJob, SyncStatus

router = APIRouter(prefix="/hooks/simpus", tags=["simpus-hook"])
_bearer = HTTPBearer(auto_error=False)
_INTEGRATION_ACTOR_ID = uuid.UUID(int=0)

# Default shared with kliksimpus; runtime value is settings.SIMPUS_HOOK_BEARER.
SIMPUS_HOOK_BEARER_DEFAULT = "ckg-simpus-hook-kliksimpus-shared-2026"
SIMPUS_APP_ORIGIN = "https://app.kliksimpus.com"


class SimpusHookIn(BaseModel):
    simpus_url: str = Field(min_length=1)
    nik: str | None = None
    tanggal: date | None = None
    tanggal_dari: date | None = None
    tanggal_sampai: date | None = None
    jenis: Literal["umum", "sekolah"] | None = None
    create_new: bool = False
    # Multi-tenant: app.kliksimpus.com is shared, so SIMPUS also sends the
    # clinic name to pick the matching puskesmas row.
    nama_instansi: str | None = None

    @model_validator(mode="after")
    def _normalize(self) -> "SimpusHookIn":
        if self.nik is not None:
            digits = "".join(ch for ch in self.nik if ch.isdigit())
            self.nik = digits or None
            if self.nik is not None and len(self.nik) != 16:
                raise ValueError("nik harus 16 digit")
        if (
            self.tanggal_dari
            and self.tanggal_sampai
            and self.tanggal_dari > self.tanggal_sampai
        ):
            raise ValueError("tanggal_dari harus sebelum atau sama dengan tanggal_sampai")
        if self.tanggal and (self.tanggal_dari or self.tanggal_sampai):
            raise ValueError("kirim tanggal ATAU tanggal_dari+tanggal_sampai, bukan keduanya")
        if bool(self.tanggal_dari) != bool(self.tanggal_sampai):
            raise ValueError("tanggal_dari dan tanggal_sampai harus diisi bersamaan")
        if self.nama_instansi is not None:
            self.nama_instansi = self.nama_instansi.strip() or None
        return self


class SimpusHookAccepted(BaseModel):
    job_id: uuid.UUID
    status: Literal["pending"] = "pending"


class SimpusHookNikResult(BaseModel):
    nik: str | None = None
    status: Literal["pending", "success", "failed", "cancelled"]
    error_message: str | None = None


class SimpusHookJobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    job_id: uuid.UUID
    status: Literal["pending", "success", "failed", "cancelled"]
    error_message: str | None = None
    results: list[SimpusHookNikResult]


class SimpusNikStatusIn(BaseModel):
    """Batch lookup of latest ASIK sync-job status per NIK (for SIMPUS laporan)."""

    simpus_url: str = Field(min_length=1)
    nama_instansi: str | None = None
    niks: list[str] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def _normalize_niks(self) -> "SimpusNikStatusIn":
        cleaned: list[str] = []
        seen: set[str] = set()
        for raw in self.niks:
            digits = "".join(ch for ch in str(raw) if ch.isdigit())
            if len(digits) != 16 or digits in seen:
                continue
            seen.add(digits)
            cleaned.append(digits)
        if not cleaned:
            raise ValueError("niks harus berisi minimal satu NIK 16 digit")
        self.niks = cleaned
        if self.nama_instansi is not None:
            self.nama_instansi = self.nama_instansi.strip() or None
        return self


class SimpusNikStatusItem(BaseModel):
    nik: str
    status: Literal["pending", "success", "failed", "unknown"]
    error_message: str | None = None
    sync_job_id: uuid.UUID | None = None
    finished_at: datetime | None = None


class SimpusNikStatusOut(BaseModel):
    items: list[SimpusNikStatusItem]


def _require_simpus_hook_bearer(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> None:
    expected = settings.SIMPUS_HOOK_BEARER
    if (
        not expected
        or credentials is None
        or credentials.scheme.lower() != "bearer"
        or not secrets.compare_digest(credentials.credentials, expected)
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid bearer token")


def _origin(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = "https://" + raw
    parsed = urlparse(raw)
    if not parsed.netloc:
        return ""
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}".rstrip("/")


def _find_puskesmas(
    db: Session, *, simpus_url: str, nama_instansi: str | None
) -> Puskesmas:
    origin = _origin(simpus_url) or _origin(SIMPUS_APP_ORIGIN)
    rows = list(
        db.scalars(
            select(Puskesmas)
            .options(
                load_only(
                    Puskesmas.id,
                    Puskesmas.name,
                    Puskesmas.simpus_api_url,
                    Puskesmas.simpus_api_cred,
                    Puskesmas.asik_url,
                    Puskesmas.asik_cred,
                    Puskesmas.asik_default_alamat,
                )
            )
            .where(
                Puskesmas.simpus_api_url.is_not(None),
                Puskesmas.simpus_api_cred.is_not(None),
            )
        ).all()
    )
    by_url = [p for p in rows if _origin(p.simpus_api_url or "") == origin]
    candidates = by_url or rows

    if nama_instansi:
        name_key = nama_instansi.casefold()
        named = [p for p in candidates if (p.name or "").casefold() == name_key]
        if len(named) == 1:
            return named[0]
        if not named:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                f"Puskesmas '{nama_instansi}' tidak ditemukan / belum punya akun SIMPUS",
            )
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Lebih dari satu puskesmas bernama '{nama_instansi}'",
        )

    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "Tidak ada puskesmas dengan akun SIMPUS",
        )
    raise HTTPException(
        status.HTTP_400_BAD_REQUEST,
        "simpus_url dipakai banyak puskesmas — kirim nama_instansi",
    )


@router.post(
    "",
    response_model=SimpusHookAccepted,
    status_code=status.HTTP_201_CREATED,
)
def trigger_simpus_import(
    body: SimpusHookIn,
    db: Session = Depends(get_db),
    _: None = Depends(_require_simpus_hook_bearer),
) -> SimpusHookAccepted:
    puskesmas = _find_puskesmas(
        db, simpus_url=body.simpus_url, nama_instansi=body.nama_instansi
    )
    if not puskesmas.simpus_api_url or puskesmas.simpus_api_cred is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "URL atau token SIMPUS belum diisi di CKG",
        )
    if body.create_new:
        missing = [
            label
            for label, ok in (
                ("URL ASIK", puskesmas.asik_url),
                ("akun ASIK", puskesmas.asik_cred is not None),
                ("alamat default", puskesmas.asik_default_alamat),
            )
            if not ok
        ]
        if missing:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"{', '.join(missing)} harus diisi sebelum mendaftarkan pasien baru",
            )

    today = datetime.now(UTC).date()
    tanggal = body.tanggal
    tanggal_dari = body.tanggal_dari
    tanggal_sampai = body.tanggal_sampai
    if tanggal is None and tanggal_dari is None:
        tanggal = today

    date_filter = None
    if tanggal is not None:
        date_filter = tanggal.isoformat()
    elif tanggal_dari is not None:
        date_filter = tanggal_dari.isoformat()

    try:
        job = scrape_job_crud.create(
            db,
            puskesmas_id=puskesmas.id,
            kind=ScrapeKind.SIMPUS,
            date_filter=date_filter,
            triggered_by_id=_INTEGRATION_ACTOR_ID,
            triggered_by_type=TriggererType.INTEGRATION,
            target_nik=body.nik,
        )
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "impor SIMPUS masih berjalan untuk puskesmas ini",
        ) from e

    try:
        celery_app.send_task(
            "simpus.import_jawaban",
            args=[str(job.id)],
            kwargs={
                "jenis": body.jenis,
                "nik": body.nik,
                "tanggal": tanggal.isoformat() if tanggal else None,
                "tanggal_dari": tanggal_dari.isoformat() if tanggal_dari else None,
                "tanggal_sampai": tanggal_sampai.isoformat() if tanggal_sampai else None,
                "create_new": body.create_new,
            },
        )
    except Exception as e:
        scrape_job_crud.mark_failed(
            db, job, f"broker unreachable: {e}"[:2000], datetime.now(UTC)
        )
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "scrape broker unavailable",
        ) from e

    return SimpusHookAccepted(job_id=job.id)


@router.get("/jobs/{job_id}", response_model=SimpusHookJobOut)
def get_simpus_hook_job(
    job_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: None = Depends(_require_simpus_hook_bearer),
) -> SimpusHookJobOut:
    job = db.scalar(
        select(ScrapeJob)
        .options(
            load_only(
                ScrapeJob.id,
                ScrapeJob.kind,
                ScrapeJob.status,
                ScrapeJob.target_nik,
                ScrapeJob.error_message,
                ScrapeJob.triggered_by_type,
            )
        )
        .where(ScrapeJob.id == job_id, ScrapeJob.kind == ScrapeKind.SIMPUS)
    )
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job tidak ditemukan")

    status_map = {
        ScrapeStatus.PENDING: "pending",
        ScrapeStatus.RUNNING: "pending",
        ScrapeStatus.SUCCESS: "success",
        ScrapeStatus.FAILED: "failed",
        ScrapeStatus.CANCELLED: "cancelled",
    }
    mapped = status_map[job.status]
    nik = job.target_nik
    results: list[SimpusHookNikResult] = []
    if nik:
        results.append(
            SimpusHookNikResult(
                nik=nik,
                status=mapped,  # type: ignore[arg-type]
                error_message=job.error_message if mapped == "failed" else None,
            )
        )
    elif mapped in ("success", "failed", "cancelled"):
        results.append(
            SimpusHookNikResult(
                nik=None,
                status=mapped,  # type: ignore[arg-type]
                error_message=job.error_message if mapped == "failed" else None,
            )
        )

    return SimpusHookJobOut(
        job_id=job.id,
        status=mapped,  # type: ignore[arg-type]
        error_message=job.error_message if mapped == "failed" else None,
        results=results,
    )


def _map_sync_status(status_val: SyncStatus) -> Literal["pending", "success", "failed"]:
    if status_val is SyncStatus.SUCCESS:
        return "success"
    if status_val in (SyncStatus.FAILED, SyncStatus.CANCELLED):
        return "failed"
    return "pending"


@router.post("/nik-status", response_model=SimpusNikStatusOut)
def simpus_nik_sync_status(
    body: SimpusNikStatusIn,
    db: Session = Depends(get_db),
    _: None = Depends(_require_simpus_hook_bearer),
) -> SimpusNikStatusOut:
    """Return the latest ASIK sync-job status for each NIK at this puskesmas.

    SIMPUS laporan uses this to flip local ``ckg_kirim_logs`` to success when
    admin sync-jobs already succeeded (avoids stuck Gagal/Belum after 409).
    """
    puskesmas = _find_puskesmas(
        db, simpus_url=body.simpus_url, nama_instansi=body.nama_instansi
    )
    patients = list(
        db.scalars(
            select(Patient)
            .options(load_only(Patient.id, Patient.nik))
            .where(
                Patient.puskesmas_id == puskesmas.id,
                Patient.nik.in_(body.niks),
            )
        ).all()
    )
    by_nik: dict[str, list[uuid.UUID]] = {nik: [] for nik in body.niks}
    for p in patients:
        by_nik.setdefault(p.nik, []).append(p.id)

    patient_ids = [p.id for p in patients]
    latest_by_patient: dict[uuid.UUID, SyncJob] = {}
    if patient_ids:
        latest_subq = (
            select(
                SyncJob.patient_id.label("patient_id"),
                func.max(SyncJob.created_at).label("max_created"),
            )
            .where(SyncJob.patient_id.in_(patient_ids))
            .group_by(SyncJob.patient_id)
            .subquery()
        )
        rows = db.scalars(
            select(SyncJob)
            .options(
                load_only(
                    SyncJob.id,
                    SyncJob.patient_id,
                    SyncJob.status,
                    SyncJob.error_message,
                    SyncJob.finished_at,
                    SyncJob.created_at,
                )
            )
            .join(
                latest_subq,
                (SyncJob.patient_id == latest_subq.c.patient_id)
                & (SyncJob.created_at == latest_subq.c.max_created),
            )
        ).all()
        for job in rows:
            latest_by_patient[job.patient_id] = job

    items: list[SimpusNikStatusItem] = []
    for nik in body.niks:
        candidates = [
            latest_by_patient[pid]
            for pid in by_nik.get(nik, [])
            if pid in latest_by_patient
        ]
        if not candidates:
            items.append(SimpusNikStatusItem(nik=nik, status="unknown"))
            continue
        best = max(candidates, key=lambda j: j.created_at or datetime.min.replace(tzinfo=UTC))
        mapped = _map_sync_status(best.status)
        items.append(
            SimpusNikStatusItem(
                nik=nik,
                status=mapped,
                error_message=best.error_message if mapped == "failed" else None,
                sync_job_id=best.id,
                finished_at=best.finished_at or best.created_at,
            )
        )
    return SimpusNikStatusOut(items=items)
