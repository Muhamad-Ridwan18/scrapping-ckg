"""Inspect patient + sync jobs created after the SIMPUS import."""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import load_only

from app.database import SessionLocal
from app.models.patient import Patient
from app.models.sync_job import SyncJob

sample = json.loads(Path("/tmp/ckg_e2e_sample.json").read_text(encoding="utf-8"))
nik = sample["nik"]

db = SessionLocal()
try:
    patients = list(
        db.scalars(
            select(Patient)
            .options(
                load_only(
                    Patient.id,
                    Patient.nik,
                    Patient.nama,
                    Patient.filter_date,
                    Patient.from_simpus,
                    Patient.scraped_asik_data,
                )
            )
            .where(Patient.nik == nik)
            .order_by(Patient.created_at.desc())
            .limit(5)
        ).all()
    )
    print(f"patients={len(patients)}")
    for p in patients:
        has_blob = p.scraped_asik_data is not None
        print(
            f"patient_id={p.id} nama={p.nama!r} date={p.filter_date} "
            f"from_simpus={p.from_simpus} has_asik_blob={has_blob}"
        )
        jobs = list(
            db.scalars(
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
                .where(SyncJob.patient_id == p.id)
                .order_by(SyncJob.created_at.desc())
                .limit(5)
            ).all()
        )
        print(f"  sync_jobs={len(jobs)}")
        for j in jobs:
            print(
                f"  sync_id={j.id} status={j.status} "
                f"error={(j.error_message or '')[:300]!r} "
                f"created={j.created_at} finished={j.finished_at}"
            )
finally:
    db.close()
