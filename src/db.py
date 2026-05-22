"""
Database setup and session management.
"""

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.models.schema import Base

_engine = None
_SessionFactory = None


def init_db(config):
    """Initialize the database, creating tables if they don't exist."""
    global _engine, _SessionFactory

    db_path = Path(config["database"]["path"])
    db_path.parent.mkdir(parents=True, exist_ok=True)

    _engine = create_engine(f"sqlite:///{db_path}", echo=False)
    Base.metadata.create_all(_engine)
    _SessionFactory = sessionmaker(bind=_engine)


def get_session(config=None):
    """Get a new database session."""
    global _engine, _SessionFactory

    if _SessionFactory is None:
        if config is None:
            raise RuntimeError("Database not initialized. Call init_db(config) first.")
        init_db(config)

    return _SessionFactory()
