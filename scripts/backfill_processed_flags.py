"""
Backfill rhp_processed, drhp_processed, and unified_updated_at for IPOs
that were resolved/parsed before these columns were added.

Run once against the Supabase DB:
    DATABASE_URL=<url> python scripts/backfill_processed_flags.py
"""
import os
import sys

try:
    import psycopg2
except ImportError:
    print("psycopg2 not found. Install it: pip install psycopg2-binary")
    sys.exit(1)

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    print("Set DATABASE_URL env var before running this script.")
    sys.exit(1)

conn = psycopg2.connect(DATABASE_URL)
conn.autocommit = False
cur = conn.cursor()

print("=== Backfill: processed flags + unified_updated_at ===\n")

# 1. Backfill rhp_processed
cur.execute("""
    UPDATE ipo_master
    SET rhp_processed = 1
    WHERE rhp_processed = 0
      AND id IN (
          SELECT DISTINCT ipo_master_id
          FROM document_sections
          WHERE doc_type IN ('rhp', 'fp')
      )
""")
print(f"rhp_processed backfilled: {cur.rowcount} rows")

# 2. Backfill drhp_processed
cur.execute("""
    UPDATE ipo_master
    SET drhp_processed = 1
    WHERE drhp_processed = 0
      AND id IN (
          SELECT DISTINCT ipo_master_id
          FROM document_sections
          WHERE doc_type = 'drhp'
      )
""")
print(f"drhp_processed backfilled: {cur.rowcount} rows")

# 3. Backfill unified_updated_at for IPOs that have unified_data but no timestamp.
#    Uses now() as the stamp — good enough to gate the pipeline's "to_parse" check.
cur.execute("""
    UPDATE ipo_master
    SET unified_updated_at = NOW()
    WHERE unified_updated_at IS NULL
      AND unified_data IS NOT NULL
""")
print(f"unified_updated_at backfilled: {cur.rowcount} rows")

conn.commit()
cur.close()
conn.close()
print("\nDone. All changes committed.")
