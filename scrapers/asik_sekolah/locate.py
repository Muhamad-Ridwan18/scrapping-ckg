"""Open one CKG Sekolah student on the ASIK detail page.

The sekolah list has no NIK search. The student is reached by school name and
class (the same comboboxes the scraper drives), then the matching Mulai row.
"""

from __future__ import annotations

import re
import time
from difflib import SequenceMatcher

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
    page.wait_for_timeout(2000)
    _accept_privacy(page)
    page.wait_for_timeout(1000)
    _cache_schools_from_interceptor(interceptor)

    if not _select_school_fuzzy(page, school_name):
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


def _cache_schools_from_interceptor(interceptor) -> None:
    """Extract and save school list from get-sarana-sekolah response."""
    import json
    from pathlib import Path

    sarana_url = "/api/pkg/sarana/get-sarana-sekolah"
    raw_schools = []
    for entry in reversed(getattr(interceptor, "all_responses", [])):
        url = str(entry.get("url") or "")
        if url.endswith(sarana_url) and entry.get("status") == 200:
            data = (entry.get("body") or {}).get("data")
            if isinstance(data, list) and data:
                raw_schools = data
                break

    if not raw_schools:
        return

    schools = []
    seen = set()
    for s in raw_schools:
        name = (s.get("school_name") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        cat = (s.get("category_short_name") or s.get("category_code_name") or "").upper().strip()
        classes = []
        if "SD" in cat or "MI" in cat:
            classes = [f"Kelas {i}" for i in range(1, 7)]
        elif "SMP" in cat or "MTS" in cat:
            classes = [f"Kelas {i}" for i in range(7, 10)]
        elif "SMA" in cat or "SMK" in cat or "MA" in cat:
            classes = [f"Kelas {i}" for i in range(10, 13)]
        else:
            classes = [f"Kelas {i}" for i in range(1, 13)]
        schools.append({"name": name, "classes": classes})

    schools.sort(key=lambda x: x["name"])
    candidates = [
        Path(__file__).resolve().parent.parent.parent / "backend" / "app" / "data",
        Path("/app/app/data"),
        Path("/app/backend/app/data"),
        Path(__file__).resolve().parent,
    ]
    for d in candidates:
        if d.is_dir():
            try:
                (d / "asik_schools_cache.json").write_text(
                    json.dumps(schools, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                print(f"  [locate] cached {len(schools)} ASIK schools for SIMPUS")
            except Exception:
                pass


def _normalize_school(name: str) -> str:
    """Normalize a school name for fuzzy comparison."""
    s = name.upper().strip()
    # Common variations: SDN/SD NEGERI, SMPN/SMP NEGERI, etc.
    s = re.sub(r"\bSD\s+NEGERI\b", "SDN", s)
    s = re.sub(r"\bSMP\s+NEGERI\b", "SMPN", s)
    s = re.sub(r"\bSMA\s+NEGERI\b", "SMAN", s)
    s = re.sub(r"\bSMK\s+NEGERI\b", "SMKN", s)
    s = re.sub(r"\s+", " ", s)
    return s


def _select_school_fuzzy(page: Page, school_name: str) -> bool:
    """Try exact match first, then fuzzy-match against visible dropdown options.

    ASIK's school dropdown has a search box. We type a keyword, read the
    visible options, and pick the best match by similarity ratio.
    """
    # 1) Try exact match (fast path)
    if _select_option(page, ["Pilih sekolah", "Pilih Sekolah"], school_name, search=True):
        return True

    # 2) Fuzzy: re-open dropdown, search with a shorter keyword
    keywords = re.sub(r"\b(SD|SDN|SMP|SMPN|SMA|SMAN|SMK|SMKN|NEGERI|INPRES|SWASTA)\b", "", school_name, flags=re.I).strip()
    keywords = re.sub(r"\s+", " ", keywords).strip()
    if not keywords or len(keywords) < 3:
        keywords = school_name  # fallback to full name

    if not _open_combobox(page, ["Pilih sekolah", "Pilih Sekolah", school_name]):
        return False

    try:
        page.locator("input:visible").last.fill(keywords[:20])
        page.wait_for_timeout(1200)
    except Exception:
        return False

    # 3) Read all visible options from the dropdown
    try:
        # ponytail: assumes ant-select / rc-select option class; upgrade if ASIK changes UI framework
        options = page.locator('[class*="option"]:visible, [role="option"]:visible, .ant-select-item:visible')
        count = options.count()
        if count == 0:
            # Try broader selector
            options = page.locator("li:visible, div[class*='select']:visible")
            count = min(options.count(), 30)
    except Exception:
        page.keyboard.press("Escape")
        return False

    if count == 0:
        page.keyboard.press("Escape")
        return False

    # 4) Score each option against the input name
    norm_input = _normalize_school(school_name)
    best_score = 0.0
    best_idx = -1
    best_text = ""

    for i in range(min(count, 30)):
        try:
            text = options.nth(i).inner_text(timeout=1000).strip()
        except Exception:
            continue
        if not text or text.startswith("Pilih") or len(text) < 3:
            continue
        norm_opt = _normalize_school(text)
        score = SequenceMatcher(None, norm_input, norm_opt).ratio()
        # Boost if all significant words from input appear in option
        input_words = set(norm_input.split()) - {"SD", "SDN", "SMP", "SMPN", "SMA", "SMAN", "SMK", "SMKN"}
        opt_words = set(norm_opt.split())
        if input_words and input_words.issubset(opt_words):
            score += 0.2
        if score > best_score:
            best_score = score
            best_idx = i
            best_text = text

    # 5) Click best match if similarity >= 0.6
    if best_idx >= 0 and best_score >= 0.6:
        try:
            options.nth(best_idx).click(timeout=5000)
            page.wait_for_timeout(800)
            return True
        except Exception:
            pass

    page.keyboard.press("Escape")
    return False


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
        print(f"  [locate] _open_combobox failed for triggers={triggers}, option='{option_text}'")
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
        print(f"  [locate] option '{option_text}' not found in dropdown")
        return False


def _open_combobox(page: Page, candidate_texts: list[str]) -> bool:
    # Method 1: click element matching placeholder/selected text
    for text in candidate_texts:
        try:
            el = page.get_by_text(text, exact=True).first
            if el.is_visible():
                el.click(timeout=4000)
                page.wait_for_timeout(700)
                return True
        except Exception:
            continue
    # Method 2: try clicking ant-select / rc-select combobox by placeholder attribute
    for text in candidate_texts:
        try:
            sel = page.locator(f'[class*="select"]:has([title="{text}"]):visible, [class*="select"]:has([placeholder="{text}"]):visible').first
            if sel.is_visible():
                sel.click(timeout=4000)
                page.wait_for_timeout(700)
                return True
        except Exception:
            continue
    # Method 3: try aria/role-based combobox
    try:
        combos = page.locator('[role="combobox"]:visible')
        if combos.count() > 0:
            combos.first.click(timeout=4000)
            page.wait_for_timeout(700)
            return True
    except Exception:
        pass
    print(f"  [locate] _open_combobox: no combobox found for {candidate_texts}")
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
    """Dismiss any privacy/consent modal blocking the page.

    Handles:
    1. Checkbox #verify + button (Setuju / Lanjutkan / Simpan)
    2. Fallback DOM removal if modal overlay remains
    """
    for attempt in range(3):
        # 1) Try checking #verify checkbox if visible
        try:
            cb = page.locator("#verify:visible, input[type=checkbox]:visible").first
            if cb.is_visible():
                cb.check(timeout=3000)
                page.wait_for_timeout(400)
                print("  [locate] checked privacy #verify checkbox")
        except Exception:
            pass

        # 2) Try clicking accept button (Setuju / Lanjutkan / Simpan / Ya)
        clicked = False
        for btn_text in ["Setuju", "Lanjutkan", "Simpan", "Ya, Saya Setuju", "SAYA TELAH MEMBACA"]:
            try:
                btn = page.get_by_role("button", name=re.compile(btn_text, re.I)).first
                if btn.is_visible() and btn.is_enabled():
                    btn.click(timeout=3000)
                    page.wait_for_timeout(800)
                    print(f"  [locate] clicked privacy button: '{btn_text}'")
                    clicked = True
                    break
            except Exception:
                pass
            try:
                btn = page.locator(f"button:has-text('{btn_text}'):visible").first
                if btn.is_visible() and btn.is_enabled():
                    btn.click(timeout=3000)
                    page.wait_for_timeout(800)
                    print(f"  [locate] clicked privacy button (selector): '{btn_text}'")
                    clicked = True
                    break
            except Exception:
                pass

        page.wait_for_timeout(500)

        # 3) Check if #verify or modal overlay is still present
        still_blocked = page.evaluate("""() => {
            const cb = document.querySelector('#verify, input[type=checkbox]');
            return cb && cb.offsetParent !== null;
        }""")

        if not still_blocked:
            print("  [locate] privacy modal successfully dismissed")
            return

        # 4) If still blocked on attempt 2+, nuke the modal from DOM
        print(f"  [locate] modal still up (attempt {attempt + 1}), nuking overlay from DOM...")
        try:
            page.evaluate("""() => {
                // Find modal container containing #verify or consent text
                for (const el of document.querySelectorAll('div, section, modal')) {
                    const hasVerify = el.querySelector('#verify');
                    const text = el.innerText || '';
                    if (hasVerify || /pencatatan layanan kesehatan|Pemberitahuan Privasi|Persetujuan/i.test(text)) {
                        let m = el;
                        for (let i = 0; i < 10 && m.parentElement && m.parentElement !== document.body; i++) {
                            const cs = getComputedStyle(m.parentElement);
                            if (cs.position === 'fixed' || cs.position === 'absolute') {
                                m = m.parentElement;
                            }
                        }
                        const s = getComputedStyle(m);
                        if (s.position === 'fixed' || s.position === 'absolute' || m.clientWidth > 400) {
                            m.remove();
                            break;
                        }
                    }
                }
                // Also remove any backdrop / overlay
                document.querySelectorAll('.modal-backdrop, [class*="backdrop"], [class*="overlay"]').forEach(e => e.remove());
                document.body.style.overflow = 'auto';
                document.documentElement.style.overflow = 'auto';
            }""")
            page.wait_for_timeout(800)
        except Exception as e:
            print(f"  [locate] nuke overlay error: {e}")

    print("  [locate] _accept_privacy finished")
