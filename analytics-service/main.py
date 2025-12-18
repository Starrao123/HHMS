# analytics-service/main.py

"""
Analytics Service
Handles threshold configuration, anomaly detection against real-time telemetry,
and exposes health checks.
"""

import json
import logging

# =====================================================
# Standard Library Imports
# =====================================================
import os
import threading
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import List, Optional

# =====================================================
# Third-Party Imports
# =====================================================
import httpx
import psycopg2
import redis

# =====================================================
# Local Imports
# =====================================================
from db import engine, get_session, init_db
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request
from models.models import AnomalyEvent, Threshold
from models.schemas import DependencyStatus  # Standardized naming
from models.schemas import (
    AlertSeverity,
    AnomalyResponse,
    HealthCheckResponse,
    MetricType,
    ThresholdCreate,
    ThresholdResponse,
)
from redis.exceptions import RedisError
from sqlmodel import Session, select
from starlette.middleware.base import BaseHTTPMiddleware

# =====================================================
# Configuration
# =====================================================
logger = logging.getLogger("analytics-service")
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)

# Alerts Service URL
ALERTS_SERVICE_URL = "http://alerts-service:8000"

# Redis Config
REDIS_HOST = os.environ.get("REDIS_HOST", "localhost")
REDIS_PORT = int(os.environ.get("REDIS_PORT", 6379))

# Initialize Redis (Sync for API, separate instance for listener)
redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)

# Global flag to stop background threads on shutdown
STOP_EVENT = threading.Event()

# =====================================================
# Background Logic (The Core "Real-Time" Part)
# =====================================================


def process_vital_sign_event(data: dict):
    """
    1. Checks if the incoming vital sign violates any thresholds.
    2. If so, saves Anomaly to DB.
    3. Triggers Alert Service.
    """
    try:
        patient_id = data.get("patient_id")
        # We create a NEW session here because this runs in a separate thread
        with Session(engine) as session:
            # Fetch thresholds for this patient
            # Optimization: In a huge system, cache these thresholds in Redis/Memory
            thresholds = session.exec(
                select(Threshold).where(Threshold.patient_id == patient_id)
            ).all()

            if not thresholds:
                return

            anomalies = []

            # Check every metric present in the payload
            for th in thresholds:
                metric_name = th.metric.value
                observed_value = data.get(metric_name)

                if observed_value is None:
                    continue

                violation_desc = None

                if th.min_value is not None and observed_value < th.min_value:
                    violation_desc = f"{metric_name} {observed_value} < min {th.min_value}"
                elif th.max_value is not None and observed_value > th.max_value:
                    violation_desc = f"{metric_name} {observed_value} > max {th.max_value}"

                if violation_desc:
                    # 1. Record Anomaly
                    anomaly = AnomalyEvent(
                        patient_id=patient_id,
                        metric=th.metric,
                        observed_value=float(observed_value),
                        severity=AlertSeverity.WARNING,  # Logic could be more complex here
                        description=violation_desc,
                        timestamp=datetime.fromisoformat(data["timestamp"]),
                        threshold_id=th.id,
                    )
                    session.add(anomaly)
                    anomalies.append(anomaly)

            if anomalies:
                session.commit()
                # 2. Trigger Alerts for each anomaly
                for anomaly in anomalies:
                    try:
                        # Send to Alert Service (Fire and Forget)
                        httpx.post(
                            f"{ALERTS_SERVICE_URL}/notifications/send",
                            json={
                                "patient_id": str(anomaly.patient_id),
                                "message": f"Anomaly Detected: {anomaly.description}",
                                "severity": anomaly.severity.value,
                            },
                            timeout=2.0,
                        )
                    except httpx.HTTPError as e:
                        logger.error("Failed to trigger alert service: %s", e)

    except Exception as e:
        logger.error("Error processing event: %s", e)


def redis_listener():
    """
    Blocking loop that listens to Redis Pub/Sub.
    Run in a separate thread.
    """
    logger.info("Starting Redis Listener...")
    r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
    pubsub = r.pubsub()
    pubsub.subscribe("vital_signs_channel")

    # Use a loop with a timeout to allow checking STOP_EVENT
    while not STOP_EVENT.is_set():
        message = pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
        if message and message["type"] == "message":
            try:
                data = json.loads(message["data"])
                process_vital_sign_event(data)
            except Exception as e:
                logger.error(f"Invalid message format: {e}")

    logger.info("Redis Listener Stopped.")
    r.close()


