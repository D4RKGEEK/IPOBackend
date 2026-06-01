"""
DeepSeek-based section parser.

Reads all document sections for an IPO from the DB, sends them to DeepSeek
in a single merged call, and writes the extracted structured data back to
each section row. Also triggers build_unified() to update ipo_master.

Entry point: parse_all_sections(ipo_id, company_name, force)
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Target sections to parse (in priority order)
TARGET_SECTIONS = [
    "GENERAL_INFORMATION",
    "CAPITAL_STRUCTURE",
    "ISSUE_STRUCTURE",
    "OBJECTS_OF_THE_OFFER",
    "BASIS_FOR_OFFER_PRICE",
    "RESTATED_FINANCIAL_STATEMENTS",
    "ISSUE_PROCEDURE",
    "OUR_PROMOTERS_AND_PROMOTER_GROUP",
]

# Maximum characters of markdown to send per section to avoid token limits
MAX_SECTION_CHARS = 8_000


def _call_deepseek(prompt: str, api_key: str, model: str) -> dict[str, Any]:
    """Make a single DeepSeek chat completion call and return parsed JSON."""
    import httpx

    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a financial data extraction assistant. "
                    "Extract structured data from Indian IPO prospectus sections. "
                    "Return ONLY valid JSON. No markdown fences, no explanation."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
    }

    with httpx.Client(timeout=120) as client:
        resp = client.post(
            "https://api.deepseek.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()

    content = data["choices"][0]["message"]["content"]
    return json.loads(content)


def _build_prompt(sections: list[dict[str, Any]], company_name: str) -> str:
    """Build a single merged prompt for all sections."""
    lines = [
        f"Extract structured data from the following IPO prospectus sections for {company_name}.",
        "Return a JSON object where each key is the SECTION_NAME and the value is the extracted fields.",
        "Use empty string \"\" for missing text fields and [] for missing arrays.",
        "Preserve units (e.g. 'Rs. 1,234 crore', '12.45%') in their original form.",
        "",
    ]
    for sec in sections:
        lines.append(f"## {sec['section_name']}")
        lines.append(sec["raw_md"][:MAX_SECTION_CHARS])
        lines.append("")
    return "\n".join(lines)


def parse_all_sections(
    ipo_id: int,
    company_name: str = "",
    force: bool = False,
) -> dict[str, Any]:
    """Parse all available sections for an IPO using DeepSeek.

    Args:
        ipo_id: The IPO master ID.
        company_name: Human-readable company name (for prompt context).
        force: If True, re-parse even if sections already have parsed_data.

    Returns:
        Summary dict with counts and timing.
    """
    from app.config import require_deepseek, settings
    from app.db.operations import (
        get_sections, get_section_raw_md, mark_section_parsed,
    )
    from app.db.engine import get_session
    from app.db.models import DocumentSection
    from app.unified import build_unified

    t0 = time.time()
    api_key = require_deepseek()
    model = settings.deepseek_model

    # Collect sections that need parsing
    sections_to_parse: list[dict[str, Any]] = []
    doc_type_pref = {"drhp": 2, "rhp": 1, "fp": 0}  # lower = more preferred
    seen: dict[str, dict] = {}  # section_name → best row

    for dt in ("drhp", "rhp", "fp"):
        for row in get_sections(ipo_id, dt):
            sn = row["section_name"].upper().replace(" ", "_").replace("&", "AND")
            if sn not in TARGET_SECTIONS:
                continue
            if not row.get("char_count"):
                continue
            if not force and row.get("parsed"):
                continue
            # Keep the most preferred doc_type for each section
            if sn not in seen or doc_type_pref[dt] < doc_type_pref.get(seen[sn]["doc_type"], 99):
                raw_md = get_section_raw_md(ipo_id, dt, row["section_name"])
                if raw_md:
                    seen[sn] = {
                        "id": row["id"],
                        "section_name": sn,
                        "doc_type": dt,
                        "raw_md": raw_md,
                    }

    sections_to_parse = list(seen.values())

    if not sections_to_parse:
        logger.info("parse_all_sections: no sections to parse for ipo_id=%d", ipo_id)
        return {
            "ipo_id": ipo_id,
            "company_name": company_name,
            "status": "skipped",
            "reason": "no sections to parse (all already parsed or none available)",
            "sections_parsed": 0,
            "execution_time_ms": round((time.time() - t0) * 1000),
        }

    logger.info(
        "parse_all_sections: parsing %d sections for ipo_id=%d via DeepSeek",
        len(sections_to_parse), ipo_id,
    )

    # Call DeepSeek with all sections merged
    prompt = _build_prompt(sections_to_parse, company_name or f"IPO #{ipo_id}")
    errors: list[str] = []
    parsed_count = 0

    try:
        result = _call_deepseek(prompt, api_key, model)
    except Exception as exc:
        logger.error("parse_all_sections: DeepSeek call failed: %s", exc)
        return {
            "ipo_id": ipo_id,
            "company_name": company_name,
            "status": "error",
            "error": str(exc),
            "sections_parsed": 0,
            "execution_time_ms": round((time.time() - t0) * 1000),
        }

    # Write parsed data back to each section row
    parsed_at = datetime.now(timezone.utc).isoformat()
    for sec in sections_to_parse:
        sn = sec["section_name"]
        section_data = result.get(sn) or result.get(sn.lower()) or {}
        if not isinstance(section_data, dict):
            section_data = {}
        try:
            mark_section_parsed(
                sec["id"],
                {"data": section_data, "parsed_at": parsed_at, "schema_version": 1},
            )
            parsed_count += 1
        except Exception as exc:
            logger.warning("parse_all_sections: failed to save section %s: %s", sn, exc)
            errors.append(f"{sn}: {exc}")

    # Rebuild unified data
    try:
        build_unified(ipo_id)
    except Exception as exc:
        logger.warning("parse_all_sections: build_unified failed: %s", exc)
        errors.append(f"build_unified: {exc}")

    elapsed_ms = round((time.time() - t0) * 1000)
    logger.info(
        "parse_all_sections: done ipo_id=%d parsed=%d errors=%d elapsed=%dms",
        ipo_id, parsed_count, len(errors), elapsed_ms,
    )

    return {
        "ipo_id": ipo_id,
        "company_name": company_name,
        "status": "ok" if not errors else "partial",
        "sections_parsed": parsed_count,
        "sections_attempted": len(sections_to_parse),
        "errors": errors,
        "execution_time_ms": elapsed_ms,
    }
