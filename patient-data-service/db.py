# patient-data-service/db.py

import os
from typing import Generator

from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine

# 1. Load the Connection String
# Matches the DATABASE_URL environment variable in your docker-compose
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    # Fallback for local testing if needed
    DATABASE_URL = os.getenv("PG_DSN")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL environment variable is not set")

# 2. Create the Engine
# pool_pre_ping=True ensures we don't use stale connections after a DB restart
engine = create_engine(DATABASE_URL, pool_pre_ping=True)


# 3. Initialize Database & Timescale Hypertable
def init_db() -> None:
    """
    Creates tables and converts the vital_signs table into a TimescaleDB Hypertable.
    """
    # Create tables based on SQLModel definitions
    SQLModel.metadata.create_all(engine)

    # TimescaleDB Specific: Convert to Hypertable
    # We partition by the 'timestamp' column.
    # 'if_not_exists => TRUE' prevents errors on service restarts.
    hypertable_sql = text(
        "SELECT create_hypertable('vital_signs', 'timestamp', if_not_exists => TRUE);"
    )

    with Session(engine) as session:
        try:
            session.exec(hypertable_sql)
            session.commit()
            print("Successfully initialized TimescaleDB Hypertable.")
        except Exception as e:
            # If Timescale extension isn't installed in the DB, this will fail.
            # But since you use timescale/timescaledb:latest-pg15 image, it will work.
            print(
                f"Note: Could not create hypertable (might already exist or extension missing): {e}"
            )


# 4. FastAPI Session Dependency
def get_session() -> Generator[Session, None, None]:
    """
    FastAPI dependency that provides a database session.
    Automatically closes the session after the request is finished.
    """
    with Session(engine) as session:
        yield session


# 5. Clean Cleanup
def close_db_connection() -> None:
    engine.dispose()
    print("Patient Data Service database connection closed.")
