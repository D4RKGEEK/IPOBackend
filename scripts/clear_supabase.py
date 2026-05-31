"""Clear all data from Supabase (Postgres) database.

Usage:
    python scripts/clear_supabase.py          # prompts for confirmation
    python scripts/clear_supabase.py --force   # skip confirmation
    python scripts/clear_supabase.py --dry-run # show what would be cleared

Reads DATABASE_URL from .env (same as the app).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, text as _sql
from app.config import settings


TABLES = [
    "ipo_master",
    "ipo_status_history",
    "ipo_documents",
    "document_sections",
    "ipo_parsed_data",
    "scraper_logs",
    "background_tasks",
]


def dim(s):   return f"\033[2m{s}\033[0m"
def green(s): return f"\033[92m{s}\033[0m"
def red(s):   return f"\033[91m{s}\033[0m"
def yellow(s):return f"\033[93m{s}\033[0m"
def bold(s):  return f"\033[1m{s}\033[0m"


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Clear all data from Supabase.")
    ap.add_argument("--force", action="store_true", help="Skip confirmation prompt.")
    ap.add_argument("--dry-run", action="store_true", help="Show counts without deleting.")
    args = ap.parse_args()

    db_url = settings.db_url
    if not db_url or "postgres" not in db_url:
        print(red("DATABASE_URL must point to a Postgres database. Set it in .env"))
        return 1

    print(dim(f"Database: {db_url[:55]}..."))
    print()

    engine = create_engine(db_url)

    # Count rows in each table
    counts = {}
    with engine.connect() as conn:
        for table in TABLES:
            try:
                result = conn.execute(_sql(f"SELECT COUNT(*) FROM {table}"))
                counts[table] = result.scalar() or 0
            except Exception:
                counts[table] = None  # table doesn't exist

    total = sum(c for c in counts.values() if c is not None)
    print(f"  {'Table':30s} {'Rows':>8s}")
    print(dim("  " + "-" * 40))
    for table in TABLES:
        c = counts.get(table)
        if c is None:
            print(f"  {table:30s} {yellow('N/A'):>8s}")
        elif c == 0:
            print(dim(f"  {table:30s} {str(c):>8s}"))
        else:
            print(f"  {table:30s} {green(str(c)):>8s}")
    print(dim("  " + "-" * 40))
    print(f"  {'TOTAL':30s} {bold(str(total)):>8s}")
    print()

    if args.dry_run:
        print(yellow("Dry-run — no changes made."))
        return 0

    if not args.force:
        print(red(f"⚠  This will DELETE {total} rows from {len(TABLES)} tables."))
        confirm = input("  Type 'yes' to confirm: ").strip().lower()
        if confirm != "yes":
            print(yellow("Aborted."))
            return 0

    # Truncate in reverse FK-safe order (children first, then parents)
    truncate_order = list(reversed(TABLES))
    with engine.begin() as conn:
        for table in truncate_order:
            if counts.get(table) is None or counts[table] == 0:
                print(dim(f"  ⏭  {table:30s} (empty or doesn't exist)"))
                continue
            conn.execute(_sql(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE"))
            print(green(f"  ✓  {table:30s} cleared"))

    print()
    print(green(bold(f"Done — {total} rows deleted, all sequences reset.")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
