"""Retry simpus.create_asik for LUDIA + NIKANDER on a date with quota."""

from __future__ import annotations

import json
import time
import uuid
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.orm import load_only

from app.celery_app import celery_app
from app.database import SessionLocal
from app.models.patient import Patient
from app.models.puskesmas import Puskesmas
from app.models.sync_job import SyncJob, SyncStatus

TARGETS = [
    ("9110086810950001", "LUDIA KENI"),
    ("9110071111830001", "NIKANDER WEPPE"),
]
EXAM_DATE = date(2026, 11, 4)  # known non-zero quota from prior ANAMARIA run

db = SessionLocal()
try:
    pk = db.scalar(
        select(Puskesmas)
        .options(load_only(Puskesmas.id, Puskesmas.name))
        .where(Puskesmas.name == "PUSKESMAS SARMI")
    )
    if pk is None:
        raise SystemExit("puskesmas missing")
    patient_ids: list[str] = []
    for nik, nama in TARGETS:
        p = db.scalar(
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
            .where(Patient.nik == nik, Patient.from_simpus.is_(True))
            .order_by(Patient.created_at.desc())
        )
        if p is None:
            print(f"missing patient {nama}")
            continue
        p.filter_date = EXAM_DATE
        patient_ids.append(str(p.id))
        print(f"queued {nama} id={p.id} exam={EXAM_DATE}")
    db.commit()
    started = datetime.now(UTC)
    task = celery_app.send_task(
        "simpus.create_asik",
        kwargs={
            "puskesmas_id": str(pk.id),
            "patient_ids": patient_ids,
            "triggered_by_id": str(uuid.UUID(int=0)),
            "triggered_by_type": "integration",
        },
    )
    print(f"task_id={task.id} n={len(patient_ids)}")
finally:
    db.close()

# Wait for a sync job per patient created after started
pending = set(patient_ids)
done: dict[str, dict] = {}
deadline = time.time() + 1800
while time.time() < deadline and pending:
    time.sleep(15)
    db = SessionLocal()
    try:
        for pid in list(pending):
            job = db.scalar(
                select(SyncJob)
                .options(
                    load_only(
                        SyncJob.id,
                        SyncJob.status,
                        SyncJob.error_message,
                        SyncJob.created_at,
                        SyncJob.patient_id,
                    )
                )
                .where(
                    SyncJob.patient_id == uuid.UUID(pid),
                    SyncJob.created_at >= started,
                )
                .order_by(SyncJob.created_at.desc())
            )
            if job is None:
                print(f"{pid}: waiting…")
                continue
            err = (job.error_message or "")[:220]
            print(f"{pid}: {job.status} {err!r}")
            if job.status in (SyncStatus.SUCCESS, SyncStatus.FAILED, SyncStatus.CANCELLED):
                p = db.scalar(
                    select(Patient)
                    .options(load_only(Patient.id, Patient.nik, Patient.nama))
                    .where(Patient.id == uuid.UUID(pid))
                )
                done[pid] = {
                    "patient_id": pid,
                    "nik": p.nik if p else None,
                    "nama": p.nama if p else None,
                    "sync_id": str(job.id),
                    "status": str(job.status),
                    "error": err,
                }
                pending.discard(pid)
    finally:
        db.close()

print(json.dumps(list(done.values()), ensure_ascii=False, indent=2))
ok = all(v["status"] == "SyncStatus.SUCCESS" for v in done.values()) and len(done) == len(patient_ids)
raise SystemExit(0 if ok else 1)