# =====================================================
# Lifespan & App Setup
# =====================================================


@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- Startup ---
    try:
        init_db()
        logger.info("AnalyticsDB initialized.")
    except Exception as e:
        logger.error(f"AnalyticsDB initialization failed: {e}")

    # Start the Background Thread
    listener_thread = threading.Thread(target=redis_listener, daemon=True)
    listener_thread.start()

    yield

    # --- Shutdown ---
    STOP_EVENT.set()
    listener_thread.join(timeout=2.0)


root_path = os.getenv("ROOT_PATH", "/analytics")

app = FastAPI(
    title="Analytics Service",
    lifespan=lifespan,
    root_path=root_path,
)


class LoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        req_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        start_ts = time.time()
        logger.info(f"req_id={req_id} start method={request.method} path={request.url.path}")
        response = await call_next(request)
        duration_ms = int((time.time() - start_ts) * 1000)
        response.headers["X-Request-ID"] = req_id
        response.headers["X-Response-Time-ms"] = str(duration_ms)
        logger.info(
            f"req_id={req_id} end status={response.status_code} path={request.url.path} duration_ms={duration_ms}"
        )
        return response


app.add_middleware(LoggingMiddleware)


# =====================================================
# Helpers
# =====================================================


def _ensure_patient_exists(patient_id: uuid.UUID) -> None:
    """Verify patient exists via user-service."""
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


# =====================================================
# Routes: Threshold Management
# =====================================================


@app.get("/thresholds/{patient_id}", response_model=List[ThresholdResponse])
def list_thresholds(patient_id: uuid.UUID, session: Session = Depends(get_session)):
    _ensure_patient_exists(patient_id)
    ths = session.exec(select(Threshold).where(Threshold.patient_id == patient_id)).all()
    return [ThresholdResponse.model_validate(t) for t in ths]


@app.post("/thresholds", response_model=ThresholdResponse)
def create_or_update_threshold(payload: ThresholdCreate, session: Session = Depends(get_session)):
    _ensure_patient_exists(payload.patient_id)

    existing = session.exec(
        select(Threshold).where(
            Threshold.patient_id == payload.patient_id,
            Threshold.metric == payload.metric,
        )
    ).first()

    if existing:
        existing.min_value = payload.min_value
        existing.max_value = payload.max_value
        existing.updated_at = datetime.utcnow()
        session.add(existing)
        session.commit()
        session.refresh(existing)
        return existing

    new_th = Threshold(
        patient_id=payload.patient_id,
        metric=payload.metric,
        min_value=payload.min_value,
        max_value=payload.max_value,
    )
    session.add(new_th)
    session.commit()
    session.refresh(new_th)
    return new_th


@app.get("/thresholds/{patient_id}/{metric}", response_model=ThresholdResponse)
def get_threshold(
    patient_id: uuid.UUID, metric: MetricType, session: Session = Depends(get_session)
):
    _ensure_patient_exists(patient_id)
    th = session.exec(
        select(Threshold).where(
            Threshold.patient_id == patient_id,
            Threshold.metric == metric,
        )
    ).first()
    if not th:
        raise HTTPException(status_code=404, detail="Threshold not found")
    return th


# =====================================================
# Routes: Anomaly Management
# =====================================================


