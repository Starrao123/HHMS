"""User Service

Manages user identities, relationships (doctor-patient), caching, and health.
"""

# =====================================================
# Standard Library Imports
# =====================================================
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import List

# =====================================================
# Third-Party Imports
# =====================================================
import httpx
import psycopg2
import redis

# =====================================================
# Local Imports
# =====================================================
from db import get_session, init_db
from fastapi import Depends, FastAPI, HTTPException, Request
from models.models import User
from models.schemas import (
    Dependency,
    HealthCheckResponse,
    RelationshipLink,
    UserCreate,
    UserResponse,
    UserRole,
    UserUpdate,
)
from pydantic import EmailStr
from redis.exceptions import RedisError
from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select
from starlette.middleware.base import BaseHTTPMiddleware

# =====================================================
# Configuration & Middleware
# =====================================================

# Logging setup
logger = logging.getLogger("user-service")
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)


class LoggingMiddleware(BaseHTTPMiddleware):
    """Structured request logging with request ID and response time."""

    async def dispatch(self, request: Request, call_next):
        req_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        start_ts = time.time()
        logger.info("req_id=%s start method=%s path=%s", req_id, request.method, request.url.path)

        response = await call_next(request)

        duration_ms = int((time.time() - start_ts) * 1000)
        response.headers["X-Request-ID"] = req_id
        response.headers["X-Response-Time-ms"] = str(duration_ms)
        logger.info(
            "req_id=%s end status=%s path=%s duration_ms=%s",
            req_id,
            response.status_code,
            request.url.path,
            duration_ms,
        )
        return response


# Initialize Redis client
redis_client = redis.Redis(
    host=os.environ.get("REDIS_HOST", "localhost"),
    port=int(os.environ.get("REDIS_PORT", 6379)),
    decode_responses=True,
)


# Lifespan (startup/shutdown hooks)
@asynccontextmanager
async def lifespan(_app: FastAPI):
    try:
        init_db()
        logger.info("Database initialized successfully.")
    except (SQLAlchemyError, psycopg2.Error) as e:
        logger.error("Database initialization failed: %s", e)
    yield
    # Shutdown logic (if needed) goes here


# Get the ROOT_PATH environment variable defined in docker-compose
root_path = os.getenv("ROOT_PATH", "")

app = FastAPI(title="User Service", lifespan=lifespan, root_path=root_path)
app.add_middleware(LoggingMiddleware)

# =====================================================
# Constants & Helpers
# =====================================================

CACHE_TTL_SECONDS = 600


def _canonical_email(value: str | EmailStr | None) -> str:
    return (str(value).lower()) if value else ""


def _invalidate_user_cache(user) -> None:
    try:
        # By ID
        redis_client.delete(f"user:{user.id}")
        # By email (old/new callers handle providing both keys)
    except RedisError:
        # Best-effort cache invalidation
        logger.debug("Redis error during user cache invalidation", exc_info=True)


# =====================================================
# Health Check
# =====================================================


@app.get("/health", response_model=HealthCheckResponse)
def health():
    """
    Health check endpoint to monitor service and its dependencies.
    1. Checks Postgres connection.
    2. Checks Redis connection.
    """
    dependencies = {}
    service_name = "user-service"

    # 1. Check Postgres (Direct connection check)
    start = time.time()
    try:
        conn = psycopg2.connect(
            dbname=os.environ.get("POSTGRES_DB"),
            user=os.environ.get("POSTGRES_USER"),
            password=os.environ.get("POSTGRES_PASSWORD"),
            host=os.environ.get("POSTGRES_HOST"),
            port=int(os.environ.get("POSTGRES_PORT", 5432)),
        )
        conn.close()
        dependencies["postgres-user"] = Dependency(
            status="healthy", response_time_ms=int((time.time() - start) * 1000)
        )
    except psycopg2.Error as e:
        logger.error("Health check failed for Postgres: %s", e)
        dependencies["postgres-user"] = Dependency(
            status="unhealthy", response_time_ms=None, error=str(e)
        )

    # 2. Check Redis
    start = time.time()
    try:
        if redis_client.ping():
            dependencies["redis"] = Dependency(
                status="healthy", response_time_ms=int((time.time() - start) * 1000)
            )
        else:
            dependencies["redis"] = Dependency(
                status="unhealthy", response_time_ms=None, error="Ping failed"
            )
    except RedisError as e:
        logger.error("Health check failed for Redis: %s", e)
        dependencies["redis"] = Dependency(status="unhealthy", response_time_ms=None, error=str(e))

    # Aggregate status
    overall_status = (
        "healthy" if all(d.status == "healthy" for d in dependencies.values()) else "unhealthy"
    )

    response = HealthCheckResponse(
        service=service_name, status=overall_status, dependencies=dependencies
    )

    if overall_status == "unhealthy":
        raise HTTPException(status_code=503, detail=response.model_dump())

    return response


