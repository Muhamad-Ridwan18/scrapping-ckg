#!/usr/bin/env bash
set -euo pipefail
cd /mnt/d/DEV/laragon/www/ckg-ai/backend

# Postgres/Redis already expected on 127.0.0.1 inside WSL.
pkill -f 'uvicorn app.main:app' || true
pkill -f 'celery_app.celery_app worker' || true
sleep 2

nohup /tmp/ckg-backend-venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload > /tmp/ckg-api.log 2>&1 &
echo "API_PID=$!"
nohup /tmp/ckg-backend-venv/bin/celery -A app.celery_app.celery_app worker --loglevel=info --pool=solo > /tmp/ckg-celery.log 2>&1 &
echo "CELERY_PID=$!"
sleep 6
curl -sS -m 5 http://127.0.0.1:8001/health || true
echo
tail -n 5 /tmp/ckg-celery.log
