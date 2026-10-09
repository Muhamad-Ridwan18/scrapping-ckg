"""Pull CKG answers from a SIMPUS GET /api/v1/ckg/jawaban export.

The payload is already keyed by ASIK form and question codes
({FRM: {PPM: value}}). This module turns that into the ASIK scrape blob
the patient detail page renders: pelayanan_nakes / pemeriksaan_mandiri
with question labels, not codes.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_JAWABAN_SUFFIX = "/api/v1/ckg/jawaban"
_LOGIN_SUFFIX = "/api/v1/auth/login"
_MAX_PAGES = 500
_PAGE_SIZE = 200
_TIMEOUT_SECONDS = 60


class SimpusApiError(RuntimeError):
    pass


class SimpusUnauthorized(SimpusApiError):
    """Jawaban API rejected the bearer token."""


@dataclass(frozen=True)
class _Question:
    label: str
    mandiri: bool
    choices: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class _Form:
    layanan: str
    mandiri: bool
    questions: dict[str, _Question]


def _api_origin(api_url: str) -> str:
    base = api_url.rstrip("/")
    if base.endswith(_JAWABAN_SUFFIX):
        base = base[: -len(_JAWABAN_SUFFIX)]
    if base.endswith(_LOGIN_SUFFIX):
        base = base[: -len(_LOGIN_SUFFIX)]
    return base.rstrip("/")


def login_endpoint(api_url: str) -> str:
    return _api_origin(api_url) + _LOGIN_SUFFIX


def login_instansi(api_url: str, email: str, password: str) -> str:
    """POST /api/v1/auth/login and return the instansi bearer token."""
    body = json.dumps({"email": email, "password": password}).encode("utf-8")
    request = Request(
        login_endpoint(api_url),
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        message = _api_message(detail) or f"SIMPUS HTTP {exc.code}"
        raise SimpusApiError(message) from exc
    except URLError as exc:
        raise SimpusApiError(f"SIMPUS tidak terjangkau: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise SimpusApiError("SIMPUS mengembalikan JSON yang tidak valid") from exc
    if not isinstance(payload, dict) or payload.get("success") is not True:
        raise SimpusApiError(str(_payload_message(payload) or "Login SIMPUS ditolak"))
    data = payload.get("data")
    token = data.get("token") if isinstance(data, dict) else None
    if not isinstance(token, str) or not token.strip():
        raise SimpusApiError("Login SIMPUS tidak mengembalikan token")
    return token.strip()


def _payload_message(payload: Any) -> str | None:
    if isinstance(payload, dict) and isinstance(payload.get("message"), str):
        return payload["message"]
    return None


def _api_message(raw: str) -> str | None:
    try:
        return _payload_message(json.loads(raw))
    except json.JSONDecodeError:
        return raw.strip() or None


def jawaban_endpoint(api_url: str) -> str:
    return _api_origin(api_url) + _JAWABAN_SUFFIX


def _qid(raw: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", raw)
    return cleaned or raw


def _is_mandiri(tipe: object) -> bool:
    return isinstance(tipe, str) and "mandiri" in tipe.lower()


def _choices(question: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for choice in question.get("choices") or []:
        if not isinstance(choice, dict):
            continue
        code = choice.get("code")
        line = choice.get("line")
        if isinstance(code, str) and code and isinstance(line, str) and line:
            out[code] = line
    return out


@lru_cache(maxsize=1)
def _catalog() -> dict[str, _Form]:
    forms: dict[str, _Form] = {}
    umum_path = _DATA_DIR / "asik_form_mapping.json"
    if umum_path.is_file():
        umum = json.loads(umum_path.read_text(encoding="utf-8"))
        for form in umum.get("forms") or []:
            if not isinstance(form, dict):
                continue
            frm = form.get("frm_code")
            if not isinstance(frm, str) or not frm:
                continue
            questions: dict[str, _Question] = {}
            for question in form.get("questions") or []:
                if not isinstance(question, dict):
                    continue
                codes = question.get("parameter_codes") or []
                code = codes[0] if codes else None
                label = question.get("label")
                if not isinstance(code, str) or not isinstance(label, str) or not label:
                    continue
                meta = _Question(
                    label=label,
                    mandiri=_is_mandiri(question.get("tipe")),
                    choices=_choices(question),
                )
                questions[code] = meta
                questions[_qid(code)] = meta
            forms[frm] = _Form(
                layanan=str(form.get("layanan_name") or frm),
                mandiri=False,
                questions=questions,
            )

    sekolah_path = _DATA_DIR / "asik_sekolah_form_catalog.json"
    if sekolah_path.is_file():
        sekolah = json.loads(sekolah_path.read_text(encoding="utf-8"))
        raw_forms = sekolah.get("forms") or {}
        if isinstance(raw_forms, dict):
            for frm, form in raw_forms.items():
                if frm in forms or not isinstance(form, dict):
                    continue
                form_mandiri = _is_mandiri(form.get("tipe"))
                questions = {}
                for question in form.get("questions") or []:
                    if not isinstance(question, dict):
                        continue
                    parameter = question.get("parameter") or question.get("label")
                    label = question.get("label") or parameter
                    if not isinstance(parameter, str) or not isinstance(label, str) or not label:
                        continue
                    meta = _Question(label=label, mandiri=form_mandiri)
                    questions[parameter] = meta
                    questions[_qid(parameter)] = meta
                forms[str(frm)] = _Form(
                    layanan=str(form.get("layanan") or frm),
                    mandiri=form_mandiri,
                    questions=questions,
                )
    return forms


def _display(value: Any, question: _Question | None) -> Any:
    if isinstance(value, str):
        text = value.strip()
        if question and text in question.choices:
            return question.choices[text]
        return text
    if isinstance(value, bool):
        return "Ya" if value else "Tidak"
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, list):
        parts = [str(_display(item, question)) for item in value if item not in (None, "")]
        return ", ".join(parts)
    return str(value)


def jawaban_to_asik_blob(nik: str, jawaban: dict) -> dict:
    """Map one API `jawaban` object into the ASIK patient blob."""
    catalog = _catalog()
    nakes: dict[str, dict[str, Any]] = {}
    mandiri: dict[str, dict[str, Any]] = {}
    for frm, fields in jawaban.items():
        if not isinstance(frm, str) or not isinstance(fields, dict):
            continue
        form = catalog.get(frm)
        layanan = form.layanan if form else frm
        for key, raw in fields.items():
            if not isinstance(key, str) or raw is None:
                continue
            if isinstance(raw, str) and not raw.strip():
                continue
            question = None
            if form is not None:
                question = form.questions.get(key) or form.questions.get(_qid(key))
            label = question.label if question else key
            shown = _display(raw, question)
            if shown is None or shown == "":
                continue
            is_mandiri = bool(
                (question and question.mandiri) or (form and form.mandiri)
            )
            bucket = mandiri if is_mandiri else nakes
            bucket.setdefault(layanan, {})[label] = shown

    def _blocks(groups: dict[str, dict[str, Any]]) -> list[dict]:
        return [
            {"layanan": name, "form_data": fields}
            for name, fields in groups.items()
            if fields
        ]

    return {
        "detail_data": {"data_individu": {"NIK": nik}},
        "pelayanan_nakes": _blocks(nakes),
        "pemeriksaan_mandiri": _blocks(mandiri),
        "source": "simpus",
    }


def simpus_blob_to_sync_preview(blob: dict | None) -> dict[str, dict]:
    """Turn a SIMPUS ASIK blob into the form→field map the ASIK sync runner fills.

    The runner already accepts this shape (`asik_preview`): each key is a
    layanan name, each value is label→answer. Both nakes and mandiri blocks
    are included. Later blocks do not overwrite a label already set.
    """
    if not isinstance(blob, dict):
        return {}
    out: dict[str, dict] = {}
    for key in ("pelayanan_nakes", "pemeriksaan_mandiri"):
        blocks = blob.get(key) or []
        if not isinstance(blocks, list):
            continue
        for block in blocks:
            if not isinstance(block, dict):
                continue
            layanan = block.get("layanan")
            fields = block.get("form_data")
            if not isinstance(layanan, str) or not layanan or not isinstance(fields, dict):
                continue
            bucket = out.setdefault(layanan, {})
            for label, value in fields.items():
                if not isinstance(label, str) or label in bucket:
                    continue
                if value is None or value == "":
                    continue
                bucket[label] = value
    return {name: fields for name, fields in out.items() if fields}


def fetch_jawaban_page(
    api_url: str,
    token: str,
    *,
    page: int,
    jenis: str | None = None,
    nik: str | None = None,
    tanggal: str | None = None,
    tanggal_dari: str | None = None,
    tanggal_sampai: str | None = None,
) -> dict:
    params: dict[str, str] = {"page": str(page), "per_page": str(_PAGE_SIZE)}
    if jenis:
        params["jenis"] = jenis
    if nik:
        params["nik"] = nik
    if tanggal:
        params["tanggal"] = tanggal
    if tanggal_dari:
        params["tanggal_dari"] = tanggal_dari
    if tanggal_sampai:
        params["tanggal_sampai"] = tanggal_sampai
    url = f"{jawaban_endpoint(api_url)}?{urlencode(params)}"
    request = Request(
        url,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        method="GET",
    )
    try:
        with urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        message = _api_message(detail) or f"SIMPUS HTTP {exc.code}"
        if exc.code == 401:
            raise SimpusUnauthorized(message) from exc
        raise SimpusApiError(message) from exc
    except URLError as exc:
        raise SimpusApiError(f"SIMPUS tidak terjangkau: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise SimpusApiError("SIMPUS mengembalikan JSON yang tidak valid") from exc
    if not isinstance(payload, dict) or payload.get("success") is not True:
        message = payload.get("message") if isinstance(payload, dict) else None
        raise SimpusApiError(str(message or "SIMPUS menolak permintaan"))
    return payload


def iter_jawaban(
    api_url: str,
    token: str,
    *,
    jenis: str | None = None,
    nik: str | None = None,
    tanggal: str | None = None,
    tanggal_dari: str | None = None,
    tanggal_sampai: str | None = None,
):
    page = 1
    while page <= _MAX_PAGES:
        payload = fetch_jawaban_page(
            api_url,
            token,
            page=page,
            jenis=jenis,
            nik=nik,
            tanggal=tanggal,
            tanggal_dari=tanggal_dari,
            tanggal_sampai=tanggal_sampai,
        )
        rows = payload.get("data") or []
        if not isinstance(rows, list):
            raise SimpusApiError("SIMPUS data bukan daftar")
        yield from rows
        meta = payload.get("meta") or {}
        last_page = int(meta.get("last_page") or page)
        if page >= last_page or not rows:
            return
        page += 1
