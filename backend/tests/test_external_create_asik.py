import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.api.routes import sync
from app.config import settings
from app.main import app
from app.models.patient import MatchStatus
from app.models.scrape_job import TriggererType
from app.models.sync_job import SyncStatus
from app.schemas.sync_job import SyncJobOut


def _job_out(job_id: uuid.UUID, patient_id: uuid.UUID, puskesmas_id: uuid.UUID) -> SyncJobOut:
    now = datetime.now(UTC)
    return SyncJobOut(
        id=job_id,
        puskesmas_id=puskesmas_id,
        puskesmas_name="Puskesmas Example",
        patient_id=patient_id,
        patient_name="Patient Example",
        patient_nik="1234567890123456",
        status=SyncStatus.PENDING,
        triggered_by_id=uuid.UUID(int=0),
        triggered_by_type=TriggererType.INTEGRATION,
        celery_task_id=None,
        forms_total=0,
        forms_succeeded=0,
        forms_failed=0,
        forms_skipped=0,
        duration_seconds=None,
        cpu_avg_pct=None,
        cpu_peak_pct=None,
        mem_avg_mb=None,
        mem_peak_mb=None,
        resource_samples=None,
        notes=None,
        started_at=None,
        finished_at=None,
        error_message=None,
        created_at=now,
        updated_at=now,
    )


def test_external_create_triggers_one_patient_and_returns_patient_details(monkeypatch):
    patient_id = uuid.uuid4()
    puskesmas_id = uuid.uuid4()
    job_id = uuid.uuid4()
    patient = SimpleNamespace(
        id=patient_id,
        puskesmas_id=puskesmas_id,
        nik="1234567890123456",
        nama="Patient Example",
        match_status=MatchStatus.EPUS_ONLY,
        epus_tandai_ckg=True,
        scraped_epus_data={"data_pasien": {}},
    )
    fake_db = MagicMock()
    fake_db.scalar.return_value = patient
    fake_db.execute.return_value.one_or_none.return_value = (
        "Puskesmas Example",
        True,
        "asik.example",
        {"provinsi": {}},
    )
    queued = []
    created = []

    def create_job(_db, **kwargs):
        created.append(kwargs)
        return SimpleNamespace(id=job_id)

    monkeypatch.setattr(settings, "ASIK_CREATE_TRIGGER_TOKEN", "integration-secret")
    monkeypatch.setattr(sync.crud, "create", create_job)
    monkeypatch.setattr(
        sync.celery_app,
        "send_task",
        lambda *args, **kwargs: queued.append((args, kwargs)),
    )
    monkeypatch.setattr(
        sync,
        "_to_job_out",
        lambda _job, **kwargs: _job_out(job_id, patient_id, puskesmas_id),
    )
    app.dependency_overrides[get_db] = lambda: fake_db

    try:
        with TestClient(app) as client:
            response = client.post(
                f"/integrations/asik/patients/{patient_id}/create",
                headers={"Authorization": "Bearer integration-secret"},
                json={},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 201
    assert response.json()["patient_id"] == str(patient_id)
    assert response.json()["patient_name"] == "Patient Example"
    assert response.json()["patient_nik"] == "1234567890123456"
    assert created == [{
        "puskesmas_id": puskesmas_id,
        "patient_id": patient_id,
        "triggered_by_id": uuid.UUID(int=0),
        "triggered_by_type": TriggererType.INTEGRATION,
    }]
    assert queued == [
        (("create.run_one",), {"args": [str(job_id)], "kwargs": {"headless": True}})
    ]


def test_external_create_rejects_bad_token(monkeypatch):
    monkeypatch.setattr(settings, "ASIK_CREATE_TRIGGER_TOKEN", "integration-secret")
    fake_db = MagicMock()
    app.dependency_overrides[get_db] = lambda: fake_db

    try:
        with TestClient(app) as client:
            response = client.post(
                f"/integrations/asik/patients/{uuid.uuid4()}/create",
                headers={"Authorization": "Bearer wrong-token"},
                json={},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 401
    fake_db.scalar.assert_not_called()


def test_external_create_is_disabled_without_trigger_token(monkeypatch):
    monkeypatch.setattr(settings, "ASIK_CREATE_TRIGGER_TOKEN", None)
    fake_db = MagicMock()
    app.dependency_overrides[get_db] = lambda: fake_db

    try:
        with TestClient(app) as client:
            response = client.post(
                f"/integrations/asik/patients/{uuid.uuid4()}/create",
                json={},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    fake_db.scalar.assert_not_called()
