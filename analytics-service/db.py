import os
from contextlib import contextmanager
from typing import Iterator, Optional

from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel, create_engine, Session


# Load the Postgres DSN (connection string) from environment variables
PG_DSN = os.getenv("PG_DSN")
if not PG_DSN:
    raise RuntimeError("PG_DSN environment variable is not set")

# Create the SQLAlchemy engine that connects to your database
engine = create_engine(PG_DSN, pool_pre_ping=True)

# create tables if they don't exist
def init_db() -> None:
    SQLModel.metadata.create_all(engine)
    print("Database initialized and tables created (if not exist).")


# obtain the session
@contextmanager
def get_session() -> Iterator[Session]:
    session = Session(engine)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

# close the database connection cleanly
def close_db_connection() -> None:
    engine.dispose()
    print("Database connection closed.")
