"""
Phase A/B migration — add unified_data + provenance + publish_status columns to ipo_master.

Idempotent: each ALTER is guarded by a check on existing columns, so this is safe
to run multiple times (e.g. after pulling fresh changes on a deploy).

Run:
    .venv/bin/python scripts/migrate_unified_columns.py
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings


COLUMNS_TO_ADD = [
    # (column_name, sqlite_type, default_sql)
    ("unified_data",        "JSON",     "NULL"),
    ("unified_provenance",  "JSON",     "NULL"),
    ("unified_version",     "INTEGER",  "0"),
    ("unified_updated_at",  "DATETIME", "NULL"),
    ("publish_status",      "TEXT",     "'pending'"),
    ("confidence_score",    "FLOAT",    "0.0"),
    ("validation_issues",   "JSON",     "NULL"),
]


def dim(s):   return f"\033[2m{s}\033[0m"
def green(s): return f"\033[92m{s}\033[0m"
def yellow(s):return f"\033[93m{s}\033[0m"


def main() -> int:
    db_path = settings.ipos_db_path
    print(dim(f"Migrating {db_path}\n"))

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(ipo_master)")}

        added = 0
        skipped = 0
        for name, sql_type, default in COLUMNS_TO_ADD:
            if name in existing:
                print(yellow(f"  ⏭  {name:25s} (already exists)"))
                skipped += 1
                continue
            try:
                conn.execute(
                    f"ALTER TABLE ipo_master ADD COLUMN {name} {sql_type} DEFAULT {default}"
                )
                conn.commit()
                print(green(f"  ✓ added {name:25s} {sql_type} DEFAULT {default}"))
                added += 1
            except sqlite3.OperationalError as e:
                print(f"  ✗ failed {name}: {e}")
                return 1

        # Add index on publish_status so /api/ipos?needs_review=true is fast
        try:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_ipo_publish_status ON ipo_master(publish_status)")
            conn.commit()
            print(green("  ✓ index idx_ipo_publish_status"))
        except sqlite3.OperationalError as e:
            print(f"  ✗ index: {e}")

        print()
        print(dim(f"Done. Added: {added}, skipped: {skipped}/{len(COLUMNS_TO_ADD)}"))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
