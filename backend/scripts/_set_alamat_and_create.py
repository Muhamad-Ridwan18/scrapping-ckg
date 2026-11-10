"""Set asik_default_alamat for SARMI and dispatch simpus.create_asik."""

from __future__ import annotations

import json
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import load_only

from app.celery_app import celery_app
from app.database import SessionLocal
from app.models.patient import Patient
from app.models.puskesmas import Puskesmas
from app.models.sync_job import SyncJob, SyncStatus

sample = json.loads(Path("/tmp/ckg_e2e_sample.json").read_text(encoding="utf-8"))
alamat_raw = json.loads(
    Path("/mnt/d/DEV/Mitra Bestari Teknologi/kliksimpus/scripts/_alamat_sample.json").read_text(
        encoding="utf-8"
    )
)
dom = alamat_raw["alamat_domisili"]
default_alamat = {
    "provinsi": {"name": dom["province_name"], "code": str(dom["province_id"])},
    "kota": {"name": dom["regency_name"], "code": str(dom["regency_id"])},
    "kecamatan": {"name": dom["district_name"], "code": str(dom["district_id"])},
    "kelurahan": {"name": dom["village_name"], "code": str(dom["village_id"])},
}

started_at = datetime.now(UTC)
db = SessionLocal()
try:
    pk = db.scalar(
        select(Puskesmas)
        .options(
            load_only(
                Puskesmas.id,
                Puskesmas.name,
                Puskesmas.asik_default_alamat,
            )
        )
        .where(Puskesmas.name == sample["nama_instansi"])
    )
    patient = db.scalar(
        select(Patient)
        .options(load_only(Patient.id, Patient.nik, Patient.nama, Patient.from_simpus))
        .where(Patient.nik == sample["nik"], Patient.from_simpus.is_(True))
        .order_by(Patient.created_at.desc())
    )
    if pk is None or patient is None:
        raise SystemExit("missing pk/patient")
    pk.asik_default_alamat = default_alamat
    db.commit()
    print(f"alamat_set puskesmas_id={pk.id}")
    print(f"patient_id={patient.id} nama={patient.nama!r}")
    task = celery_app.send_task(
        "simpus.create_asik",
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
        if job is None:
            print("waiting for sync job")
            continue
        if job.created_at < started_at:
            print(f"skip old sync_id={job.id} status={job.status}")
            continue
        err = (job.error_message or "")[:500]
        print(f"sync_id={job.id} status={job.status} error={err!r}")
        last = {"id": str(job.id), "status": str(job.status), "error": err}
        if job.status in (SyncStatus.SUCCESS, SyncStatus.FAILED, SyncStatus.CANCELLED):
            break
    finally:
        db.close()

Path("/tmp/ckg_e2e_create_result.json").write_text(json.dumps(last, indent=2), encoding="utf-8")
print("DONE", json.dumps(last, ensure_ascii=False))
