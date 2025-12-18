import os
from typing import Generator

from sqlmodel import Session, SQLModel, create_engine

# 1. Load Config
# Matches the environment variables in your docker-compose
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL environment variable is not set")

# 2. Create Engine
# pool_pre_ping=True is crucial for Docker to handle connection drops/restarts automatically
engine = create_engine(DATABASE_URL, pool_pre_ping=True)


# 3. Initialization
def init_db() -> None:
    """
    Creates the 'alerts' table if it doesn't exist.
    """
    SQLModel.metadata.create_all(engine)
    print("Alerts Database initialized.")


# 4. Dependency for FastAPI
def get_session() -> Generator[Session, None, None]:
    """
    Yields a database session. Used in FastAPI routes via Depends(get_session).
    """
    with Session(engine) as session:
        yield session


def close_db_connection() -> None:
    engine.dispose()
