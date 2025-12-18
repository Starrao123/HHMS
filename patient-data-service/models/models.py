# patient-data-service/models/models.py

import uuid
from datetime import datetime
from typing import Dict, Optional

from pydantic import BaseModel
from pydantic import Field as PydField
from sqlmodel import Field, SQLModel


class VitalSign(SQLModel, table=True):
    __tablename__ = "vital_signs"

    patient_id: uuid.UUID = Field(primary_key=True, index=True)
    timestamp: datetime = Field(primary_key=True, index=True)

    # All optional now
    heart_rate: Optional[int] = Field(default=None)
    spo2: Optional[int] = Field(default=None)
    respiratory_rate: Optional[int] = Field(default=None)
    systolic_bp: Optional[int] = Field(default=None)
    diastolic_bp: Optional[int] = Field(default=None)
    temperature: Optional[float] = Field(default=None)
    glucose: Optional[int] = Field(default=None)
    weight_kg: Optional[float] = Field(default=None)


# Health check models (kept local to this service)
class Dependency(BaseModel):
    status: str
    response_time_ms: Optional[int] = None


class HealthCheckResponse(BaseModel):
    service: str
    status: str
    dependencies: Dict[str, Dependency] = PydField(default_factory=dict)
