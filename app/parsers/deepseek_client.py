"""
DeepSeek API client for IPO document parsing.
Reads DEEPSEEK_API_KEY from environment.
"""
import json
import os
import subprocess
from typing import Optional

API_URL = "https://api.deepseek.com/v1/chat/completions"


def _get_api_key() -> str:
    """Get DeepSeek API key from environment or .env files."""
    key = os.environ.get("DEEPSEEK_API_KEY")
    if key:
        return key
    
    # Check project .env
    project_env = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    for env_path in [
        project_env,
        os.path.expanduser("~/.hermes/.env"),
    ]:
        try:
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("DEEPSEEK_API_KEY="):
                        val = line.split("=", 1)[1].strip().strip("\"'")
                        if val and val != "***":
                            return val
        except Exception:
            continue
    
    raise RuntimeError("DEEPSEEK_API_KEY not found. Create .env in project root with: DEEPSEEK_API_KEY=***")


def extract_fields(
    section_text: str,
    section_name: str,
    doc_type: str,
    fields: list[str],
    company_name: str = "",
) -> dict:
    """
    Send a section of an IPO document to DeepSeek and get structured data back.
    
    Args:
        section_text: The text of the section (20-50 pages)
        section_name: e.g. "GENERAL INFORMATION", "CAPITAL STRUCTURE"
        doc_type: "DRHP" or "RHP"
        fields: List of fields to extract
        company_name: Company name for context
    
    Returns: dict with requested fields
    """
    key = _get_api_key()
    
    prompt = f"""You are extracting data from an Indian IPO {doc_type} document.

SECTION: {section_name}
COMPANY: {company_name}

Extract the following fields from this section text.
Return ONLY valid JSON. If a field is not found, use an empty string "" or 0.
DO NOT use null or None — always provide a default value.

Fields to extract:
{json.dumps(fields, indent=2)}

Section text:
{section_text[:80000]}
"""
    
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    
    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": "You extract structured data from Indian IPO documents. Return only valid JSON with no null values."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.0,
        "max_tokens": 4000,
    }
    
    import httpx
    resp = httpx.post(API_URL, headers=headers, json=payload, timeout=120)
    resp.raise_for_status()
    
    data = resp.json()
    content = data["choices"][0]["message"]["content"]
    
    # Handle markdown code blocks
    if "```json" in content:
        content = content.split("```json")[1].split("```")[0].strip()
    elif "```" in content:
        content = content.split("```")[1].split("```")[0].strip()
    
    try:
        result = json.loads(content)
    except json.JSONDecodeError:
        # If JSON parsing fails, return raw text wrapped in a dict
        return {"_raw_response": content, "_parse_error": True}
    
    # Ensure no nulls
    for key, val in result.items():
        if val is None:
            result[key] = "" if isinstance(val, str) else 0
    
    return result


def extract_general_information(text: str, doc_type: str = "DRHP", company: str = "") -> dict:
    """Extract company details from GENERAL INFORMATION section."""
    return extract_fields(text, "GENERAL INFORMATION", doc_type, [
        "cin", "company_name", "registered_address", "telephone",
        "email", "website", "brlm_name", "registrar_name",
        "statutory_auditor", "legal_advisor", "cfo_name",
        "company_secretary_name",
    ], company)


def extract_capital_structure(text: str, doc_type: str = "DRHP", company: str = "") -> dict:
    """Extract capital details from CAPITAL STRUCTURE section."""
    return extract_fields(text, "CAPITAL STRUCTURE", doc_type, [
        "authorized_shares", "authorized_amount", "paid_up_shares",
        "paid_up_amount", "face_value", "fresh_issue_shares",
        "offer_for_sale_shares", "pre_issue_shares", "post_issue_shares",
        "qib_shares", "nii_shares", "retail_shares", "market_maker_shares",
        "anchor_shares",
    ], company)


def extract_kpis(text: str, doc_type: str = "DRHP", company: str = "") -> dict:
    """Extract KPIs from BASIS FOR OFFER PRICE section."""
    return extract_fields(text, "BASIS FOR OFFER PRICE", doc_type, [
        "eps_basic", "eps_diluted", "pe_ratio", "nav_per_share",
        "roe_percent", "roce_percent", "revenue_growth_percent",
        "pat_margin_percent", "ebitda_margin_percent",
        "price_to_book_value", "market_lot",
    ], company)


def extract_financials(text: str, doc_type: str = "DRHP", company: str = "") -> dict:
    """Extract financial statements from RESTATED FINANCIAL STATEMENTS section."""
    return extract_fields(text, "RESTATED FINANCIAL STATEMENTS", doc_type, [
        "financial_years",
        "total_income", "total_revenue",
        "profit_after_tax", "ebitda",
        "total_assets", "net_worth",
        "reserves_and_surplus", "total_borrowings",
    ], company)
