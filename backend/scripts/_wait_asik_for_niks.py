"""Wait until latest SyncJob for each NIK reaches a terminal status."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import load_only

from app.database import SessionLocal
from app.models.patient import Patient
from app.models.sync_job import SyncJob, SyncStatus

NIKS = [
    "9110086810950001",  # LUDIA KENI
    "9110071111830001",  # NIKANDER WEPPE
]

started = datetime.now(UTC) - timedelta(minutes=5)
deadline = time.time() + 1800
done: dict[str, dict] = {}

while time.time() < deadline and len(done) < len(NIKS):
    db = SessionLocal()
    try:
        for nik in NIKS:
            if nik in done:
                continue
            patient = db.scalar(
                select(Patient)
                .options(load_only(Patient.id, Patient.nik, Patient.nama, Patient.from_simpus))
                .where(Patient.nik == nik, Patient.from_simpus.is_(True))
                .order_by(Patient.created_at.desc())
            )
            if patient is None:
                print(f"{nik}: no patient yet")
                continue
            job = db.scalar(
                select(SyncJob)
                .options(
                    load_only(
                        SyncJob.id,
                        SyncJob.status,
                        SyncJob.error_message,
                        SyncJob.created_at,
                        SyncJob.finished_at,
                        SyncJob.patient_id,
                    )
                )
                .where(SyncJob.patient_id == patient.id, SyncJob.created_at >= started)
                .order_by(SyncJob.created_at.desc())
            )
            if job is None:
                print(f"{nik} ({patient.nama}): waiting sync job…")
                continue
            err = (job.error_message or "")[:200]
            print(f"{nik} ({patient.nama}): {job.status} {err!r}")
            if job.status in (SyncStatus.SUCCESS, SyncStatus.FAILED, SyncStatus.CANCELLED):
                done[nik] = {
                    "nik": nik,
                    "nama": patient.nama,
                    "patient_id": str(patient.id),
                    "sync_id": str(job.id),
                    "status": str(job.status),
                    "error": err,
                }
    finally:
        db.close()
    if len(done) < len(NIKS):
        time.sleep(15)

print("DONE", json.dumps(list(done.values()), ensure_ascii=False, indent=2))
Path = __import__("pathlib").Path
Path("/tmp/wait_asik_niks.json").write_text(json.dumps(list(done.values()), indent=2), encoding="utf-8")
ok = all(v.get("status") == "SyncStatus.SUCCESS" for v in done.values()) and len(done) == len(NIKS)
raise SystemExit(0 if ok else 1)
