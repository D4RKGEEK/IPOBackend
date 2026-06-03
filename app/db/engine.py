"""Database engine and session — single source of truth for DB connections."""
import os
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session, DeclarativeBase


class Base(DeclarativeBase):
    pass


_engine = None
_SessionLocal = None


def _resolve_db_url() -> str:
    """Pick the DB connection URL. DATABASE_URL wins, else local SQLite."""
    from app.config import settings
    return settings.db_url


def get_engine():
    global _engine
    if _engine is None:
        url = _resolve_db_url()
        # Ensure we use psycopg v3 (installed as 'psycopg') not the missing psycopg2.
        # SQLAlchemy defaults to psycopg2 for plain postgresql:// URLs.
        if url.startswith("postgresql://") or url.startswith("postgres://"):
            url = url.replace("postgresql://", "postgresql+psycopg://", 1)
            url = url.replace("postgres://", "postgresql+psycopg://", 1)
        kwargs = {}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False}
        if url.startswith("postgresql"):
            kwargs.update({
                "pool_pre_ping": True,
                "pool_size": 15,
                "max_overflow": 30,
                "connect_args": {"prepare_threshold": None},
            })
        _engine = create_engine(url, echo=False, **kwargs)
    return _engine


def get_session() -> Session:
    global _SessionLocal
    if _SessionLocal is None:
        _engine = get_engine()
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
    return _SessionLocal()


def init_db():
    """Create all tables. Safe to call multiple times."""
    from app.db.models import Base as ModelsBase
    engine = get_engine()
    ModelsBase.metadata.create_all(engine)


def utcnow():
    return datetime.now(timezone.utc)
