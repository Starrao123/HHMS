# This file contains Pydantic models (schemas) for the User Service API

from datetime import datetime
from enum import Enum
from typing import Dict, Optional

from pydantic import UUID4, BaseModel, ConfigDict, EmailStr, Field


# --- Health Check Models ---
class Dependency(BaseModel):
    """
    Represents the health status of a dependency service.
    """

    status: str
    response_time_ms: Optional[int] = None
    error: Optional[str] = None


class HealthCheckResponse(BaseModel):
    """
    Represents the health check response for the service.
    """

    service: str
    status: str
    dependencies: Dict[str, Dependency] = Field(default_factory=dict)


# --- Enums (Shared) ---
class UserRole(str, Enum):
    """
    Enum for user roles in the system.
    """

    PATIENT = "patient"
    DOCTOR = "doctor"
    ADMIN = "admin"


# --- Base Schema (Shared fields) ---
class UserBase(BaseModel):
    """
    Base schema for User with shared fields.
    """

    email: EmailStr
    full_name: str
    role: UserRole
    phone: Optional[str] = None
    organization: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None


# --- INPUT: Registration Request ---
class UserCreate(BaseModel):
    """
    Schema for creating a new user.
    Allows only core identity fields.
    """

    email: EmailStr
    # Accept "name" as an alias for full_name for client compatibility
    full_name: str = Field(alias="name")
    role: UserRole
    phone: Optional[str] = None
    organization: Optional[str] = None
    # Do not allow any extra/unknown fields; allow population by field name and alias
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


# --- INPUT: Update Request ---
class UserUpdate(BaseModel):
    """
    Schema for updating user information.
    """

    email: Optional[EmailStr] = None
    full_name: Optional[str] = None
    phone: Optional[str] = None
    doctor_id: Optional[UUID4] = None
    model_config = ConfigDict(extra="forbid")


# --- OUTPUT: API Response ---
class UserResponse(UserBase):
    """
    Schema for user data returned in API responses.
    """

    id: UUID4
    is_active: bool
    doctor_id: Optional[UUID4] = None
    # This configuration tells Pydantic: "It's okay to read data from a SQLAlchemy/SQLModel object, even though this is a standard BaseModel"
    model_config = ConfigDict(from_attributes=True)


# --- INPUT: Relationship Link ---
class RelationshipLink(BaseModel):
    """
    Request payload to link a patient to a doctor.
    """

    doctor_id: UUID4
    patient_id: UUID4
    model_config = ConfigDict(extra="forbid")
