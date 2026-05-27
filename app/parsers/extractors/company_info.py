"""
Extractor: Company info — retrained on actual PyMuPDF text format.
"""
import re


def extract(text: str) -> dict:
    """Extract company information from DRHP/RHP text (PyMuPDF format)."""
    result = {
        "company_name": "",
        "cin": "",
        "registered_address": "",
        "website": "",
        "email": "",
        "telephone": "",
        "year_of_incorporation": "",
    }
    
    header = text[:5000]
    
    # CIN from "CORPORATE IDENTITY NUMBER: XXXX"
    m = re.search(r'CORPORATE\s+IDENTITY\s+(?:NUMBER|NO)[:\s]*([A-Z0-9]+)', header, re.I)
    if m:
        result["cin"] = m.group(1)
    
    # Company name: first significant ALL CAPS line between DRHP header and "CORPORATE IDENTITY"
    # Pattern: skip the DRHP header lines, find the company name in all caps
    lines = header.split('\n')
    found_header_end = False
    for line in lines:
        clean = line.strip()
        if not clean:
            continue
        # Skip DRHP/offer header lines
        if any(x in clean.upper() for x in ['DRHP', 'RHP', 'DRAFT RED', 'RED HERRING', 
                                              'PROSPECTUS', 'DATED', 'PLEASE READ',
                                              'BOOK BUILT', 'FIXED PRICE', 'QR CODE',
                                              'SCAN THIS', '(FORMERLY']):
            if any(x in clean.upper() for x in ['DRAFT RED', 'RED HERRING', 'PROSPECTUS',
                                                  'DATED', 'BOOK BUILT']):
                found_header_end = True
            continue
        # First substantial ALL CAPS line = company name
        if found_header_end and len(clean) > 5 and clean.isupper() and clean.isascii():
            result["company_name"] = clean
            break
    
    # Website: look for www. or http:// in header area
    web = re.search(r'(?:www\.[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})', header)
    if web:
        result["website"] = web.group(0).rstrip(';.')
    
    # Email
    email = re.search(r'([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})', header)
    if email:
        result["email"] = email.group(1).rstrip('.')
    
    # Telephone
    phone = re.search(r'(?:Tel|Phone|Telephone)[.\s:]*(?:[+\d][-\d\s()]{7,20})', header, re.I)
    if phone:
        num = re.search(r'[+\d][-\d\s()]{7,20}', phone.group(0))
        if num:
            result["telephone"] = num.group(0).strip()
    
    # Address: between "REGISTERED OFFICE" and "CORPORATE OFFICE" or "CONTACT PERSON"
    addr_match = re.search(
        r'REGISTERED\s+(?:AND\s+)?(?:CORPORATE\s+)?OFFICE\s*(.*?)(?:CONTACT\s+PERSON|CORPORATE\s+OFFICE|OUR\s+PROMOTERS|Tel[.:]|\n\n\n)',
        header, re.I | re.S
    )
    if addr_match:
        addr = addr_match.group(1).strip()
        addr = re.sub(r'\s+', ' ', addr)
        # Clean up fragmented text artifacts
        addr = re.sub(r'\s*,\s*', ', ', addr)
        addr = re.sub(r'\s*-\s*', ' - ', addr)
        if len(addr) > 15:
            result["registered_address"] = addr
    
    # Year of incorporation
    inc = re.search(
        r'(?:incorporated|constituted|registered)\s+(?:as|on|originally)?\s*(?:\d{1,2}[-/]\d{1,2}[-/])?(\d{4})',
        header[:3000], re.I
    )
    if inc:
        yr = inc.group(1)
        if yr.isdigit() and len(yr) == 4 and int(yr) > 1950 and int(yr) < 2030:
            result["year_of_incorporation"] = yr
    
    return result
