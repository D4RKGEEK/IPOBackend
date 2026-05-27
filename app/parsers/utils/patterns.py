"""
Common regex patterns and helper functions for Indian IPO document parsing.
"""
import re
from typing import Optional

# ─── Company Name & CIN ─────────────────────────────────

RE_CIN = re.compile(
    r'[A-Z]{1}\d{2}[A-Z]{2}\d{4}[A-Z]{3}\d{6}'
)
RE_COMPANY_NAME_HEADER = re.compile(
    r'(?:^|\n)\s*([A-Z][A-Z\s&.,()/-]+(?:LIMITED|LTD|PRIVATE\s+LIMITED))\s*\n',
    re.I
)

# ─── Issue Details ──────────────────────────────────────

RE_FACE_VALUE = re.compile(
    r'face\s+value\s+of\s+(?:₹|Rs\.?|INR)?\s*(\d+(?:\.\d+)?)\s*',
    re.I
)

RE_TOTAL_ISSUE_SIZE = re.compile(
    r'(?:public\s+)?issue\s+(?:of\s+)?(\d[\d,]*)\s*(?:equity\s+)?shares.*?(?:aggregating|for\s+cash).*?(?:up\s+to\s+)?(?:₹|Rs\.?|INR)?\s*(\d[\d,.]*)\s*(?:lakhs|crores?|million|billion|mn|cr|lac)',
    re.I
)

RE_LOT_SIZE = re.compile(
    r'(?:lot\s+size|minimum\s+(?:application\s+)?bid)\s*(?::|is)?\s*(\d[\d,]*)\s*(?:equity\s+)?shares',
    re.I
)

RE_MIN_LOT_AMOUNT = re.compile(
    r'(?:minimum\s+(?:application\s+)?amount|bid\s+amount)\s*(?::|is|to\s+be)?\s*(?:₹|Rs\.?|INR)\s*(\d[\d,.]*)\s*(?:lakhs?|crores?)?',
    re.I
)

RE_PRICE_BAND = re.compile(
    r'price\s+band\s*(?::|is)?\s*(?:₹|Rs\.?|INR)?\s*(\d[\d.,]*)\s*(?:to|-|–)\s*(?:₹|Rs\.?|INR)?\s*(\d[\d.,]*)',
    re.I
)

RE_FIXED_PRICE = re.compile(
    r'(?:fixed\s+price|issue\s+price\s*(?::|is)?)\s*(?:₹|Rs\.?|INR)?\s*(\d[\d.,]*)\s*per\s*(?:equity\s+)?share',
    re.I
)

RE_ISSUE_TYPE = re.compile(
    r'(?:book\s+built|fixed\s+price)\s*(?:issue|offer)|\d+%\s*(?:book\s+built|fixed\s+price)\s+(?:issue|offer)',
    re.I
)

# ─── Fresh Issue vs OFS ─────────────────────────────────

RE_FRESH_ISSUE = re.compile(
    r'(?:fresh\s+issue|fresh\s+offer)\s*(?::|of)?\s*(?:up\s+to\s+)?(\d[\d,]*)\s*(?:equity\s+)?shares',
    re.I
)

RE_OFS = re.compile(
    r'(?:offer\s+for\s+sale|OFS)\s*(?::|of)?\s*(?:up\s+to\s+)?(\d[\d,]*)\s*(?:equity\s+)?shares',
    re.I
)

RE_TOTAL_SHARES_AFTER = re.compile(
    r'(?:post\s+issue|after\s+the\s+issue)\s*(?:paid[-\s]up\s+)?(?:equity\s+)?(?:share\s+)?capital\s*(?::|is|of)?\s*(?:₹|Rs\.?|INR)?\s*(\d[\d,.]*)\s*(?:crores?|cr|lakhs?)',
    re.I
)

# ─── Promoters ──────────────────────────────────────────

RE_PROMOTERS = re.compile(
    r'(?:promoters?\s+(?:of\s+our\s+company|of\s+the\s+company|are|is|and)\s*:?\s*)(.+?)(?:\.\s+(?:the\s+issue|details|registered)|\n\n)',
    re.I | re.S
)

RE_PROMOTER_SECTION = re.compile(
    r'(?:our\s+)?promoters?\s*(?::|are|is)?\s*([A-Z][A-Z\s.,&()/-]+(?:and|&)[A-Z\s.,&()/-]+)',
    re.I
)

RE_PROMOTER_CAPS = re.compile(
    r'PROMOTERS?\s*(?::|OF\s+OUR\s+COMPANY)?\s*:\s*([A-Z\s.,&()/-]+?)(?:\s*(?:THE\s+ISSUE|DETAILS|REGISTERED|PAGE|\d))',
    re.I
)

