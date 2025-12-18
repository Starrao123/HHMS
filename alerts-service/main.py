import logging
import os
import time
import uuid
from datetime import datetime
from typing import List

import httpx
import redis
from db import get_session, init_db
from fastapi import Depends, FastAPI, HTTPException, Request
from models.models import Alert
from models.schemas import (
    AcknowledgeRequest,
    AlertCreate,
    AlertResponse,
    AlertStatus,
    DependencyStatus,
    HealthCheckResponse,
)
from sqlmodel import Session, select
from starlette.middleware.base import BaseHTTPMiddleware

# Get the ROOT_PATH environment variable defined in docker-compose
root_path = os.getenv("ROOT_PATH", "")

# Logging middleware
logger = logging.getLogger("alerts-service")
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
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


# Initialize DB on startup
from contextlib import asynccontextmanager


@asynccontextmanager
async def lifespan(application: FastAPI):
    try:
        init_db()
        logger.info("Alerts DB initialized.")
    except Exception as e:
        logger.error("Alerts DB init failed: %s", e)
    yield


app = FastAPI(
    title="Alerts Service",
    root_path=root_path,
    lifespan=lifespan,
)

# Lifespan is already set on app via constructor
app.add_middleware(LoggingMiddleware)


@app.get("/health", response_model=HealthCheckResponse)
def health():
    service_name = "alerts-service"
    dependencies = {}

    # Check Redis
    start = time.time()
    try:
        # Create a redis connection
        r = redis.Redis(host=os.environ["REDIS_HOST"], port=int(os.environ.get("REDIS_PORT", 6379)))

        # Ping Redis to check health
        if r.ping():
            dependencies["redis"] = DependencyStatus(
                status="healthy", response_time_ms=int((time.time() - start) * 1000)
            )

        # If ping fails, we know it is unhealthy
        else:
            dependencies["redis"] = DependencyStatus(
                status="unhealthy", response_time_ms=int((time.time() - start) * 1000)
            )
    except Exception as e:
        dependencies["redis"] = DependencyStatus(
            status="unhealthy", response_time_ms=None, error=str(e)
        )

    # Check analytics service
    start = time.time()
    try:
        # url inside docker network
        url = "http://analytics-service:8000/health"
        with httpx.Client(timeout=2.0) as client:
            resp = client.get(url)

        # Check if the response is healthy
        if resp.status_code == 200:
            dependencies["analytics-service"] = DependencyStatus(
                status="healthy", response_time_ms=int((time.time() - start) * 1000)
            )
        else:
            dependencies["analytics-service"] = DependencyStatus(
                status="unhealthy", response_time_ms=int((time.time() - start) * 1000)
            )
    except Exception as e:
        dependencies["analytics-service"] = DependencyStatus(
            status="unhealthy", response_time_ms=None, error=str(e)
        )

    # Aggregate status
    overall_status = (
        "healthy" if all(dep.status == "healthy" for dep in dependencies.values()) else "unhealthy"
    )
    if overall_status == "unhealthy":
        raise HTTPException(
            status_code=503,
            detail=HealthCheckResponse(
                service=service_name, status=overall_status, dependencies=dependencies
            ).model_dump(),
        )

    return HealthCheckResponse(
        service=service_name, status=overall_status, dependencies=dependencies
    )


# =====================================================
# Notifications: Send via Twilio
# =====================================================


def _twilio_env() -> dict:
    return {
        "sid": os.environ.get("TWILIO_ACCOUNT_SID"),
        "token": os.environ.get("TWILIO_AUTH_TOKEN"),
        "from": os.environ.get("TWILIO_FROM_NUMBER"),
    }


def _twilio_test_mode() -> bool:
    val = os.environ.get("TWILIO_TEST_MODE", "0").lower()
    return val in ("1", "true", "yes")


