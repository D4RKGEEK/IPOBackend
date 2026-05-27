"""
Phase 2 parsing pipeline — orchestrates all extractors.

Flow:
  1. For each document, extract sections via ToC
  2. For each section, run regex extractors
  3. If DeepSeek key available and regex confidence < threshold, fall back to DeepSeek
  4. Merge results across documents (DRHP + RHP)
  5. Save to ipo_parsed_data table
"""
import json
import logging
import time
from datetime import datetime, timezone
from typing import Optional

from app.db_service import DatabaseService
from app.db_models import get_session, IPOMaster, IPODocument, IPOParsedData
from app.parsers.pipeline import parse_document, ParsedIPOResult
from app.parsers.extractors import company_info, issue_details, intermediaries

logger = logging.getLogger(__name__)

# Fields that regex can handle reliably
REGEX_FIELDS = {
    "cin": r"CIN\s*:\s*([A-Z0-9]+)",
    "website": r"Website[:\s]*(https?://[^\s]+)",
    "email": r"([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})",
    "telephone": r"Tel[:\s]*\+?\d[-\d\s()]{7,}",
    "face_value": r"face\s+value\s+of\s*₹?\s*(\d+(?:\.\d+)?)\s*(?:each|per)",
    "authorized_shares": r"AUTHORIZED[.\s\S]{0,300}?(\d[\d,]+)\s*Equity\s*Shares",
    "paid_up_shares": r"(?:ISSUED|PAID.?UP|SUBSCRIBED)[.\s\S]{0,300}?(\d[\d,]+)\s*Equity\s*Shares",
}


def has_deepseek_key() -> bool:
    """Check if DeepSeek API key is available."""
    import os, re
    try:
        with open(os.path.expanduser("~/.hermes/.env")) as f:
            c = f.read()
        for line in c.split("\n"):
            if "DEEPSEEK_API_KEY" in line and "=" in line:
                val = line.split("=", 1)[1].strip()
                return bool(val) and val != "***"
    except Exception:
        pass
    return bool(os.environ.get("DEEPSEEK_API_KEY"))


def run_regex_on_text(text: str) -> dict:
    """Run regex extractors on raw document text."""
    result = {}
    
    # Company info
    info = company_info.extract(text)
    if info.get("cin"):
        result["cin"] = info["cin"]
    if info.get("website"):
        result["website"] = info["website"]
    if info.get("email"):
        result["email"] = info["email"]
    if info.get("telephone"):
        result["telephone"] = info["telephone"]
    if info.get("registered_address"):
        result["registered_address"] = info["registered_address"]
    
    # Issue details
    issue = issue_details.extract(text)
    if issue.get("face_value"):
        result["face_value"] = issue["face_value"]
    if issue.get("fresh_issue_shares"):
        result["fresh_issue_shares"] = issue["fresh_issue_shares"]
    if issue.get("offer_for_sale_shares"):
        result["offer_for_sale_shares"] = issue["offer_for_sale_shares"]
    if issue.get("is_book_built"):
        result["is_book_built"] = issue["is_book_built"]
    
    # Intermediaries
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


def run_deepseek_on_section(section_text: str, section_name: str, doc_type: str) -> dict:
    """Run DeepSeek on a section (if key is available)."""
    if not has_deepseek_key():
        return {}
    
    try:
        from app.parsers.deepseek_client import extract_fields
        
        field_map = {
            "GENERAL_INFORMATION": [
                "cin", "company_name", "registered_address", "telephone",
                "email", "website", "brlm_name", "registrar_name",
                "statutory_auditor", "legal_advisor", "cfo_name",
            ],
            "CAPITAL_STRUCTURE": [
                "authorized_shares", "paid_up_shares", "face_value",
                "fresh_issue_shares", "offer_for_sale_shares",
                "qib_shares", "nii_shares", "retail_shares",
                "market_maker_shares", "anchor_shares",
            ],
            "BASIS_FOR_ISSUE_PRICE": [
                "eps_basic", "pe_ratio", "nav_per_share",
                "roe_percent", "roce_percent", "price_to_book_value",
            ],
            "RESTATED_FINANCIAL_STATEMENTS": [
                "financial_years", "total_income", "profit_after_tax",
                "ebitda", "total_assets", "net_worth", "total_borrowings",
            ],
            "OBJECTS_OF_THE_ISSUE": [
                "total_project_cost", "fund_usage_breakdown",
            ],
        }
        
        fields = field_map.get(section_name.upper(), [])
        if not fields:
            return {}
        
        return extract_fields(section_text, section_name, doc_type, fields)
    
    except Exception as e:
        logger.warning(f"DeepSeek extraction failed: {e}")
        return {}


