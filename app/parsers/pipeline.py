"""
Parsing pipeline: runs all extractors on text, combines into unified ParsedIPOResult.
"""
import time
import logging
from typing import Optional

from .models import ParsedIPOResult, FinancialYearData, PromoterInfo
from .extractors import company_info, issue_details, intermediaries, financials

logger = logging.getLogger(__name__)


def parse_document(
    text: str,
    document_type: str = "drhp",
    company_name: Optional[str] = None,
) -> ParsedIPOResult:
    """
    Run the full parsing pipeline on extracted document text.
    
    Args:
        text: Raw extracted text from a DRHP/RHP/FP PDF
        document_type: 'drhp', 'rhp', or 'final_prospectus'
        company_name: Optional known company name (used as hint)
    
    Returns:
        ParsedIPOResult with all extracted data (no None fields)
    """
    start = time.monotonic()
    
    if not text or len(text.strip()) < 100:
        return ParsedIPOResult(
            status="failed",
            document_type=document_type,
            parsing_time_ms=int((time.monotonic() - start) * 1000),
        )
    
    try:
        # Run all extractors
        info = company_info.extract(text)
        issue = issue_details.extract(text)
        inter = intermediaries.extract(text)
        fin = financials.extract(text)
        
        # Build result
        result = ParsedIPOResult(
            company_name=info.get("company_name", company_name or ""),
            cin=info.get("cin", ""),
            registered_address=info.get("registered_address", ""),
            website=info.get("website", ""),
            email=info.get("email", ""),
            telephone=info.get("telephone", ""),
            year_of_incorporation=info.get("year_of_incorporation", ""),
            status="completed",
            document_type=document_type,
            parsing_time_ms=int((time.monotonic() - start) * 1000),
        )
        
        # Financial data
        if fin.get("has_data"):
            yrs = []
            for y in fin.get("years", []):
                yrs.append(FinancialYearData(
                    year=y.get("year", ""),
                    revenue=y.get("revenue", 0.0),
                    profit_after_tax=y.get("profit_after_tax", 0.0),
                    total_assets=y.get("total_assets", 0.0),
                    net_worth=y.get("net_worth", 0.0),
                ))
            result.financials.has_data = True
            result.financials.years = yrs
            result.financials.latest_revenue = fin.get("latest_revenue", 0.0)
            result.financials.latest_pat = fin.get("latest_pat", 0.0)
            result.financials.latest_assets = fin.get("latest_assets", 0.0)
            result.financials.latest_net_worth = fin.get("latest_net_worth", 0.0)
        
        # Promoters
        for name in inter.get("promoters", []):
            if name:
                result.promoters.append(PromoterInfo(name=name))
        
        # Issue details
        result.issue.face_value = issue.get("face_value", 0.0)
        result.issue.fresh_issue_shares = issue.get("fresh_issue_shares", 0)
        result.issue.offer_for_sale_shares = issue.get("offer_for_sale_shares", 0)
        result.issue.total_issue_shares = issue.get("total_issue_shares", 0)
        result.issue.total_issue_amount_cr = issue.get("total_issue_amount_cr", 0.0)
        result.issue.is_fixed_price = issue.get("is_fixed_price", False)
        result.issue.is_book_built = issue.get("is_book_built", False)
        
        # Price
        result.price.has_price_band = issue.get("has_price_band", False)
        result.price.floor_price = issue.get("floor_price", 0.0)
        result.price.cap_price = issue.get("cap_price", 0.0)
        result.price.issue_price = issue.get("issue_price", 0.0)
        result.price.price_band_lower = issue.get("price_band_lower", 0.0)
        result.price.price_band_upper = issue.get("price_band_upper", 0.0)
        result.price.lot_size = issue.get("lot_size", 0)
        
        # Intermediaries
        result.intermediaries.brlms = inter.get("brlms", [])
        result.intermediaries.registrar = inter.get("registrar", "")
        result.intermediaries.bankers = inter.get("bankers", [])
        result.intermediaries.market_maker = inter.get("market_maker", "")
        result.intermediaries.legal_advisors = inter.get("legal_advisors", [])
        result.intermediaries.auditors = inter.get("auditors", [])
        
        # Confidence scoring
        result.confidence_score = _compute_confidence(result)
        
        return result
        
    except Exception as e:
        logger.error(f"Parsing failed: {e}")
        return ParsedIPOResult(
            company_name=company_name or "",
            status="failed",
            document_type=document_type,
            parsing_time_ms=int((time.monotonic() - start) * 1000),
        )


