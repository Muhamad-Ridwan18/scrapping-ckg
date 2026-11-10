"""Local-only: attach SIMPUS token + ASIK creds for end-to-end probe."""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, load_only

from app.core.security import encrypt_cred, encrypt_json
from app.database import SessionLocal
from app.models.puskesmas import Puskesmas

ASIK_URL = "sehatindonesiaku.kemkes.go.id"
# Prod origin — reachable from WSL; local Windows Laravel is not.
SIMPUS_API_URL = "https://app.kliksimpus.com"

TOKEN_PATH = Path("/mnt/d/DEV/Mitra Bestari Teknologi/kliksimpus/scripts/_simpus_token.txt")
SAMPLE_PATH = Path("/mnt/d/DEV/Mitra Bestari Teknologi/kliksimpus/scripts/_ckg_sample.json")


def main() -> None:
    email = os.environ["ASIK_EMAIL"]
    password = os.environ["ASIK_PASSWORD"]
    token = TOKEN_PATH.read_text(encoding="utf-8").strip()
    sample = json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))
    name = sample["nama_instansi"]

    db: Session = SessionLocal()
    try:
        row = db.scalar(
            select(Puskesmas)
            .options(
                load_only(
                    Puskesmas.id,
                    Puskesmas.name,
                    Puskesmas.simpus_api_url,
                    Puskesmas.simpus_api_cred,
                    Puskesmas.asik_url,
                    Puskesmas.asik_cred,
                )
            )
            .where(Puskesmas.name == name)
        )
        if row is None:
            row = Puskesmas(id=uuid.uuid4(), name=name)
            db.add(row)
            db.flush()

        row.simpus_api_url = SIMPUS_API_URL
        row.simpus_api_cred = encrypt_json({"token": token})
        row.asik_url = ASIK_URL
        row.asik_cred = encrypt_cred({"email": email, "password": password})
        db.commit()
        print(f"puskesmas_id={row.id}")
        print(f"name={row.name}")
        print(f"sample_nik_mask={sample['nik'][:6]}****{sample['nik'][-4:]}")
        print(f"sample_tanggal={sample['tanggal']}")
        Path("/tmp/ckg_e2e_sample.json").write_text(
            json.dumps(
                {
                    "nik": sample["nik"],
                    "tanggal": sample["tanggal"],
                    "nama_instansi": name,
                }
            ),
            encoding="utf-8",
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