# =====================================================
# Main Routes
# =====================================================


@app.post("/register", response_model=UserResponse, status_code=201)
def register_user(payload: UserCreate, session: Session = Depends(get_session)):
    """
    Registers a new user (Patient or Doctor).
    """

    # 1. Check for existing email (case-insensitive)
    canonical_email = str(payload.email).lower()
    existing = session.exec(select(User).where(func.lower(User.email) == canonical_email)).first()
    if existing:
        raise HTTPException(status_code=409, detail="User with this email already exists")

    # 3. Create Database Model
    # Explicitly map fields to ensure separation of API schema and DB Model
    db_user = User(
        email=canonical_email,
        full_name=payload.full_name,
        role=payload.role,
        phone=payload.phone,
        organization=payload.organization,
        is_active=True,
    )

    # 4. Save to DB
    session.add(db_user)
    session.commit()
    session.refresh(db_user)

    # 5. Warm caches for newly created user (Cache-aside)
    # Convert to response schema and cache by ID and email for fast reads
    response_obj = UserResponse.model_validate(db_user)
    try:
        redis_client.setex(f"user:{db_user.id}", CACHE_TTL_SECONDS, response_obj.model_dump_json())
        if db_user.email:
            redis_client.setex(
                f"user:email:{str(db_user.email).lower()}",
                CACHE_TTL_SECONDS,
                response_obj.model_dump_json(),
            )
    except RedisError:
        # Best-effort caching; proceed even if Redis fails
        logger.debug("Redis error during cache warm for new user", exc_info=True)

    # 6. Return
    return response_obj


@app.get("/{user_id}", response_model=UserResponse)
def get_user(user_id: uuid.UUID, session: Session = Depends(get_session)):
    """
    Retrieves user details by ID.
    """
    # 1. Check Cache
    cache_key = f"user:{user_id}"
    cached_data = redis_client.get(cache_key)

    if cached_data:
        # Deserialize JSON back into Pydantic Schema
        return UserResponse.model_validate_json(cached_data)

    # 3. Query DB
    db_user = session.get(User, user_id)
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")

    # 4. Convert to Response Schema & Cache
    # We convert to the Schema *before* caching to ensure we don't cache
    # the hashed_password or internal DB fields.
    response_obj = UserResponse.model_validate(db_user)

    # Store in Redis (Expire in 10 minutes)
    redis_client.setex(cache_key, CACHE_TTL_SECONDS, response_obj.model_dump_json())

    return response_obj


@app.get("/email/{email}", response_model=UserResponse)
def get_user_by_email(email: EmailStr, session: Session = Depends(get_session)):
    """
    Retrieves user details by email.
    Uses a separate cache key per canonical (lowercased) email.
    """
    # Canonicalize email
    canonical_email = str(email).lower()

    # 1. Check Cache
    cache_key = f"user:email:{canonical_email}"
    cached_data = redis_client.get(cache_key)
    if cached_data:
        return UserResponse.model_validate_json(cached_data)

    # 2. Query DB (case-insensitive match)
    db_user = session.exec(select(User).where(func.lower(User.email) == canonical_email)).first()
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")

    # 3. Convert & Cache
    response_obj = UserResponse.model_validate(db_user)
    redis_client.setex(cache_key, CACHE_TTL_SECONDS, response_obj.model_dump_json())

    return response_obj