def parse_all_available(
    drhp_text: Optional[str] = None,
    rhp_text: Optional[str] = None,
    fp_text: Optional[str] = None,
    company_name: Optional[str] = None,
) -> ParsedIPOResult:
    """
    Parse all available documents for an IPO, merging the best data.
    
    Strategy: use DRHP first (most detail), then RHP (updates), then FP.
    Later documents override earlier ones where they have data.
    """
    result = ParsedIPOResult(status="pending", company_name=company_name or "")
    
    docs = [
        ("drhp", drhp_text),
        ("rhp", rhp_text),
        ("final_prospectus", fp_text),
    ]
    
    for doc_type, text in docs:
        if not text:
            continue
        parsed = parse_document(text, document_type=doc_type, company_name=company_name)
        _merge_parsed(result, parsed)
    
    result.status = "completed" if result.company_name else "partial"
    return result


def _merge_parsed(base: ParsedIPOResult, incoming: ParsedIPOResult) -> None:
    """Merge incoming parsed data into base, preferring incoming where non-empty."""
    if incoming.company_name and not base.company_name:
        base.company_name = incoming.company_name
    if incoming.cin and not base.cin:
        base.cin = incoming.cin
    if incoming.website and not base.website:
        base.website = incoming.website
    if incoming.registered_address and not base.registered_address:
        base.registered_address = incoming.registered_address
    
    # Merge promoters (prefer longer list)
    if len(incoming.promoters) > len(base.promoters):
        base.promoters = incoming.promoters
    
    # Merge financials (prefer data with more years)
    if incoming.financials.has_data and len(incoming.financials.years) > len(base.financials.years):
        base.financials = incoming.financials
    
    # Merge issue — prefer incoming config values (including booleans)
    if incoming.issue.is_book_built:
        base.issue.is_book_built = True
    if incoming.issue.is_fixed_price:
        base.issue.is_fixed_price = True
    if incoming.issue.fresh_issue_shares > base.issue.fresh_issue_shares:
        base.issue.fresh_issue_shares = incoming.issue.fresh_issue_shares
    if incoming.issue.offer_for_sale_shares > base.issue.offer_for_sale_shares:
        base.issue.offer_for_sale_shares = incoming.issue.offer_for_sale_shares
    if incoming.issue.total_issue_shares > base.issue.total_issue_shares:
        base.issue.total_issue_shares = incoming.issue.total_issue_shares
    if incoming.issue.total_issue_amount_cr > base.issue.total_issue_amount_cr:
        base.issue.total_issue_amount_cr = incoming.issue.total_issue_amount_cr
    if incoming.issue.fresh_issue_amount_cr > base.issue.fresh_issue_amount_cr:
        base.issue.fresh_issue_amount_cr = incoming.issue.fresh_issue_amount_cr
    if incoming.issue.face_value > 0:
        base.issue.face_value = incoming.issue.face_value
    if incoming.price.has_price_band or incoming.price.price_band_lower > 0 or incoming.price.price_band_upper > 0:
        base.price = incoming.price
    if incoming.intermediaries.brlms:
        base.intermediaries.brlms = incoming.intermediaries.brlms
    if incoming.intermediaries.registrar:
        base.intermediaries.registrar = incoming.intermediaries.registrar
    
    # Confidence: take the higher score
    if incoming.confidence_score > base.confidence_score:
        base.confidence_score = incoming.confidence_score


def _compute_confidence(result: ParsedIPOResult) -> float:
    """Compute a confidence score (0-1) based on how many fields were populated."""
    score = 0.0
    total = 0
    
    checks = [
        (result.company_name, 3),
        (result.cin, 2),
        (result.website, 1),
        (len(result.promoters) > 0, 2),
        (result.issue.face_value > 0, 2),
        (result.issue.total_issue_shares > 0, 2),
        (result.issue.total_issue_amount_cr > 0, 2),
        (result.price.price_band_upper > 0 or result.price.issue_price > 0, 2),
        (result.price.lot_size > 0, 1),
        (len(result.intermediaries.brlms) > 0, 2),
        (result.intermediaries.registrar != "", 1),
        (result.financials.has_data, 3),
        (result.financials.latest_revenue > 0, 2),
        (result.financials.latest_pat > 0, 2),
    ]
    
    for passed, weight in checks:
        total += weight
        if passed:
            score += weight
    
    return round(score / max(total, 1), 2)
