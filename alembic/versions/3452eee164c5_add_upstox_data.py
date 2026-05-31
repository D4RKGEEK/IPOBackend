"""Add upstox_data JSON column to ipo_master.

Revision ID: 3452eee164c5
Revises: 3452eee164c4
Create Date: 2026-05-31
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "3452eee164c5"
down_revision: Union[str, Sequence[str], None] = "3452eee164c4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "postgresql":
        op.add_column("ipo_master", sa.Column("upstox_data", JSONB, nullable=True))
    else:
        # SQLite — JSON is stored as TEXT
        op.add_column("ipo_master", sa.Column("upstox_data", sa.Text, nullable=True))


def downgrade() -> None:
    op.drop_column("ipo_master", "upstox_data")
