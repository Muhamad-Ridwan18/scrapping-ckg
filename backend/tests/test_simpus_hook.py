import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.api.routes import simpus_hook
from app.config import settings
from app.main import app
from app.models.scrape_job import ScrapeKind, ScrapeStatus, TriggererType


def test_simpus_hook_rejects_bad_bearer():
    client = TestClient(app)
    res = client.post(
        "/hooks/simpus",
        headers={"Authorization": "Bearer wrong"},
        json={"simpus_url": "https://app.kliksimpus.com", "nik": "1234567890123456", "tanggal": "2026-11-03"},
    )
    assert res.status_code == 401


def test_simpus_hook_queues_import(monkeypatch):
    puskesmas_id = uuid.uuid4()
    job_id = uuid.uuid4()
    pk = SimpleNamespace(
        id=puskesmas_id,
        name="Puskesmas Contoh",
        simpus_api_url="https://app.kliksimpus.com",
        simpus_api_cred=b"x",
        asik_url="https://asik.example",
        asik_cred=b"y",
        asik_default_alamat={"provinsi": {}},
    )
    fake_db = MagicMock()
    fake_db.scalars.return_value.all.return_value = [pk]
    created = []
    queued = []

    def create_job(_db, **kwargs):
        created.append(kwargs)
        return SimpleNamespace(id=job_id)

    monkeypatch.setattr(simpus_hook.scrape_job_crud, "create", create_job)
    monkeypatch.setattr(
        simpus_hook.celery_app,
        "send_task",
        lambda *a, **k: queued.append((a, k)),
    )
    app.dependency_overrides[get_db] = lambda: fake_db
    try:
        client = TestClient(app)
        res = client.post(
            "/hooks/simpus",
            headers={"Authorization": f"Bearer {settings.SIMPUS_HOOK_BEARER}"},
            json={
                "simpus_url": "https://app.kliksimpus.com",
                "nama_instansi": "Puskesmas Contoh",
                "nik": "1234567890123456",
                "tanggal": "2026-11-03",
                "jenis": "umum",
            },
        )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert res.status_code == 201
    body = res.json()
    assert body["job_id"] == str(job_id)
    assert body["status"] == "pending"
    assert created[0]["kind"] is ScrapeKind.SIMPUS
    assert created[0]["triggered_by_type"] is TriggererType.INTEGRATION
    assert created[0]["target_nik"] == "1234567890123456"
    assert queued and queued[0][0][0] == "simpus.import_jawaban"


def test_simpus_hook_job_status(monkeypatch):
    job_id = uuid.uuid4()
    job = SimpleNamespace(
        id=job_id,
        kind=ScrapeKind.SIMPUS,
        status=ScrapeStatus.FAILED,
        target_nik="1234567890123456",
        error_message="token SIMPUS kosong",
        triggered_by_type=TriggererType.INTEGRATION,
    )
    fake_db = MagicMock()
    fake_db.scalar.return_value = job
    app.dependency_overrides[get_db] = lambda: fake_db
    try:
        client = TestClient(app)
        res = client.get(
            f"/hooks/simpus/jobs/{job_id}",
            headers={"Authorization": f"Bearer {settings.SIMPUS_HOOK_BEARER}"},
        )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "failed"
    assert body["results"][0]["nik"] == "1234567890123456"
    assert "token SIMPUS" in body["results"][0]["error_message"]
