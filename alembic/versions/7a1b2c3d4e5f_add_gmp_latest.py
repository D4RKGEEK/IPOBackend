"""add gmp_latest column to ipo_master

Revision ID: 7a1b2c3d4e5f
Revises: 3452eee164c4
Create Date: 2026-06-06
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "7a1b2c3d4e5f"
down_revision: Union[str, None] = "3452eee164c4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("ipo_master", sa.Column("gmp_latest", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("ipo_master", "gmp_latest")
