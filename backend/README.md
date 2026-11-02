# CKG Backend

FastAPI + SQLAlchemy + Postgres backend.

## Quickstart

### Local dev (backend on host, infra in Docker)

```bash
# from repo root: bring up Postgres + Redis
docker compose -f docker-compose.local.yml up -d

cd backend
cp .env.example .env
# ensure CRED_ENCRYPTION_KEY in .env — see scrapers/README.md §1-2 to generate
python3 -m venv .venv && source .venv/bin/activate
pip3 install -e ".[dev]"
playwright install chromium      # required by scraper Celery tasks
alembic upgrade head

# Terminal A — API
uvicorn app.main:app --reload

# Terminal B — Celery worker (handles /scrape/*, /merge/*, cron tasks)
celery -A app.celery_app.celery_app worker --loglevel=info --concurrency=4

# Terminal C — Celery beat (cron scheduler; singleton — only run one)
celery -A app.celery_app.celery_app beat --loglevel=info
```

API at http://localhost:8000. OpenAPI docs at /docs.

### Production-like stack (everything in Docker)

```bash
# from repo root — requires backend/.env to exist
cp backend/.env.example backend/.env  # edit values for prod
docker compose up --build -d
```

Backend container runs `alembic upgrade head` on start, then `uvicorn` with 4 workers (no reload).

## Auth

Login as the seeded admin:

```bash
curl -X POST http://localhost:8000/admin/auth/login \
  -H 'content-type: application/json' \
  -d '{"email":"admin@ckg.com","password":"Admin123!"}'
```

Use the returned `access_token` as `Authorization: Bearer <token>` for admin endpoints.

## ASIK registration failure webhook

Set `ASIK_CREATE_WEBHOOK_URL` and `ASIK_CREATE_WEBHOOK_TOKEN` in the backend
environment to enable outbound notifications. The endpoint must use HTTPS and
accept a JSON `POST` with a Bearer token. Configure both values for the API and
Celery worker, then restart them.

The webhook is queued for terminal ASIK registration failures, including
Dukcapil rejection and local registration preflight failures. It does not fire
for an already-registered patient or a cancelled job. HTTP failures are retried
by Celery; the receiver should use `event_id` / `Idempotency-Key` to deduplicate
retries.

Example request:

```json
{
  "event": "asik.registration.failed",
  "event_id": "sync-job-uuid",
  "occurred_at": "2026-11-02T07:15:00+00:00",
  "job_id": "sync-job-uuid",
  "patient_id": "patient-uuid",
  "patient_name": "Example Patient",
  "nik": "patient-nik",
  "puskesmas_id": "puskesmas-uuid",
  "outcome": "dukcapil_invalid",
  "message": "Dukcapil validation failed (NIK / name / DOB mismatch)"
}
```

The payload contains patient name and NIK. Only configure a trusted receiver
authorized to process that sensitive data. A successful response must be 2xx.

## Soft Delete

All tables carry `deleted_at`. ORM queries auto-exclude soft-deleted rows. No hard deletes.
