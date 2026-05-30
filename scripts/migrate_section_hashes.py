"""
Phase E migration — add content-hash columns to document_sections + backfill.

Idempotent. Adds:
  - raw_md_sha256       (computed from raw_md, index)
  - parsed_md_sha256    (set whenever parsed_data is written; backfilled to NULL)

After adding the columns, backfills raw_md_sha256 for any existing section
that has raw_md but no hash.

Run:
    .venv/bin/python scripts/migrate_section_hashes.py
"""
from __future__ import annotations

import hashlib
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings


COLUMNS_TO_ADD = [
    ("raw_md_sha256",    "TEXT"),
    ("parsed_md_sha256", "TEXT"),
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
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(document_sections)")}

        for name, sql_type in COLUMNS_TO_ADD:
            if name in existing:
                print(yellow(f"  ⏭  {name:20s} (already exists)"))
                continue
            conn.execute(f"ALTER TABLE document_sections ADD COLUMN {name} {sql_type}")
            conn.commit()
            print(green(f"  ✓ added {name:20s} {sql_type}"))

        # Index on raw_md_sha256 so the gate check is fast at parse time
        conn.execute("CREATE INDEX IF NOT EXISTS idx_doc_sections_raw_hash ON document_sections(raw_md_sha256)")
        conn.commit()
        print(green("  ✓ index idx_doc_sections_raw_hash"))

        # Backfill raw_md_sha256 for rows that have raw_md but no hash yet
        cur = conn.execute(
            "SELECT id, raw_md FROM document_sections "
            "WHERE raw_md IS NOT NULL AND (raw_md_sha256 IS NULL OR raw_md_sha256 = '')"
        )
        rows = cur.fetchall()
        if rows:
            print(dim(f"\n  Backfilling {len(rows)} raw_md hashes..."))
            for r in rows:
                h = hashlib.sha256(r["raw_md"].encode("utf-8")).hexdigest()
                conn.execute(
                    "UPDATE document_sections SET raw_md_sha256 = ? WHERE id = ?",
                    (h, r["id"]),
                )
            conn.commit()
            print(green(f"  ✓ backfilled {len(rows)} hashes"))
        else:
            print(yellow("  ⏭  no rows need backfill"))

        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
