"""
Phase 2 pipeline — full section-based parsing with DeepSeek fallback.

Flow:
  1. Read raw document text (with page markers: "--- Page N ---")
  2. Detect Table of Contents → section → page ranges
  3. Split text by section
  4. For each section: run regex, then DeepSeek fallback if key set
  5. Merge results across sections and document types
  6. Save to DB
"""
import json, logging, re, time
from datetime import datetime, timezone
from typing import Optional

from app.db_service import DatabaseService
from app.db_models import get_session, IPOParsedData, IPODocument
from app.parsers.extractors import company_info, issue_details, intermediaries

logger = logging.getLogger(__name__)

# Standard section headers found in ALL DRHPs and RHPs
SECTION_HEADERS = [
    "GENERAL INFORMATION",
    "CAPITAL STRUCTURE",
    "OBJECTS OF THE OFFER",
    "OBJECTS OF THE ISSUE",
    "BASIS FOR OFFER PRICE",
    "BASIS FOR ISSUE PRICE",
    "RISK FACTORS",
    "OUR MANAGEMENT",
    "OUR PROMOTERS AND PROMOTER GROUP",
    "OUR PROMOTERS & PROMOTER GROUP",
    "OUR PROMOTER & PROMOTER GROUP",
    "DIVIDEND POLICY",
    "INDUSTRY OVERVIEW",
    "OUR BUSINESS",
    "BUSINESS OVERVIEW",
    "STATEMENT OF SPECIAL TAX BENEFITS",
    "RESTATED FINANCIAL STATEMENTS",
    "RESTATED FINANCIAL STATEMENT",
    "RESTATED FINANCIAL INFORMATION",
    "RESTATED CONSOLIDATED FINANCIAL STATEMENTS",
    "OTHER FINANCIAL INFORMATION",
    "STATEMENT OF FINANCIAL INDEBTEDNESS",
    "CAPITALISATION STATEMENT",
    "OUTSTANDING LITIGATION",
    "ISSUE PROCEDURE",
    "ISSUE STRUCTURE",
    "TERMS OF THE ISSUE",
    "TERMS OF THE OFFER",
]

# What data to extract from each section
SECTION_FIELDS = {
    "GENERAL_INFORMATION": [
        "cin", "company_name", "registered_address", "telephone",
        "email", "website", "brlm_name", "registrar_name",
        "statutory_auditor", "legal_advisor", "cfo_name", "company_secretary_name",
    ],
    "CAPITAL_STRUCTURE": [
        "authorized_shares", "paid_up_shares", "face_value",
        "fresh_issue_shares", "offer_for_sale_shares", "pre_issue_shares",
        "post_issue_shares", "qib_shares", "nii_shares", "retail_shares",
        "market_maker_shares", "anchor_shares",
    ],
    "BASIS_FOR_OFFER_PRICE": [
        "eps_basic", "eps_diluted", "pe_ratio", "nav_per_share",
        "roe_percent", "roce_percent", "price_to_book_value",
        "market_lot",
    ],
    "BASIS_FOR_ISSUE_PRICE": [
        "eps_basic", "eps_diluted", "pe_ratio", "nav_per_share",
        "roe_percent", "roce_percent", "price_to_book_value",
        "market_lot",
    ],
    "OBJECTS_OF_THE_OFFER": ["total_project_cost", "fund_usage_breakdown"],
    "OBJECTS_OF_THE_ISSUE": ["total_project_cost", "fund_usage_breakdown"],
    "RESTATED_FINANCIAL_STATEMENTS": [
        "financial_years", "total_income", "profit_after_tax",
        "ebitda", "total_assets", "net_worth", "total_borrowings",
    ],
}


def has_deepseek_key() -> bool:
    import os
    try:
        env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("DEEPSEEK_API_KEY="):
                    val = line.split("=", 1)[1].strip().strip("\"'")
                    if val and val != "***":
                        return True
    except Exception:
        pass
    return bool(os.environ.get("DEEPSEEK_API_KEY"))


