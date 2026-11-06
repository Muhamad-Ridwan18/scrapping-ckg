"""Open one CKG Sekolah student on the ASIK detail page.

The sekolah list has no NIK search. The student is reached by school name and
class (the same comboboxes the scraper drives), then the matching Mulai row.
"""

from __future__ import annotations

import re
import time

from playwright.sync_api import Page

LIST_PATIENT_URL = "/api/pkg/anak-sekolah/list-patient"
_TABS = ("Belum Pemeriksaan", "Sedang Pemeriksaan", "Selesai Pemeriksaan")
_PRIVACY = (
    "() => /Pemberitahuan Privasi|SAYA TELAH MEMBACA/i.test(document.body.innerText)"
)


def open_school_student(
    page: Page,
    interceptor,
    base_url: str,
    school_name: str,
    class_name: str,
    nik: str,
) -> dict:
    page.goto(
        f"{base_url.rstrip('/')}/ckg-pelayanan-sekolah",
        wait_until="networkidle",
        timeout=60000,
    )
    page.wait_for_timeout(1500)
    _accept_privacy(page)
    if not _select_option(page, ["Pilih sekolah", "Pilih Sekolah"], school_name, search=True):
        return {"found": False, "error": f"sekolah '{school_name}' tidak ada di ASIK"}
    page.wait_for_timeout(600)
    if not _select_option(page, ["Pilih kelas", "Pilih Kelas"], class_name, search=False):
        return {
            "found": False,
            "error": f"kelas '{class_name}' tidak ada untuk sekolah '{school_name}'",
        }
    try:
        page.get_by_text("Tampilkan Pencarian", exact=False).first.click(timeout=5000)
    except Exception:
        return {"found": False, "error": "Tampilkan Pencarian tidak bisa diklik"}
    page.wait_for_timeout(2000)

    for tab in _TABS:
        _click_tab(page, tab)
        since = len(interceptor.all_responses)
        page.wait_for_timeout(400)
        page_num = 1
        while page_num <= 30:
            rows, pagination = _wait_list(page, interceptor, since)
            index = _nik_index(rows, nik)
            if index is not None:
                if not _click_row_action(page, index):
                    return {
                        "found": False,
                        "error": "baris siswa ketemu tapi tombol Mulai tidak bisa diklik",
                    }
                return {"found": True, "tab": tab}
            total = int(pagination.get("total_page") or 1)
            if page_num >= total:
                break
            since = len(interceptor.all_responses)
            if not _click_next_page(page, page_num + 1):
                break
            page.wait_for_timeout(1500)
            page_num += 1
    return {
        "found": False,
        "error": (
            f"NIK {nik} tidak ada di {school_name} / {class_name} "
            "(Belum, Sedang, maupun Selesai)"
        ),
    }


def _nik_index(rows: list, nik: str) -> int | None:
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        patient = row.get("patient") or {}
        if not isinstance(patient, dict):
            continue
        if str(patient.get("nik") or "").strip() == nik:
            return index
    return None


def _wait_list(page: Page, interceptor, since: int, timeout_s: float = 8):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        found = _latest_list(interceptor, since)
        if found is not None:
            return found
        page.wait_for_timeout(200)
    return [], {}


def _latest_list(interceptor, since: int):
    responses = getattr(interceptor, "all_responses", None) or []
    for entry in reversed(responses[since:]):
        url = entry.get("url") or ""
        if not str(url).endswith(LIST_PATIENT_URL) or entry.get("status") != 200:
            continue
        body = entry.get("body") or {}
        data = body.get("data")
        if isinstance(data, list):
            pagination = body.get("pagination") or {}
            return data, pagination if isinstance(pagination, dict) else {}
    return None


def _click_row_action(page: Page, index: int) -> bool:
    buttons = page.locator("button", has_text=re.compile(r"^\s*(Mulai|Lanjutkan)\s*$"))
    try:
        count = buttons.count()
    except Exception:
        return False
    if index >= count:
        return False
    try:
        buttons.nth(index).click(timeout=5000)
        return True
    except Exception:
        return False


def _select_option(page: Page, triggers: list[str], option_text: str, *, search: bool) -> bool:
    if not _open_combobox(page, triggers + [option_text]):
        return False
    if search:
        try:
            page.locator("input:visible").last.fill(option_text[:24])
            page.wait_for_timeout(900)
        except Exception:
            pass
    try:
        page.get_by_text(option_text, exact=True).first.click(timeout=5000)
        page.wait_for_timeout(800)
        return True
    except Exception:
        pass
    try:
        page.get_by_text(option_text, exact=False).first.click(timeout=4000)
        page.wait_for_timeout(800)
        return True
    except Exception:
        return False


def _open_combobox(page: Page, candidate_texts: list[str]) -> bool:
    for text in candidate_texts:
        try:
            el = page.get_by_text(text, exact=True).first
            if el.is_visible():
                el.click(timeout=4000)
                page.wait_for_timeout(700)
                return True
        except Exception:
            continue
    return False


def _click_tab(page: Page, tab_name: str) -> None:
    try:
        page.get_by_role("tab", name=tab_name).first.click(timeout=3000)
        page.wait_for_timeout(800)
        return
    except Exception:
        pass
    try:
        page.get_by_text(tab_name, exact=False).first.click(timeout=3000)
        page.wait_for_timeout(800)
    except Exception:
        pass


def _click_next_page(page: Page, target_page: int) -> bool:
    for scope in ('[class*="pagination"]', "nav", "ul"):
        try:
            region = page.locator(scope).filter(has_text=str(target_page)).last
            button = region.get_by_text(str(target_page), exact=True).first
            if button.is_visible():
                button.click(timeout=2500)
                return True
        except Exception:
            continue
    return False


def _accept_privacy(page: Page) -> None:
    try:
        page.wait_for_function(_PRIVACY, timeout=6000)
    except Exception:
        return
    for _ in range(3):
        try:
            if not page.evaluate(_PRIVACY):
                return
        except Exception:
            return
        try:
            box = page.locator("input[type=checkbox]").first
            if box.is_visible():
                box.check(timeout=4000)
                page.wait_for_timeout(250)
        except Exception:
            pass
        try:
            page.get_by_text("Setuju", exact=True).first.click(timeout=4000)
            page.wait_for_timeout(900)
        except Exception:
            pass
