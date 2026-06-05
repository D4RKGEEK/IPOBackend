"""
Per-group Firecrawl parser with content-hash gating.

Two cost-control techniques layered on top of the original per-section model:

1. SECTION GROUPING — Combine related sections into a single Firecrawl call
   with a merged JSON schema. Cuts 7 calls → 4. Sections in a group are
   delivered to Firecrawl as a *concatenated markdown payload*, hosted on R2:
   each group is uploaded as one .md file with `## SECTION` separators, and
   that single URL is sent to Firecrawl. Saves 3 credits per IPO.

2. CONTENT-HASH GATING — Each `document_sections` row stores raw_md_sha256
   (current content) and parsed_md_sha256 (content at last parse time). If
   they're equal AND the section already has parsed_data, we skip Firecrawl
   entirely — re-runs cost zero credits when content hasn't changed.

Flow per IPO:
    for each group in SECTION_GROUPS:
        ─ find which member sections are in DB with raw_md_sha256
        ─ if ALL of them have parsed_md_sha256 == raw_md_sha256 → skip
        ─ else: concat their raw_md, upload to R2 as group .md, send to
                Firecrawl with merged schema, dispatch fields back to
                each section
    build_unified() → validate → write to ipo_master.unified_data
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from app.db.engine import get_session
from app.db.operations import DatabaseService
from app.parsers.firecrawl_client import FirecrawlError, extract
from app.parsers.section_schemas import (
    COMMON_INSTRUCTION,
    SCHEMA_VERSION,
    SECTION_GROUPS,
    SECTION_SCHEMAS,
    TARGET_SECTIONS,
    merged_group_schema,
    resolve_schema,
)
from app.storage.r2 import section_url, upload_section
from app.unified import build_unified

logger = logging.getLogger(__name__)


# Cost estimates — Firecrawl /scrape with JSON extraction ~ 5 credits.
CREDITS_PER_CALL = 5
USD_PER_CREDIT = 0.00083   # ~$83 / 100k credits (Standard tier estimate)
INR_PER_USD = 96


def _estimate_cost(calls: int) -> dict[str, float]:
    credits = calls * CREDITS_PER_CALL
    usd = credits * USD_PER_CREDIT
    return {
        "calls": calls,
        "credits": credits,
        "cost_usd": round(usd, 4),
        "cost_inr": round(usd * INR_PER_USD, 4),
    }


def _section_rows_for_ipo(ipo_id: int) -> dict[str, dict]:
    """Return canonical_section_name → best section row (preferring DRHP > RHP > FP)
    for any section in TARGET_SECTIONS that exists in DB with non-empty raw_md.
    """
    db = DatabaseService()
    pref = {"fp": 0, "rhp": 1, "drhp": 2}
    best: dict[str, dict] = {}
    for dt in ("drhp", "rhp", "fp"):
        for row in db.get_sections(ipo_id, dt):
            canonical, _ = resolve_schema(row["section_name"])
            if not canonical or canonical not in TARGET_SECTIONS:
                continue
            if not row.get("char_count"):
                continue
            existing = best.get(canonical)
            if existing is None or pref[dt] < pref[existing["doc_type"]]:
                best[canonical] = {**row, "doc_type": dt, "_canonical": canonical}
    return best


def parse_all_sections_firecrawl(
    ipo_id: int,
    company_name: str = "",
    force: bool = False,
    progress: Optional[Callable[[float, str], None]] = None,
) -> dict[str, Any]:
    """Parse an IPO's sections by group, with content-hash gating.

    `force=True` bypasses the hash gate and re-parses every group.
    """
    db = DatabaseService()
    started = time.monotonic()

    available = _section_rows_for_ipo(ipo_id)
    if not available:
        return {
            "ipo_id": ipo_id, "company_name": company_name, "provider": "firecrawl",
            "groups_attempted": 0, "groups_parsed": 0, "groups_skipped": 0,
            "groups_failed": 0, "calls_made": 0, "errors": [], "data": {},
            "message": "No target sections found in DB. Run /resolve first.",
            "parsing_time_ms": int((time.monotonic() - started) * 1000),
            **_estimate_cost(0),
        }

    # Pre-fetch raw_md (only for sections we'll actually parse — avoids loading
    # huge text we'd just skip).
    def load_raw_md(row: dict) -> Optional[str]:
        return db.get_section_raw_md(ipo_id, row["doc_type"], row["section_name"])

    groups_attempted = 0
    groups_parsed = 0
    groups_skipped = 0
    groups_failed = 0
    calls_made = 0
    errors: list[dict] = []
    per_section_results: dict[str, Any] = {}

    group_names = list(SECTION_GROUPS.keys())
    total_groups = len(group_names)
    logger.info("[firecrawl] IPO %d (%s): %d groups to consider", ipo_id, company_name, total_groups)

    for gi, group_name in enumerate(group_names):
        members = SECTION_GROUPS[group_name]
        # Which member sections do we actually have content for?
        present = [s for s in members if s in available]
        if not present:
            logger.debug("[group %s] no member sections present, skipping", group_name)
            continue
        groups_attempted += 1

        # Gate: skip if every section in this group is already up-to-date
        if not force:
            all_fresh = True
            for s in present:
                row = available[s]
                raw_h = row.get("raw_md_sha256")
                pars_h = row.get("parsed_md_sha256")
                if not raw_h or raw_h != pars_h or not row.get("parsed"):
                    all_fresh = False
                    break
            if all_fresh:
                logger.info("[group %s] cache hit (all sections fresh), skipping", group_name)
                groups_skipped += 1
                for s in present:
                    cached = db.get_section_parsed(ipo_id, available[s]["doc_type"], available[s]["section_name"])
                    if cached:
                        per_section_results[s] = cached.get("data") or {}
                if progress:
                    progress((gi + 1) / total_groups, f"{group_name} cached")
                continue

        # Build the concatenated markdown payload
        chunks: list[str] = []
        for s in present:
            row = available[s]
            raw_md = load_raw_md(row)
            if not raw_md:
                continue
            chunks.append(f"## {s} ({row['doc_type'].upper()})\n\n{raw_md}")
        if not chunks:
            groups_skipped += 1
            continue
        payload_md = "\n\n---\n\n".join(chunks)

        # Upload the grouped payload to R2 as one .md file → single URL for Firecrawl
        group_section_name = f"GROUP_{group_name.upper()}"
        # Use DRHP as the doc_type bucket for grouped uploads (it's just a path convention)
        try:
            group_url = upload_section(ipo_id, "drhp", group_section_name, payload_md)
        except Exception as e:
            logger.warning("[group %s] R2 upload failed: %s — falling back to first section URL", group_name, e)
            first = available[present[0]]
            group_url = section_url(ipo_id, first["doc_type"], first["section_name"])

        # Build the merged schema for this group
        schema = merged_group_schema(group_name)
        prompt = (
            f"{COMMON_INSTRUCTION}\n\n"
            f"Group: {group_name} (covers {', '.join(present)})\n"
            f"Company: {company_name}\n"
        )

        if progress:
            progress((gi + 0.5) / total_groups, f"calling Firecrawl: {group_name}")

        t0 = time.monotonic()
        try:
            extracted = extract(group_url, schema, prompt=prompt)
            calls_made += 1
            elapsed = int((time.monotonic() - t0) * 1000)
            logger.info("[group %s] ✓ %d fields in %dms", group_name, len(extracted), elapsed)
        except FirecrawlError as e:
            groups_failed += 1
            errors.append({"group": group_name, "members": present, "error": str(e)[:300]})
            logger.warning("[group %s] ✗ %s", group_name, e)
            if progress:
                progress((gi + 1) / total_groups, f"failed {group_name}")
            continue
        except Exception as e:
            groups_failed += 1
            errors.append({"group": group_name, "members": present, "error": str(e)[:300]})
            logger.exception("[group %s] unexpected error", group_name)
            continue

        # Dispatch fields back to each member section based on its schema
        now_iso = datetime.now(timezone.utc).isoformat()
        for s in present:
            row = available[s]
            section_schema = SECTION_SCHEMAS.get(s) or {}
            section_field_names = set(section_schema.get("properties", {}).keys())

            section_payload = {
                k: v for k, v in extracted.items() if k in section_field_names
            }
            section_payload["_provider"]       = "firecrawl"
            section_payload["_group"]          = group_name
            section_payload["_source_url"]     = group_url
            section_payload["_doc_type"]       = row["doc_type"]
            section_payload["_section_name"]   = s
            section_payload["_schema_version"] = SCHEMA_VERSION
            section_payload["_extracted_at"]   = now_iso

            db.mark_section_parsed(row["id"], section_payload)
            per_section_results[s] = section_payload

        groups_parsed += 1
        if progress:
            progress((gi + 1) / total_groups, f"done {group_name}")

    # Build unified_data + run validation
    try:
        unified_persisted = build_unified(ipo_id)
    except Exception as e:
        logger.exception("build_unified failed for ipo=%s: %s", ipo_id, e)
        unified_persisted = {}

    # Pick up confidence/publish_status that build_unified just wrote
    with get_session() as s:
        from app.db.models import IPOMaster
        ipo_row = s.query(IPOMaster).filter(IPOMaster.id == ipo_id).first()
        publish_status = ipo_row.publish_status if ipo_row else None
        confidence = ipo_row.confidence_score if ipo_row else None
        unified_version = ipo_row.unified_version if ipo_row else None
        issues = ipo_row.validation_issues if ipo_row else None

    elapsed_ms = int((time.monotonic() - started) * 1000)

    # Notifications
    from app.notifications import notify
    cost = _estimate_cost(calls_made)
    if groups_failed and groups_parsed == 0:
        notify(
            f"🚨 Parse failed · <b>{company_name}</b> · 0/{groups_attempted} groups",
            level="error",
            details={"ipo_id": ipo_id, "errors": errors[:5]},
        )
    elif publish_status == "needs_review":
        notify(
            f"👀 Needs review · <b>{company_name}</b> · conf={confidence:.2f} · {len(issues or [])} issues",
            level="warn",
            details={"ipo_id": ipo_id, "publish_status": publish_status,
                     "confidence": confidence, "validation_issues": (issues or [])[:5]},
        )
    elif publish_status == "rejected":
        notify(
            f"❌ Rejected · <b>{company_name}</b> · conf={confidence:.2f}",
            level="warn",
            details={"ipo_id": ipo_id, "confidence": confidence, "issues": (issues or [])[:5]},
        )
    elif publish_status == "published":
        cache_pct = (groups_skipped / max(groups_attempted, 1)) * 100
        notify(
            f"✅ Parsed · <b>{company_name}</b> · {calls_made} calls · ₹{cost['cost_inr']:.2f} · "
            f"{groups_skipped}/{groups_attempted} cached · conf={confidence:.2f} · v{unified_version}",
            level="info",
            details={
                "ipo_id": ipo_id,
                "groups_parsed": groups_parsed,
                "groups_skipped": groups_skipped,
                "cost_inr": cost["cost_inr"],
                "cache_hit_pct": round(cache_pct, 1),
                "fields": len(unified_persisted or {}),
            },
        )

    return {
        "ipo_id": ipo_id,
        "company_name": company_name,
        "provider": "firecrawl",
        "groups_attempted": groups_attempted,
        "groups_parsed": groups_parsed,
        "groups_skipped": groups_skipped,
        "groups_failed": groups_failed,
        "calls_made": calls_made,
        "data": unified_persisted,
        "per_section": per_section_results,
        "errors": errors,
        "publish_status": publish_status,
        "confidence_score": confidence,
        "unified_version": unified_version,
        "validation_issues": issues or [],
        "parsing_time_ms": elapsed_ms,
        **cost,
    }
