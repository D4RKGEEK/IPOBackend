"""
Section-based DeepSeek parser — 1 call for ALL sections.

Merges all section text from all document types (DRHP+RHP+FP)
into one document. Sends 1 DeepSeek call asking for ALL fields.
Returns one unified JSON. Missing fields = empty string.
"""
import json, logging, os, time
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

API_URL = "https://api.deepseek.com/v1/chat/completions"

# ALL fields across all section types — one unified list
ALL_FIELDS = {
    # General Information
    "cin": "", "company_name": "", "registered_address": "", "telephone": "",
    "email": "", "website": "", "brlm_name": "", "registrar_name": "",
    "statutory_auditor": "", "legal_advisor": "", "cfo_name": "", "company_secretary_name": "",
    # Capital Structure
    "authorized_shares": "", "authorized_amount": "", "paid_up_shares": "",
    "paid_up_amount": "", "face_value": "", "fresh_issue_shares": "",
    "offer_for_sale_shares": "", "pre_issue_shares": "", "post_issue_shares": "",
    "qib_shares": "", "nii_shares": "", "retail_shares": "",
    "market_maker_shares": "", "anchor_shares": "",
    # Offer Details
    "total_project_cost": "", "fund_usage_breakdown": "",
    # KPIs
    "eps_basic": "", "eps_diluted": "", "pe_ratio": "", "nav_per_share": "",
    "roe_percent": "", "roce_percent": "", "price_to_book_value": "", "market_lot": "",
    "revenue_growth_percent": "", "pat_margin_percent": "", "ebitda_margin_percent": "",
    # Management
    "board_of_directors": "", "key_managerial_personnel": "", "promoter_names": "",
    "dividend_percent": "", "dividend_policy_summary": "",
    "risk_factors_summary": "",
    # Issue Dates
    "bid_open_date": "", "bid_close_date": "", "allotment_date": "", "listing_date": "",
    "retail_min_lots": "", "retail_min_shares": "", "s_hni_min_lots": "",
    "b_hni_min_lots": "", "application_amounts": "", "minimum_application": "",
    # Financials
    "financial_years": [], "total_income": "", "total_revenue": "",
    "profit_after_tax": "", "ebitda": "", "total_assets": "", "net_worth": "",
    "reserves_and_surplus": "", "total_borrowings": "", "borrowings_breakdown": "",
    "contingent_liabilities": "",
}


def _get_api_key() -> str:
    key = os.environ.get("DEEPSEEK_API_KEY")
    if key: return key
    project_env = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    try:
        with open(project_env) as f:
            for line in f:
                line = line.strip()
                if line.startswith("DEEPSEEK_API_KEY="):
                    val = line.split("=", 1)[1].strip().strip("\"'")
                    if val and val != "***": return val
    except: pass
    raise RuntimeError("DEEPSEEK_API_KEY not found in .env")


