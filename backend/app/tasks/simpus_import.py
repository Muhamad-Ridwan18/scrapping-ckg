"""Import CKG visits from a SIMPUS jawaban API into patients."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, date, datetime

import redis
from sqlalchemy import select
from sqlalchemy.orm import Session, load_only

from app.celery_app import celery_app
from app.config import settings
from app.core.security import decrypt_json, encrypt_json
from app.crud import patient as patient_crud
from app.crud import scrape_job as scrape_job_crud
from app.database import SessionLocal
from app.models.puskesmas import Puskesmas
from app.models.scrape_job import ScrapeJob, ScrapeStatus
from app.services.simpus_jawaban import SimpusApiError, iter_jawaban, jawaban_to_asik_blob

log = logging.getLogger(__name__)

LOG_RING_MAX = 500
LOG_TTL_SECONDS = 86400


def _log_key(job_id: str) -> str:
    return f"scrape:job:{job_id}:log"


def _stream_chan(job_id: str) -> str:
    return f"scrape:job:{job_id}:stream"


def _cancel_key(job_id: str) -> str:
    return f"scrape:job:{job_id}:cancel"


def _publish(rc: redis.Redis, job_id: str, line: str) -> None:
    try:
        log_key = _log_key(job_id)
        rc.rpush(log_key, line)
        rc.ltrim(log_key, -LOG_RING_MAX, -1)
        rc.expire(log_key, LOG_TTL_SECONDS)
        rc.publish(_stream_chan(job_id), line)
    except Exception:
        pass


def _parse_date(value: object) -> date | None:
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


@celery_app.task(bind=True, name="simpus.import_jawaban")
def import_jawaban(
    self,
    job_id: str,
    jenis: str | None = None,
    nik: str | None = None,
    tanggal: str | None = None,
    tanggal_dari: str | None = None,
    tanggal_sampai: str | None = None,
) -> None:
    db: Session = SessionLocal()
    rc = redis.from_url(settings.REDIS_URL, decode_responses=True)
    started = datetime.now(UTC)
    job: ScrapeJob | None = None
    try:
        job = db.scalar(
            select(ScrapeJob)
            .options(
                load_only(
                    ScrapeJob.id, ScrapeJob.puskesmas_id, ScrapeJob.status,
                    ScrapeJob.kind,
                )
            )
            .where(ScrapeJob.id == uuid.UUID(job_id))
        )
        if job is None or job.status == ScrapeStatus.CANCELLED:
            return
        puskesmas = db.scalar(
            select(Puskesmas)
            .options(
                load_only(
                    Puskesmas.id, Puskesmas.simpus_api_url, Puskesmas.simpus_api_cred,
                )
            )
            .where(Puskesmas.id == job.puskesmas_id)
        )
        if puskesmas is None or not puskesmas.simpus_api_url or puskesmas.simpus_api_cred is None:
            scrape_job_crud.mark_failed(
                db, job, "URL atau token SIMPUS belum diisi", datetime.now(UTC)
            )
            return
        try:
            token = decrypt_json(puskesmas.simpus_api_cred)["token"]
        except Exception:
            scrape_job_crud.mark_failed(
                db, job, "token SIMPUS tidak bisa dibaca", datetime.now(UTC)
            )
            return
        if not token:
            scrape_job_crud.mark_failed(db, job, "token SIMPUS kosong", datetime.now(UTC))
            return

        scrape_job_crud.mark_running(db, job, self.request.id or "", started)
        _publish(rc, job_id, "[run] menarik jawaban CKG dari SIMPUS")

        scraped = inserted = updated = skipped = 0
        cancel_key = _cancel_key(job_id)
        api_url = puskesmas.simpus_api_url
        for row in iter_jawaban(
            api_url,
            token,
            jenis=jenis,
            nik=nik,
            tanggal=tanggal,
            tanggal_dari=tanggal_dari,
            tanggal_sampai=tanggal_sampai,
        ):
            try:
                if rc.get(cancel_key) == "1":
                    db.commit()
                    scrape_job_crud.mark_cancelled(db, job)
                    _publish(rc, job_id, "[cancel] dihentikan")
                    return
            except Exception:
                pass
            if not isinstance(row, dict):
                skipped += 1
                continue
            row_nik = row.get("nik")
            parsed = _parse_date(row.get("tanggal"))
            jawaban = row.get("jawaban")
            if not isinstance(row_nik, str) or not row_nik.strip() or parsed is None:
                skipped += 1
                continue
            if not isinstance(jawaban, dict) or not jawaban:
                skipped += 1
                continue
            row_nik = row_nik.strip()
            is_sekolah = row.get("jenis") == "sekolah"
            blob = jawaban_to_asik_blob(row_nik, jawaban)
            has_mandiri = bool(blob.get("pemeriksaan_mandiri"))
            outcome = patient_crud.upsert_simpus_visit(
                db,
                puskesmas_id=job.puskesmas_id,
                nik=row_nik,
                nama=row_nik,
                encrypted=encrypt_json(blob),
                parsed_date=parsed,
                ruangan="sekolah" if is_sekolah else "",
                is_sekolah=is_sekolah,
                has_mandiri=has_mandiri,
            )
            db.commit()
            scraped += 1
            if outcome == "inserted":
                inserted += 1
            else:
                updated += 1

        finished = datetime.now(UTC)
        note = f"dilewati {skipped}" if skipped else None
        scrape_job_crud.mark_success(
            db,
            job,
            scraped_count=scraped,
            inserted_count=inserted,
            updated_count=updated,
            duration_seconds=(finished - started).total_seconds(),
            finished_at=finished,
            notes=note,
        )
        _publish(
            rc, job_id,
            f"[ok] {scraped} kunjungan, {inserted} baru, {updated} diperbarui",
        )
    except SimpusApiError as exc:
        db.rollback()
        if job is not None:
            scrape_job_crud.mark_failed(db, job, str(exc)[:2000], datetime.now(UTC))
        _publish(rc, job_id, f"[fail] {exc}")
    except Exception as exc:
        log.exception("simpus import failed")
        db.rollback()
        if job is not None:
            scrape_job_crud.mark_failed(db, job, str(exc)[:2000], datetime.now(UTC))
        _publish(rc, job_id, f"[fail] {exc}")
    finally:
        db.close()
