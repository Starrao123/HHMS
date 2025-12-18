from datetime import datetime
from enum import Enum
from typing import Dict, Optional

from pydantic import UUID4, BaseModel, ConfigDict, Field, field_validator


# =====================================================
# Enums
# =====================================================
class AlertSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AlertStatus(str, Enum):
    PENDING = "pending"  # Created in DB, not yet sent
    SENT = "sent"  # Successfully handed off to Twilio
    FAILED = "failed"  # Error occurred (e.g., invalid phone, API down)
    DELIVERED = "delivered"  # Optional: updated via webhook later
    ACKNOWLEDGED = "acknowledged"  # Marked read by a doctor
    RESOLVED = "resolved"  # Marked resolved by a doctor


# =====================================================
# API Models
# =====================================================


class AlertCreate(BaseModel):
    """
    Input Payload: Received from Analytics Service.
    """

    # Optional to support compatibility route with path param
    patient_id: Optional[UUID4] = None
    message: str = Field(..., min_length=1, description="The alert content")
    severity: AlertSeverity = AlertSeverity.INFO

    # Accept uppercase values like "INFO" by normalizing before Enum conversion
    @field_validator("severity", mode="before")
    def _normalize_severity(cls, v):
        if isinstance(v, str):
            return v.lower()
        return v


class AlertResponse(BaseModel):
    """
    Output Payload: Sent back to UI/Doctor Dashboard.
    """

    id: UUID4
    patient_id: UUID4
    severity: AlertSeverity
    message: str
    status: AlertStatus

    # Audit details
    recipient_phone: Optional[str] = None
    created_at: datetime
    sent_at: Optional[datetime] = None
    error_message: Optional[str] = None
    acknowledged_by: Optional[UUID4] = None
    acknowledged_at: Optional[datetime] = None

    # Configuration to allow Pydantic to read SQLModel objects
    model_config = ConfigDict(from_attributes=True)


# =====================================================
# Health Check Models
# =====================================================


class DependencyStatus(BaseModel):
    status: str
    response_time_ms: Optional[float] = None
    error: Optional[str] = None


class HealthCheckResponse(BaseModel):
    service: str
    status: str
    dependencies: Dict[str, DependencyStatus]


# =====================================================
# Acknowledge Models
# =====================================================


class AcknowledgeRequest(BaseModel):
    """
    Payload for acknowledging or resolving an alert.
    """

    status: AlertStatus = Field(..., description="acknowledged or resolved")
    doctor_id: UUID4
    model_config = ConfigDict(extra="forbid")
