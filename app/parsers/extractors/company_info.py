"""
Extractor: Company info — name, CIN, address, website, incorporation year.
"""
import re
from ..utils.patterns import (
    RE_CIN, RE_COMPANY_NAME_HEADER, RE_PROMOTER_CAPS,
)


def extract(text: str) -> dict:
    """Extract company information from DRHP/RHP text."""
    result = {
        "company_name": "",
        "cin": "",
        "registered_address": "",
        "website": "",
        "email": "",
        "telephone": "",
        "year_of_incorporation": "",
    }
    
    # CIN
    m = RE_CIN.search(text)
    if m:
        result["cin"] = m.group(0)
    
    # Company name from header (first 2000 chars)
    header = text[:2000]
    m = RE_COMPANY_NAME_HEADER.search(header)
    if m:
        name = m.group(1).strip()
        # Clean up
        name = re.sub(r'\s+', ' ', name)
        result["company_name"] = name
    
    # CIN often appears near company name
    cin_match = re.search(
        r'(?:CIN|Corporate\s*Identity\s*(?:Number|No))\s*[:\-]?(?:\s*(?:is|no))?\s*([A-Z0-9]+)',
        text[:3000], re.I
    )
    if cin_match:
        result["cin"] = cin_match.group(1)
    
    # Website
    web = re.search(r'(?:website|web\s*site|site)\s*(?::|–|-)?\s*(https?://[^\s\n]+)', text[:3000], re.I)
    if not web:
        web = re.search(r'(?:website|web\s*site|site)\s*(?::|–|-)?\s*(www\.[^\s\n]+)', text[:3000], re.I)
    if web:
        result["website"] = web.group(1).strip().rstrip('.')
    
    # Email (from first 3000 chars)
    email = re.search(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', text[:3000])
    if email:
        result["email"] = email.group(0)
    
    # Telephone (from first 3000 chars)
    phone = re.search(r'(?:tel|phone|telephone)\s*(?::|no|\.)?\s*[+\d\s\-()]{7,20}', text[:3000], re.I)
    if phone:
        result["telephone"] = phone.group(0).strip()
    
    # Year of incorporation
    inc = re.search(
        r'(?:incorporated|constituted|established)\s+(?:as|on|under)?\s*(\d{1,2}[-/]\d{1,2}[-/]\d{4}|\d{4})',
        text[:3000], re.I
    )
    if inc:
        yr = inc.group(1)
        if re.match(r'^\d{4}$', yr):
            result["year_of_incorporation"] = yr
        elif '/' in yr or '-' in yr:
            parts = re.split(r'[-/]', yr)
            if len(parts) == 3:
                result["year_of_incorporation"] = parts[2] if len(parts[2]) == 4 else ('20' + parts[2])
    
    # Registered address block (between "Registered Office" and next section)
    addr_block = re.search(
        r'(?:registered\s+office|regd\.?\s*office)\s*(?::|–|-)?\s*(.*?)(?:\n\s*\n|(?:corporate|Tel|Email|Website|Contact))',
        text[:4000], re.I | re.S
    )
    if addr_block:
        addr = addr_block.group(1).strip()
        addr = re.sub(r'\s+', ' ', addr)
        if len(addr) > 10:  # Sanity check
            result["registered_address"] = addr
    
    return result
