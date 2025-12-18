import os
from typing import Generator

from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine

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

    # Ensure case-insensitive uniqueness on email
    # Create unique index on lower(email)
    try:
        with engine.connect() as conn:
            conn.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS users_email_lower_idx ON users (lower(email))"
                )
            )
            conn.commit()
    except Exception as e:
        # Log or print, but don't crash startup if index creation fails
        print(f"Warning: could not ensure users_email_lower_idx: {e}")

    print("Database initialized.")


# 4. Dependency for FastAPI
def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session
