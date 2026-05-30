"""baseline — create all tables from current ORM models.

For Postgres (Supabase): this is the "fresh install" migration — builds every
table the app needs in one shot.

For SQLite (existing dev): the live DB was built incrementally by the earlier
raw-SQL migration scripts (scripts/migrate_*.py). After pulling these changes,
run `alembic stamp head` to mark the existing DB as up-to-date without
re-applying anything.

Revision ID: 3452eee164c4
Revises:
Create Date: 2026-05-30
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

from app.db_models import Base

revision: str = "3452eee164c4"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create every table defined on Base.metadata that doesn't already exist."""
    Base.metadata.create_all(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    """Drop everything. Destructive — only use when you mean it."""
    Base.metadata.drop_all(op.get_bind())
