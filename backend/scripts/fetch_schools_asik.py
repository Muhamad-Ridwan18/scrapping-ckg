"""Fetch all schools for the Puskesmas from ASIK and cache them.

Reuses the puskesmas credentials and CAPTCHA solver from the database.
If session is valid, skips login; if expired, logs in automatically with LLM CAPTCHA.

Usage (inside container):
    python scripts/fetch_schools_asik.py
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright
from sqlalchemy import select

# Setup python path
BACKEND_ROOT = Path(__file__).resolve().parent.parent
SCRAPERS_ROOT = BACKEND_ROOT.parent / "scrapers"
if not SCRAPERS_ROOT.exists():
    SCRAPERS_ROOT = Path("/app/scrapers")
sys.path.insert(0, str(SCRAPERS_ROOT))
sys.path.insert(0, str(SCRAPERS_ROOT / "asik"))

from app.core.security import decrypt_json
from app.database import SessionLocal
from app.models.puskesmas import Puskesmas
from app.tasks.scrape import _asik_sekolah_session_dir, _build_captcha_solver
from helpers import (
    APIInterceptor,
    launch_persistent_context,
    restore_saved_auth_state,
    save_auth_state,
)
from helpers.login import close_popups, login
from locate import _accept_privacy

SARANA_URL = "/api/pkg/sarana/get-sarana-sekolah"


def main():
    db = SessionLocal()
    try:
        # Find active Puskesmas with ASIK credentials
        p = db.scalar(
            select(Puskesmas).where(
                Puskesmas.asik_url.is_not(None),
                Puskesmas.asik_cred.is_not(None),
                Puskesmas.deleted_at.is_(None),
            ).limit(1)
        )
        if not p:
            print("[ERROR] Tidak ada Puskesmas dengan akun ASIK di database.")
            return

        print(f"Puskesmas: {p.name} (ID: {p.id})")
        base_url = f"https://{p.asik_url}" if not p.asik_url.startswith("http") else p.asik_url
        creds = decrypt_json(p.asik_cred)
        username = creds.get("email") or creds.get("username", "")
        password = creds.get("password", "")

        try:
            captcha_solver = _build_captcha_solver(db)
        except Exception:
            captcha_solver = {"type": "manual"}

        session_dir = _asik_sekolah_session_dir(p.id)
        os.environ["SCRAPER_SESSION_DIR"] = str(session_dir)
        print(f"Session dir: {session_dir}")

        cfg = {
            "credentials": {"username": username, "password": password},
            "captcha_solver": captcha_solver,
            "headless": True,
            "timeout": 60000,
        }

        interceptor = APIInterceptor()

        with sync_playwright() as pw:
            context = launch_persistent_context(pw, headless=True)
            page = context.pages[0] if context.pages else context.new_page()
            restore_saved_auth_state(context, page)
            context.on("response", interceptor.on_response)

            print(f"Navigating to {base_url}/ckg-pelayanan-sekolah...")
            page.goto(f"{base_url.rstrip('/')}/ckg-pelayanan-sekolah", wait_until="networkidle", timeout=60000)
            page.wait_for_timeout(2000)

            # Check if bounced to login
            if "/login" in page.url:
                print("Session expired/unauthenticated, logging in...")
                login(page, base_url, cfg["credentials"], headless=True, config=cfg)
                save_auth_state(context, page, base_url)
                print(f"Re-navigating to {base_url}/ckg-pelayanan-sekolah...")
                page.goto(f"{base_url.rstrip('/')}/ckg-pelayanan-sekolah", wait_until="networkidle", timeout=60000)
                page.wait_for_timeout(2000)

            # Dismiss privacy modal
            _accept_privacy(page)
            page.wait_for_timeout(1000)

            # Look for sarana response in interceptor
            captured_schools = []
            deadline = time.time() + 10
            while not captured_schools and time.time() < deadline:
                for entry in reversed(interceptor.all_responses):
                    url = str(entry.get("url") or "")
                    if "sarana" in url.lower() and entry.get("status") == 200:
                        data = (entry.get("body") or {}).get("data")
                        if isinstance(data, list) and data:
                            captured_schools = data
                            break
                if not captured_schools:
                    page.wait_for_timeout(500)

            # Nudge combobox if still not captured
            if not captured_schools:
                print("Nudging combobox to trigger sarana fetch...")
                for selector in ["text=Pilih sekolah", "text=Pilih Sekolah", '[class*="select"]:visible']:
                    try:
                        el = page.locator(selector).first
                        if el.is_visible():
                            el.click(timeout=3000)
                            page.wait_for_timeout(2000)
                            page.keyboard.press("Escape")
                            break
                    except Exception:
                        pass

                for entry in reversed(interceptor.all_responses):
                    url = str(entry.get("url") or "")
                    if "sarana" in url.lower() and entry.get("status") == 200:
                        data = (entry.get("body") or {}).get("data")
                        if isinstance(data, list) and data:
                            captured_schools = data
                            break

            save_auth_state(context, page, base_url)
            context.close()

        if not captured_schools:
            print("[ERROR] Gagal mendapatkan daftar sekolah dari ASIK.")
            print("Intercepted URLs:")
            for r in interceptor.all_responses:
                print(f"  [{r.get('status')}] {r.get('url')}")
            return

        # Format and save
        schools = []
        seen = set()
        for s in captured_schools:
            name = (s.get("school_name") or "").strip()
            if not name or name in seen:
                continue
            seen.add(name)
            cat = (s.get("category_short_name") or s.get("category_code_name") or "").upper().strip()
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

        # Write to cache files
        cache_locations = [
            BACKEND_ROOT / "app" / "data" / "asik_schools_cache.json",
            Path("/app/app/data/asik_schools_cache.json"),
        ]
        written = []
        for loc in cache_locations:
            if loc.parent.exists():
                loc.write_text(json.dumps(schools, ensure_ascii=False, indent=2), encoding="utf-8")
                written.append(str(loc))

        print(f"\n[SUKSES] Berhasil mengambil {len(schools)} sekolah dari ASIK untuk {p.name}:")
        for s in schools:
            print(f"  * {s['name']} ({s['category']})")
        print(f"\nFile cache tersimpan di: {', '.join(written)}")

    finally:
        db.close()


if __name__ == "__main__":
    main()