# ─── Intermediaries ─────────────────────────────────────

RE_BRLM = re.compile(
    r'(?:book\s+running\s+lead\s+manager(?:s)?|brlms?|lead\s+manager(?:s)?)\s*(?::|are|is|to\s+the\s+issue)?\s*:?\s*([A-Z][A-Z\s.,&()/-]+?)(?:\s*(?:registrar|banker|legal|auditor|\d\.|\n\n))',
    re.I
)

RE_REGISTRAR = re.compile(
    r'(?:registrar\s+(?:to\s+the\s+)?(?:issue|offer)?|registrars?)\s*(?::|is|are|to\s+the\s+issue)?\s*:?\s*([A-Z][A-Z\s.,&()/-]+?)(?:\s*(?:banker|legal|auditor|\d\.|\n\n))',
    re.I
)

RE_BANKERS = re.compile(
    r'(?:banker(?:s)?\s+to\s+the\s+(?:issue|company)|bankers?)\s*(?::|are|is)?\s*:?\s*([A-Z][A-Z\s.,&()/-]+?)(?:\s*(?:legal|auditor|registrar|\d\.|\n\n))',
    re.I
)

RE_REGISTRAR_WEBSITE = re.compile(
    r'registrar.*?(?:website|email|contact)\s*:?\s*(https?://[^\s]+)',
    re.I
)

RE_MARKET_MAKER = re.compile(
    r'(?:market\s+maker|mm)\s*(?::|is|are)?\s*:?\s*([A-Z][A-Z\s.,&()/-]+?)(?:\s*(?:the\s+issue|page|\n\n|\d\.))',
    re.I
)

# ─── Dates ──────────────────────────────────────────────

RE_DATE = re.compile(
    r'(\d{1,2}[-/]\d{1,2}[-/]\d{2,4}|\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4}|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+\d{4})',
    re.I
)

RE_BID_OPEN = re.compile(
    r'(?:bid(?:\s*/\s*issue)?\s*open(?:s|ing)?|opening\s+date)\s*(?::|on|is)?\s*(\d{1,2}[-/]\d{1,2}[-/]\d{2,4}|\d{1,2}\s+[A-Z][a-z]+\s+\d{4})',
    re.I
)

RE_BID_CLOSE = re.compile(
    r'(?:bid(?:\s*/\s*issue)?\s*close(?:s)?(?:ing)?|closing\s+date)\s*(?::|on|is)?\s*(\d{1,2}[-/]\d{1,2}[-/]\d{2,4}|\d{1,2}\s+[A-Z][a-z]+\s+\d{4})',
    re.I
)

RE_LISTING_DATE = re.compile(
    r'(?:listing|trading)\s+date\s*(?::|on|is)?\s*(\d{1,2}[-/]\d{1,2}[-/]\d{2,4}|\d{1,2}\s+[A-Z][a-z]+\s+\d{4})',
    re.I
)

RE_ALLOTMENT_DATE = re.compile(
    r'(?:basis\s+of\s+)?allotment\s+date\s*(?::|on|is)?\s*(\d{1,2}[-/]\d{1,2}[-/]\d{2,4}|\d{1,2}\s+[A-Z][a-z]+\s+\d{4})',
    re.I
)

# ─── Capital Structure Keywords ─────────────────────────

RE_PRE_ISSUE_CAPITAL = re.compile(
    r'(?:existing|present|current|pre[-\s]issue)\s*(?:paid[-\s]up\s+)?(?:equity\s+)?(?:share\s+)?capital\s*(?::|is|of)?\s*(?:₹|Rs\.?|INR)?\s*(\d[\d,.]*)\s*(?:crores?|cr|lakhs?|million)',
    re.I
)

# ─── Financial Table Detection ──────────────────────────

RE_FINANCIAL_TABLE = re.compile(
    r'(?:restated\s+(?:summarised\s+)?(?:statement\s+of\s+)?(?:assets\s+and\s+liabilities|profit\s+(?:and|&)\s+loss|cash\s+flow))|(?:summarised\s+)?(?:statement\s+of\s+(?:profit\s+(?:and|&)\s+loss|assets\s+and\s+liabilities))',
    re.I
)

RE_REVENUE_LINE = re.compile(
    r'(?:revenue|income|sales|turnover)\s*(?:from\s+operations)?\s*:?\s*(\d[\d,.]*)\s*(?:\.\s+(\d[\d,.]*))?\s*(?:\.\s+(\d[\d,.]*))?',
    re.I
)

RE_PROFIT_LINE = re.compile(
    r'(?:profit|pat|net\s+(?:profit|income)|profit\s+(?:after|for)\s+the\s+(?:year|period))\s*(?:for\s+the\s+(?:year|period))?\s*:?\s*(\d[\d,.]*)\s*(?:\.\s+(\d[\d,.]*))?\s*(?:\.\s+(\d[\d,.]*))?',
    re.I
)

