from sqlalchemy import select
from sqlalchemy.orm import load_only

from app.database import SessionLocal
from app.models.llm_config import LlmConfig

db = SessionLocal()
try:
    rows = list(
        db.scalars(
            select(LlmConfig)
            .options(
                load_only(
                    LlmConfig.id,
                    LlmConfig.provider,
                    LlmConfig.model,
                    LlmConfig.label,
                    LlmConfig.is_active,
                    LlmConfig.is_active_captcha,
                    LlmConfig.is_active_chatbot,
                )
            )
            .order_by(LlmConfig.created_at.desc())
            .limit(20)
        ).all()
    )
    print(f"count={len(rows)}")
    for r in rows:
        print(
            f"id={r.id} provider={r.provider} model={r.model} label={r.label!r} "
            f"active={r.is_active} captcha={r.is_active_captcha} chatbot={r.is_active_chatbot}"
        )
finally:
    db.close()