def parse_ipo(ipo_id: int) -> dict:
    """
    Full parsing pipeline for a single IPO.
    
    1. Find all documents for this IPO
    2. For each document with extracted text, run regex extractors
    3. If DeepSeek key available, run on key sections
    4. Merge results across documents
    5. Save to DB
    6. Return merged result
    """
    db = DatabaseService()
    ipo = db.get_ipo_by_id(ipo_id)
    if not ipo:
        return {"error": "IPO not found"}
    
    start_time = time.monotonic()
    
    # Get all documents and their extracted text
    docs = []
    with get_session() as s:
        doc_records = (
            s.query(IPODocument)
            .filter(
                IPODocument.ipo_master_id == ipo_id,
                IPODocument.phase.in_(["downloaded", "parsed"]),
                IPODocument.url.isnot(None),
            )
            .all()
        )
        for doc in doc_records:
            # Try to get text from ipo_parsed_data
            parsed = (
                s.query(IPOParsedData)
                .filter(
                    IPOParsedData.ipo_master_id == ipo_id,
                    IPOParsedData.data_type == f"raw_text_{doc.doc_type}",
                )
                .first()
            )
            text = None
            if parsed:
                text = parsed.extracted_data.get("text")
            if not text:
                # Try loading from cache
                cache_path = f".doc_cache/{ipo_id}_{doc.doc_type}.json"
                try:
                    with open(cache_path) as f:
                        data = json.load(f)
                        text = data.get("text")
                except (FileNotFoundError, json.JSONDecodeError):
                    pass
            
            if text:
                docs.append({
                    "doc_type": doc.doc_type,
                    "text": text,
                    "phase": doc.phase,
                })
    
    if not docs:
        return {
            "ipo_id": ipo_id,
            "company_name": ipo.company_name,
            "status": "no_text",
            "message": "No extracted text found. Run resolve first.",
        }
    
    # Run regex on all documents
    regex_results = {}
    for doc in docs:
        regex_results[doc["doc_type"]] = run_regex_on_text(doc["text"])
    
    # Run parse_document (full regex pipeline from app/parsers)
    merged_result = ParsedIPOResult()
    for doc in docs:
        parsed = parse_document(doc["text"], document_type=doc["doc_type"], company_name=ipo.company_name)
        # Merge into final result
        from app.parsers.pipeline import _merge_parsed
        _merge_parsed(merged_result, parsed)
    
    # Try DeepSeek if available
    deepseek_results = {}
    if has_deepseek_key():
        for doc in docs:
            sections = {
                "GENERAL_INFORMATION": None,
                "CAPITAL_STRUCTURE": None,
                "BASIS_FOR_ISSUE_PRICE": None,
            }
            # Check if we have section-level extraction (from .md files)
            # For now, pass the full text — DeepSeek will find the right section
            for section_name in sections:
                if doc["text"]:
                    ds_result = run_deepseek_on_section(
                        doc["text"][:50000],
                        section_name,
                        doc["doc_type"].upper(),
                    )
                    if ds_result:
                        deepseek_results[f"{doc['doc_type']}_{section_name}"] = ds_result
    
    # Build final result
    result = merged_result.model_dump()
    result["ipo_id"] = ipo_id
    result["company_name"] = ipo.company_name
    result["status"] = "completed"
    result["parsing_time_ms"] = int((time.monotonic() - start_time) * 1000)
    result["regex_fields"] = len(regex_results)
    result["deepseek_fields"] = len(deepseek_results)
    
    # Save to DB
    with get_session() as s:
        existing = (
            s.query(IPOParsedData)
            .filter(
                IPOParsedData.ipo_master_id == ipo_id,
                IPOParsedData.data_type == "parsed_merged",
            )
            .first()
        )
        if existing:
            existing.extracted_data = result
            existing.extraction_date = datetime.now(timezone.utc)
            existing.confidence_score = result.get("confidence_score", 0.0)
        else:
            entry = IPOParsedData(
                ipo_master_id=ipo_id,
                data_type="parsed_merged",
                extracted_data=result,
                confidence_score=result.get("confidence_score", 0.0),
            )
            s.add(entry)
        s.commit()
    
    return result
