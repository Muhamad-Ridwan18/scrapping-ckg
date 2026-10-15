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
from app.crud import cron_run as cron_run_crud
from app.crud import patient as patient_crud
from app.crud import scrape_job as scrape_job_crud
from app.crud.cron_config import compute_next_run_at
from app.database import SessionLocal
from app.models.cron_config import CronSourceScope
from app.models.cron_run import CronRun, CronRunStatus, CronStep
from app.models.puskesmas import Puskesmas
from app.models.scrape_job import ScrapeJob, ScrapeKind, ScrapeStatus, TriggererType
from app.models.simpus_schedule import SimpusSchedule
from app.tasks.create_patient import _derive_from_nik
from app.services.simpus_jawaban import (
    SimpusApiError,
    SimpusUnauthorized,
    iter_jawaban,
    jawaban_to_asik_blob,
    pendaftaran_from_row,
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


def _fail_cron_run(run_id: uuid.UUID, message: str) -> None:
    db = SessionLocal()
    try:
        run = db.get(CronRun, run_id)
        if run is None or run.status not in (
            CronRunStatus.PENDING, CronRunStatus.RUNNING
        ):
            return
        cron_run_crud.mark_failed(
            db, run, CronStep.SIMPUS, message[:2000], datetime.now(UTC)
        )
    finally:
        db.close()


def _settle_cron_run(
    cron_run_id: str | None, outcome: str | None, error: str | None
) -> None:
    if not cron_run_id or not outcome:
        return
    try:
        run_id = uuid.UUID(cron_run_id)
    except ValueError:
        return
    db = SessionLocal()
    try:
        run = db.get(CronRun, run_id)
        if run is None or run.status not in (
            CronRunStatus.PENDING, CronRunStatus.RUNNING
        ):
            return
        now = datetime.now(UTC)
        if outcome == "success":
            cron_run_crud.mark_success(db, run, now)
        elif outcome == "cancelled":
            cron_run_crud.mark_cancelled(db, run)
        else:
            cron_run_crud.mark_failed(
                db, run, CronStep.SIMPUS, (error or "gagal")[:2000], now
            )
    finally:
        db.close()


def _restamp_schedule(
    schedule_id: uuid.UUID, hour: int, minute: int, now: datetime
) -> None:
    db = SessionLocal()
    try:
        again = db.get(SimpusSchedule, schedule_id)
        if again is None:
            return
        again.next_run_at = compute_next_run_at(hour, minute, from_=now)
        again.last_fired_at = now
        db.commit()
    finally:
        db.close()


def open_simpus_run(
    db: Session,
    *,
    puskesmas_id: uuid.UUID,
    date_from: date,
    date_to: date,
    create_new: bool,
    triggered_by_id: uuid.UUID,
    triggered_by_type: TriggererType,
) -> CronRun:
    """Open one SIMPUS cron run and enqueue its jawaban import.

    The cron run is committed before the scrape job so a second fire hits
    uq_cron_runs_active_simpus. A scrape-job conflict (manual import already
    running) fails that run and re-raises IntegrityError.
    """
    run = cron_run_crud.create(
        db,
        puskesmas_id=puskesmas_id,
        target_date=date_to,
        cron_config_id=None,
        triggered_by_id=triggered_by_id,
        source_scope=CronSourceScope.SIMPUS,
    )
    try:
        job = scrape_job_crud.create(
            db,
            puskesmas_id=puskesmas_id,
            kind=ScrapeKind.SIMPUS,
            date_filter=date_to.isoformat(),
            triggered_by_id=triggered_by_id,
            triggered_by_type=triggered_by_type,
            cron_run_id=run.id,
        )
    except IntegrityError:
        db.rollback()
        _fail_cron_run(run.id, "impor SIMPUS masih berjalan")
        raise
    cron_run_crud.set_step(db, run, CronStep.SIMPUS, attempt=1, job_id=job.id)
    cron_run_crud.mark_running(db, run, datetime.now(UTC))
    try:
        celery_app.send_task(
            "simpus.import_jawaban",
            args=[str(job.id)],
            kwargs={
                "tanggal_dari": date_from.isoformat(),
                "tanggal_sampai": date_to.isoformat(),
                "create_new": create_new,
                "cron_run_id": str(run.id),
            },
        )
    except Exception as exc:
        now = datetime.now(UTC)
        scrape_job_crud.mark_failed(
            db, job, f"broker unreachable: {exc}"[:2000], now
        )
        cron_run_crud.mark_failed(
            db, run, CronStep.SIMPUS, f"broker unreachable: {exc}"[:2000], now
        )
        raise
    return run


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
            create_new = cfg.create_new
            cfg.next_run_at = compute_next_run_at(hour, minute, from_=now)
            cfg.last_fired_at = now
            try:
                open_simpus_run(
                    db,
                    puskesmas_id=puskesmas_id,
                    date_from=date_from,
                    date_to=date_to,
                    create_new=create_new,
                    triggered_by_id=puskesmas_id,
                    triggered_by_type=TriggererType.CRON,
                )
            except IntegrityError:
                db.rollback()
                _restamp_schedule(schedule_id, hour, minute, now)
                log.info("simpus schedule skip pk=%s — import already running", puskesmas_id)
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
    create_new: bool = False,
    cron_run_id: str | None = None,
) -> None:
    db: Session = SessionLocal()
    rc = redis.from_url(settings.REDIS_URL, decode_responses=True)
    started = datetime.now(UTC)
    job: ScrapeJob | None = None
    cron_outcome: str | None = None
    cron_error: str | None = None
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
        if job is None:
            cron_outcome = "failed"
            cron_error = "job tidak ditemukan"
            return
        if job.status == ScrapeStatus.CANCELLED:
            cron_outcome = "cancelled"
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
            cron_outcome = "failed"
            cron_error = "URL atau token SIMPUS belum diisi"
            scrape_job_crud.mark_failed(
                db, job, cron_error, datetime.now(UTC)
            )
            return
        try:
            stored = decrypt_json(puskesmas.simpus_api_cred)
        except Exception:
            cron_outcome = "failed"
            cron_error = "kredensial SIMPUS tidak bisa dibaca"
            scrape_job_crud.mark_failed(
                db, job, cron_error, datetime.now(UTC)
            )
            return
        token = stored.get("token") or ""
        email = stored.get("email") or ""
        password = stored.get("password") or ""
        if not token and email and password:
            try:
                token = login_instansi(puskesmas.simpus_api_url, email, password)
            except SimpusApiError as exc:
                cron_outcome = "failed"
                cron_error = str(exc)[:2000]
                scrape_job_crud.mark_failed(db, job, cron_error, datetime.now(UTC))
                return
            stored = {"email": email, "password": password, "token": token}
            puskesmas.simpus_api_cred = encrypt_json(stored)
            db.commit()
        if not token:
            cron_outcome = "failed"
            cron_error = "token SIMPUS kosong — simpan email dan password instansi"
            scrape_job_crud.mark_failed(
                db, job, cron_error, datetime.now(UTC)
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
                try:
                    birth_date, _gender = _derive_from_nik(row_nik)
                except ValueError:
                    birth_date = None
                is_sekolah = row.get("jenis") == "sekolah"
                blob = jawaban_to_asik_blob(
                    row_nik,
                    jawaban,
                    nama or None,
                    pendaftaran_from_row(row),
                )
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
                    birth_date=birth_date,
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
                    cron_outcome = "cancelled"
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
                task_name = "simpus.sync_asik"
                if create_new:
                    has_alamat = db.scalar(
                        select(Puskesmas.asik_default_alamat).where(
                            Puskesmas.id == job.puskesmas_id,
                            Puskesmas.asik_default_alamat.isnot(None),
                        )
                    )
                    if has_alamat is None:
                        _publish(
                            rc, job_id,
                            "[skip] alamat default ASIK kosong, pasien baru tidak didaftarkan",
                        )
                    else:
                        task_name = "simpus.create_asik"
                verb = (
                    "mendaftarkan dan mengisi"
                    if task_name == "simpus.create_asik"
                    else "mengirim"
                )
                _publish(
                    rc, job_id,
                    f"[run] {verb} {len(umum_ids)} kunjungan umum ke ASIK",
                )
                celery_app.send_task(
                    task_name,
                    kwargs={
                        "puskesmas_id": str(job.puskesmas_id),
                        "patient_ids": umum_ids,
                        "triggered_by_id": str(job.triggered_by_id),
                        "triggered_by_type": job.triggered_by_type.value,
                    },
                )
        cron_outcome = "success"
    except SimpusApiError as exc:
        cron_outcome = "failed"
        cron_error = str(exc)[:2000]
        db.rollback()
        if job is not None:
            scrape_job_crud.mark_failed(db, job, cron_error, datetime.now(UTC))
        _publish(rc, job_id, f"[fail] {exc}")
    except Exception as exc:
        cron_outcome = "failed"
        cron_error = str(exc)[:2000]
        log.exception("simpus import failed")
        db.rollback()
        if job is not None:
            scrape_job_crud.mark_failed(db, job, cron_error, datetime.now(UTC))
        _publish(rc, job_id, f"[fail] {exc}")
    finally:
        _settle_cron_run(cron_run_id, cron_outcome, cron_error)
        db.close()
