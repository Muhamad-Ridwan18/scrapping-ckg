"""Re-dispatch simpus.sync_asik for the e2e patient and wait for a SyncJob."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import load_only

from app.celery_app import celery_app
from app.database import SessionLocal
from app.models.llm_config import LlmConfig
from app.models.patient import Patient
from app.models.puskesmas import Puskesmas
from app.models.sync_job import SyncJob, SyncStatus

sample = json.loads(Path("/tmp/ckg_e2e_sample.json").read_text(encoding="utf-8"))
nik = sample["nik"]
name = sample["nama_instansi"]

db = SessionLocal()
try:
    captcha = list(
        db.scalars(
            select(LlmConfig)
            .options(
                load_only(
                    LlmConfig.id,
                    LlmConfig.label,
                    LlmConfig.model,
                    LlmConfig.is_active_captcha,
                    LlmConfig.is_active,
                )
            )
            .where(LlmConfig.is_active_captcha.is_(True))
            .limit(5)
        ).all()
    )
    print(f"captcha_llm_active={len(captcha)}")
    for c in captcha:
        print(f"  llm={c.label or c.model} active={c.is_active}")

    pk = db.scalar(
        select(Puskesmas)
        .options(load_only(Puskesmas.id, Puskesmas.name))
        .where(Puskesmas.name == name)
    )
    patient = db.scalar(
        select(Patient)
        .options(load_only(Patient.id, Patient.nik, Patient.nama, Patient.from_simpus))
        .where(Patient.nik == nik, Patient.from_simpus.is_(True))
        .order_by(Patient.created_at.desc())
    )
    if pk is None or patient is None:
        raise SystemExit("missing puskesmas/patient")
    print(f"patient_id={patient.id} puskesmas_id={pk.id}")

    async_result = celery_app.send_task(
        "simpus.sync_asik",
        kwargs={
            "puskesmas_id": str(pk.id),
            "patient_ids": [str(patient.id)],
            "triggered_by_id": str(uuid.UUID(int=0)),
            "triggered_by_type": "integration",
        },
    )
    print(f"task_id={async_result.id}")
finally:
    db.close()

# Poll sync job
deadline = time.time() + 300
last = None
while time.time() < deadline:
    time.sleep(5)
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
            .where(SyncJob.patient_id == patient.id)
            .order_by(SyncJob.created_at.desc())
        )
        if job is None:
            print("no sync job yet")
            continue
        status = job.status
        err = (job.error_message or "")[:400]
        print(f"sync_id={job.id} status={status} error={err!r}")
        last = {"id": str(job.id), "status": str(status), "error": err}
        if status in (SyncStatus.SUCCESS, SyncStatus.FAILED, SyncStatus.CANCELLED):
            break
    finally:
        db.close()

Path("/tmp/ckg_e2e_sync_result.json").write_text(json.dumps(last, indent=2), encoding="utf-8")
print("DONE", json.dumps(last))