@app.post("/notifications/send", response_model=AlertResponse, status_code=201)
def send_notification(payload: AlertCreate, session: Session = Depends(get_session)):
    # Validate patient_id present
    if not payload.patient_id:
        raise HTTPException(status_code=422, detail="patient_id is required")
    # Create initial alert record (pending)
    alert = Alert(
        patient_id=uuid.UUID(str(payload.patient_id)),
        message=payload.message,
        severity=payload.severity,
        status=AlertStatus.PENDING,
    )

    # Attempt to fetch patient phone from user-service
    try:
        user_url = f"http://user-service:8000/{payload.patient_id}"
        with httpx.Client(timeout=3.0) as client:
            resp = client.get(user_url)
        if resp.status_code == 200:
            data = resp.json()
            alert.recipient_phone = data.get("phone")
    except Exception as e:
        # Non-blocking: we'll still record the alert and mark failed if we cannot send
        alert.error_message = f"user-lookup-failed: {e}"

    # Persist initial record
    session.add(alert)
    session.commit()
    session.refresh(alert)

    # If we have phone and Twilio config, try sending SMS
    env = _twilio_env()
    if _twilio_test_mode():
        # Mock sending for local/dev
        alert.provider_message_id = f"mock-{uuid.uuid4()}"
        alert.status = AlertStatus.SENT
        alert.sent_at = datetime.utcnow()
        logger.info("Twilio TEST MODE: mock-sent alert %s to %s", alert.id, alert.recipient_phone)
    elif alert.recipient_phone and env["sid"] and env["token"] and env["from"]:
        try:
            twilio_url = f"https://api.twilio.com/2010-04-01/Accounts/{env['sid']}/Messages.json"
            form = {
                "From": env["from"],
                "To": alert.recipient_phone,
                "Body": payload.message,
            }
            with httpx.Client(timeout=5.0) as client:
                tw_resp = client.post(twilio_url, data=form, auth=(env["sid"], env["token"]))
            if tw_resp.status_code in (200, 201):
                tw_data = tw_resp.json()
                alert.provider_message_id = tw_data.get("sid")
                alert.status = AlertStatus.SENT
                alert.sent_at = datetime.utcnow()
            else:
                alert.status = AlertStatus.FAILED
                alert.error_message = f"twilio-error: {tw_resp.text}"
        except Exception as e:
            alert.status = AlertStatus.FAILED
            alert.error_message = f"twilio-exception: {e}"
    else:
        # Missing phone or config
        if not alert.recipient_phone:
            alert.error_message = (alert.error_message or "") + " missing-phone"
        if not (env["sid"] and env["token"] and env["from"]):
            alert.error_message = (alert.error_message or "") + " missing-twilio-config"
        alert.status = AlertStatus.FAILED

    session.add(alert)
    session.commit()
    session.refresh(alert)

    return AlertResponse.model_validate(alert)


# =====================================================
# Alerts History
# =====================================================


@app.get("/{patient_id}", response_model=List[AlertResponse])
def get_alert_history(patient_id: uuid.UUID, session: Session = Depends(get_session)):
    results = session.exec(
        select(Alert).where(Alert.patient_id == patient_id).order_by(Alert.created_at.desc())
    ).all()
    return [AlertResponse.model_validate(a) for a in results]


# =====================================================
# Acknowledge / Resolve Alert
# =====================================================


@app.post("/{alert_id}/acknowledge", response_model=AlertResponse)
def acknowledge_alert(
    alert_id: uuid.UUID, payload: AcknowledgeRequest, session: Session = Depends(get_session)
):
    alert = session.get(Alert, alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")

    if payload.status not in (AlertStatus.ACKNOWLEDGED, AlertStatus.RESOLVED):
        raise HTTPException(status_code=400, detail="status must be acknowledged or resolved")

    alert.status = payload.status
    alert.acknowledged_by = uuid.UUID(str(payload.doctor_id))
    alert.acknowledged_at = datetime.utcnow()

    session.add(alert)
    session.commit()
    session.refresh(alert)
    return AlertResponse.model_validate(alert)


# =====================================================
# System Status (Admin only)
# =====================================================


def _require_admin(request: Request):
    token_env = os.environ.get("ADMIN_TOKEN")
    if not token_env:
        return True  # If not configured, do not block (optional)
    token_hdr = request.headers.get("X-Admin-Token")
    if token_hdr != token_env:
        raise HTTPException(status_code=403, detail="Forbidden")
    return True


@app.get("/system/status")
def system_status(request: Request):
    _require_admin(request)
    env = _twilio_env()
    dep = {}

    # Check Twilio credentials validity by fetching account info
    if env["sid"] and env["token"]:
        try:
            url = f"https://api.twilio.com/2010-04-01/Accounts/{env['sid']}.json"
            with httpx.Client(timeout=5.0) as client:
                r = client.get(url, auth=(env["sid"], env["token"]))
            if r.status_code == 200:
                dep["twilio_api"] = {"status": "healthy"}
            else:
                dep["twilio_api"] = {"status": "unhealthy", "error": r.text}
        except Exception as e:
            dep["twilio_api"] = {"status": "unhealthy", "error": str(e)}
    else:
        dep["twilio_api"] = {"status": "unhealthy", "error": "missing-credentials"}

    return {
        "service": "alerts-service",
        "twilio": dep.get("twilio_api"),
    }


# -----------------------------------------------------
# Compatibility route aligning with README: /alerts/send/{patient_id}
# -----------------------------------------------------


@app.post("/send/{patient_id}", response_model=AlertResponse, status_code=201)
def send_alert_for_patient(
    patient_id: uuid.UUID, payload: AlertCreate, session: Session = Depends(get_session)
):
    # Ensure payload patient_id matches path
    payload.patient_id = patient_id
    return send_notification(payload, session)
