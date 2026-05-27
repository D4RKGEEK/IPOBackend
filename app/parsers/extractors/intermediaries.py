"""
Extractor: Promoters and intermediaries — retrained on actual text format.
"""
import re


def extract(text: str) -> dict:
    """Extract promoters and intermediaries from PyMuPDF text."""
    result = {
        "promoters": [],
        "brlms": [],
        "registrar": "",
        "bankers": [],
        "market_maker": "",
        "legal_advisors": [],
        "auditors": [],
    }
    
    # ─── Promoters ───────────────────────────────────────
    # Format: "OUR PROMOTERS: NAME1 AND NAME2 AND NAME3"
    prom = re.search(
        r'(?:OUR\s+)?PROMOTERS?\s*:\s*([A-Z][A-Z\s.,&()/-]+?)(?:\n(?!\s*[A-Z]+\s)|\s*(?:DETAILS|THE\s+ISSUE|REGISTERED|CORPORATE|RISKS|Page|\d))',
        text[:10000]
    )
    if prom:
        names = prom.group(1).strip()
        # Split by "AND" (with word boundaries) and also try commas
        parts = re.split(r'\s+(?:AND|&)\s+', names)
        result["promoters"] = [p.strip().title() for p in parts if len(p.strip()) > 3][:10]
    
    # ─── BRLMs ────────────────────────────────────────────
    # Found deeper in text - in "Other Regulatory" or "Basis for Offer Price" sections
    # Format: "Book Running Lead Managers: Name1, Name2"
    brlm = re.search(
        r'(?:Book\s+Running\s+Lead\s+(?:Manager|Managers)|BRLMs?)[\s:]*(?::)?\s*(.+?)(?:\n\s*\n|\s*(?:Registrar|Banker|Legal|Auditor|Compliance|Selling\s+Shareholder))',
        text[:100000], re.I | re.S
    )
    if brlm:
        names = brlm.group(1).strip()
        # Clean up fragmented whitespace from PyMuPDF
        names = re.sub(r'\s+', ' ', names)
        parts = re.split(r'\s*[,;]\s*|\s+AND\s+|\s*&\s*', names)
        result["brlms"] = [p.strip() for p in parts if len(p.strip()) > 5][:5]
    
    # Try simpler pattern: look for text containing "BRLM" followed by capitalized names
    if not result["brlms"]:
        brlm2 = re.search(
            r'BRLM[:\s]+([A-Z][A-Z\s.]+(?:LIMITED|LTD|PVT|PRIVATE|LLP)[A-Z\s.]*)',
            text[:50000]
        )
        if brlm2:
            result["brlms"] = [brlm2.group(1).strip().title()]
    
    # ─── Registrar ────────────────────────────────────────
    reg = re.search(
        r'(?:Registrar\s+(?:to\s+the\s+)?(?:Issue|Offer)?)[\s:]*(?::)?\s*(.+?)(?:\n\s*\n|\s*(?:Banker|BRLM|Legal|Contact|Website))',
        text[:100000], re.I | re.S
    )
    if reg:
        name = reg.group(1).strip()
        name = re.sub(r'\s+', ' ', name)
        result["registrar"] = name.title()
    
    # ─── Bankers ──────────────────────────────────────────
    bank = re.search(
        r'(?:Banker(?:s)?\s+to\s+the\s+(?:Issue|Company)|Bankers?)[\s:]*(?::)?\s*(.+?)(?:\n\s*\n|\s*(?:Registrar|BRLM|Legal|Auditor))',
        text[:100000], re.I | re.S
    )
    if bank:
        names = bank.group(1).strip()
        names = re.sub(r'\s+', ' ', names)
        parts = re.split(r'\s*[,;]\s*|\s+AND\s+|\s*&\s*', names)
        result["bankers"] = [p.strip().title() for p in parts if len(p.strip()) > 5][:5]
    
    # ─── Auditors ─────────────────────────────────────────
    aud = re.search(
        r'(?:Statutory\s+)?Auditors?[\s:]*(?::)?\s*(.+?)(?:\n\s*\n|\s*(?:Registrar|BRLM|Banker|Legal|Compliance))',
        text[:100000], re.I | re.S
    )
    if aud:
        name = aud.group(1).strip()
        name = re.sub(r'\s+', ' ', name)
        result["auditors"] = [name.title()]
    
    return result
