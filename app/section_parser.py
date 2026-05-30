"""
Section-based DeepSeek parser — 2 calls:
  Call 1: Non-financial (company info, capital, management, risk, issue terms)
  Call 2: Financial (restated financials, KPIs)

Returns merged unified JSON. Missing fields = empty string.
Saves raw prompt + raw response per call for debugging.
"""
import json, logging, os, time
from typing import Optional, Callable

import httpx

logger = logging.getLogger(__name__)

API_URL = "https://api.deepseek.com/v1/chat/completions"

ALL_FIELDS = {
    "cin": "", "company_name": "", "registered_address": "", "telephone": "",
    "email": "", "website": "", "brlm_name": "", "registrar_name": "",
    "statutory_auditor": "", "legal_advisor": "", "cfo_name": "", "company_secretary_name": "",
    "authorized_shares": "", "authorized_amount": "", "paid_up_shares": "",
    "paid_up_amount": "", "face_value": "", "fresh_issue_shares": "",
    "offer_for_sale_shares": "", "pre_issue_shares": "", "post_issue_shares": "",
    "qib_shares": "", "nii_shares": "", "retail_shares": "",
    "market_maker_shares": "", "anchor_shares": "",
    "total_project_cost": "", "fund_usage_breakdown": "",
    "eps_basic": "", "eps_diluted": "", "pe_ratio": "", "nav_per_share": "",
    "roe_percent": "", "roce_percent": "", "price_to_book_value": "", "market_lot": "",
    "revenue_growth_percent": "", "pat_margin_percent": "", "ebitda_margin_percent": "",
    "board_of_directors": "", "key_managerial_personnel": "", "promoter_names": "",
    "dividend_percent": "", "dividend_policy_summary": "", "risk_factors_summary": "",
    "bid_open_date": "", "bid_close_date": "", "allotment_date": "", "listing_date": "",
    "retail_min_lots": "", "retail_min_shares": "", "s_hni_min_lots": "",
    "b_hni_min_lots": "", "application_amounts": "", "minimum_application": "",
    "financial_years": [], "total_income": "", "total_revenue": "",
    "profit_after_tax": "", "ebitda": "", "total_assets": "", "net_worth": "",
    "reserves_and_surplus": "", "total_borrowings": "", "borrowings_breakdown": "",
    "contingent_liabilities": "",
}

FINANCIAL_FIELD_NAMES = {"financial_years","total_income","total_revenue","profit_after_tax",
    "ebitda","total_assets","net_worth","reserves_and_surplus","total_borrowings",
    "borrowings_breakdown","contingent_liabilities"}

FINANCIAL_SECTION_NAMES = {"RESTATED_FINANCIAL_STATEMENTS","OTHER_FINANCIAL_INFORMATION",
    "STATEMENT_OF_FINANCIAL_INDEBTEDNESS"}


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


def _call_ds(text: str, company_name: str, fields: dict, doc_types: str, key: str) -> tuple[Optional[dict], Optional[dict], str, str]:
    """Single DeepSeek call. Returns (result_dict, usage, prompt_preview, raw_response)."""
    group_desc = "non-financial" if "cin" in fields else "financial"
    prompt = f"""Extract structured data from an Indian IPO document.

Company: {company_name}
Documents: {doc_types}
Type: {group_desc}

Return ONLY valid JSON. Empty string "" for missing text, 0 for missing numbers. Never use null.

Fields:
{json.dumps(list(fields.keys()), indent=2)}

Text:
{text[:60000]}
"""
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    payload = {"model": "deepseek-v4-flash", "messages": [
        {"role": "system", "content": "Extract structured IPO data. Return valid JSON. No nulls."},
        {"role": "user", "content": prompt},
    ], "temperature": 0.0, "max_tokens": 8000}

    resp = httpx.post(API_URL, headers=headers, json=payload, timeout=180)
    resp.raise_for_status()
    data = resp.json()
    content = data["choices"][0]["message"]["content"]
    if "```json" in content: content = content.split("```json")[1].split("```")[0].strip()
    elif "```" in content: content = content.split("```")[1].split("```")[0].strip()

    ds_result = json.loads(content)
    for k in fields:
        if k not in ds_result or ds_result[k] is None:
            ds_result[k] = fields[k]

    usage = data.get("usage", {})
    cost = {"prompt_tokens": usage.get("prompt_tokens", 0), "completion_tokens": usage.get("completion_tokens", 0)}
    return ds_result, cost, prompt[:2000], content[:10000]


