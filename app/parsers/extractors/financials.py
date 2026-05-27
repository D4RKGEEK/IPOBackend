"""
Extractor: Financial data — revenue, profit, assets, net worth, EPS from financial tables.
"""
import re
from ..utils.patterns import normalize_number, normalize_date


def extract(text: str) -> dict:
    """
    Extract financial data from restated financial statements.
    Works across DRHP, RHP, and Final Prospectus formats.
    """
    result = {
        "has_data": False,
        "years": [],
        "latest_revenue": 0.0,
        "latest_pat": 0.0,
        "latest_assets": 0.0,
        "latest_net_worth": 0.0,
        "revenue_cagr_pct": 0.0,
        "pat_cagr_pct": 0.0,
    }

    # Find the financial section — look for key tables
    fin_section = _find_financial_section(text)
    if not fin_section:
        return result

    # Try to extract structured financial data
    years = _extract_year_columns(fin_section)
    if not years:
        return result

    revenues = _extract_line_items(fin_section, 
        [r'revenue\s+from\s+operations', r'revenue\s*', r'income\s+from\s+operations', 
         r'total\s+(?:revenue|income)', r'sales\s+(?:and|&)\s+(?:services|revenue)'])
    profits = _extract_line_items(fin_section,
        [r'profit\s+(?:for\s+the\s+)?(?:year|period)', r'net\s+profit', r'profit\s+after\s+tax',
         r'net\s+income', r'profit\s+&', r'profit\s+and'])
    assets = _extract_line_items(fin_section,
        [r'total\s+(?:assets|equity\s+and\s+liabilities)', r'balance\s+sheet\s+total'])
    net_worth = _extract_line_items(fin_section,
        [r'net\s+worth', r'(?:share\s+)?(?:holders\s+)?(?:equity|funds)', r'share\s+capital\s+and\s+reserves'])
    
    # Build financial years
    if years:
        result["has_data"] = True
        for i, year in enumerate(years):
            yr_data = {
                "year": year,
                "revenue": float(revenues[i]) if i < len(revenues) else 0.0,
                "profit_after_tax": float(profits[i]) if i < len(profits) else 0.0,
                "total_assets": float(assets[i]) if i < len(assets) else 0.0,
                "net_worth": float(net_worth[i]) if i < len(net_worth) else 0.0,
            }
            result["years"].append(yr_data)
        
        # Latest values
        if result["years"]:
            latest = result["years"][-1]
            result["latest_revenue"] = latest["revenue"]
            result["latest_pat"] = latest["profit_after_tax"]
            result["latest_assets"] = latest["total_assets"]
            result["latest_net_worth"] = latest["net_worth"]
    
    # Also try to find standalone key financial figures
    _extract_key_figures(text, result)
    
    return result


def _find_financial_section(text: str) -> str:
    """Locate the restated financial statements section."""
    markers = [
        r'(?:restated\s+)?(?:summarised\s+)?(?:statement\s+of\s+)?(?:profit\s+(?:and|&)\s+loss|assets\s+and\s+liabilities|cash\s+flow)',
        r'(?:financial\s+statements|financial\s+data|financials?)\s*(?:for\s+the\s+(?:year|period))',
        r'(?:restated\s+)?summarised\s+(?:financial|statement)',
        r'(?:statement\s+of\s+)?profit\s+(?:and|&)\s+loss',
        r'(?:balance\s+sheet|statement\s+of\s+assets)',
    ]
    
    for marker in markers:
        m = re.search(marker, text, re.I)
        if m:
            start = max(0, m.start() - 200)
            # Take a generous chunk after the marker
            chunk = text[start:start + 30000]
            if len(chunk) > 500:
                return chunk
    
    # Fallback: look for financial tables by number patterns
    # Find sections with large tables of numbers
    table_candidates = re.finditer(
        r'(?:Particulars|Year|Period).*?(?:\d{4}[\s-]*\d{2,4}.*?){2,}(?:\d[\d,.]+\s*.*?){10,}',
        text[:200000], re.I | re.S
    )
    for tc in table_candidates:
        chunk = tc.group(0)
        if len(chunk) > 500:
            return chunk
    
    return ""


def _extract_year_columns(text: str) -> list[str]:
    """Extract year headers from financial tables."""
    # Look for year patterns like "31 March 2024" or "FY2024" or "2023-24"
    years = []
    
    # Pattern: "31 March 2024" or "March 31, 2024"
    for m in re.finditer(
        r'(?:31\s+(?:March|Mar)\s+(\d{4})|(?:March|Mar)\s+31\s*,?\s*(\d{4})|(\d{4})\s*[-–]\s*(\d{2,4}))',
        text[:5000], re.I
    ):
        if m.group(1):
            yr = f"FY{m.group(1)}"
            if yr not in years:
                years.append(yr)
        elif m.group(3) and m.group(4):
            end_yr = m.group(4) if len(m.group(4)) == 4 else f"20{m.group(4)}"
            yr = f"FY{end_yr}"
            if yr not in years:
                years.append(yr)
    
    # If no structured years found, try to find table headings
    if not years:
        for m in re.finditer(r'(?:(\d{4})\s*[-–]\s*(\d{2,4}))', text[:5000]):
            end_yr = m.group(2) if len(m.group(2)) == 4 else f"20{m.group(2)}"
            yr = f"FY{end_yr}"
            if yr not in years:
                years.append(yr)
    
    return years[:5]  # Max 5 years


def _extract_line_items(text: str, patterns: list[str]) -> list[float]:
    """Extract numeric values following a line item label across columns."""
    values = []
    
    for pat in patterns:
        # Find the line item
        m = re.search(pat, text, re.I)
        if not m:
            continue
        
        # Take text from this line + next few to capture column values
        start = m.start()
        chunk = text[start:start + 300]
        line = chunk.split('\n')[0]
        
        # Find all numbers in this line (potential column values)
        numbers = re.findall(r'(\d[\d,]*\.?\d*)\s*(?:crore|Cr|cr|lakh|Lac|lac)?', chunk[:200])
        
        for n in numbers:
            try:
                v = float(n.replace(',', ''))
                # Filter out ridiculously large numbers (likely not financial figures)
                if 0 < v < 1e15:
                    values.append(v)
            except ValueError:
                continue
        
        if values:
            break
    
    return values


def _extract_key_figures(text: str, result: dict) -> None:
    """Fallback: extract key financial figures from anywhere in the document."""
    # Revenue
    rev_m = re.search(
        r'(?:revenue|sales|turnover|income)\s*(?:from\s+operations)?\s*(?:for\s+the\s+(?:year|period))\s*(?:ended)?\s*(?:.*?)\s*(?:₹|Rs\.?|INR)?\s*(\d[\d,.]*)\s*(?:crore|cr|Cr|lakh|lac|million|mn)',
        text[:50000], re.I
    )
    if rev_m and result["latest_revenue"] == 0.0:
        result["latest_revenue"] = normalize_number(rev_m.group(0))
    
    # PAT
    pat_m = re.search(
        r'(?:profit\s+after\s+tax|net\s+profit|PAT)\s*(?:for\s+the\s+(?:year|period))?\s*(?:.*?)\s*(?:₹|Rs\.?|INR)?\s*(\d[\d,.]*)\s*(?:crore|cr|Cr|lakh|lac|million|mn)',
        text[:50000], re.I
    )
    if pat_m and result["latest_pat"] == 0.0:
        result["latest_pat"] = normalize_number(pat_m.group(0))
    
    if not result.get("years"):
        result["has_data"] = bool(result["latest_revenue"] or result["latest_pat"])
