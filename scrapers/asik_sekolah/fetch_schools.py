"""Fetch and cache all schools for the Puskesmas from ASIK.

ASIK fires GET /api/pkg/sarana/get-sarana-sekolah on the ckg-pelayanan-sekolah page.
This script reuses the existing login session, loads the page, intercepts the response,
and writes the full school list to backend/app/data/asik_schools_cache.json.

Usage:
    python fetch_schools.py
    python fetch_schools.py --no-headless
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

SCRAPERS_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRAPERS_ROOT))
sys.path.insert(0, str(SCRAPERS_ROOT / "asik"))

# Auto-discover the Puskesmas session directory
def _discover_session_dir() -> Path | None:
    if os.environ.get("SCRAPER_SESSION_DIR"):
        p = Path(os.environ["SCRAPER_SESSION_DIR"])
        if p.exists():
            return p

    candidate_roots = [
        Path(os.environ.get("SCRAPER_SESSIONS_ROOT") or "/data/scraper-sessions"),
        SCRAPERS_ROOT.parent / ".scraper-sessions",
        SCRAPERS_ROOT / "asik" / "session",
    ]
    for root in candidate_roots:
        if not root.exists():
            continue
        for prog in ("asik_sekolah", "asik"):
            prog_dir = root / prog
            if prog_dir.is_dir():
                for sub in sorted(prog_dir.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
                    if sub.is_dir():
                        if (sub / "playwright_state.json").exists() or (sub / "Default").exists():
                            return sub
    return None

found_session = _discover_session_dir()
if found_session:
    os.environ["SCRAPER_SESSION_DIR"] = str(found_session)
    print(f"Using saved session at: {found_session}")
else:
    print("Warning: No existing session found, will use default.")

from helpers import (  # noqa: E402
    APIInterceptor,
    SESSION_DIR,
    launch_persistent_context,
    restore_saved_auth_state,
    save_auth_state,
    login,
    close_popups,
)
from locate import _accept_privacy  # noqa: E402
from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser(description="Fetch ASIK school list for Puskesmas")
    parser.add_argument("--headless", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--base-url", default="https://sehatindonesiaku.kemkes.go.id")
    args = parser.parse_args()

    interceptor = APIInterceptor()

    with sync_playwright() as p:
        context = launch_persistent_context(p, headless=args.headless)
        page = context.pages[0] if context.pages else context.new_page()
        restore_saved_auth_state(context, page)
        context.on("response", interceptor.on_response)

        target_url = f"{args.base_url.rstrip('/')}/ckg-pelayanan-sekolah"
        print(f"Navigating to {target_url}...")
        page.goto(target_url, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(2000)

        # Handle privacy / consent modal
        _accept_privacy(page)
        page.wait_for_timeout(1000)

        # Print all intercepted API responses for visibility
        api_urls = [r.get("url", "") for r in interceptor.all_responses]
        print(f"Intercepted {len(api_urls)} API calls so far:")
        for u in api_urls:
            print(f"  -> {u}")

        # Check for sarana in interceptor
        captured_schools = []
        for entry in reversed(interceptor.all_responses):
            url = str(entry.get("url") or "")
            if "sarana" in url.lower() and entry.get("status") == 200:
                data = (entry.get("body") or {}).get("data")
                if isinstance(data, list) and data:
                    captured_schools = data
                    print(f"Found {len(captured_schools)} schools in response: {url}")
                    break

        # If still empty, click the combobox to trigger the fetch
        if not captured_schools:
            print("Sarana not found yet. Nudging combobox...")
            for selector in [
                "text=Pilih sekolah",
                "text=Pilih Sekolah",
                '[class*="select"]:visible',
                '[role="combobox"]:visible',
            ]:
                try:
                    el = page.locator(selector).first
                    if el.is_visible():
                        el.click(timeout=3000)
                        page.wait_for_timeout(2000)
                        page.keyboard.press("Escape")
                        break
                except Exception:
                    pass

            # Check interceptor again after click
            for entry in reversed(interceptor.all_responses):
                url = str(entry.get("url") or "")
                if "sarana" in url.lower() and entry.get("status") == 200:
                    data = (entry.get("body") or {}).get("data")
                    if isinstance(data, list) and data:
                        captured_schools = data
                        print(f"Found {len(captured_schools)} schools after click: {url}")
                        break

        save_auth_state(context, page, args.base_url)
        context.close()

    if not captured_schools:
        print("[ERROR] Could not capture school list from ASIK.")
        print("All intercepted URLs were:")
        for r in interceptor.all_responses:
            print(f"  [{r.get('status')}] {r.get('url')}")
        sys.exit(1)

    # Format cleanly
    schools = []
    seen = set()
    for s in captured_schools:
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

        schools.append({
            "name": name,
            "code": s.get("ihs_no") or s.get("school_code") or "",
            "category": cat,
            "classes": classes,
        })

    schools.sort(key=lambda x: x["name"])

    # Save to candidate locations
    saved_to = []
    candidates = [
        Path(__file__).resolve().parent.parent.parent / "backend" / "app" / "data",
        Path("/app/app/data"),
        Path("/app/backend/app/data"),
        Path(__file__).resolve().parent,
    ]
    for d in candidates:
        if d.is_dir():
            out_file = d / "asik_schools_cache.json"
            out_file.write_text(json.dumps(schools, ensure_ascii=False, indent=2), encoding="utf-8")
            saved_to.append(str(out_file))

    print(f"\n[OK] Berhasil menarik {len(schools)} sekolah dari ASIK:")
    for s in schools:
        print(f"  - {s['name']} ({s['category']}) -> {len(s['classes'])} kelas")
    print(f"\nDisimpan ke: {', '.join(saved_to)}")


if __name__ == "__main__":
    main()