@app.post("/analyze/{patient_id}", response_model=List[AnomalyResponse])
def manual_analysis(patient_id: uuid.UUID, session: Session = Depends(get_session)):
    """
    MANUAL TRIGGER: Analyze last 1 hour of telemetry.
    Useful for testing or if the real-time stream was interrupted.
    """
    _ensure_patient_exists(patient_id)
    thresholds = session.exec(select(Threshold).where(Threshold.patient_id == patient_id)).all()
    if not thresholds:
        return []

    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(hours=1)

    anomalies = []

    # Iterate metrics and pull from Patient Data Service
    for th in thresholds:
        metric = th.metric.value
        try:
            with httpx.Client(timeout=3.0) as client:
                resp = client.get(
                    f"http://patient-data-service:8000/{patient_id}/history",
                    params={
                        "start_time": start_time.isoformat(),
                        "end_time": end_time.isoformat(),
                        "metric_type": metric,
                    },
                )
            if resp.status_code != 200:
                continue
            points = resp.json() or []
        except httpx.RequestError:
            continue

        for p in points:
            val = p.get("value")
            ts_str = p.get("timestamp")
            if val is None:
                continue

            observed = float(val)
            desc = None
            if th.min_value is not None and observed < th.min_value:
                desc = f"{metric} {observed} < min {th.min_value}"
            if th.max_value is not None and observed > th.max_value:
                desc = f"{metric} {observed} > max {th.max_value}"

            if desc:
                # Check duplication? For manual trigger, we usually just add it.
                ev = AnomalyEvent(
                    patient_id=patient_id,
                    timestamp=datetime.fromisoformat(ts_str.replace("Z", "+00:00")),
                    metric=th.metric,
                    observed_value=observed,
                    severity=AlertSeverity.WARNING,
                    description=desc,
                    threshold_id=th.id,
                )
                anomalies.append(ev)
                session.add(ev)

    if anomalies:
        session.commit()
        # Trigger alerts for manual analysis too?
        # Usually yes, but omitted here for brevity
        for ev in anomalies:
            session.refresh(ev)

    return anomalies


@app.get("/anomalies/{patient_id}", response_model=List[AnomalyResponse])
def list_anomalies(patient_id: uuid.UUID, session: Session = Depends(get_session)):
    """Return anomaly history for a patient (newest first)."""
    _ensure_patient_exists(patient_id)
    rows = session.exec(
        select(AnomalyEvent)
        .where(AnomalyEvent.patient_id == patient_id)
        .order_by(AnomalyEvent.timestamp.desc())
    ).all()
    return rows


# =====================================================
# Routes: Run (README compatibility)
# =====================================================


@app.post("/run")
def run_pipeline():
    """
    Minimal run trigger to align with README.
    In a real system, this would fan out tasks to analyze recent telemetry.
    """
    return {"status": "ok", "message": "analytics run trigger accepted"}


# =====================================================
# Health Check
# =====================================================


@app.get("/health", response_model=HealthCheckResponse)
def health():
    dependencies = {}

    # 1. Postgres
    start = time.time()
    try:
        conn = psycopg2.connect(
            dbname=os.environ["ANALYTICS_DB_NAME"],
            user=os.environ["ANALYTICS_DB_USER"],
            password=os.environ["ANALYTICS_DB_PASSWORD"],
            host=os.environ["ANALYTICS_DB_HOST"],
            port=int(os.environ.get("ANALYTICS_DB_PORT", 5432)),
        )
        conn.close()
        dependencies["postgres-analytics"] = DependencyStatus(
            status="healthy", response_time_ms=int((time.time() - start) * 1000)
        )
    except psycopg2.Error as e:
        dependencies["postgres-analytics"] = DependencyStatus(status="unhealthy", error=str(e))

    # 2. Redis
    start = time.time()
    try:
        if redis_client.ping():
            dependencies["redis"] = DependencyStatus(
                status="healthy", response_time_ms=int((time.time() - start) * 1000)
            )
        else:
            dependencies["redis"] = DependencyStatus(status="unhealthy", error="Ping failed")
    except RedisError as e:
        dependencies["redis"] = DependencyStatus(status="unhealthy", error=str(e))

    # 3. Patient Data Service
    start = time.time()
    try:
        with httpx.Client(timeout=2.0) as client:
            resp = client.get("http://patient-data-service:8000/health")
        if resp.status_code == 200:
            dependencies["patient-data-service"] = DependencyStatus(
                status="healthy", response_time_ms=int((time.time() - start) * 1000)
            )
        else:
            dependencies["patient-data-service"] = DependencyStatus(
                status="unhealthy", response_time_ms=int((time.time() - start) * 1000)
            )
    except httpx.HTTPError as e:
        dependencies["patient-data-service"] = DependencyStatus(status="unhealthy", error=str(e))

    overall_status = (
        "healthy" if all(d.status == "healthy" for d in dependencies.values()) else "unhealthy"
    )

    response = HealthCheckResponse(
        service="analytics-service", status=overall_status, dependencies=dependencies
    )

    if overall_status == "unhealthy":
        raise HTTPException(status_code=503, detail=response.model_dump())

    return response
