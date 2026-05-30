"""
One-shot SQLite → Postgres data copy.

After you create a Supabase project and set DATABASE_URL in `.env`:

    1. .venv/bin/alembic upgrade head        # creates tables in Postgres
    2. .venv/bin/python scripts/copy_to_postgres.py
       └─► copies every row from the local ipos.db into Postgres in
           dependency order (ipo_master first, then children).
    3. Restart the API. It will now read/write from Postgres.

Safe to re-run: uses INSERT ... ON CONFLICT DO UPDATE so existing rows are
upserted by primary key. background_tasks is excluded (it's transient).

Flags:
    --tables ipo_master,document_sections   limit which tables to copy
    --dry-run                                print counts, don't write
    --batch 500                              batch size (default 500)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, MetaData, Table, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.config import settings


# Tables to copy, in FK-safe order. Children reference parents above them.
DEFAULT_TABLES = [
    "ipo_master",
    "ipo_status_history",
    "ipo_documents",
    "document_sections",
    "ipo_parsed_data",
    "scraper_logs",
    # background_tasks intentionally skipped — transient
]


def dim(s):   return f"\033[2m{s}\033[0m"
def green(s): return f"\033[92m{s}\033[0m"
def red(s):   return f"\033[91m{s}\033[0m"
def yellow(s):return f"\033[93m{s}\033[0m"
def bold(s):  return f"\033[1m{s}\033[0m"


def main() -> int:
    ap = argparse.ArgumentParser(description="Copy data from local SQLite to Postgres.")
    ap.add_argument("--tables", default=",".join(DEFAULT_TABLES),
                    help="Comma-separated list of tables to copy (in order).")
    ap.add_argument("--batch", type=int, default=500, help="Insert batch size.")
    ap.add_argument("--dry-run", action="store_true", help="Show counts, don't write.")
    args = ap.parse_args()

    # Source = local SQLite (always, regardless of DATABASE_URL env value)
    sqlite_path = settings.ipos_db_path
    if not Path(sqlite_path).exists():
        print(red(f"Source SQLite DB not found: {sqlite_path}"))
        return 1
    source = create_engine(f"sqlite:///{sqlite_path}")

    # Destination = whatever settings.db_url resolves to.
    dest_url = settings.db_url
    if dest_url.startswith("sqlite"):
        print(red("Destination is SQLite — nothing to do. Set DATABASE_URL=postgresql://... first."))
        return 1
    if "postgresql" not in dest_url:
        print(red(f"Destination doesn't look like Postgres: {dest_url[:60]}"))
        return 1
    dest = create_engine(dest_url, pool_pre_ping=True)

    table_names = [t.strip() for t in args.tables.split(",") if t.strip()]

    print(bold(f"\nCopy plan: {sqlite_path}  →  {dest_url.split('@')[-1][:60]}\n"))
    print(dim(f"Tables: {', '.join(table_names)}"))
    print(dim(f"Batch:  {args.batch}  Dry-run: {args.dry_run}\n"))

    src_meta = MetaData()
    src_meta.reflect(source)
    dest_meta = MetaData()
    dest_meta.reflect(dest)

    total_copied = 0
    for table_name in table_names:
        if table_name not in src_meta.tables:
            print(yellow(f"  ⏭  {table_name} (not in source SQLite, skipping)"))
            continue
        if table_name not in dest_meta.tables:
            print(red(f"  ✗  {table_name} missing in destination — run `alembic upgrade head` first."))
            return 2

        src_table: Table = src_meta.tables[table_name]
        dest_table: Table = dest_meta.tables[table_name]

        # Only carry columns that exist in BOTH source and destination — silently
        # drops columns the ORM has dropped (e.g. legacy `metadata_json`).
        dest_col_names = {c.name for c in dest_table.columns}
        carry_cols = [c.name for c in src_table.columns if c.name in dest_col_names]
        dropped = [c.name for c in src_table.columns if c.name not in dest_col_names]
        if dropped:
            print(dim(f"     dropping source-only columns: {', '.join(dropped)}"))

        with source.connect() as sc:
            src_rows = sc.execute(select(*[src_table.c[c] for c in carry_cols])).mappings().all()

        if not src_rows:
            print(yellow(f"  ⏭  {table_name:25s} 0 rows"))
            continue

        if args.dry_run:
            print(dim(f"  [dry] {table_name:25s} {len(src_rows)} rows would copy"))
            total_copied += len(src_rows)
            continue

        pk_cols = [c.name for c in dest_table.primary_key.columns]
        if not pk_cols:
            print(red(f"  ✗  {table_name} has no PK — can't safely upsert"))
            return 3
        non_pk = [c for c in carry_cols if c not in pk_cols]

        with dest.begin() as conn:
            for i in range(0, len(src_rows), args.batch):
                batch = [dict(r) for r in src_rows[i : i + args.batch]]
                stmt = pg_insert(dest_table).values(batch)
                if non_pk:
                    stmt = stmt.on_conflict_do_update(
                        index_elements=pk_cols,
                        set_={c: stmt.excluded[c] for c in non_pk},
                    )
                else:
                    stmt = stmt.on_conflict_do_nothing(index_elements=pk_cols)
                conn.execute(stmt)
        print(green(f"  ✓ {table_name:25s} {len(src_rows):>5d} rows copied"))
        total_copied += len(src_rows)

    print(dim("─" * 60))
    print(bold(f"  Total: {total_copied} rows {'would be copied' if args.dry_run else 'copied'}"))

    # Advance every auto-id sequence past existing rows. Required after copying
    # data from SQLite — Postgres SEQUENCE objects don't move with the rows, so
    # the next INSERT tries to use id=1 and collides.
    if not args.dry_run and total_copied > 0:
        print()
        print(dim("Resetting Postgres sequences..."))
        from sqlalchemy import text as _sql
        with dest.begin() as conn:
            for t in table_names:
                if t not in dest_meta.tables: continue
                seq = conn.execute(_sql(f"SELECT pg_get_serial_sequence('{t}', 'id')")).scalar()
                if not seq: continue
                max_id = conn.execute(_sql(f"SELECT COALESCE(MAX(id), 0) FROM {t}")).scalar() or 0
                conn.execute(_sql(f"SELECT setval('{seq}', {max_id + 1}, false)"))
                print(green(f"  ✓ {t:25s} seq → next={max_id + 1}"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
