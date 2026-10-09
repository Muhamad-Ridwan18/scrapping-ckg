import datetime as _dt
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from app.models.scrape_job import ScrapeKind, ScrapeStatus, TriggererType


class ScrapeStart(BaseModel):
    date: _dt.date | None = None
    headless: bool = True


class PatientScrapeStart(BaseModel):
    headless: bool = True


class SimpusImportStart(BaseModel):
    jenis: Literal["umum", "sekolah"] | None = None
    nik: str | None = None
    tanggal: _dt.date | None = None
    tanggal_dari: _dt.date | None = None
    tanggal_sampai: _dt.date | None = None

    @model_validator(mode="after")
    def _range_order(self) -> "SimpusImportStart":
        if (
            self.tanggal_dari
            and self.tanggal_sampai
            and self.tanggal_dari > self.tanggal_sampai
        ):
            raise ValueError("tanggal_dari harus sebelum atau sama dengan tanggal_sampai")
        if self.nik is not None:
            self.nik = self.nik.strip() or None
        return self


class ScrapeJobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    puskesmas_id: uuid.UUID
    puskesmas_name: str
    patient_id: uuid.UUID | None = None
    patient_nik: str | None = None
    patient_name: str | None = None
    kind: ScrapeKind
    date_filter: str | None = None
    status: ScrapeStatus
    triggered_by_id: uuid.UUID
    triggered_by_type: TriggererType
    celery_task_id: str | None
    scraped_count: int | None
    inserted_count: int | None
    updated_count: int | None
    duration_seconds: float | None
    cpu_avg_pct: float | None
    cpu_peak_pct: float | None
    mem_avg_mb: float | None
    mem_peak_mb: float | None
    resource_samples: int | None
    notes: str | None
    started_at: datetime | None
    finished_at: datetime | None
    error_message: str | None
    cron_run_id: uuid.UUID | None = None
    parent_gdp_job_id: uuid.UUID | None = None
    target_nik: str | None = None
    created_at: datetime
    updated_at: datetime


class ScrapeJobLogOut(BaseModel):
    lines: list[str]
