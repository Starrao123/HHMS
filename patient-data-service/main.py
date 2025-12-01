import os
import time

import httpx
import psycopg2
import redis
from fastapi import FastAPI, HTTPException
from models import Dependency, HealthCheckResponse

app = FastAPI(title="Patient Data Service")


@app.get("/health", response_model=HealthCheckResponse)
def health():
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
            port=int(os.environ.get("TIMESCALE_PORT", 5432))
        )
        # If connection is successful, mark as healthy
        conn.close()

        dependencies["timescale-data"] = Dependency(
            status="healthy",
            response_time_ms=int((time.time() - start) * 1000)
        )
    except Exception:
        dependencies["timescale-data"] = Dependency(
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
                status="healthy",
                response_time_ms=int((time.time() - start) * 1000)
            )
        else:
            dependencies["user-service"] = Dependency(
                status="unhealthy",
                response_time_ms=int((time.time() - start) * 1000)
            )

    except Exception:
        dependencies["user-service"] = Dependency(
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