def extract_sections(text: str) -> dict[str, str]:
    """
    Split document text into sections using page markers.
    Text format: "--- Page N ---\ncontent"
    Returns: {SECTION_NAME: text_content}
    """
    # Build regex pattern from all section headers
    pattern = r"--- Page \d+ ---\n.*?(?=" + "|".join(
        re.escape(h) for h in sorted(SECTION_HEADERS, key=len, reverse=True)
    ) + r")"
    
    sections = {}
    for header in SECTION_HEADERS:
        # Find the section by scanning page by page
        pages = re.split(r"--- Page \d+ ---\n", text)
        found = False
        section_pages = []
        
        for i, page_text in enumerate(pages):
            if not found and header in page_text:
                found = True
                section_pages.append(page_text)
            elif found:
                # Check if next section starts
                is_next = any(
                    h in page_text for h in SECTION_HEADERS 
                    if h != header
                )
                if is_next:
                    break
                section_pages.append(page_text)
        
        if section_pages:
            section_key = header.upper().replace(" ", "_")
            sections[section_key] = "\n".join(section_pages)
    
    return sections


def run_regex_on_text(text: str) -> dict:
    """Run all regex extractors on text."""
    result = {}
    
    info = company_info.extract(text)
    for k in ("cin", "website", "email", "telephone", "registered_address"):
        if info.get(k):
            result[k] = info[k]
    
    issue = issue_details.extract(text)
    for k in ("face_value", "fresh_issue_shares", "offer_for_sale_shares", "is_book_built", "is_fixed_price"):
        if issue.get(k):
            result[k] = issue[k]
    
    interm = intermediaries.extract(text)
    if interm.get("promoters"):
        result["promoters"] = interm["promoters"]
    if interm.get("brlms"):
        result["brlms"] = interm["brlms"]
    if interm.get("registrar"):
        result["registrar"] = interm["registrar"]
    if interm.get("bankers"):
        result["bankers"] = interm["bankers"]
    
    return result


def run_deepseek_on_section(text: str, section_name: str, fields: list[str]) -> dict:
    """Run DeepSeek on a section (if key is set)."""
    if not has_deepseek_key():
        return {}
    try:
        from app.parsers.deepseek_client import extract_fields as ds
        return ds(text, section_name, "DRHP", fields)
    except Exception as e:
        logger.warning(f"DeepSeek failed on {section_name}: {e}")
        return {}


def parse_ipo(ipo_id: int, use_deepseek: bool = True) -> dict:
    """Full section-based parsing pipeline."""
    db = DatabaseService()
    ipo = db.get_ipo_by_id(ipo_id)
    if not ipo:
        return {"error": "IPO not found"}
    
    start = time.monotonic()
    
    # 1. Collect all texts
    texts = {}
    for doc_type in ("drhp", "rhp", "final_prospectus"):
        t = db.get_document_text(ipo_id, doc_type)
        if t:
            texts[doc_type] = t
    
    if not texts:
        return {"ipo_id": ipo_id, "company_name": ipo.company_name, "status": "no_text",
                "message": "Run resolve first."}
    
    # 2. Extract sections from each document
    merged = {}
    ds_used = False
    
    for doc_type, raw_text in texts.items():
        sections = extract_sections(raw_text)
        
        for section_name, section_text in sections.items():
            fields = SECTION_FIELDS.get(section_name, [])
            
            # Try regex first
            regex_result = run_regex_on_text(section_text)
            
            # Try DeepSeek fallback
            ds_result = {}
            if fields and use_deepseek and has_deepseek_key():
                ds_result = run_deepseek_on_section(section_text, section_name, fields)
                if ds_result:
                    ds_used = True
                    # DeepSeek wins over regex for the same fields
                    for k, v in ds_result.items():
                        if v and v not in ("", 0, []):
                            regex_result[k] = v
            
            # Merge into final result (RHP wins over DRHP for same field)
            for k, v in regex_result.items():
                if v and v not in ("", 0, []):
                    merged[k] = v
    
    # 3. Build result
    result = {
        "ipo_id": ipo_id,
        "company_name": ipo.company_name,
        "status": "completed",
        "parsing_time_ms": int((time.monotonic() - start) * 1000),
        "sources": list(texts.keys()),
        "sections_parsed": len(merged),
        "deepseek_used": ds_used,
        **merged,
    }
    
    # 4. Save to DB
    with get_session() as s:
        existing = s.query(IPOParsedData).filter(
            IPOParsedData.ipo_master_id == ipo_id,
            IPOParsedData.data_type == "parsed_merged",
        ).first()
        if existing:
            existing.extracted_data = result
            existing.extraction_date = datetime.now(timezone.utc)
        else:
            s.add(IPOParsedData(
                ipo_master_id=ipo_id,
                data_type="parsed_merged",
                extracted_data=result,
            ))
        s.commit()
    
    return result
