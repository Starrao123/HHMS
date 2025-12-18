import uuid
from datetime import datetime
from typing import Optional

from models.schemas import AlertSeverity, AlertStatus
from sqlmodel import Field, SQLModel


class Alert(SQLModel, table=True):
    __tablename__ = "alerts"

    # Primary Key
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True, index=True)

    # Target User (Patient)
    patient_id: uuid.UUID = Field(index=True, nullable=False)

    # Content
    severity: AlertSeverity = Field(default=AlertSeverity.INFO)
    message: str = Field(nullable=False)

    # Status Tracking
    status: AlertStatus = Field(default=AlertStatus.PENDING)

    # Audit Trail
    # We store the phone number used at the time of sending for historical accuracy
    recipient_phone: Optional[str] = Field(default=None)

    # Twilio Message SID (Provider ID) - useful for debugging delivery issues with Twilio support
    provider_message_id: Optional[str] = Field(default=None)

    # If status == FAILED, this stores the exception message
    error_message: Optional[str] = Field(default=None)

    # Timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow)
    sent_at: Optional[datetime] = Field(default=None)
    acknowledged_by: Optional[uuid.UUID] = Field(default=None)
    acknowledged_at: Optional[datetime] = Field(default=None)