def parse_all_sections(ipo_id: int, company_name: str = "", force: bool = False) -> dict:
    """Parse ALL sections in 2 DeepSeek calls. Returns unified JSON with input/output debug."""
    from app.db_service import DatabaseService
    from app.db_models import DocumentSection, get_session
    db = DatabaseService()

    all_sections = []
    doc_types_available = set()
    for dt in ("drhp", "rhp", "fp"):
        secs = db.get_sections(ipo_id, dt)
        all_sections.extend(secs)
        if secs: doc_types_available.add(dt)

    if not all_sections:
        return {"ipo_id": ipo_id, "company_name": company_name, "sections_parsed": 0,
                "message": "No sections found. Run resolve first."}

    # Build input manifest
    sections_input = {}
    for sec in all_sections:
        name, dt = sec["section_name"], sec["doc_type"]
        raw_md = db.get_section_raw_md(ipo_id, dt, name)
        if raw_md and len(raw_md) >= 50:
            sections_input.setdefault(name, []).append(f"[{dt.upper()}] char_count={len(raw_md)}")

    # Gather text for non-financial and financial sections
    non_fin_texts = []
    fin_texts = []
    for sec in all_sections:
        name, dt = sec["section_name"], sec["doc_type"]
        raw_md = db.get_section_raw_md(ipo_id, dt, name)
        if not raw_md or len(raw_md) < 50: continue
        text = f"## {name} ({dt.upper()})\n\n{raw_md}"
        if name in FINANCIAL_SECTION_NAMES:
            fin_texts.append(text)
        else:
            non_fin_texts.append(text)

    check_skip_key = list(FINANCIAL_SECTION_NAMES)[0] if fin_texts else all_sections[0]["section_name"]
    check_dt = "drhp"

    if not force:
        existing = db.get_section_parsed(ipo_id, check_dt, check_skip_key)
        if existing:
            prev = existing.get("data", {}).get("_sources", [])
            if set(prev) >= doc_types_available:
                return {"ipo_id": ipo_id, "company_name": company_name, "sections_parsed": 0,
                        "sections_skipped": len(sections_input), "message": "Already parsed. Use ?force=true."}

    merged_result = {}
    total_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
                   "cost_usd": 0.0, "cost_inr": 0.0}
    raw_prompts = {}
    raw_responses = {}
    calls_made = 0

    try:
        key = _get_api_key()
    except RuntimeError:
        return {"ipo_id": ipo_id, "company_name": company_name, "sections_parsed": 0,
                "message": "DEEPSEEK_API_KEY not set"}

    for texts, field_dict, group in [
        (non_fin_texts, {k: v for k, v in ALL_FIELDS.items() if k not in FINANCIAL_FIELD_NAMES}, "non_financial"),
        (fin_texts, {k: v for k, v in ALL_FIELDS.items() if k in FINANCIAL_FIELD_NAMES}, "financial"),
    ]:
        if not texts: continue
        calls_made += 1
        merged = "\n\n".join(texts)
        logger.info(f"  Call {calls_made}/2: {group} ({len(merged):,} chars)...")
        t0 = time.monotonic()
        try:
            result, usage, rp, rr = _call_ds(merged, company_name, field_dict,
                                              ", ".join(sorted(doc_types_available)), key)
            merged_result.update(result)
            raw_prompts[group] = rp
            raw_responses[group] = rr
            for k in total_usage:
                if k in usage: total_usage[k] += usage[k]
            logger.info(f"    -> {len(result)} fields in {int((time.monotonic()-t0)*1000)}ms")
        except Exception as e:
            logger.error(f"DeepSeek {group} call failed: {e}")

    input_cost = total_usage["prompt_tokens"] * 0.28 / 1_000_000
    output_cost = total_usage["completion_tokens"] * 1.10 / 1_000_000
    total_usage["cost_usd"] = round(input_cost + output_cost, 6)
    total_usage["cost_inr"] = round(total_usage["cost_usd"] * 96, 4)

    result_data = merged_result
    result_data["_sources"] = list(doc_types_available)
    result_data["_usage"] = total_usage
    result_data["_raw_prompt"] = raw_prompts
    result_data["_raw_response"] = raw_responses
    result_data["_sections_input"] = sections_input

    # Save to ALL sections
    for sec in all_sections:
        with get_session() as s:
            record = s.query(DocumentSection).filter(DocumentSection.id == sec["id"]).first()
            if record:
                record.parsed = True
                record.parsed_data = result_data
                record.parsed_at = __import__('datetime').datetime.now(__import__('datetime').timezone.utc)
                s.commit()

    return {"ipo_id": ipo_id, "company_name": company_name, "sections_parsed": 1,
            "total_sections": len(sections_input), "calls_made": calls_made,
            "total_usage": total_usage, "data": result_data,
            "parsing_time_ms": int(time.monotonic() - t0) * 1000}
