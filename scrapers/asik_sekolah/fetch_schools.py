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
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

SCRAPERS_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRAPERS_ROOT))
sys.path.insert(0, str(SCRAPERS_ROOT / "asik"))

from helpers import (  # noqa: E402
    SESSION_DIR,
    launch_persistent_context,
    restore_saved_auth_state,
    save_auth_state,
    login,
    close_popups,
)
from locate import _accept_privacy  # noqa: E402

SARANA_URL = "/api/pkg/sarana/get-sarana-sekolah"


def main():
    parser = argparse.ArgumentParser(description="Fetch ASIK school list for Puskesmas")
    parser.add_argument("--headless", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--base-url", default="https://sehatindonesiaku.kemkes.go.id")
    args = parser.parse_args()

    captured_schools = []

    def on_response(response):
        url = response.url or ""
        if url.endswith(SARANA_URL) and response.status == 200:
            try:
                body = response.json()
                data = body.get("data")
                if isinstance(data, list) and data:
                    captured_schools.extend(data)
            except Exception:
                pass

    with sync_playwright() as p:
        context = launch_persistent_context(p, headless=args.headless)
        page = context.pages[0] if context.pages else context.new_page()
        restore_saved_auth_state(context, page)
        context.on("response", on_response)

        target_url = f"{args.base_url.rstrip('/')}/ckg-pelayanan-sekolah"
        print(f"Navigating to {target_url}...")
        page.goto(target_url, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(2000)

        # Handle privacy / consent modal
        _accept_privacy(page)
        page.wait_for_timeout(1000)

        # Wait for SARANA_URL to fire (up to 10 seconds)
        deadline = time.time() + 10
        while not captured_schools and time.time() < deadline:
            page.wait_for_timeout(500)

        # Nudge combobox if still not captured
        if not captured_schools:
            try:
                el = page.get_by_text("Pilih sekolah", exact=True).first
                if el.is_visible():
                    el.click(timeout=3000)
                    page.wait_for_timeout(1500)
                    page.keyboard.press("Escape")
            except Exception:
                pass

        save_auth_state(context, page, args.base_url)
        context.close()

    if not captured_schools:
        print("[ERROR] Could not capture school list from ASIK.")
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
