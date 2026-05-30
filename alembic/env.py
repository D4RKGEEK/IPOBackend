"""
Alembic environment.

Drives migrations against whichever DB `app.config.settings.db_url` returns:
    - DATABASE_URL=postgresql://...   → Postgres (Supabase)
    - unset                            → SQLite (local dev)

Target metadata = app.db_models.Base.metadata, so `alembic revision --autogenerate`
picks up every column we add to ORM models.
"""
from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import create_engine, pool

from alembic import context

# Make `app/*` importable when alembic runs from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings           # noqa: E402
from app.db_models import Base            # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# NOTE: we deliberately do NOT call config.set_main_option("sqlalchemy.url", ...)
# because Alembic's ConfigParser would interpret `%` chars in passwords as
# interpolation syntax and raise. We pass settings.db_url directly to
# create_engine / context.configure below.

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Generate SQL without a live connection (`alembic upgrade --sql`)."""
    context.configure(
        url=settings.db_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=settings.db_dialect == "sqlite",
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live connection."""
    connectable = create_engine(settings.db_url, poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # Batch mode is required for ALTER TABLE on SQLite.
            render_as_batch=settings.db_dialect == "sqlite",
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
