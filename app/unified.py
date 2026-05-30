"""
Unified-data builder.

Walks `document_sections.parsed_data` for an IPO and produces a single flat
dict that is the contract shipped to Next.js.

Conflict resolution: when the same field appears in multiple sections OR
across multiple doc_types, the most authoritative source wins.

  doc_type preference:  FP > RHP > DRHP   (final prospectus is the truth)
  within same doc_type: later parse wins

Provenance is recorded per-field so consumers can audit:
    {
        "cin":            {"doc_type": "drhp", "parsed_at": "...", "schema_version": 1},
        "bid_open_date":  {"doc_type": "rhp",  "parsed_at": "...", "schema_version": 1}
    }

After building, runs validation and stamps publish_status + confidence on
ipo_master.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from app.db_models import DocumentSection, IPOMaster, get_session
from app.validation import CrossSourceContext, validate

logger = logging.getLogger(__name__)


_DOC_PREFERENCE = {"fp": 0, "rhp": 1, "drhp": 2}   # lower = more authoritative


def _is_better(new_doc_type: str, new_parsed_at: Optional[str],
               cur_doc_type: str, cur_parsed_at: Optional[str]) -> bool:
    """Should new_* overwrite cur_*?"""
    new_rank = _DOC_PREFERENCE.get(new_doc_type, 99)
    cur_rank = _DOC_PREFERENCE.get(cur_doc_type, 99)
    if new_rank != cur_rank:
        return new_rank < cur_rank
    # Same doc type → later parse wins
    return (new_parsed_at or "") > (cur_parsed_at or "")


def _is_empty(v: Any) -> bool:
    """Treat empties from BOTH LLM providers as 'no value'.

    DeepSeek used to write 0 for missing numeric fields, "" for text, [] for arrays.
    Firecrawl writes "" / [] / sometimes "●" (DRHP placeholder) / sometimes "[●]".
    All of these are "this field wasn't found" and shouldn't overwrite a real value.
    """
    if v is None: return True
    if isinstance(v, str): return v.strip() in ("", "●", "[●]", "-", "—", "N/A", "NA")
    if isinstance(v, (list, dict)): return len(v) == 0
    # NUMERIC ZERO from the legacy DeepSeek parser also means "missing"
    if isinstance(v, (int, float)) and v == 0: return True
    return False


def build_unified(ipo_id: int) -> dict[str, Any]:
    """Build (or rebuild) ipo_master.unified_data from current parsed sections.

    Returns the unified dict that was written. If no parsed data exists,
    leaves the row unchanged and returns {}.

    Side effects on ipo_master:
        unified_data, unified_provenance, unified_version, unified_updated_at,
        confidence_score, publish_status, validation_issues
    """
    unified: dict[str, Any] = {}
    provenance: dict[str, dict] = {}

    with get_session() as s:
        ipo = s.query(IPOMaster).filter(IPOMaster.id == ipo_id).first()
        if not ipo:
            raise ValueError(f"IPO {ipo_id} not found")

        sections = s.query(DocumentSection).filter(
            DocumentSection.ipo_master_id == ipo_id,
            DocumentSection.parsed == 1,
            DocumentSection.parsed_data.isnot(None),
        ).all()

        # Snapshot ipo_dict + cross-source context BEFORE we mutate the row
        ipo_dict = ipo.to_dict()
        ipo_dict["bse_data"] = ipo.bse_data
        ipo_dict["nse_data"] = ipo.nse_data
        ctx = CrossSourceContext.from_ipo_row(ipo_dict)

        # Prefer sections written by the new pipeline. Legacy DeepSeek sections
        # stuffed all 60 fields into every section with zeros/empties — those
        # leak garbage into unified_data if we merge them with real Firecrawl data.
        firecrawl_sections = [s for s in sections if (s.parsed_data or {}).get("_provider") == "firecrawl"]
        consider = firecrawl_sections if firecrawl_sections else sections

        for sec in consider:
            data = sec.parsed_data or {}
            doc_type = sec.doc_type
            parsed_at_iso = sec.parsed_at.isoformat() if sec.parsed_at else None
            section_schema_version = data.get("_schema_version", 1)

            for field, value in data.items():
                if field.startswith("_"): continue          # internal keys
                if _is_empty(value): continue               # empty values never overwrite

                existing_prov = provenance.get(field)
                if existing_prov is None or _is_better(
                    doc_type, parsed_at_iso,
                    existing_prov["doc_type"], existing_prov.get("parsed_at"),
                ):
                    unified[field] = value
                    provenance[field] = {
                        "doc_type": doc_type,
                        "parsed_at": parsed_at_iso,
                        "section_name": sec.section_name,
                        "schema_version": section_schema_version,
                    }

        # Run validation
        result = validate(unified, ctx=ctx)

        # Bump unified_version only if anything actually changed
        previous = ipo.unified_data or {}
        changed = previous != unified

        ipo.unified_data = unified
        ipo.unified_provenance = provenance
        if changed:
            ipo.unified_version = (ipo.unified_version or 0) + 1
            ipo.unified_updated_at = datetime.now(timezone.utc)
        ipo.confidence_score = result.confidence_score
        ipo.validation_issues = result.issues or None
        ipo.publish_status = result.publish_status
        s.commit()

    logger.info(
        "[unified] ipo=%d fields=%d confidence=%.2f publish_status=%s issues=%d",
        ipo_id, len(unified), result.confidence_score, result.publish_status, len(result.issues),
    )
    return unified


def diff_unified(previous: dict, current: dict) -> dict:
    """Return {added, removed, changed} between two unified dicts.

    Useful for webhook payloads — tells Next.js exactly which fields changed.
    """
    prev_keys = {k for k in previous if not k.startswith("_")}
    cur_keys = {k for k in current if not k.startswith("_")}

    added = sorted(cur_keys - prev_keys)
    removed = sorted(prev_keys - cur_keys)
    changed = sorted(
        k for k in (prev_keys & cur_keys)
        if previous.get(k) != current.get(k)
    )
    return {"added": added, "removed": removed, "changed": changed}
