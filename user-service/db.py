import os
from typing import Generator
from sqlmodel import SQLModel, create_engine, Session

# 1. Load Config
# We prioritize DATABASE_URL (standard for Docker), fallback to PG_DSN
PG_DSN = os.getenv("DATABASE_URL") or os.getenv("PG_DSN")
if not PG_DSN:
    raise RuntimeError("DATABASE_URL environment variable is not set")

# 2. Create Engine
# pool_pre_ping=True is excellent for production (reconnects if DB drops connection)
engine = create_engine(PG_DSN, pool_pre_ping=True, echo=False)

# 3. Initialization
def init_db() -> None:
    """
    Creates tables if they don't exist.
    IMPORTANT: You must import your models 'main.py' or wherever this is called
    BEFORE running this, or SQLModel won't know the tables exist.
    """
    SQLModel.metadata.create_all(engine)
    print("Database initialized.")

# 4. Dependency for FastAPI
def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session