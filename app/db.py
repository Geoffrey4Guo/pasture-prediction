"""
Database setup — SQLite via SQLModel / SQLAlchemy.
DB file lives next to main.py (configurable via DATABASE_URL env var).
"""
import os
from pathlib import Path
from contextlib import contextmanager
from sqlmodel import SQLModel, Session, create_engine

DB_PATH = Path(os.getenv("DATABASE_URL", "pasture.db"))
ENGINE = create_engine(f"sqlite:///{DB_PATH}", echo=False)


def create_db_and_tables() -> None:
    """Create all tables if they don't exist yet."""
    # Import all models so SQLModel registers them before metadata.create_all
    import app.models  # noqa: F401
    SQLModel.metadata.create_all(ENGINE)


@contextmanager
def get_session():
    with Session(ENGINE) as session:
        yield session
