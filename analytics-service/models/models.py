"""Data models for Analytics Service.

Defines SQLModel tables for `Threshold` and `AnomalyEvent`, and local
Pydantic models for health check responses.
"""

# analytics-service/models/models.py
import uuid
from datetime import datetime
from typing import Dict, Optional

from models.schemas import AlertSeverity, MetricType
from pydantic import BaseModel
from pydantic import Field as PydField
from sqlmodel import Field, SQLModel, UniqueConstraint


# --- Table 1: Thresholds ---
class Threshold(SQLModel, table=True):
    """Threshold rules per patient and metric."""

    __tablename__ = "thresholds"
    # Ensure one rule per metric per patient to avoid logic conflicts
    __table_args__ = (
        UniqueConstraint("patient_id", "metric", name="unique_patient_metric_threshold"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    patient_id: uuid.UUID = Field(index=True, nullable=False)

    metric: MetricType = Field(nullable=False)

    min_value: Optional[float] = Field(default=None)
    max_value: Optional[float] = Field(default=None)

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: Optional[datetime] = Field(default_factory=datetime.utcnow)


# --- Table 2: Anomalies ---
class AnomalyEvent(SQLModel, table=True):
    """Recorded events where telemetry violated a threshold."""

    __tablename__ = "anomalies"

    id: Optional[int] = Field(default=None, primary_key=True)
    patient_id: uuid.UUID = Field(index=True, nullable=False)

    # When did the violation happen? (Matches the timestamp from the device)
    timestamp: datetime = Field(nullable=False, index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)

    metric: MetricType = Field(nullable=False)
    observed_value: float = Field(nullable=False)

    # Severity helps the Alert Service decide whether to SMS or just log
    severity: AlertSeverity = Field(default=AlertSeverity.WARNING)

    # Human readable text: "Heart Rate 140 > Max 100"
    description: str = Field(nullable=False)

    # Link back to the rule that caused this (Optional, in case rule is deleted later)
    threshold_id: Optional[int] = Field(default=None, foreign_key="thresholds.id")


# --- Health Check Models ---
class Dependency(BaseModel):
    status: str
    response_time_ms: Optional[int] = None


class HealthCheckResponse(BaseModel):
    service: str
    status: str
    dependencies: Dict[str, Dependency] = PydField(default_factory=dict)
