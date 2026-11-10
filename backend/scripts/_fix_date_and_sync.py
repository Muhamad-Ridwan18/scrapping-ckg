"""After create picked exam day+1, align patient.filter_date and re-sync."""

from __future__ import annotations

import json
import time
import uuid
from datetime import UTC, date, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import load_only

from app.celery_app import celery_app
from app.database import SessionLocal
from app.models.patient import Patient
from app.models.puskesmas import Puskesmas
from app.models.sync_job import SyncJob, SyncStatus

sample = json.loads(Path("/tmp/ckg_e2e_sample.json").read_text(encoding="utf-8"))
started_at = datetime.now(UTC)

db = SessionLocal()
try:
    pk = db.scalar(
        select(Puskesmas)
        .options(load_only(Puskesmas.id, Puskesmas.name))
        .where(Puskesmas.name == sample["nama_instansi"])
    )
    patient = db.scalar(
        select(Patient)
        .options(
            load_only(
                Patient.id,
                Patient.nik,
                Patient.nama,
                Patient.filter_date,
                Patient.from_simpus,
            )
        )
        .where(Patient.nik == sample["nik"], Patient.from_simpus.is_(True))
        .order_by(Patient.created_at.desc())
    )
    if pk is None or patient is None:
        raise SystemExit("missing")
    print(f"old_filter_date={patient.filter_date}")
    patient.filter_date = date(2026, 11, 4)
    db.commit()
    print(f"new_filter_date={patient.filter_date} patient_id={patient.id}")
    task = celery_app.send_task(
        "simpus.sync_asik",
        kwargs={
            "puskesmas_id": str(pk.id),
            "patient_ids": [str(patient.id)],
            "triggered_by_id": str(uuid.UUID(int=0)),
            "triggered_by_type": "integration",
        },
    )
    print(f"task_id={task.id}")
    patient_id = patient.id
finally:
    db.close()

last = None
deadline = time.time() + 600
while time.time() < deadline:
    time.sleep(8)
    db = SessionLocal()
    try:
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
            .where(SyncJob.patient_id == patient_id)
            .order_by(SyncJob.created_at.desc())
        )
        if job is None or job.created_at < started_at:
            print("waiting…")
            continue
        err = (job.error_message or "")[:500]
        print(f"sync_id={job.id} status={job.status} error={err!r}")
        last = {"id": str(job.id), "status": str(job.status), "error": err}
        if job.status in (SyncStatus.SUCCESS, SyncStatus.FAILED, SyncStatus.CANCELLED):
            break
    finally:
        db.close()

Path("/tmp/ckg_e2e_resync_result.json").write_text(json.dumps(last, indent=2), encoding="utf-8")
print("DONE", json.dumps(last, ensure_ascii=False))
