"""Pesan gagal ASIK yang ditampilkan ke petugas SIMPUS (bukan log teknis)."""

from __future__ import annotations

import re

# Kode outcome register → kalimat untuk user awam.
_REGISTER_OUTCOME_ID = {
    "dukcapil_invalid": (
        "NIK, nama, atau tanggal lahir tidak cocok dengan data Dukcapil. "
        "Periksa kembali data pasien, lalu kirim ulang."
    ),
    "already_served": (
        "Pasien sudah terdaftar atau sudah dilayani di ASIK pada tanggal tersebut."
    ),
    "step2_filled": "Pendaftaran ke ASIK belum selesai. Hubungi admin CKG.",
    "needs_wali": (
        "Pasien masih anak dan membutuhkan data wali untuk didaftarkan ke ASIK. "
        "Lengkapi data wali di SIMPUS, lalu kirim ulang."
    ),
    "missing_default_address": (
        "Alamat default ASIK belum diisi di pengaturan puskesmas. Hubungi admin."
    ),
    "invalid_identity_data": (
        "NIK pasien tidak valid atau tidak bisa dipakai untuk daftar ke ASIK. "
        "Periksa NIK di SIMPUS."
    ),
    "cancelled": "Pengiriman dibatalkan sebelum selesai.",
    "error": (
        "Gagal mendaftarkan pasien ke ASIK. Coba kirim ulang beberapa saat lagi. "
        "Jika berulang, hubungi admin."
    ),
}

_CONFIG = {
    "asik_url_missing": "Alamat website ASIK belum diatur di CKG. Hubungi admin.",
    "asik_cred_missing": "Akun login ASIK belum diatur di CKG. Hubungi admin.",
    "asik_alamat_missing": (
        "Alamat default ASIK belum diisi di pengaturan puskesmas. Hubungi admin."
    ),
    "decrypt_failed": (
        "Data jawaban pasien tidak bisa dibaca. Hubungi admin untuk diperiksa."
    ),
    "fill_failed": (
        "Pengisian form pemeriksaan di ASIK gagal. Coba kirim ulang. "
        "Jika berulang, hubungi admin."
    ),
    "fill_cancelled": "Pengisian form di ASIK dibatalkan sebelum selesai.",
    "create_crashed": (
        "Terjadi gangguan saat mendaftarkan ke ASIK. Coba kirim ulang nanti."
    ),
    "sync_crashed": (
        "Terjadi gangguan saat mengisi form ASIK. Coba kirim ulang nanti."
    ),
}


def patient_prefix(nama: str | None, nik: str | None = None) -> str:
    name = (nama or "").strip() or "Pasien"
    digits = re.sub(r"\D", "", nik or "")
    if len(digits) >= 4:
        return f"{name} (NIK …{digits[-4:]})"
    return name


def for_patient(nama: str | None, message: str, *, nik: str | None = None) -> str:
    return f"{patient_prefix(nama, nik)}: {message}"


def register_outcome(outcome: str | None, detail: str | None = None) -> str:
    key = (outcome or "error").strip().lower()
    if key in _REGISTER_OUTCOME_ID:
        return _REGISTER_OUTCOME_ID[key]
    # Detail teknis (exit code, traceback) tidak ditampilkan ke petugas.
    _ = detail
    return _REGISTER_OUTCOME_ID["error"]


def wali_or_alamat(block: str | None) -> str:
    if not block:
        return _REGISTER_OUTCOME_ID["missing_default_address"]
    low = block.lower()
    if "under 6" in low or "wali" in low:
        return _REGISTER_OUTCOME_ID["needs_wali"]
    if "guardian" in low or "does not match" in low:
        return (
            "NIK pasien tampaknya milik orang lain (misalnya wali), "
            "bukan cocok dengan tanggal lahir. Perbaiki NIK di SIMPUS, lalu kirim ulang."
        )
    if "birth_date" in low or "no birth" in low:
        return (
            "Tanggal lahir pasien belum lengkap. Lengkapi di SIMPUS, lalu kirim ulang."
        )
    if "alamat" in low:
        return _REGISTER_OUTCOME_ID["missing_default_address"]
    return _REGISTER_OUTCOME_ID["needs_wali"]


def identity_error(_exc: BaseException | str | None = None) -> str:
    return _REGISTER_OUTCOME_ID["invalid_identity_data"]


def config(key: str) -> str:
    return _CONFIG.get(key, _CONFIG["create_crashed"])


def fill_status(status: str | None, detail: str | None = None) -> str:
    s = (status or "").strip().lower()
    if s == "cancelled":
        return _CONFIG["fill_cancelled"]
    text = " ".join(x for x in (detail or "", s) if x).lower()
    if "not found" in text or "tidak ditemukan" in text:
        return (
            "Pasien belum ditemukan di ASIK untuk tanggal pemeriksaan ini. "
            "Daftarkan dulu ke ASIK, atau aktifkan opsi daftar pasien baru, lalu kirim ulang."
        )
    if "login" in text or "credential" in text or "captcha" in text:
        return (
            "Gagal masuk ke ASIK (login/captcha). Coba kirim ulang. "
            "Jika berulang, hubungi admin."
        )
    return _CONFIG["fill_failed"]
