"""
One-time consolidation: Merge GMP placeholder data into existing IPOs + fix real duplicates.

Single transaction, minimal queries. Dry-run with --dry first.
"""
import sys, re, json
sys.path.insert(0, "/opt/IPOScraper")
from app.db.engine import get_session
from app.db.models import IPOMaster
from sqlalchemy import text

DRY_RUN = "--dry" in sys.argv

# ─── Name normalization ──────────────────────────────────

def _norm(name):
    n = re.sub(r"[^a-z0-9\s]", "", name.lower()).strip()
    n = re.sub(r"\s+", " ", n)
    for w in [" limited", " private limited", " ipo", " ltd", " pvt", " co ", " llp"]:
        n = n.replace(w, "")
    for w in ["the ", "m/s "]:
        if n.startswith(w): n = n[len(w):]
    return n.strip()

# ─── Main ────────────────────────────────────────────────

with get_session() as s:
    rows = s.execute(text("""
        SELECT id, company_name, normalized_name, status,
               rhp_processed, drhp_processed,
               gmp_latest IS NOT NULL as has_gmp
        FROM ipo_master
        ORDER BY company_name
    """)).fetchall()

    # Build name groups
    groups = {}
    for r in rows:
        key = _norm(r[1])
        groups.setdefault(key, []).append(r)

    merged_count = 0
    deleted_placeholders = 0
    deleted_dups = 0

    for key, members in groups.items():
        if len(members) < 2:
            continue

        # Sort by data richness: most data first
        def _score(m):
            # Status-based weight: published > listed > closed > open > rhp_filed > drhp_filed > discovered
            status_weight = {"published": 5, "listed": 4, "closed": 3, "open": 2,
                             "rhp_filed": 2, "drhp_filed": 1, "discovered": 0}
            sw = status_weight.get(m[3], 0)
            return sw * 100 + (m[4] + m[5]) * 10 + (1 if m[6] else 0)
        members.sort(key=_score, reverse=True)

        primary = members[0]   # the one with most data (existing IPO)
        dupes = members[1:]    # placeholders / typos

        print(f"\n  [{key}]")
        print(f"    KEEP: #{primary[0]:5} | {primary[1][:40]:40s} | {primary[3]:15s}")
        for d in dupes:
            print(f"    MERGE/DEL: #{d[0]:5} | {d[1][:40]:40s} | {d[3]:15s}")
            
            if DRY_RUN:
                continue

            try:
                # 1. Copy gmp_latest if placeholder has it and primary doesn't
                if d[6] and not primary[6]:
                    ph = s.query(IPOMaster).filter(IPOMaster.id == d[0]).first()
                    pr = s.query(IPOMaster).filter(IPOMaster.id == primary[0]).first()
                    if ph and pr and ph.gmp_latest:
                        pr.gmp_latest = ph.gmp_latest
                        print(f"      → gmp_latest copied")

                # 2. Update source_ids
                if d[0]:
                    ph2 = s.query(IPOMaster).filter(IPOMaster.id == d[0]).first()
                    if ph2 and ph2.source_ids:
                        gmp_id = ph2.source_ids.get("chittorgarh_gmp_id")
                        if gmp_id and primary[0]:
                            pr2 = s.query(IPOMaster).filter(IPOMaster.id == primary[0]).first()
                            if pr2:
                                src = pr2.source_ids or {}
                                src["chittorgarh_gmp_id"] = gmp_id
                                pr2.source_ids = src
                                print(f"      → source_ids updated (chittorgarh_gmp_id={gmp_id})")

                # 3. Delete the duplicate/placeholder
                s.execute(text("DELETE FROM ipo_master WHERE id = :did"), {"did": d[0]})
                
                if d[6]:
                    deleted_placeholders += 1
                else:
                    deleted_dups += 1
                merged_count += 1

            except Exception as e:
                print(f"      ❌ ERROR: {e}")
                s.rollback()
                raise

        if not DRY_RUN:
            s.commit()

    print(f"\n{'='*60}")
    print(f"DRY RUN: {DRY_RUN}")
    print(f"Merged: {merged_count}")
    print(f"Placeholders deleted: {deleted_placeholders}")
    print(f"Real duplicates deleted: {deleted_dups}")
    if not DRY_RUN:
        print("✅ COMMITTED")