@app.patch("/{user_id}", response_model=UserResponse)
def update_user(user_id: uuid.UUID, payload: UserUpdate, session: Session = Depends(get_session)):
    """
    Updates a user's editable fields.
    Allows updating: full_name, phone, doctor_id.
    """
    # Fetch user
    db_user = session.get(User, user_id)
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")

    # Track old email for cache invalidation
    old_email_canonical = (db_user.email or "").lower()

    # Apply updates if provided
    # 1) Email change: normalize and check uniqueness (case-insensitive)
    if payload.email is not None:
        canonical_email = _canonical_email(payload.email)
        # Only proceed if different than current canonical
        if canonical_email != _canonical_email(db_user.email):
            existing = session.exec(
                select(User).where(func.lower(User.email) == canonical_email)
            ).first()
            if existing and existing.id != db_user.id:
                raise HTTPException(status_code=409, detail="Email already in use")
            # Update email after passing uniqueness check
            db_user.email = canonical_email

    if payload.full_name is not None:
        db_user.full_name = payload.full_name
    if payload.phone is not None:
        db_user.phone = payload.phone
    if payload.doctor_id is not None:
        db_user.doctor_id = payload.doctor_id

    # Touch updated_at
    db_user.updated_at = datetime.utcnow()

    # Persist
    session.add(db_user)
    session.commit()
    session.refresh(db_user)

    # Invalidate cache (by id and by email keys)
    cache_key = f"user:{db_user.id}"
    new_email_canonical = (db_user.email or "").lower()
    cache_key_email_old = f"user:email:{old_email_canonical}"
    cache_key_email_new = f"user:email:{new_email_canonical}"
    try:
        redis_client.delete(cache_key)
        if old_email_canonical:
            redis_client.delete(cache_key_email_old)
        if new_email_canonical:
            redis_client.delete(cache_key_email_new)
    except RedisError:
        logger.debug("Redis error during cache invalidation on update", exc_info=True)

    return UserResponse.model_validate(db_user)


# =====================================================
# Delete User
# =====================================================


@app.delete("/relationships/unlink", response_model=UserResponse)
def unlink_patient_from_doctor(payload: RelationshipLink, session: Session = Depends(get_session)):
    """Unlinks a patient from a doctor.

    - Validates both users exist and roles are appropriate
    - Idempotent: if patient not linked to this doctor, returns current patient state
    - Returns the updated Patient as `UserResponse`
    """
    doctor = session.get(User, payload.doctor_id)
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor not found")
    if doctor.role != UserRole.DOCTOR:
        raise HTTPException(
            status_code=400, detail="Provided doctor_id does not belong to a doctor"
        )

    patient = session.get(User, payload.patient_id)
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
    if patient.role != UserRole.PATIENT:
        raise HTTPException(
            status_code=400, detail="Provided patient_id does not belong to a patient"
        )
    if patient.id == doctor.id:
        raise HTTPException(status_code=400, detail="A user cannot be their own doctor")

    # If not linked to this doctor, return current patient state (idempotent)
    if patient.doctor_id != doctor.id:
        return UserResponse.model_validate(patient)

    # Unlink
    patient.doctor_id = None
    patient.updated_at = datetime.utcnow()
    session.add(patient)
    session.commit()
    session.refresh(patient)

    # Invalidate caches for patient
    try:
        redis_client.delete(f"user:{patient.id}")
        if patient.email:
            redis_client.delete(f"user:email:{str(patient.email).lower()}")
    except RedisError:
        logger.debug("Redis error during cache invalidation on unlink", exc_info=True)

    return UserResponse.model_validate(patient)


