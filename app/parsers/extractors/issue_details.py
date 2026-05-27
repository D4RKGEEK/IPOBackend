"""
Extractor: Issue details — retrained on actual PyMuPDF text format.
DRHPs in India have a table format that PyMuPDF renders as scattered text.
"""
import re


def extract(text: str) -> dict:
    """Extract issue details. DRHPs use [●] as price placeholder."""
    result = {
        "face_value": 0.0,
        "fresh_issue_shares": 0,
        "fresh_issue_amount_cr": 0.0,
        "offer_for_sale_shares": 0,
        "offer_for_sale_amount_cr": 0.0,
        "total_issue_shares": 0,
        "total_issue_amount_cr": 0.0,
        "is_fixed_price": False,
        "is_book_built": True,  # Most modern IPOs are book built
        "has_price_band": False,
        "price_band_lower": 0.0,
        "price_band_upper": 0.0,
        "issue_price": 0.0,
        "lot_size": 0,
    }

    header = text[:8000]
    
    # Issue type: "100% Book Built Offer" or "Fixed Price Issue"
    if re.search(r'\d*\s*Fixed\s+Price\s+(?:Issue|Offer)', header, re.I):
        result["is_fixed_price"] = True
        result["is_book_built"] = False
    
    # Face value: "face value of ₹X each" or "Face Value ₹X"
    fv = re.search(r'(?:face\s+value|Face\s+Value)\s*(?:of\s+)?(?:₹|Rs\.?|INR)?\s*(\d+(?:\.\d+)?)\s*(?:each|per)', header, re.I)
    if fv:
        result["face_value"] = float(fv.group(1))
    
    # Fresh Issue shares: Only match if number appears close to "FRESH ISSUE" (within 500 chars)
    # and "Not Applicable" doesn't appear in between (which means it's an OFS-only issue)
    for fresh_pat in [
        r'FRESH\s+ISSUE[\s\S]{0,80}(?:Up\s*|up\s*)?to\s*(\d[\d,]*)\s*Equity\s*Shares',
        r'Fresh\s+Issue[\s\S]{0,80}(?:Up\s*|up\s*)?to\s*(\d[\d,]*)\s*Equity\s*Shares',
    ]:
        fresh_section = re.search(fresh_pat, text[:10000], re.S)
        if fresh_section:
            # Check that "Not Applicable" isn't between FRESH ISSUE and the number
            context = text[fresh_section.start():fresh_section.start() + 200]
            if not re.search(r'Not\s+Applicable|Nil|None|N\.A\.', context, re.I):
                result["fresh_issue_shares"] = int(fresh_section.group(1).replace(',', ''))
                break
    
    # OFS shares: Only match if number appears close to "OFFER FOR SALE" (within 500 chars)
    # and NOT preceded by "Not Applicable" in the same table cell
    for ofs_pat in [
        r'OFFER\s+FOR\s+SALE[\s\S]{0,300}(?:Up\s*|up\s*)?to\s*(\d[\d,]*)\s*Equity\s*Shares',
        r'Offer\s+for\s+Sale[\s\S]{0,200}(?:Up\s*|up\s*)?to\s*(\d[\d,]*)\s*Equity\s*Shares',
    ]:
        ofs_section = re.search(ofs_pat, text[:15000], re.S)
        if ofs_section:
            result["offer_for_sale_shares"] = int(ofs_section.group(1).replace(',', ''))
            break
    
    # Total issue: "Up\nto\nX,XXX,XXX\nEquity\nShares" after "TOTAL OFFER SIZE"
    total_section = re.search(
        r'TOTAL\s+(?:OFFER\s+SIZE|ISSUE\s+SIZE).*?(?:Up\s*|up\s*)?to\s*(\d[\d,]*)\s*Equity\s*Shares',
        text[:15000], re.I | re.S
    )
    if total_section:
        result["total_issue_shares"] = int(total_section.group(1).replace(',', ''))
    
    # Fresh Issue amount (look for aggregating up to ₹ X,XXX million)
    fresh_amt = re.search(
        r'FRESH\s+ISSUE.*?aggregating\s+(?:up\s+)?to\s*(?:₹|Rs\.?|INR)?\s*\[?●?\]?\s*([\d,]+(?:\.\d+)?)\s*(?:million|crore|Cr)',
        text[:20000], re.I | re.S
    )
    if fresh_amt:
        val = float(fresh_amt.group(1).replace(',', ''))
        # Amounts are in millions in DRHPs, convert to crore
        if 'million' in fresh_amt.group(0).lower():
            val = val / 10  # 1 million = 0.1 crore
        result["fresh_issue_amount_cr"] = round(val, 2)
    
    # Lot size: appears in "Offer Structure" section deeper in the doc
    lot = re.search(
        r'(?:Lot\s+Size|Market\s+Lot)\s*(?::|is)?\s*(\d[\d,]*)',
        text[:50000], re.I
    )
    if lot:
        result["lot_size"] = int(lot.group(1).replace(',', ''))
    
    # Price band (floor/cap) — often in "Basis for Offer Price" section
    band = re.search(
        r'Price\s+Band\s*(?::|is)?\s*(?:₹|Rs\.?|INR)?\s*(\d[\d.,]*)\s*(?:to|-|–)\s*(?:₹|Rs\.?|INR)?\s*(\d[\d.,]*)',
        text[:100000], re.I
    )
    if band:
        result["price_band_lower"] = float(band.group(1).replace(',', ''))
        result["price_band_upper"] = float(band.group(2).replace(',', ''))
        result["has_price_band"] = True
    
    return result
