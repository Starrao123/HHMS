"""Patient Data Service

Responsible for ingesting, storing, and serving patient vital telemetry.
Includes health checks, Redis caching/publish, and S2S validations.
"""

# =====================================================
# Standard Library Imports
# =====================================================
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Dict, List

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
from models.models import Dependency, HealthCheckResponse, VitalSign
from models.schemas import (
    TelemetryBatch,
    TelemetryIn,
    TelemetryOut,
    TimeseriesPoint,
)
from pydantic import UUID4
from redis.exceptions import RedisError
from sqlalchemy import delete as sa_delete
from sqlalchemy import text
from sqlmodel import Session, select
from starlette.middleware.base import BaseHTTPMiddleware

# Get the ROOT_PATH environment variable defined in docker-compose
root_path = os.getenv("ROOT_PATH", "/patient")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Service lifespan for startup/shutdown hooks."""
    try:
        init_db()
        logger.info("TimescaleDB initialized successfully.")
    except Exception as e:
        logger.error("TimescaleDB initialization failed: %s", e)
    yield


app = FastAPI(title="Patient Data Service", lifespan=lifespan, root_path=root_path)

# =====================================================
# Configuration & Middleware
# =====================================================
logger = logging.getLogger("patient-data-service")
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)


class LoggingMiddleware(BaseHTTPMiddleware):
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


app.add_middleware(LoggingMiddleware)

# =====================================================
# Dependencies (Redis)
# =====================================================
redis_client = redis.Redis(
    host=os.environ.get("REDIS_HOST", "localhost"),
    port=int(os.environ.get("REDIS_PORT", 6379)),
    decode_responses=True,
)


def _ensure_patient_exists(patient_id: UUID4) -> None:
    """Ensure patient exists via user-service.

    Raises 404 if not found, 502 if user-service is unavailable.
    """
    url = f"http://user-service:8000/{patient_id}"
    try:
        with httpx.Client(timeout=2.0) as client:
            resp = client.get(url)
        if resp.status_code == 200:
            return
        if resp.status_code == 404:
            raise HTTPException(status_code=404, detail="Patient not found")
        raise HTTPException(status_code=502, detail="User service error")
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"User service unavailable: {e}")


def _get_doctor_patients(doctor_id: UUID4) -> List[UUID4]:
    """Verify doctor exists and fetch associated patient IDs from user-service."""
    # Verify doctor exists and is doctor
    url_doctor = f"http://user-service:8000/{doctor_id}"
    try:
        with httpx.Client(timeout=2.0) as client:
            dresp = client.get(url_doctor)
        if dresp.status_code == 404:
            raise HTTPException(status_code=404, detail="Doctor not found")
        if dresp.status_code != 200:
            raise HTTPException(status_code=502, detail="User service error")
        data = dresp.json()
        if data.get("role") != "doctor":
            raise HTTPException(status_code=400, detail="User is not a doctor")
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"User service unavailable: {e}")

    # Get patients list
    url_patients = f"http://user-service:8000/{doctor_id}/patients"
    try:
        with httpx.Client(timeout=3.0) as client:
            presp = client.get(url_patients)
        if presp.status_code != 200:
            raise HTTPException(status_code=502, detail="Failed to fetch doctor's patients")
        arr = presp.json() or []
        ids: List[UUID4] = []
        for item in arr:
            pid = item.get("id")
            if pid:
                try:
                    # Coerce to UUID4-compatible type using pydantic validation at call sites
                    ids.append(UUID4(str(pid)))
                except Exception:
                    continue
        return ids
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"User service unavailable: {e}")


@app.get("/health", response_model=HealthCheckResponse)
def health():
    """Health check for TimescaleDB, Redis, and user-service."""
    dependencies = {}
    service_name = "patient-data-service"

    # Check TimescaleDB
    start = time.time()
    try:
        # try to connect to the database
        conn = psycopg2.connect(
            dbname=os.environ["TIMESCALE_DB"],
            user=os.environ["TIMESCALE_USER"],
            password=os.environ["TIMESCALE_PASSWORD"],
            host=os.environ["TIMESCALE_HOST"],
            port=int(os.environ.get("TIMESCALE_PORT", 5432)),
        )
        # If connection is successful, mark as healthy
        conn.close()

        dependencies["timescale-data"] = Dependency(
            status="healthy", response_time_ms=int((time.time() - start) * 1000)
        )
    except psycopg2.Error as e:
        logger.error("Timescale health check failed: %s", e)
        dependencies["timescale-data"] = Dependency(status="unhealthy", response_time_ms=None)

    # Check Redis
    start = time.time()
    try:
        # Create a redis connection
        r = redis.Redis(host=os.environ["REDIS_HOST"], port=int(os.environ.get("REDIS_PORT", 6379)))
        # Ping Redis to check health
        if r.ping():
            dependencies["redis"] = Dependency(
                status="healthy", response_time_ms=int((time.time() - start) * 1000)
            )
        else:
            dependencies["redis"] = Dependency(
                status="unhealthy", response_time_ms=int((time.time() - start) * 1000)
            )
    except RedisError as e:
        logger.error("Redis health check failed: %s", e)
        dependencies["redis"] = Dependency(status="unhealthy", response_time_ms=None)

    # Check user service
    start = time.time()
    try:
        # url inside docker network
        url = "http://user-service:8000/health"

        # Check if the response is healthy
        with httpx.Client(timeout=2.0) as client:
            resp = client.get(url)

        if resp.status_code == 200:
            dependencies["user-service"] = Dependency(
                status="healthy", response_time_ms=int((time.time() - start) * 1000)
            )
        else:
            dependencies["user-service"] = Dependency(
                status="unhealthy", response_time_ms=int((time.time() - start) * 1000)
            )

    except httpx.HTTPError as e:
        logger.error("User-service health check failed: %s", e)
        dependencies["user-service"] = Dependency(status="unhealthy", response_time_ms=None)

    # Aggregate status
    status = (
        "healthy" if all(dep.status == "healthy" for dep in dependencies.values()) else "unhealthy"
    )
    if status == "unhealthy":
        raise HTTPException(
            status_code=503,
            detail=HealthCheckResponse(
                service=service_name, status=status, dependencies=dependencies
            ).dict(),
        )

    return HealthCheckResponse(service=service_name, status=status, dependencies=dependencies)


# =====================================================
# Ingestion
# =====================================================
@app.post("/{patient_id}", response_model=TelemetryOut)
def ingest_vitals(patient_id: UUID4, payload: TelemetryIn, session: Session = Depends(get_session)):
    """
    1. Validate Data
    2. Save to TimescaleDB (Persistence)
    3. Publish to Redis (Real-time Analytics)
    """

    # Ensure patient exists (S2S validation)
    _ensure_patient_exists(patient_id)

    # Step 1: Create DB model
    # We map the Pydantic input to the SQLModel table
    # Ensure timestamp exists and is timezone-aware (default to current UTC)
    ts = payload.timestamp or datetime.now(timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)

    vital_sign = VitalSign(
        patient_id=patient_id,
        timestamp=ts,
        heart_rate=payload.heart_rate,
        spo2=payload.spo2,
        respiratory_rate=payload.respiratory_rate,
        systolic_bp=payload.systolic_bp,
        diastolic_bp=payload.diastolic_bp,
        temperature=payload.temperature,
        glucose=payload.glucose,
        weight_kg=payload.weight_kg,
    )

    # Step 2: Save to TimescaleDB
    try:
        session.add(vital_sign)
        session.commit()
        session.refresh(vital_sign)
    except Exception as e:
        # If DB write fails, we return error. We do NOT publish to Redis.
        # Data integrity is priority #1.
        raise HTTPException(status_code=500, detail=f"Database Write Failed: {str(e)}") from e

    # Step 3: Publish to Redis (event bus)
    # The Analytics Service is listening to the 'vital_signs_channel'
    # Build a compact event payload (exclude None values)
    base_event = {
        "patient_id": str(patient_id),
        "timestamp": ts.isoformat(),
        "heart_rate": payload.heart_rate,
        "spo2": payload.spo2,
        "respiratory_rate": payload.respiratory_rate,
        "systolic_bp": payload.systolic_bp,
        "diastolic_bp": payload.diastolic_bp,
        "temperature": payload.temperature,
        "glucose": payload.glucose,
        "weight_kg": payload.weight_kg,
    }
    event_data = {k: v for k, v in base_event.items() if v is not None}

    try:
        # A. Pub/Sub for Analytics
        redis_client.publish("vital_signs_channel", json.dumps(event_data))

        # B. Cache "Latest" for Dashboard (Optional but fast)
        # Allows "GET /latest" to skip the DB entirely
        redis_client.set(f"latest:{patient_id}", json.dumps(event_data))

    except RedisError as e:
        # If Redis fails, log and continue; DB has the source of truth
        logger.warning("Redis publish failed for patient %s: %s", patient_id, e)

    return vital_sign


# =====================================================
# Batch Ingestion
# =====================================================
@app.post("/batch/{patient_id}", response_model=List[TelemetryOut])
def ingest_vitals_batch(
    patient_id: UUID4, payload: TelemetryBatch, session: Session = Depends(get_session)
):
    """
    Batch ingest multiple telemetry readings for a patient.
    1. Validate and default timestamps
    2. Persist all to TimescaleDB in one transaction
    3. Publish events to Redis (best-effort) and cache the latest
    """

    # Ensure patient exists once per batch
    _ensure_patient_exists(patient_id)

    if not payload.readings:
        raise HTTPException(status_code=400, detail="No readings provided")

    vital_signs: List[VitalSign] = []
    latest_ts: datetime | None = None
    latest_event: dict | None = None

    for reading in payload.readings:
        ts = reading.timestamp or datetime.now(timezone.utc)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)

        vs = VitalSign(
            patient_id=patient_id,
            timestamp=ts,
            heart_rate=reading.heart_rate,
            spo2=reading.spo2,
            respiratory_rate=reading.respiratory_rate,
            systolic_bp=reading.systolic_bp,
            diastolic_bp=reading.diastolic_bp,
            temperature=reading.temperature,
            glucose=reading.glucose,
            weight_kg=reading.weight_kg,
        )
        vital_signs.append(vs)

        # Prepare event payload; track latest by timestamp
        base_event = {
            "patient_id": str(patient_id),
            "timestamp": ts.isoformat(),
            "heart_rate": reading.heart_rate,
            "spo2": reading.spo2,
            "respiratory_rate": reading.respiratory_rate,
            "systolic_bp": reading.systolic_bp,
            "diastolic_bp": reading.diastolic_bp,
            "temperature": reading.temperature,
            "glucose": reading.glucose,
            "weight_kg": reading.weight_kg,
        }
        event = {k: v for k, v in base_event.items() if v is not None}
        if latest_ts is None or ts > latest_ts:
            latest_ts = ts
            latest_event = event

    # Persist all records in one transaction
    try:
        session.add_all(vital_signs)
        session.commit()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database Write Failed: {str(e)}") from e

    # Publish events (best-effort)
    try:
        pipe = redis_client.pipeline()
        for vs in vital_signs:
            ev = {
                "patient_id": str(vs.patient_id),
                "timestamp": vs.timestamp.isoformat(),
                "heart_rate": vs.heart_rate,
                "spo2": vs.spo2,
                "respiratory_rate": vs.respiratory_rate,
                "systolic_bp": vs.systolic_bp,
                "diastolic_bp": vs.diastolic_bp,
                "temperature": vs.temperature,
                "glucose": vs.glucose,
                "weight_kg": vs.weight_kg,
            }
            ev = {k: v for k, v in ev.items() if v is not None}
            pipe.publish("vital_signs_channel", json.dumps(ev))

        # Cache the latest (if any)
        if latest_event is not None:
            pipe.set(f"latest:{patient_id}", json.dumps(latest_event))

        pipe.execute()
    except RedisError as e:
        logger.warning("Redis publish (batch) failed for patient %s: %s", patient_id, e)

    return vital_signs


# =====================================================
# Latest Snapshot
# =====================================================
@app.get("/{patient_id}/latest", response_model=TelemetryOut)
def get_latest_vitals(patient_id: UUID4, session: Session = Depends(get_session)):
    """
    Return the most recent vitals snapshot for a patient.
    1) Try Redis cache (O(1))
    2) Fallback to DB (ORDER BY timestamp DESC LIMIT 1)
    """

    # Ensure patient exists before reading
    _ensure_patient_exists(patient_id)

    cache_key = f"latest:{patient_id}"
    try:
        cached = redis_client.get(cache_key)
        if cached:
            data = json.loads(cached)
            # Ensure patient_id present and consistent
            data["patient_id"] = str(patient_id)
            return TelemetryOut(**data)
    except RedisError as e:
        logger.warning("Redis get latest failed for patient %s: %s", patient_id, e)

    # Fallback to DB
    stmt = (
        select(VitalSign)
        .where(VitalSign.patient_id == patient_id)
        .order_by(VitalSign.timestamp.desc())
        .limit(1)
    )
    result = session.exec(stmt).first()
    if not result:
        raise HTTPException(status_code=404, detail="No vitals found for patient")

    # Optionally refresh cache with the DB result
    try:
        ev = {
            "patient_id": str(result.patient_id),
            "timestamp": result.timestamp.isoformat(),
            "heart_rate": result.heart_rate,
            "spo2": result.spo2,
            "respiratory_rate": result.respiratory_rate,
            "systolic_bp": result.systolic_bp,
            "diastolic_bp": result.diastolic_bp,
            "temperature": result.temperature,
            "glucose": result.glucose,
            "weight_kg": result.weight_kg,
        }
        ev = {k: v for k, v in ev.items() if v is not None}
        redis_client.set(cache_key, json.dumps(ev))
    except RedisError:
        # best-effort cache refresh
        logger.debug("Redis error refreshing latest cache", exc_info=True)

    return result


# =====================================================
# Historical: Single Metric
# =====================================================
@app.get("/{patient_id}/history", response_model=List[TimeseriesPoint])
def get_history(
    patient_id: UUID4,
    start_time: datetime,
    end_time: datetime,
    metric_type: str,
    session: Session = Depends(get_session),
):
    """
    Return a time series of a single metric over a time window.
    Required query params: start_time, end_time, metric_type.
    metric_type ∈ {heart_rate, spo2, respiratory_rate, systolic_bp, diastolic_bp, temperature, glucose, weight_kg}
    """

    # Ensure patient exists before reading
    _ensure_patient_exists(patient_id)

    allowed_metrics = {
        "heart_rate",
        "spo2",
        "respiratory_rate",
        "systolic_bp",
        "diastolic_bp",
        "temperature",
        "glucose",
        "weight_kg",
    }

    if metric_type not in allowed_metrics:
        raise HTTPException(status_code=400, detail=f"Unsupported metric_type: {metric_type}")

    # Normalize times to UTC and validate range
    st = start_time if start_time.tzinfo else start_time.replace(tzinfo=timezone.utc)
    et = end_time if end_time.tzinfo else end_time.replace(tzinfo=timezone.utc)
    if et < st:
        raise HTTPException(status_code=400, detail="end_time must be >= start_time")

    # Query DB for the window
    stmt = (
        select(VitalSign)
        .where(VitalSign.patient_id == patient_id)
        .where(VitalSign.timestamp >= st)
        .where(VitalSign.timestamp <= et)
        .order_by(VitalSign.timestamp.asc())
    )

    rows = session.exec(stmt).all()
    points: List[TimeseriesPoint] = []
    for vs in rows:
        val = getattr(vs, metric_type)
        if val is not None:
            points.append(TimeseriesPoint(timestamp=vs.timestamp, value=float(val)))

    return points


# =====================================================
# Historical: Full Telemetry
# =====================================================
@app.get("/{patient_id}/history/telemetry", response_model=List[TelemetryOut])
def get_history_telemetry(
    patient_id: UUID4,
    start_time: datetime,
    end_time: datetime,
    metric_type: str | None = None,
    session: Session = Depends(get_session),
):
    """
    Return full telemetry records over a time window.
    Optional metric_type filters out rows where that metric is null.
    """

    _ensure_patient_exists(patient_id)

    st = start_time if start_time.tzinfo else start_time.replace(tzinfo=timezone.utc)
    et = end_time if end_time.tzinfo else end_time.replace(tzinfo=timezone.utc)
    if et < st:
        raise HTTPException(status_code=400, detail="end_time must be >= start_time")

    stmt = (
        select(VitalSign)
        .where(VitalSign.patient_id == patient_id)
        .where(VitalSign.timestamp >= st)
        .where(VitalSign.timestamp <= et)
        .order_by(VitalSign.timestamp.asc())
    )

    rows = session.exec(stmt).all()
    if metric_type:
        allowed_metrics = {
            "heart_rate",
            "spo2",
            "respiratory_rate",
            "systolic_bp",
            "diastolic_bp",
            "temperature",
            "glucose",
            "weight_kg",
        }
        if metric_type not in allowed_metrics:
            raise HTTPException(status_code=400, detail=f"Unsupported metric_type: {metric_type}")
        rows = [vs for vs in rows if getattr(vs, metric_type) is not None]

    return rows


# =====================================================
# Deletion
# =====================================================
@app.delete("/{patient_id}", status_code=204)
def delete_patient_vitals(patient_id: UUID4, session: Session = Depends(get_session)):
    """
    Delete all vitals rows for a patient. Returns 204 on success.
    Checks existence in TimescaleDB only (user-service may have already deleted the user).
    Also clears Redis latest cache for this patient.
    """

    # Check existence in DB first
    exists_stmt = select(VitalSign.timestamp).where(VitalSign.patient_id == patient_id).limit(1)
    exists = session.exec(exists_stmt).first()
    if not exists:
        raise HTTPException(status_code=404, detail="No vitals found for patient")

    try:
        stmt = sa_delete(VitalSign).where(VitalSign.patient_id == patient_id)
        session.exec(stmt)
        session.commit()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Deletion failed: {e}")

    try:
        redis_client.delete(f"latest:{patient_id}")
    except RedisError:
        # best-effort cache cleanup
        logger.debug("Redis error deleting latest cache", exc_info=True)

    return None


# =====================================================
# Doctor Overview
# =====================================================
@app.get("/{doctor_id}/overview", response_model=List[TelemetryOut])
def get_doctor_overview(doctor_id: UUID4, session: Session = Depends(get_session)):
    """
    Return latest vitals for all patients associated with the given doctor.
    Uses Redis cache first, then optimized DB fallback.
    """

    patient_ids = _get_doctor_patients(doctor_id)
    if not patient_ids:
        return []

    # 1) Try Redis MGET
    keys = [f"latest:{pid}" for pid in patient_ids]
    latest_map: Dict[str, TelemetryOut] = {}
    missing_ids: List[UUID4] = []

    try:
        values = redis_client.mget(keys)
        for pid, raw in zip(patient_ids, values):
            if raw:
                try:
                    data = json.loads(raw)
                    data["patient_id"] = str(pid)
                    latest_map[str(pid)] = TelemetryOut(**data)
                except Exception:
                    missing_ids.append(pid)
            else:
                missing_ids.append(pid)
    except RedisError:
        # If Redis fails, fetch all from DB
        missing_ids = list(patient_ids)

    # 2) Optimized DB fallback using DISTINCT ON
    if missing_ids:
        # We need to cast UUIDs to strings for the SQL IN clause to work safely with params
        # or pass them as a list if the driver supports it.
        # Using text() allows us to use the efficient DISTINCT ON syntax which SQLModel
        # doesn't fully support in its high-level API yet.

        query = text(
            """
            SELECT DISTINCT ON (patient_id) *
            FROM vital_signs
            WHERE patient_id = ANY(:pids)
            ORDER BY patient_id, timestamp DESC
        """
        )

        # Execute query
        result = session.exec(query, params={"pids": missing_ids}).all()

        for row in result:
            # Row is a result object, accessing fields by name
            pid_str = str(row.patient_id)

            # Construct object
            telemetry = TelemetryOut(
                patient_id=row.patient_id,
                timestamp=row.timestamp,
                heart_rate=row.heart_rate,
                spo2=row.spo2,
                respiratory_rate=row.respiratory_rate,
                systolic_bp=row.systolic_bp,
                diastolic_bp=row.diastolic_bp,
                temperature=row.temperature,
                glucose=row.glucose,
                weight_kg=row.weight_kg,
            )

            latest_map[pid_str] = telemetry

            # Cache repair (Self-Healing Cache)
            try:
                # Convert back to dict for JSON serialization
                # We use telemetry.model_dump() (Pydantic v2) or .dict() (v1)
                # Since we used model_dump in other parts, stick to that.
                dump = telemetry.model_dump(mode="json")
                redis_client.set(f"latest:{pid_str}", json.dumps(dump))
            except RedisError as e:
                logger.warning(f"Cache repair failed for {pid_str}: {e}")

    # 3) Build final list preserving order
    result: List[TelemetryOut] = []
    for pid in patient_ids:
        item = latest_map.get(str(pid))
        if item:
            result.append(item)

    return result
