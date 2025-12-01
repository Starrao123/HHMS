import os
import time

import psycopg2
import redis
from fastapi import FastAPI, HTTPException
from models import Dependency, HealthCheckResponse

app = FastAPI(title="User Service")


@app.get("/health", response_model=HealthCheckResponse)
def health():
    dependencies = {}
    service_name = "user-service"

    # Check Postgres
    start = time.time()
    try:
        # try to connect to the database
        conn = psycopg2.connect(
            dbname=os.environ["POSTGRES_DB"],
            user=os.environ["POSTGRES_USER"],
            password=os.environ["POSTGRES_PASSWORD"],
            host=os.environ["POSTGRES_HOST"],
            port=int(os.environ.get("POSTGRES_PORT", 5432))
        )
        # If connection is successful, mark as healthy
        conn.close()

        # If connection is successful, mark as healthy
        dependencies["postgres-user"] = Dependency(
            status="healthy",
            response_time_ms=int((time.time() - start) * 1000)
        )
    except Exception:
        dependencies["postgres-user"] = Dependency(
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
