# analytics-service/models/schemas.py
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional

from pydantic import UUID4, BaseModel, ConfigDict
from pydantic import Field as PydField


# --- Enums ---
class MetricType(str, Enum):
    HEART_RATE = "heart_rate"
    SPO2 = "spo2"
    RESPIRATORY_RATE = "respiratory_rate"
    SYSTOLIC_BP = "systolic_bp"
    DIASTOLIC_BP = "diastolic_bp"
    TEMPERATURE = "temperature"
    GLUCOSE = "glucose"


class AlertSeverity(str, Enum):
    INFO = "info"  # e.g., Battery low (if tracked)
    WARNING = "warning"  # e.g., HR slightly elevated
    CRITICAL = "critical"  # e.g., HR > 150 or SpO2 < 85


# --- Thresholds (Configuration) ---
class ThresholdBase(BaseModel):
    patient_id: UUID4
    metric: MetricType
    # We use optional because you might set a Max without a Min (e.g. fever)
    min_value: Optional[float] = None
    max_value: Optional[float] = None


class ThresholdCreate(ThresholdBase):
    pass


class ThresholdResponse(ThresholdBase):
    id: int
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


# --- Anomalies (Reporting) ---
class AnomalyBase(BaseModel):
    patient_id: UUID4
    metric: MetricType
    severity: AlertSeverity
    observed_value: float
    description: str
    timestamp: datetime


class AnomalyResponse(AnomalyBase):
    id: int
    # We allow returning which specific rule was broken
    threshold_id: Optional[int] = None

    model_config = ConfigDict(from_attributes=True)


class DependencyStatus(BaseModel):
    """Standardized dependency health model used across services."""

    status: str
    response_time_ms: Optional[int] = None
    error: Optional[str] = None


class HealthCheckResponse(BaseModel):
    service: str
    status: str
    dependencies: Dict[str, DependencyStatus] = PydField(default_factory=dict)
