"""
Backfill R2 with every section's raw markdown that's already in the DB.

Reads `document_sections.raw_md` from SQLite and uploads each non-empty row to
Cloudflare R2 using the same key convention as `app.storage.r2.section_key`.

Run:
    .venv/bin/python scripts/backfill_r2.py            # all IPOs
    .venv/bin/python scripts/backfill_r2.py --ipo-id 88
    .venv/bin/python scripts/backfill_r2.py --dry-run  # show what would upload

Idempotent — re-running just overwrites objects with the same content.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db_models import DocumentSection, get_session
from app.storage.r2 import upload_section, section_url


def dim(s):   return f"\033[2m{s}\033[0m"
def green(s): return f"\033[92m{s}\033[0m"
def red(s):   return f"\033[91m{s}\033[0m"
def yellow(s):return f"\033[93m{s}\033[0m"


def main() -> int:
    ap = argparse.ArgumentParser(description="Upload existing DB sections to R2.")
    ap.add_argument("--ipo-id", type=int, default=None, help="Limit to a single IPO.")
    ap.add_argument("--dry-run", action="store_true", help="Print what would be uploaded, don't touch R2.")
    ap.add_argument("--min-chars", type=int, default=100, help="Skip sections with raw_md shorter than this (default: 100).")
    args = ap.parse_args()

    with get_session() as s:
        q = s.query(DocumentSection).filter(DocumentSection.raw_md.isnot(None))
        if args.ipo_id is not None:
            q = q.filter(DocumentSection.ipo_master_id == args.ipo_id)
        rows = q.all()
        # Detach from session so we can use the values after the session closes
        rows = [
            {
                "ipo_master_id": r.ipo_master_id,
                "doc_type": r.doc_type,
                "section_name": r.section_name,
                "raw_md": r.raw_md or "",
                "char_count": len(r.raw_md or ""),
            }
            for r in rows
        ]

    if not rows:
        print(yellow("No sections found with non-null raw_md."))
        return 0

    total = len(rows)
    skipped_small = 0
    uploaded = 0
    failed = 0
    skipped_dryrun = 0

    print(dim(f"Found {total} sections with raw_md "
              f"({'IPO ' + str(args.ipo_id) if args.ipo_id else 'all IPOs'}, "
              f"min {args.min_chars} chars).\n"))

    t0 = time.monotonic()
    by_ipo: dict[int, int] = {}

    for i, r in enumerate(rows, 1):
        ipo_id = r["ipo_master_id"]
        doc_type = r["doc_type"]
        section_name = r["section_name"]
        raw_md = r["raw_md"]
        chars = r["char_count"]

        if chars < args.min_chars:
            skipped_small += 1
            continue

        if args.dry_run:
            url = section_url(ipo_id, doc_type, section_name)
            print(dim(f"  [dry] {i:4d}/{total}  {chars:>7,} chars  ipo={ipo_id:<4}  {doc_type:>4}  {section_name}"))
            print(dim(f"        → {url}"))
            skipped_dryrun += 1
            continue

        try:
            url = upload_section(ipo_id, doc_type, section_name, raw_md)
            uploaded += 1
            by_ipo[ipo_id] = by_ipo.get(ipo_id, 0) + 1
            if i % 10 == 0 or i == total:
                rate = i / max(0.001, time.monotonic() - t0)
                print(green(f"  ✓ {i:4d}/{total}  {chars:>7,} chars  ipo={ipo_id:<4}  {doc_type:>4}  {section_name}  ({rate:.1f}/s)"))
        except Exception as e:
            failed += 1
            print(red(f"  ✗ {i:4d}/{total}  ipo={ipo_id}  {doc_type}  {section_name}  — {e}"))

    elapsed = time.monotonic() - t0
    print()
    print(dim("─" * 60))
    print(f"  Total sections:     {total}")
    print(f"  Uploaded:           {green(str(uploaded))}")
    if skipped_small:
        print(f"  Skipped (<{args.min_chars}c):    {yellow(str(skipped_small))}")
    if skipped_dryrun:
        print(f"  Dry-run:            {yellow(str(skipped_dryrun))}")
    if failed:
        print(f"  Failed:             {red(str(failed))}")
    print(f"  IPOs covered:       {len(by_ipo)}")
    print(f"  Elapsed:            {elapsed:.1f}s")
    if uploaded:
        print(f"  Sample URL:         {section_url(rows[0]['ipo_master_id'], rows[0]['doc_type'], rows[0]['section_name'])}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
