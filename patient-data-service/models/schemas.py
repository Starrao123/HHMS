from datetime import datetime
from typing import List, Optional

from pydantic import UUID4, BaseModel, ConfigDict, Field, model_validator


class TelemetryBase(BaseModel):
    timestamp: datetime

    # --- Metrics (All Optional) ---
    heart_rate: Optional[int] = Field(None, gt=0, lt=300, description="BPM")
    spo2: Optional[int] = Field(None, ge=50, le=100, description="SpO2 %")

    # New additions
    respiratory_rate: Optional[int] = Field(None, ge=5, le=60, description="Breaths/min")
    systolic_bp: Optional[int] = Field(None, ge=70, le=250, description="mmHg")
    diastolic_bp: Optional[int] = Field(None, ge=40, le=150, description="mmHg")
    temperature: Optional[float] = Field(None, ge=35.0, le=42.0, description="Celsius")
    glucose: Optional[int] = Field(None, ge=20, le=600, description="mg/dL")
    weight_kg: Optional[float] = Field(None, ge=20.0, le=300.0, description="Weight in kg")

    model_config = ConfigDict(extra="forbid")

    # --- Validation Logic ---
    @model_validator(mode="after")
    def check_payload_integrity(self):
        # 1. Ensure at least one metric is provided (don't accept empty payloads)
        metrics = [
            "heart_rate",
            "spo2",
            "respiratory_rate",
            "systolic_bp",
            "diastolic_bp",
            "temperature",
            "glucose",
            "weight_kg",
        ]
        if not any(getattr(self, m) is not None for m in metrics):
            raise ValueError("Payload must contain at least one vital sign reading")

        # 2. Ensure Blood Pressure comes in pairs (cannot have Systolic without Diastolic)
        if (self.systolic_bp is None) != (self.diastolic_bp is None):
            raise ValueError("Systolic and Diastolic BP must be provided together")

        return self


# --- Input Schemas ---
class TelemetryIn(TelemetryBase):
    # Make timestamp optional for ingestion; server will default to now if missing
    timestamp: Optional[datetime] = None


class TelemetryBatch(BaseModel):
    readings: List[TelemetryIn]


# --- Output Schemas ---
class TelemetryOut(TelemetryBase):
    patient_id: UUID4
    model_config = ConfigDict(from_attributes=True)


# --- Time Series Output ---
class TimeseriesPoint(BaseModel):
    timestamp: datetime
    value: float
