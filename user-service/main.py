# /user-service/main.py

import os
import time
import uuid
import logging
from contextlib import asynccontextmanager

import psycopg2
import redis
from fastapi import FastAPI, HTTPException, Request, Depends, status
from starlette.middleware.base import BaseHTTPMiddleware
from sqlmodel import Session, select

# --- Local Imports ---
# We now import the specific classes from their respective files
from db import get_session, init_db
from models.models import User  # The Database Table
from models.schemas import (    # The API Data Structures
    UserCreate, 
    UserResponse, 
    HealthCheckResponse, 
    Dependency
)

# =====================================================
# Configuration & Middleware
# =====================================================

# Logging setup
logger = logging.getLogger("user-service")
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

# Initialize Redis client
redis_client = redis.Redis(
    host=os.environ.get("REDIS_HOST", "localhost"),
    port=int(os.environ.get("REDIS_PORT", 6379)),
    decode_responses=True,
)

# Lifespan context (Modern replacement for startup events)
@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        init_db()
        logger.info("Database initialized successfully.")
    except Exception as e:
        logger.error(f"Database initialization failed: {e}")
    yield
    # Shutdown logic (if needed) goes here

# Get the ROOT_PATH environment variable defined in docker-compose
root_path = os.getenv("ROOT_PATH", "")

app = FastAPI(
    title="User Service",
    lifespan=lifespan,
    root_path=root_path
)
app.add_middleware(LoggingMiddleware)


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
            port=int(os.environ.get("POSTGRES_PORT", 5432))
        )
        conn.close()
        dependencies["postgres-user"] = Dependency(
            status="healthy",
            response_time_ms=int((time.time() - start) * 1000)
        )
    except Exception as e:
        logger.error(f"Health check failed for Postgres: {e}")
        dependencies["postgres-user"] = Dependency(
            status="unhealthy", response_time_ms=None, error=str(e)
        )

    # 2. Check Redis
    start = time.time()
    try:
        if redis_client.ping():
            dependencies["redis"] = Dependency(
                status="healthy",
                response_time_ms=int((time.time() - start) * 1000)
            )
        else:
            dependencies["redis"] = Dependency(
                status="unhealthy", response_time_ms=None, error="Ping failed"
            )
    except Exception as e:
        logger.error(f"Health check failed for Redis: {e}")
        dependencies["redis"] = Dependency(
            status="unhealthy", response_time_ms=None, error=str(e)
        )

    # Aggregate status
    overall_status = "healthy" if all(d.status == "healthy" for d in dependencies.values()) else "unhealthy"
    
    response = HealthCheckResponse(
        service=service_name, 
        status=overall_status, 
        dependencies=dependencies
    )

    if overall_status == "unhealthy":
        raise HTTPException(status_code=503, detail=response.model_dump())

    return response


# =====================================================
# Main Routes
# =====================================================

@app.post("/users/register", response_model=UserResponse, status_code=201)
def register_user(payload: UserCreate, session: Session = Depends(get_session)):
    """
    Registers a new user (Patient or Doctor).
    """

    # 1. Check for existing email
    # Use SQLModel select syntax
    existing = session.exec(select(User).where(User.email == payload.email)).first()
    if existing:
        raise HTTPException(status_code=409, detail="User with this email already exists")

    # 3. Create Database Model
    # Explicitly map fields to ensure separation of API schema and DB Model
    db_user = User(
        email=payload.email,
        full_name=payload.full_name,
        role=payload.role,
        phone=payload.phone,
        organization=payload.organization,
        is_active=True,
        created_at=time.time(),
        updated_at=time.time()
    )

    # 4. Save to DB
    session.add(db_user)
    session.commit()
    session.refresh(db_user)

    # 5. Invalidate Cache (Cache-aside)
    cache_key = f"user:{db_user.id}"
    try:
        redis_client.delete(cache_key)
    except Exception:
        pass # Cache errors shouldn't block registration

    # 6. Return
    return db_user


@app.get("/users/{user_id}", response_model=UserResponse)
def get_user(user_id: str, session: Session = Depends(get_session)):
    """
    Retrieves user details by ID.
    """
    # 1. Check Cache
    cache_key = f"user:{user_id}"
    cached_data = redis_client.get(cache_key)

    if cached_data:
        # Deserialize JSON back into Pydantic Schema
        return UserResponse.model_validate_json(cached_data)

    # 2. Validate UUID format
    try:
        uid = uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user_id format")

    # 3. Query DB
    db_user = session.get(User, uid)
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")

    # 4. Convert to Response Schema & Cache
    # We convert to the Schema *before* caching to ensure we don't cache 
    # the hashed_password or internal DB fields.
    response_obj = UserResponse.model_validate(db_user)
    
    # Store in Redis (Expire in 10 minutes)
    redis_client.setex(cache_key, 600, response_obj.model_dump_json())

    return response_obj