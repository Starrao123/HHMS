# This file contains Pydantic models for the User Service Database

import uuid
from datetime import datetime
from typing import Optional, List
from sqlmodel import SQLModel, Field, Relationship
from .schemas import UserRole # Import the Enum only

class User(SQLModel, table=True):
    """
    Represents a user in the system, which can be a patient or a doctor.
    """
    __tablename__ = "users"

    # Database-specific columns
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    email: str = Field(unique=True, index=True, nullable=False)
    
    full_name: str = Field(nullable=False)
    role: UserRole = Field(nullable=False)
    
    phone: Optional[str] = Field(default=None)
    organization: Optional[str] = Field(default=None)
    is_active: bool = Field(default=True)
    
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: Optional[datetime] = Field(default_factory=datetime.utcnow)

    # --- Relationships ---
    # Database-level Foreign Key
    doctor_id: Optional[uuid.UUID] = Field(default=None, foreign_key="users.id")

    # ORM Relationships (SQLAlchemy magic)
    # These allow you to do user.doctor or user.patients in your code
    doctor: Optional["User"] = Relationship(
        back_populates="patients",
        sa_relationship_kwargs={"remote_side": "User.id"}
    )
    
    patients: List["User"] = Relationship(back_populates="doctor")