"""POST /hooks/simpus then poll GET until terminal status."""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx

SAMPLE = json.loads(Path("/tmp/ckg_e2e_sample.json").read_text(encoding="utf-8"))
BASE = "http://127.0.0.1:8001"
BEARER = "ckg-simpus-hook-kliksimpus-shared-2026"
HEADERS = {
    "Authorization": f"Bearer {BEARER}",
    "Content-Type": "application/json",
    "Accept": "application/json",
}


def main() -> None:
    payload = {
        "simpus_url": "http://127.0.0.1:9000",
        "nama_instansi": SAMPLE["nama_instansi"],
        "nik": SAMPLE["nik"],
        "tanggal": SAMPLE["tanggal"],
        "jenis": "umum",
        "create_new": False,
    }
    print(
        f"trigger nik={SAMPLE['nik'][:6]}****{SAMPLE['nik'][-4:]} "
        f"tanggal={SAMPLE['tanggal']} instansi={SAMPLE['nama_instansi']}"
    )
    with httpx.Client(timeout=60.0) as client:
        r = client.post(f"{BASE}/hooks/simpus", headers=HEADERS, json=payload)
        print("POST", r.status_code, r.text)
        r.raise_for_status()
        job_id = r.json()["job_id"]

        terminal = {"success", "failed", "cancelled"}
        status = "pending"
        body = {}
        for i in range(90):
            time.sleep(2)
            g = client.get(f"{BASE}/hooks/simpus/jobs/{job_id}", headers=HEADERS)
            body = g.json() if g.headers.get("content-type", "").startswith("application/json") else {"raw": g.text}
            status = body.get("status") if isinstance(body, dict) else None
            print(f"poll[{i}] HTTP {g.status_code} status={status}")
            if status in terminal:
                break
        print("FINAL", json.dumps(body, ensure_ascii=False))
        Path("/tmp/ckg_e2e_result.json").write_text(
            json.dumps({"job_id": job_id, "result": body}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
