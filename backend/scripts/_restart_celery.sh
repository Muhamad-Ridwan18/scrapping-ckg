#!/usr/bin/env bash
pkill -f 'celery_app.celery_app worker' || true
sleep 2
cd /mnt/d/DEV/laragon/www/ckg-ai/backend
nohup /tmp/ckg-backend-venv/bin/celery -A app.celery_app.celery_app worker --loglevel=info --pool=solo > /tmp/ckg-celery.log 2>&1 &
echo "PID=$!"
sleep 5
tail -n 8 /tmp/ckg-celery.log
pgrep -af celery_app || true
