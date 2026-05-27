"""
Extractor: Issue details — size, price band, lot, capital structure, OFS.
"""
import re
from ..utils.patterns import (
    RE_FACE_VALUE, RE_PRICE_BAND, RE_FIXED_PRICE, RE_LOT_SIZE,
    RE_MIN_LOT_AMOUNT, RE_ISSUE_TYPE, RE_FRESH_ISSUE, RE_OFS,
    RE_BRLM, RE_REGISTRAR, RE_BANKERS, RE_MARKET_MAKER,
    normalize_number,
)


def extract(text: str) -> dict:
    """Extract issue details from DRHP/RHP text."""
    result = {
        "face_value": 0.0,
        "fresh_issue_shares": 0,
        "fresh_issue_amount_cr": 0.0,
        "offer_for_sale_shares": 0,
        "offer_for_sale_amount_cr": 0.0,
        "total_issue_shares": 0,
        "total_issue_amount_cr": 0.0,
        "pre_issue_shares": 0,
        "pre_issue_capital_cr": 0.0,
        "post_issue_shares": 0,
        "post_issue_capital_cr": 0.0,
        "is_fixed_price": False,
        "is_book_built": False,
        "floor_price": 0.0,
        "cap_price": 0.0,
        "issue_price": 0.0,
        "price_band_lower": 0.0,
        "price_band_upper": 0.0,
        "lot_size": 0,
        "min_lot_amount": 0.0,
        "has_price_band": False,
    }
    
    # Face Value
    m = RE_FACE_VALUE.search(text[:5000])
    if m:
        result["face_value"] = float(m.group(1))
    
    # Price Band
    m = RE_PRICE_BAND.search(text[:8000])
    if m:
        lower = float(m.group(1).replace(',', ''))
        upper = float(m.group(2).replace(',', ''))
        result["price_band_lower"] = lower
        result["price_band_upper"] = upper
        result["has_price_band"] = True
    
    # Fixed Price
    m = RE_FIXED_PRICE.search(text[:5000])
    if m:
        result["issue_price"] = float(m.group(1).replace(',', ''))
    
    # Issue Type
    m = RE_ISSUE_TYPE.search(text[:3000])
    if m:
        t = m.group(1).lower()
        result["is_book_built"] = 'book' in t
        result["is_fixed_price"] = 'fixed' in t
    
    # Lot Size
    m = RE_LOT_SIZE.search(text[:10000])
    if m:
        result["lot_size"] = int(m.group(1).replace(',', ''))
    
    # Fresh Issue shares
    m = RE_FRESH_ISSUE.search(text[:10000])
    if m:
        result["fresh_issue_shares"] = int(m.group(1).replace(',', ''))
    
    # OFS shares
    m = RE_OFS.search(text[:10000])
    if m:
        result["offer_for_sale_shares"] = int(m.group(1).replace(',', ''))
    
    # Total issue shares — look for "issue of X equity shares"
    total_m = re.search(
        r'(?:public\s+)?issue\s+of\s+(\d[\d,]*)\s*(?:equity\s+)?shares',
        text[:5000], re.I
    )
    if total_m:
        result["total_issue_shares"] = int(total_m.group(1).replace(',', ''))
    
    # Total issue amount in crores
    amount_m = re.search(
        r'(?:aggregating|for\s+cash|of)\s*(?:up\s+to\s+)?(?:₹|Rs\.?|INR)?\s*(\d[\d,.]*)\s*(?:crore|crores|cr|Cr)\b',
        text[:8000], re.I
    )
    if amount_m:
        result["total_issue_amount_cr"] = normalize_number(amount_m.group(1))
    
    # Post-issue capital
    post_m = re.search(
        r'(?:post\s+issue|after\s+(?:the\s+)?issue|paid[-\s]up\s+(?:after|post))\s*(?:paid[-\s]up\s+)?(?:equity\s+)?(?:share\s+)?capital\s*(?::|is|of|will\s+be)?\s*(?:₹|Rs\.?|INR)?\s*(\d[\d,.]*)\s*(?:crore|cr|Cr|lakh|lac)',
        text[:10000], re.I
    )
    if post_m:
        result["post_issue_capital_cr"] = normalize_number(post_m.group(1))
    
    # Total issue amount from various sources
    if result["total_issue_amount_cr"] == 0.0 and result["fresh_issue_shares"] > 0 and result["price_band_upper"] > 0:
        result["total_issue_amount_cr"] = round(result["fresh_issue_shares"] * result["price_band_upper"] / 10000000, 2)
    
    return result
