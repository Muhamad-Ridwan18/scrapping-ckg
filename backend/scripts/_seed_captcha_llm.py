"""Local-only: seed OpenAI LLM config and activate it for captcha."""

from __future__ import annotations

import os

from app.crud import llm_config as llm_config_crud
from app.database import SessionLocal
from app.schemas.llm import LlmConfigCreate

api_key = os.environ.get("OPENAI_API_KEY", "").strip()
if not api_key:
    raise SystemExit("OPENAI_API_KEY missing")

db = SessionLocal()
try:
    obj = llm_config_crud.create(
        db,
        LlmConfigCreate(
            provider="openai",
            model="gpt-4o",
            base_url="https://api.openai.com/v1",
            api_key=api_key,
            label="local-captcha",
            is_active=True,
            is_active_captcha=True,
        ),
    )
    print(f"llm_id={obj.id} captcha={obj.is_active_captcha} active={obj.is_active}")
finally:
    db.close()
