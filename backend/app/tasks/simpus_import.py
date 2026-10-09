"""Import CKG visits from a SIMPUS jawaban API into patients."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import redis
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, load_only

from app.celery_app import celery_app
from app.config import settings
from app.core.security import decrypt_json, encrypt_json
from app.crud import patient as patient_crud
from app.crud import scrape_job as scrape_job_crud
from app.crud.cron_config import compute_next_run_at
from app.database import SessionLocal
from app.models.puskesmas import Puskesmas
from app.models.scrape_job import ScrapeJob, ScrapeKind, ScrapeStatus, TriggererType
from app.models.simpus_schedule import SimpusSchedule
from app.services.simpus_jawaban import (
    SimpusApiError,
    SimpusUnauthorized,
    iter_jawaban,
    jawaban_to_asik_blob,
    login_instansi,
)

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


def schedule_window(today: date, lookback_days: int) -> tuple[date, date]:
    days = max(1, min(14, lookback_days))
    return today - timedelta(days=days - 1), today


@celery_app.task(name="simpus.dispatch_due")
def dispatch_due() -> int:
    """Fire enabled SIMPUS schedules whose next_run_at has passed.

    Each fire enqueues one jawaban import over the lookback window ending today
    (Jakarta). An import already running for that puskesmas is left alone; the
    next fire is still moved to tomorrow so this does not retry every minute.
    """
    fired = 0
    now = datetime.now(UTC)
    db_ids: Session = SessionLocal()
    try:
        schedule_ids = list(
            db_ids.scalars(
                select(SimpusSchedule.id).where(
                    SimpusSchedule.enabled.is_(True),
                    SimpusSchedule.next_run_at <= now,
                )
            ).all()
        )
    finally:
        db_ids.close()

    today = now.astimezone(ZoneInfo("Asia/Jakarta")).date()
    for schedule_id in schedule_ids:
        db: Session = SessionLocal()
        try:
            cfg = db.scalar(
                select(SimpusSchedule)
                .where(SimpusSchedule.id == schedule_id)
                .with_for_update(skip_locked=True)
            )
            if cfg is None or not cfg.enabled or cfg.next_run_at > now:
                continue
            date_from, date_to = schedule_window(today, cfg.lookback_days)
            puskesmas_id = cfg.puskesmas_id
            hour, minute = cfg.hour, cfg.minute
            cfg.next_run_at = compute_next_run_at(hour, minute, from_=now)
            cfg.last_fired_at = now
            try:
                job = scrape_job_crud.create(
                    db,
                    puskesmas_id=puskesmas_id,
                    kind=ScrapeKind.SIMPUS,
                    date_filter=date_to.isoformat(),
                    triggered_by_id=puskesmas_id,
                    triggered_by_type=TriggererType.CRON,
                )
            except IntegrityError:
                db.rollback()
                db2 = SessionLocal()
                try:
                    again = db2.scalar(
                        select(SimpusSchedule).where(SimpusSchedule.id == schedule_id)
                    )
                    if again is not None:
                        again.next_run_at = compute_next_run_at(hour, minute, from_=now)
                        again.last_fired_at = now
                        db2.commit()
                finally:
                    db2.close()
                log.info("simpus schedule skip pk=%s — import already running", puskesmas_id)
                continue
            db.commit()
            try:
                celery_app.send_task(
                    "simpus.import_jawaban",
                    args=[str(job.id)],
                    kwargs={
                        "tanggal_dari": date_from.isoformat(),
                        "tanggal_sampai": date_to.isoformat(),
                    },
                )
            except Exception as exc:
                scrape_job_crud.mark_failed(
                    db, job, f"broker unreachable: {exc}"[:2000], datetime.now(UTC)
                )
                continue
            fired += 1
        except Exception:
            db.rollback()
            log.exception("simpus schedule failed id=%s", schedule_id)
        finally:
            db.close()
    return fired


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
                    ScrapeJob.kind, ScrapeJob.triggered_by_id, ScrapeJob.triggered_by_type,
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
            stored = decrypt_json(puskesmas.simpus_api_cred)
        except Exception:
            scrape_job_crud.mark_failed(
                db, job, "kredensial SIMPUS tidak bisa dibaca", datetime.now(UTC)
            )
            return
        token = stored.get("token") or ""
        email = stored.get("email") or ""
        password = stored.get("password") or ""
        if not token and email and password:
            try:
                token = login_instansi(puskesmas.simpus_api_url, email, password)
            except SimpusApiError as exc:
                scrape_job_crud.mark_failed(db, job, str(exc)[:2000], datetime.now(UTC))
                return
            stored = {"email": email, "password": password, "token": token}
            puskesmas.simpus_api_cred = encrypt_json(stored)
            db.commit()
        if not token:
            scrape_job_crud.mark_failed(
                db, job, "token SIMPUS kosong — simpan email dan password instansi", datetime.now(UTC)
            )
            return

        scrape_job_crud.mark_running(db, job, self.request.id or "", started)
        _publish(rc, job_id, "[run] menarik jawaban CKG dari SIMPUS")

        scraped = inserted = updated = skipped = sekolah = 0
        umum_ids: list[str] = []
        cancel_key = _cancel_key(job_id)
        api_url = puskesmas.simpus_api_url

        def consume() -> bool:
            nonlocal scraped, inserted, updated, skipped, sekolah
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
                        return True
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
                raw_nama = row.get("nama")
                nama = raw_nama.strip() if isinstance(raw_nama, str) else ""
                is_sekolah = row.get("jenis") == "sekolah"
                blob = jawaban_to_asik_blob(row_nik, jawaban, nama or None)
                has_mandiri = bool(blob.get("pemeriksaan_mandiri"))
                outcome, patient_id = patient_crud.upsert_simpus_visit(
                    db,
                    puskesmas_id=job.puskesmas_id,
                    nik=row_nik,
                    nama=nama or row_nik,
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
                if is_sekolah:
                    sekolah += 1
                else:
                    umum_ids.append(str(patient_id))
            return False

        refreshed = False
        while True:
            try:
                if consume():
                    return
                break
            except SimpusUnauthorized:
                if refreshed or not email or not password:
                    raise
                refreshed = True
                token = login_instansi(api_url, email, password)
                puskesmas.simpus_api_cred = encrypt_json({
                    "email": email, "password": password, "token": token,
                })
                db.commit()
                scraped = inserted = updated = skipped = sekolah = 0
                umum_ids = []
                _publish(rc, job_id, "[run] token ditolak, login instansi ulang")

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
        if sekolah:
            _publish(
                rc, job_id,
                f"[info] {sekolah} kunjungan sekolah disimpan, belum ada isi form sekolah di ASIK",
            )
        if umum_ids:
            asik_ready = db.scalar(
                select(Puskesmas.id).where(
                    Puskesmas.id == job.puskesmas_id,
                    Puskesmas.asik_url.isnot(None),
                    Puskesmas.asik_cred.isnot(None),
                )
            )
            if asik_ready is None:
                _publish(
                    rc, job_id,
                    "[skip] URL atau akun ASIK belum diisi, jawaban umum tidak dikirim ke ASIK",
                )
            else:
                _publish(
                    rc, job_id,
                    f"[run] mengirim {len(umum_ids)} kunjungan umum ke ASIK",
                )
                celery_app.send_task(
                    "simpus.sync_asik",
                    kwargs={
                        "puskesmas_id": str(job.puskesmas_id),
                        "patient_ids": umum_ids,
                        "triggered_by_id": str(job.triggered_by_id),
                        "triggered_by_type": job.triggered_by_type.value,
                    },
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