RE_TOTAL_ASSETS = re.compile(
    r'(?:total\s+)?(?:assets|property\s+plant\s+(?:and|&)\s+equipment|non[-\s]current\s+assets)\s*(?:\(.*?\))?\s*:?\s*(\d[\d,.]*)\s*(?:\.\s+(\d[\d,.]*))?\s*(?:\.\s+(\d[\d,.]*))?',
    re.I
)

RE_NET_WORTH = re.compile(
    r'(?:net\s+worth|shareholders?\s+(?:funds|equity)|equity\s+(?:share\s+)?capital|share\s+capital)\s*(?:\(.*?\))?\s*:?\s*(\d[\d,.]*)\s*(?:\.\s+(\d[\d,.]*))?\s*(?:\.\s+(\d[\d,.]*))?',
    re.I
)

RE_EPS = re.compile(
    r'(?:eps|earning[s]?\s+per\s+share)\s*(?:\(.*?\))?\s*(?:basic|diluted)?\s*:?\s*(\d[\d.,]*)',
    re.I
)

RE_RATIOS = re.compile(
    r'(?:p/e|price\s+to\s+earnings|pe\s+ratio)\s*(?::|is)?\s*(\d[\d.,]*)',
    re.I
)

# ─── Normalization Helpers ──────────────────────────────

def normalize_number(text: str) -> float:
    """Convert Indian number formats to float. Handles crore/lakh suffixes."""
    if not text:
        return 0.0
    text = text.strip().replace(',', '').replace(' ', '').replace('₹', '').replace('Rs', '').replace('INR', '').strip()
    multiplier = 1.0
    if 'crore' in text.lower() or 'cr' in text.lower():
        multiplier = 10000000
        text = re.sub(r'(?i)(?:crore|cr)', '', text)
    elif 'lakh' in text.lower() or 'lac' in text.lower():
        multiplier = 100000
        text = re.sub(r'(?i)(?:lakh|lac)', '', text)
    elif 'million' in text.lower():
        multiplier = 1000000
        text = re.sub(r'(?i)million', '', text)
    elif 'billion' in text.lower():
        multiplier = 1000000000
        text = re.sub(r'(?i)billion', '', text)
    try:
        return float(text.strip()) * multiplier
    except ValueError:
        return 0.0


def normalize_date(text: str) -> str:
    """Convert various date formats to YYYY-MM-DD. Returns empty string on failure."""
    if not text:
        return ""
    text = text.strip().strip('.').strip()
    
    patterns = [
        (r'(\d{1,2})[-/](\d{1,2})[-/](\d{4})', lambda m: f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"),
        (r'(\d{1,2})[-/](\d{1,2})[-/](\d{2})', lambda m: f"20{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"),
    ]
    
    from datetime import datetime
    month_map = {
        'jan': '01', 'feb': '02', 'mar': '03', 'apr': '04', 'may': '05', 'jun': '06',
        'jul': '07', 'aug': '08', 'sep': '09', 'oct': '10', 'nov': '11', 'dec': '12'
    }
    
    text_lower = text.lower().strip().strip('.').strip()
    
    # Try DD-Mon-YYYY or DD Month YYYY
    m = re.match(r'(\d{1,2})\s*(?:-|\s+)?([A-Za-z]{3,9})\s*(?:-|\s+)?(\d{4})', text_lower)
    if m:
        day = int(m.group(1))
        month = month_map.get(m.group(2)[:3], '01')
        year = m.group(3)
        return f"{year}-{month}-{day:02d}"
    
    # Try Mon DD, YYYY
    m = re.match(r'([A-Za-z]{3,9})\s+(\d{1,2}),?\s*(\d{4})', text_lower)
    if m:
        month = month_map.get(m.group(1)[:3], '01')
        day = int(m.group(2))
        year = m.group(3)
        return f"{year}-{month}-{day:02d}"
    
    # Try DD/MM/YYYY or MM/DD/YYYY
    m = re.match(r'(\d{1,2})/(\d{1,2})/(\d{4})', text)
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), m.group(3)
        if a > 12:
            return f"{y}-{b:02d}-{a:02d}"  # DD/MM/YYYY
        return f"{y}-{a:02d}-{b:02d}"  # MM/DD/YYYY or assume DD/MM
    
    return text


def extract_between(text: str, start: str, end: str) -> Optional[str]:
    """Extract text between two markers."""
    import re
    pattern = re.escape(start) + r'(.*?)' + re.escape(end)
    m = re.search(pattern, text, re.I | re.S)
    return m.group(1).strip() if m else None
