import os
import time

import httpx
import psycopg2
import redis
from fastapi import FastAPI, HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware
import logging
import uuid
from models import Dependency, HealthCheckResponse

# Get the ROOT_PATH environment variable defined in docker-compose
root_path = os.getenv("ROOT_PATH", "")

app = FastAPI(
    title="Analytics Service",
    root_path="/analytics"
)

# Logging middleware
logger = logging.getLogger("analytics-service")
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))

class LoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        req_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        start_ts = time.time()
        logger.info(f"req_id={req_id} start method={request.method} path={request.url.path}")
        response = await call_next(request)
        duration_ms = int((time.time() - start_ts) * 1000)
        response.headers["X-Request-ID"] = req_id
        response.headers["X-Response-Time-ms"] = str(duration_ms)
        logger.info(f"req_id={req_id} end status={response.status_code} path={request.url.path} duration_ms={duration_ms}")
        return response

app.add_middleware(LoggingMiddleware)


@app.get("/health", response_model=HealthCheckResponse)
def health():
    dependencies = {}
    service_name = "analytics-service"

    # Check Postgres analytics
    start = time.time()
    try:
        # try to connect to the database
        conn = psycopg2.connect(
            dbname=os.environ["ANALYTICS_DB_NAME"],
            user=os.environ["ANALYTICS_DB_USER"],
            password=os.environ["ANALYTICS_DB_PASSWORD"],
            host=os.environ["ANALYTICS_DB_HOST"],
            port=int(os.environ.get("ANALYTICS_DB_PORT", 5432))
        )
        conn.close()
        # If connection is successful, mark as healthy
        dependencies["postgres-analytics"] = Dependency(
            status="healthy",
            response_time_ms=int((time.time() - start) * 1000)
        )
    except Exception:
        dependencies["postgres-analytics"] = Dependency(
            status="unhealthy", response_time_ms=None)

    # Check Redis
    start = time.time()
    try:
        # Create a redis connection
        r = redis.Redis(
            host=os.environ["REDIS_HOST"],
            port=int(os.environ.get("REDIS_PORT", 6379))
        )
        # Ping Redis to check health
        if r.ping():
            dependencies["redis"] = Dependency(
                status="healthy",
                response_time_ms=int((time.time() - start) * 1000)
            )
        else:
            dependencies["redis"] = Dependency(
                status="unhealthy",
                response_time_ms=int((time.time() - start) * 1000)
            )
    except Exception:
        dependencies["redis"] = Dependency(
            status="unhealthy", response_time_ms=None)

    # Check patient data service
    start = time.time()
    try:
        # url inside docker network
        url = "http://patient-data-service:8000/health"
        with httpx.Client(timeout=2.0) as client:
            resp = client.get(url)

        # check if the response is healthy
        if resp.status_code == 200:
            dependencies["patient-data-service"] = Dependency(
                status="healthy",
                response_time_ms=int((time.time() - start) * 1000)
            )
        else:
            dependencies["patient-data-service"] = Dependency(
                status="unhealthy",
                response_time_ms=int((time.time() - start) * 1000)
            )

    except Exception:
        dependencies["patient-data-service"] = Dependency(
            status="unhealthy",
            response_time_ms=None
        )

    # Aggregate status
    status = "healthy" if all(
        dep.status == "healthy" for dep in dependencies.values()) else "unhealthy"
    if status == "unhealthy":
        raise HTTPException(
            status_code=503,
            detail=HealthCheckResponse(
                service=service_name, status=status, dependencies=dependencies).dict()
        )

    return HealthCheckResponse(service=service_name, status=status, dependencies=dependencies)
