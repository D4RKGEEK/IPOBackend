"""
End-to-end smoke test of the Firecrawl section parser.

Picks an IPO that has sections in DB (and therefore in R2 after backfill),
runs parse_all_sections_firecrawl() synchronously (no FastAPI background task),
and prints a colored summary.

Run:
    .venv/bin/python scripts/test_firecrawl.py            # IPO 88 by default
    .venv/bin/python scripts/test_firecrawl.py 1311       # specific IPO id
    .venv/bin/python scripts/test_firecrawl.py 88 --force # re-run even if cached

Exits 0 on success, non-zero if any section fails.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db_service import DatabaseService
from app.parsers.firecrawl_parser import parse_all_sections_firecrawl
from app.parsers.section_schemas import TARGET_SECTIONS


def dim(s):   return f"\033[2m{s}\033[0m"
def green(s): return f"\033[92m{s}\033[0m"
def red(s):   return f"\033[91m{s}\033[0m"
def yellow(s):return f"\033[93m{s}\033[0m"
def bold(s):  return f"\033[1m{s}\033[0m"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("ipo_id", type=int, nargs="?", default=88)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    db = DatabaseService()
    ipo = db.get_ipo_by_id(args.ipo_id)
    if not ipo:
        print(red(f"IPO #{args.ipo_id} not found in DB."))
        return 2

    print(bold(f"\nFirecrawl test — IPO #{args.ipo_id}: {ipo.company_name}"))
    print(dim(f"Target sections: {', '.join(TARGET_SECTIONS)}\n"))

    t0 = time.monotonic()
    res = parse_all_sections_firecrawl(
        args.ipo_id, company_name=ipo.company_name, force=args.force,
        progress=lambda pct, label: print(dim(f"  [{int(pct*100):3d}%] {label}")),
    )
    elapsed = time.monotonic() - t0

    print()
    print(dim("─" * 60))
    # Group-mode (current) fields
    g_attempted = res.get("groups_attempted")
    if g_attempted is not None:
        print(f"  Groups attempted:    {g_attempted}")
        print(f"  Groups parsed:       {green(str(res.get('groups_parsed', 0)))}")
        skipped = res.get("groups_skipped", 0)
        if skipped:
            print(f"  Groups cached:       {yellow(str(skipped))}  (hash-gated)")
        failed = res.get("groups_failed", 0)
        if failed:
            print(f"  Groups failed:       {red(str(failed))}")
    print(f"  Calls made:          {res.get('calls_made', 0)}")
    print(f"  Estimated credits:   {res.get('credits', 0)}")
    print(f"  Estimated cost:      ${res.get('cost_usd', 0):.4f}  /  ₹{res.get('cost_inr', 0):.2f}")
    print(f"  Publish status:      {bold(str(res.get('publish_status')))}  conf={res.get('confidence_score', 0)}")
    print(f"  Elapsed:             {elapsed:.1f}s")
    print(dim("─" * 60))

    if res.get("errors"):
        print(bold(yellow("\nErrors:")))
        for e in res["errors"]:
            label = e.get("group") or e.get("section", "?")
            print(red(f"  {str(label):35s}: {str(e.get('error',''))[:200]}"))

    print(bold("\nUnified extracted fields (top 30):"))
    data = res.get("data", {})
    items = list(data.items())[:30]
    if not items:
        print(yellow("  (empty)"))
    else:
        max_key_len = max(len(k) for k, _ in items)
        for k, v in items:
            val_repr = json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
            val_repr = val_repr if len(val_repr) <= 100 else val_repr[:97] + "..."
            print(f"  {k.ljust(max_key_len)}  {dim('=')} {val_repr}")

    return 0 if res.get("groups_failed", 0) == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
