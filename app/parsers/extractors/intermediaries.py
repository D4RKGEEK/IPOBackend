"""
Extractor: Promoters, intermediaries, and key stakeholders.
"""
import re
from ..utils.patterns import (
    RE_PROMOTER_CAPS, RE_BRLM, RE_REGISTRAR, RE_BANKERS,
    RE_MARKET_MAKER, RE_REGISTRAR_WEBSITE,
)


def extract(text: str) -> dict:
    """Extract promoters and intermediaries."""
    result = {
        "promoters": [],
        "brlms": [],
        "registrar": "",
        "registrar_website": "",
        "bankers": [],
        "market_maker": "",
        "legal_advisors": [],
        "auditors": [],
    }
    
    # ─── Promoters ───────────────────────────────────────
    # Look for PROMOTER section in all caps
    m = RE_PROMOTER_CAPS.search(text[:8000])
    if m:
        names = m.group(1).strip()
        # Split by common delimiters
        parts = re.split(r'\s+(?:AND|&)\s+|[;,]\s*', names)
        result["promoters"] = [p.strip().title() for p in parts if len(p.strip()) > 3][:10]
    
    # Also try to find in the text body
    if not result["promoters"]:
        # Look for "Our Promoters :" pattern
        m = re.search(
            r'(?:our\s+)?promoters?\s*(?::|are|is)\s*([A-Z][A-Z\s.,&()/-]+?)(?:\.\s+[A-Z]|\n\n)',
            text[:5000], re.I
        )
        if m:
            names = m.group(1).strip()
            parts = re.split(r'\s+(?:AND|&)\s+|[;,]\s*', names)
            result["promoters"] = [p.strip().title() for p in parts if len(p.strip()) > 3][:10]
    
    # ─── BRLMs ────────────────────────────────────────────
    m = RE_BRLM.search(text[:15000])
    if m:
        names = m.group(1).strip()
        parts = re.split(r'\s*[,;]\s*|\s+AND\s+|\s*&', names)
        result["brlms"] = [p.strip().title() for p in parts if len(p.strip()) > 5][:5]
    
    # Also try simple keyword search
    if not result["brlms"]:
        m = re.search(
            r'(?:book\s+running\s+lead\s+manager|brlm)\s*(?::|to\s+the\s+issue)?\s*:?\s*([A-Z][A-Z\s.]+(?:LIMITED|LTD|PVT|PRIVATE)[A-Z\s.]*)',
            text[:10000], re.I
        )
        if m:
            names = m.group(1).strip()
            result["brlms"] = [names]
    
    # ─── Registrar ────────────────────────────────────────
    m = RE_REGISTRAR.search(text[:15000])
    if m:
        result["registrar"] = m.group(1).strip().title()
    
    if not result["registrar"]:
        m = re.search(
            r'(?:registrar|rtai)\s*(?:to\s+the\s+)?(?:issue|offer)?\s*(?::|is|name)?\s*:?\s*([A-Z][A-Z\s.]+(?:LIMITED|LTD|PVT|PRIVATE)[A-Z\s.]*)',
            text[:10000], re.I
        )
        if m:
            result["registrar"] = m.group(1).strip().title()
    
    # Registrar website
    m = RE_REGISTRAR_WEBSITE.search(text[:20000])
    if m:
        result["registrar_website"] = m.group(1).strip()
    
    # ─── Bankers ──────────────────────────────────────────
    m = RE_BANKERS.search(text[:15000])
    if m:
        names = m.group(1).strip()
        parts = re.split(r'\s*[,;]\s*|\s+AND\s+|\s*&', names)
        result["bankers"] = [p.strip().title() for p in parts if len(p.strip()) > 5][:5]
    
    # ─── Market Maker ─────────────────────────────────────
    m = RE_MARKET_MAKER.search(text[:10000])
    if m:
        result["market_maker"] = m.group(1).strip().title()
    
    # ─── Auditors ─────────────────────────────────────────
    m = re.search(
        r'(?:statutory\s+)?auditors?\s*(?:of\s+the\s+company)?\s*(?::|is|are|,)?\s*:?\s*([A-Z][A-Z\s.]+(?:&\s*[A-Z][A-Z\s.]+)*(?:LIMITED|LTD|PVT|PRIVATE|CHARTERED|ASSOCIATES|LLP)?)',
        text[:10000], re.I
    )
    if m:
        names = m.group(1).strip()
        result["auditors"] = [names.title()]
    
    # ─── Legal Advisors ───────────────────────────────────
    m = re.search(
        r'(?:legal\s+(?:advisor|adviser|counsel))\s*(?::|to\s+the\s+issue)?\s*:?\s*([A-Z][A-Z\s.]+(?:&\s*[A-Z][A-Z\s.]+)*)',
        text[:10000], re.I
    )
    if m:
        names = m.group(1).strip()
        parts = re.split(r'\s*[,;]\s*|\s+AND\s+|\s*&', names)
        result["legal_advisors"] = [p.strip().title() for p in parts if len(p.strip()) > 5][:3]
    
    return result
