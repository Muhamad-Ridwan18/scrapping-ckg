"""Tests for the ASIK create-patient helpers (app/tasks/create_patient.py).

These guard the parts where a SILENT wrong answer would register wrong data into a
real citizen's ASIK record:
  - identity derived from the NIK (Dukcapil validates the NIK's own DDMMYY, so a
    disagreeing DB birth_date must never win — the documented ARYANATA class);
  - the "never guess a domicile" skip (no configured alamat → SKIP, not a blank/wrong
    address).
The create_batch orchestration itself is straight branching glue over these + the
scraper subprocess, so it is not unit-tested here.
"""

from datetime import date
from types import SimpleNamespace

import httpx
import pytest

from app.celery_app import celery_app
from app.config import settings
from app.services.simpus_jawaban import pendaftaran_from_row
from app.tasks.create_patient import (
    _build_step2,
    _derive_from_nik,
    _post_registration_result_event,
    _queue_registration_result_webhook,
    _registration_result_payload,
    _title_case_name,
    _wali_block_reason,
    _whatsapp_from_epus,
    step2_from_simpus,
)


# --- _derive_from_nik: DOB + gender come from the NIK, NOT patient.birth_date ---
def test_derive_from_nik_male_recent_century():
    # digits 7-12 = 240699 → 24 Jun 1999, male (day <= 40). The ARYANATA class: a DB
    # birth_date that disagrees with the NIK must NOT win — Dukcapil checks the NIK.
    dob, gender = _derive_from_nik("1301112406990003")
    assert dob == date(1999, 6, 24)
    assert gender == "Laki-laki"


def test_derive_from_nik_female_day_plus_40():
    # digits 7-12 = 430303 → day 43 > 40 ⇒ female, real day 3 → 3 Mar 2003 (MARIANA,
    # the live-verified registration).
    dob, gender = _derive_from_nik("3275044303030016")
    assert dob == date(2003, 3, 3)
    assert gender == "Perempuan"


def test_derive_from_nik_century_prefers_recent_past():
    # yy=85 → 1985 (a 2085 birth date is in the future ⇒ rejected).
    dob, _ = _derive_from_nik("3275041505850001")
    assert dob == date(1985, 5, 15)


def test_derive_from_nik_rejects_short():
    with pytest.raises(ValueError):
        _derive_from_nik("12345")


# --- _build_step2: never guess a domicile ---
_ALAMAT_CFG = {
    "provinsi": {"name": "Jawa Barat", "code": "32"},
    "kota": {"name": "Kota Bekasi", "code": "3275"},
    "kecamatan": {"name": "Bekasi Selatan", "code": "327504"},
    "kelurahan": {"name": "Pekayonjaya", "code": "3275041003"},
}


def test_build_step2_skips_when_no_default_alamat():
    # No configured alamat ⇒ None ⇒ caller SKIPs (we never fabricate an address).
    pk = SimpleNamespace(asik_default_alamat=None)
    assert _build_step2(None, {"data_pasien": {"Alamat": "JL X"}}, pk) is None


def test_build_step2_uses_cfg_names_and_epus_detail():
    pk = SimpleNamespace(asik_default_alamat=_ALAMAT_CFG)
    epus = {"data_pasien": {
        "Alamat": "JL. KEMANDORAN  RT 008\n RW 022",
        "Status Perkawinan": None,
    }}
    s = _build_step2(None, epus, pk)
    assert s["pekerjaan"] == "Lainnya"
    assert s["status_pernikahan"] == "Belum Menikah"
    assert s["disabilitas"] == "Tidak memiliki disabilitas"
    assert s["alamat"] == {
        "provinsi": "Jawa Barat",
        "kota": "Kota Bekasi",
        "kecamatan": "Bekasi Selatan",
        "kelurahan": "Pekayonjaya",
    }
    # ePus free-text address → Detail Alamat, whitespace-collapsed.
    assert s["detail_alamat"] == "JL. KEMANDORAN RT 008 RW 022"


def test_step2_from_simpus_uses_jawaban_row():
    pk = SimpleNamespace(asik_default_alamat=_ALAMAT_CFG)
    pend = pendaftaran_from_row({
        "status_pernikahan": "Menikah",
        "pekerjaan": "Ibu Rumah Tangga",
        "nomor_hp": "081234567890",
        "alamat": "Jl. Melati No. 12 RT 003 RW 005",
        "alamat_domisili": {
            "province_name": "DKI JAKARTA",
            "regency_name": "KOTA JAKARTA SELATAN",
            "district_name": "KEBAYORAN LAMA",
            "village_name": "GROGOL UTARA",
        },
    })
    s = step2_from_simpus(pend, pk)
    assert s["status_pernikahan"] == "Menikah"
    assert s["pekerjaan"] == "Ibu Rumah Tangga"
    assert s["detail_alamat"] == "Jl. Melati No. 12 RT 003 RW 005"
    assert s["alamat"] == {
        "provinsi": "DKI JAKARTA",
        "kota": "KOTA JAKARTA SELATAN",
        "kecamatan": "KEBAYORAN LAMA",
        "kelurahan": "GROGOL UTARA",
    }


def test_step2_from_simpus_falls_back_when_domicile_missing():
    pk = SimpleNamespace(asik_default_alamat=_ALAMAT_CFG)
    s = step2_from_simpus({"pekerjaan": "Ibu Rumah Tangga"}, pk)
    assert s["alamat"]["kelurahan"] == "Pekayonjaya"
    assert s["pekerjaan"] == "Ibu Rumah Tangga"
    assert s["detail_alamat"] == "Pekayonjaya"