def parse_all_sections(ipo_id: int, company_name: str = "", force: bool = False) -> dict:
    """Parse ALL sections in 1 DeepSeek call. Returns unified JSON with ALL fields."""
    from app.db_service import DatabaseService
    from app.db_models import DocumentSection, get_session
    db = DatabaseService()

    # 1. Collect all sections
    all_sections = []
    doc_types_available = set()
    for dt in ("drhp", "rhp", "fp"):
        secs = db.get_sections(ipo_id, dt)
        all_sections.extend(secs)
        if secs: doc_types_available.add(dt)

    if not all_sections:
        return {"ipo_id": ipo_id, "company_name": company_name, "sections_parsed": 0,
                "message": "No sections found. Run resolve first."}

    # 2. Merge all into one text
    section_groups: dict[str, list[str]] = {}
    for sec in all_sections:
        name, dt = sec["section_name"], sec["doc_type"]
        raw_md = db.get_section_raw_md(ipo_id, dt, name)
        if not raw_md or len(raw_md) < 50: continue
        section_groups.setdefault(name, []).append(f"--- {dt.upper()} ---\n\n{raw_md}")

    if not section_groups:
        return {"ipo_id": ipo_id, "company_name": company_name, "sections_parsed": 0,
                "message": "No section content found."}

    merged_parts = []
    for sec in all_sections:
        name = sec["section_name"]
        if name in section_groups:
            texts = section_groups.pop(name)
            merged_parts.append(f"## SECTION: {name}\n\n" + "\n\n".join(texts))
    merged_text = "\n\n".join(merged_parts)

    # 3. Check incremental
    if not force:
        for sec in all_sections[:3]:
            existing = db.get_section_parsed(ipo_id, sec["doc_type"], sec["section_name"])
            if existing:
                prev_sources = existing.get("data", {}).get("_sources", [])
                if set(prev_sources) >= doc_types_available:
                    return {"ipo_id": ipo_id, "company_name": company_name, "sections_parsed": 0,
                            "sections_skipped": len(merged_parts), "message": "Already parsed. Use ?force=true."}

    # 4. Single DeepSeek call
    logger.info(f"  Merged call ({len(merged_text):,} chars, {len(merged_parts)} sections)...")
    t0 = time.monotonic()

    result_data = {"_sources": list(doc_types_available)}
    total_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
                   "cost_usd": 0.0, "cost_inr": 0.0}

    try:
        key = _get_api_key()
    except RuntimeError:
        return {"ipo_id": ipo_id, "company_name": company_name, "sections_parsed": 0,
                "message": "DEEPSEEK_API_KEY not set"}

    prompt = f"""You are extracting structured data from an Indian IPO document.

Company: {company_name}
Documents: {', '.join(sorted(doc_types_available))}

Below is the COMPLETE document text with ALL sections merged.
Extract ALL of the following fields. Return ONLY valid JSON.

RULES:
- Use empty string "" for text fields not found, 0 for numeric fields not found
- For multi-year financial data, return comma-separated strings
- For arrays/list fields, return valid JSON arrays
- NEVER use null
- If a field has no data, return "" or []

Fields to extract (ALL of these):
{json.dumps(list(ALL_FIELDS.keys()), indent=2)}

Document text:
{merged_text[:120000]}
"""

    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": "You extract structured data from Indian IPO documents. Return valid flat JSON. No null values. Empty strings for missing data."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.0,
        "max_tokens": 8000,
    }

    try:
        resp = httpx.post(API_URL, headers=headers, json=payload, timeout=180)
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]

        usage = data.get("usage", {})
        total_usage = {
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
        }
        input_cost = total_usage["prompt_tokens"] * 0.28 / 1_000_000
        output_cost = total_usage["completion_tokens"] * 1.10 / 1_000_000
        total_usage["cost_usd"] = round(input_cost + output_cost, 6)
        total_usage["cost_inr"] = round(total_usage["cost_usd"] * 96, 4)

        if "```json" in content: content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content: content = content.split("```")[1].split("```")[0].strip()

        ds_result = json.loads(content)
        for k in ALL_FIELDS:
            if k not in ds_result or ds_result[k] is None:
                ds_result[k] = ALL_FIELDS[k]
        result_data.update(ds_result)
        result_data["_sources"] = list(doc_types_available)
        result_data["_usage"] = total_usage
    except Exception as e:
        logger.error(f"DeepSeek call failed: {e}")
        result_data.update(ALL_FIELDS)

    elapsed = int((time.monotonic() - t0) * 1000)

    # 5. Save to ALL sections
    for sec in all_sections:
        with get_session() as s:
            record = s.query(DocumentSection).filter(DocumentSection.id == sec["id"]).first()
            if record:
                record.parsed = True
                record.parsed_data = result_data
                record.parsed_at = __import__('datetime').datetime.now(__import__('datetime').timezone.utc)
                s.commit()

    return {"ipo_id": ipo_id, "company_name": company_name, "sections_parsed": 1,
            "total_sections": len(merged_parts), "calls_made": 1,
            "total_usage": total_usage, "data": result_data, "parsing_time_ms": elapsed}
