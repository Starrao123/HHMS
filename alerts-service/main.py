import os
import time

import httpx
import redis
from fastapi import FastAPI, HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware
import logging
import uuid
from models import Dependency, HealthCheckResponse

# Get the ROOT_PATH environment variable defined in docker-compose
root_path = os.getenv("ROOT_PATH", "")

app = FastAPI(
    title="Alerts Service",
    root_path="/alerts"
)

# Logging middleware
logger = logging.getLogger("alerts-service")
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
    service_name = "alerts-service"
    dependencies = {}

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

        # If ping fails, we know it is unhealthy
        else:
            dependencies["redis"] = Dependency(
                status="unhealthy",
                response_time_ms=int((time.time() - start) * 1000)
            )
    except Exception:
        dependencies["redis"] = Dependency(
            status="unhealthy", response_time_ms=None)

    # Check analytics service
    start = time.time()
    try:
        # url inside docker network
        url = "http://analytics-service:8000/health"
        with httpx.Client(timeout=2.0) as client:
            resp = client.get(url)

        # Check if the response is healthy
        if resp.status_code == 200:
            dependencies["analytics-service"] = Dependency(
                status="healthy",
                response_time_ms=int((time.time() - start) * 1000)
            )
        else:
            dependencies["analytics-service"] = Dependency(
                status="unhealthy",
                response_time_ms=int((time.time() - start) * 1000)
            )

    except Exception:
        dependencies["analytics-service"] = Dependency(
            status="unhealthy",
            response_time_ms=None
        )

    # Aggregate status
    overall_status = "healthy" if all(
        dep.status == "healthy" for dep in dependencies.values()) else "unhealthy"
    if overall_status == "unhealthy":
        raise HTTPException(
            status_code=503,
            detail=HealthCheckResponse(
                service=service_name, status=overall_status, dependencies=dependencies).dict()
        )

    return HealthCheckResponse(service=service_name, status=overall_status, dependencies=dependencies)