def test_build_step2_detail_falls_back_to_kelurahan():
    pk = SimpleNamespace(asik_default_alamat=_ALAMAT_CFG)
    s = _build_step2(None, {}, pk)
    assert s["detail_alamat"] == "Pekayonjaya"


# --- _whatsapp_from_epus: a fabricated number must be flagged, real ones normalized ---
def test_whatsapp_normalizes_leading_zero():
    wa, used_default = _whatsapp_from_epus(
        {"data_pasien": {"No Telp / HP": "081319373789"}}
    )
    assert wa == "81319373789"
    assert used_default is False


def test_whatsapp_falls_back_and_flags_default():
    wa, used_default = _whatsapp_from_epus({"data_pasien": {"No Telp / HP": "-"}})
    assert used_default is True
    assert wa.startswith("8")


def test_title_case_name():
    assert _title_case_name("SANTI ANI") == "Santi Ani"


# --- _wali_block_reason: the own-NIK / age create gate (a wrong pass mis-assigns a
# real citizen's ASIK identity — the fatal case the wali flow exists to prevent) ---
_EXAM = date(2026, 8, 31)


def test_wali_block_own_nik_school_age_ok():
    # NIK DDMMYY 150518 → 15 May 2018 (age 8 at exam), own NIK → creatable via own NIK.
    assert _wali_block_reason("3275041505180001", date(2018, 5, 15), _EXAM) is None


def test_wali_block_under_6_needs_wali_even_with_own_nik():
    # 10 Jan 2022 → age 4 at exam. Own NIK, but ASIK mandates wali for <6 (live-verified).
    r = _wali_block_reason("3275041001220001", date(2022, 1, 10), _EXAM)
    assert r is not None and "under 6" in r


def test_wali_block_minor_recorded_under_guardian_nik():
    # A child (birth_date 15 May 2018, age 8) recorded under an ADULT's NIK (24 Jun 1999)
    # → NIK's DOB ≠ birth_date → guardian's NIK → blocked (registering by it mis-assigns).
    r = _wali_block_reason("1301112406990003", date(2018, 5, 15), _EXAM)
    assert r is not None and "guardian" in r


def test_wali_block_adult_mismatch_not_gated():
    # An adult (age 36) whose DB birth_date disagrees with the NIK is NOT own-NIK-gated —
    # only minors are; an adult's NIK is their own and registration derives DOB from it.
    assert _wali_block_reason("3275041505850001", date(1990, 1, 1), _EXAM) is None


def test_wali_block_missing_birth_date():
    assert _wali_block_reason("3275041505180001", None, _EXAM) is not None


def test_registration_result_webhook_uses_bearer_and_idempotency_headers():
    received = {}

    def handle(request):
        received["request"] = request
        return httpx.Response(202)

    client = httpx.Client(transport=httpx.MockTransport(handle))
    try:
        status = _post_registration_result_event(
            client,
            url="https://receiver.example/events",
            token="test-token",
            event_id="job-123",
            payload={"event": "asik.registration.result"},
        )
    finally:
        client.close()

    assert status == 202
    request = received["request"]
    assert request.method == "POST"
    assert request.headers["Authorization"] == "Bearer test-token"
    assert request.headers["Idempotency-Key"] == "job-123"
    assert request.read() == b'{"event":"asik.registration.result"}'


def test_registration_result_webhook_payload_has_patient_and_status_fields():
    patient = SimpleNamespace(
        id="patient-456",
        nama="Example Patient",
        nik="1234567890123456",
        puskesmas_id="puskesmas-789",
    )

    payload = _registration_result_payload(
        job_id="job-123",
        patient=patient,
        status="failed",
        outcome="dukcapil_invalid",
        message="Dukcapil validation failed.",
    )

    assert payload["event"] == "asik.registration.result"
    assert payload["event_id"] == payload["job_id"] == "job-123"
    assert payload["patient_id"] == "patient-456"
    assert payload["patient_name"] == "Example Patient"
    assert payload["nik"] == "1234567890123456"
    assert payload["puskesmas_id"] == "puskesmas-789"
    assert payload["status"] == "failed"
    assert payload["outcome"] == "dukcapil_invalid"
    assert payload["message"] == "Dukcapil validation failed."
    assert payload["occurred_at"]

    success_payload = _registration_result_payload(
        job_id="job-124",
        patient=patient,
        status="success",
        outcome="created",
        message="Patient registered in ASIK.",
    )
    assert success_payload["event"] == "asik.registration.result"
    assert success_payload["status"] == "success"
    assert success_payload["outcome"] == "created"


def test_registration_result_webhook_is_queued_without_patient_data(monkeypatch):
    sent = []
    monkeypatch.setattr(settings, "ASIK_CREATE_WEBHOOK_URL", "https://receiver.example/events")
    monkeypatch.setattr(
        celery_app,
        "send_task",
        lambda *args, **kwargs: sent.append((args, kwargs)),
    )
    job = SimpleNamespace(id="job-123")
    patient = SimpleNamespace(id="patient-456", nik="not-in-broker")

    _queue_registration_result_webhook(
        job=job,
        patient=patient,
        status="failed",
        outcome="dukcapil_invalid",
        message="Dukcapil validation failed.",
    )

    assert sent == [
        (
            ("create.notify_registration_result",),
            {
                "args": [
                    "job-123",
                    "patient-456",
                    "failed",
                    "dukcapil_invalid",
                    "Dukcapil validation failed.",
                ]
            },
        )
    ]