@app.delete("/{user_id}", status_code=204)
def delete_user(user_id: uuid.UUID, session: Session = Depends(get_session)):
    """
    Deletes a user by ID.
    If deleting a doctor, unassigns that doctor from all linked patients.
    """
    db_user = session.get(User, user_id)
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")

    # If doctor, set doctor_id = NULL for all patients assigned to this doctor
    if getattr(db_user, "role", None) and db_user.role == UserRole.DOCTOR:
        patients = session.exec(select(User).where(User.doctor_id == db_user.id)).all()
        for p in patients:
            p.doctor_id = None
            session.add(p)
        session.commit()

    # Invalidate caches (by id and email)
    try:
        redis_client.delete(f"user:{db_user.id}")
        if db_user.email:
            redis_client.delete(f"user:email:{str(db_user.email).lower()}")
    except RedisError:
        logger.debug("Redis error during cache invalidation on delete", exc_info=True)

    session.delete(db_user)
    session.commit()
    # Best-effort: purge patient vitals in patient-data-service
    try:
        # Only patients are expected to have vitals; calling delete is harmless if none
        url = f"http://patient-data-service:8000/patient/{user_id}"
        with httpx.Client(timeout=3.0) as client:
            client.delete(url)
    except httpx.HTTPError:
        # Do not block user deletion if downstream is unavailable
        logger.debug("Downstream patient-data-service delete failed", exc_info=True)
    return None


# =====================================================
# Relationships
# =====================================================


@app.post("/relationships", response_model=UserResponse)
def link_patient_to_doctor(payload: RelationshipLink, session: Session = Depends(get_session)):
    """
    Links a patient to a doctor by setting patient's `doctor_id`.
    - Validates both users exist and roles are appropriate
    - Idempotent if already linked to the same doctor
    - Returns 409 if patient is assigned to a different doctor
    Returns the updated Patient as `UserResponse`.
    """
    doctor = session.get(User, payload.doctor_id)
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor not found")
    if doctor.role != UserRole.DOCTOR:
        raise HTTPException(
            status_code=400, detail="Provided doctor_id does not belong to a doctor"
        )

    patient = session.get(User, payload.patient_id)
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
    if patient.role != UserRole.PATIENT:
        raise HTTPException(
            status_code=400, detail="Provided patient_id does not belong to a patient"
        )
    if patient.id == doctor.id:
        raise HTTPException(status_code=400, detail="A user cannot be their own doctor")

    # Already linked to this doctor: idempotent success
    if patient.doctor_id == doctor.id:
        return UserResponse.model_validate(patient)

    # Already linked to a different doctor
    if patient.doctor_id is not None and patient.doctor_id != doctor.id:
        raise HTTPException(status_code=409, detail="Patient already assigned to another doctor")

    patient.doctor_id = doctor.id
    session.add(patient)
    session.commit()
    session.refresh(patient)

    # Invalidate caches for patient
    try:
        redis_client.delete(f"user:{patient.id}")
        if patient.email:
            redis_client.delete(f"user:email:{str(patient.email).lower()}")
    except RedisError:
        logger.debug("Redis error during cache invalidation on link", exc_info=True)

    return UserResponse.model_validate(patient)


@app.get("/{doctor_id}/patients", response_model=List[UserResponse])
def list_doctor_patients(doctor_id: uuid.UUID, session: Session = Depends(get_session)):
    """Lists all patients for a doctor."""
    doctor = session.get(User, doctor_id)
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor not found")
    if doctor.role != UserRole.DOCTOR:
        raise HTTPException(status_code=400, detail="Provided id does not belong to a doctor")

    patients = session.exec(select(User).where((User.doctor_id == doctor_id))).all()
    return [UserResponse.model_validate(p) for p in patients]


# =====================================================
# Relationship Checks (Internal)
# =====================================================


@app.get("/relationships/check")
def check_relationship(
    doctor_id: uuid.UUID, patient_id: uuid.UUID, session: Session = Depends(get_session)
):
    """Internal use: Verifies if a doctor is assigned to a patient."""
    # Validate doctor
    doctor = session.get(User, doctor_id)
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor not found")
    if doctor.role != UserRole.DOCTOR:
        raise HTTPException(
            status_code=400, detail="Provided doctor_id does not belong to a doctor"
        )

    # Validate patient
    patient = session.get(User, patient_id)
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
    if patient.role != UserRole.PATIENT:
        raise HTTPException(
            status_code=400, detail="Provided patient_id does not belong to a patient"
        )

    assigned = patient.doctor_id == doctor.id
    return {"assigned": assigned}
