import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class SimpusScheduleIn(BaseModel):
    hour: int = Field(ge=0, le=23)
    minute: int = Field(ge=0, le=59)
    lookback_days: int = Field(default=1, ge=1, le=14)
    enabled: bool = False


class SimpusScheduleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    puskesmas_id: uuid.UUID
    hour: int
    minute: int
    lookback_days: int
    enabled: bool
    next_run_at: datetime
    last_fired_at: datetime | None
