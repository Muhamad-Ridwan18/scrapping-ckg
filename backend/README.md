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

## External ASIK create trigger and result webhook

Set `ASIK_CREATE_WEBHOOK_URL` and `ASIK_CREATE_WEBHOOK_TOKEN` in the backend
environment to send results back to the external application. The receiver must
use HTTPS and accept a JSON `POST` with a Bearer token. Set
`ASIK_CREATE_TRIGGER_TOKEN` to a separate, random secret (at least 32
characters) to allow the external application to trigger a create. Configure
these values in the shared backend environment and restart the API and Celery
worker.

The external app sends the existing CKG `patient_id` (UUID), not a NIK, to:

```http
POST /integrations/asik/patients/{patient_id}/create
Authorization: Bearer <ASIK_CREATE_TRIGGER_TOKEN>
Content-Type: application/json
```

Send `{}` as the body, or `{"headless": true}`. This queues one eligible
`epus_only + Tandai-CKG` patient for ASIK registration. The immediate `201`
response contains the job ID and patient name/NIK; `409` means that patient's
sync/create is already running. A response is only an acknowledgement—the
registration runs asynchronously.

When the create attempt finishes, CKG POSTs a result event to the configured
webhook. It includes patient details and a `status`/`outcome` for validation or
application handling. Events are sent for terminal failures and completed
registrations; already-registered and cancelled jobs are excluded. HTTP
delivery failures are retried by Celery. Use `event_id` /
`Idempotency-Key` to deduplicate retries.

Example result:

```json
{
  "event": "asik.registration.result",
  "event_id": "sync-job-uuid",
  "occurred_at": "2026-11-02T07:15:00+00:00",
  "job_id": "sync-job-uuid",
  "patient_id": "patient-uuid",
  "patient_name": "Example Patient",
  "nik": "patient-nik",
  "puskesmas_id": "puskesmas-uuid",
  "status": "failed",
  "outcome": "dukcapil_invalid",
  "message": "Dukcapil validation failed (NIK / name / DOB mismatch)"
}
```

Both trigger and result payloads contain or return patient identifiers. Only
configure a trusted external application authorized to process that sensitive
data. Generate each token with
`python -c "import secrets; print(secrets.token_urlsafe(32))"`. A successful
webhook response must be 2xx.

## Soft Delete

All tables carry `deleted_at`. ORM queries auto-exclude soft-deleted rows. No hard deletes.
