"""BSE client — fetches IPO listings + SME documents from BSE."""
import re
from typing import Any, Optional

import httpx
from bs4 import BeautifulSoup

from app.schemas import BSEData, BSESMEDocument
from app.utils import AsyncRateLimiter


class BSEClient:
    """Fetch mainboard IPO data from BSE API."""
    API_URL = "https://api.bseindia.com/BseIndiaAPI/api/GetPublicIssue_par/w"
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Referer": "https://www.bseindia.com/",
        "Origin": "https://www.bseindia.com",
        "Accept": "application/json, text/plain, */*",
    }

    def __init__(self, client: httpx.AsyncClient, delay_seconds: float = 1.0):
        self.client = client
        self.limiter = AsyncRateLimiter(delay_seconds)

    async def fetch_ipos(self) -> list[BSEData]:
        await self.limiter.wait()
        response = await self.client.get(self.API_URL, headers=self.HEADERS, timeout=30)
        response.raise_for_status()
        return self.parse_ipos(response.json())

    def parse_ipos(self, data: dict[str, Any]) -> list[BSEData]:
        records = []
        for item in data.get("Table", []):
            status = item.get("Status", "")
            flag = item.get("FLAG", 0)
            ipo_status = (
                "open" if status == "L" and flag in (1, 2, 3, 15, 17, 23)
                else "upcoming" if status == "F" and flag == 7
                else "other"
            )
            records.append(BSEData(
                scrip_cd=item.get("Scrip_cd"),
                company_name=(item.get("Scrip_Name") or "").strip(),
                long_name=(item.get("LONG_NAME") or "").strip() or None,
                start_date=(item.get("Start_Dt") or "")[:10] or None,
                end_date=(item.get("End_Dt") or "")[:10] or None,
                price_band=item.get("Price_Band"),
                face_value=item.get("Face_Val"),
                issue_type=item.get("IR_flag"),
                platform=item.get("eXCHANGE_PLATFORM"),
                status=ipo_status,
                ipo_no=item.get("IPO_NO"),
            ))
        return records


class BSESmeClient:
    """Scrape BSE SME document links from bsesme.com."""
    DRHP_URL = "https://www.bsesme.com/PublicIssues/DRHP.aspx"
    RHP_URL = "https://www.bsesme.com/PublicIssues/RHP.aspx"
    HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}

    def __init__(self, client: httpx.AsyncClient, delay_seconds: float = 0.3):
        self.client = client
        self.limiter = AsyncRateLimiter(delay_seconds)

    async def fetch_drhp_list(self) -> list[BSESMEDocument]:
        await self.limiter.wait()
        resp = await self.client.get(self.DRHP_URL, headers=self.HEADERS, timeout=30)
        resp.raise_for_status()
        return self._parse_drhp(resp.text)

    async def fetch_rhp_list(self) -> list[BSESMEDocument]:
        await self.limiter.wait()
        resp = await self.client.get(self.RHP_URL, headers=self.HEADERS, timeout=30)
        resp.raise_for_status()
        return self._parse_rhp(resp.text)

    def _parse_drhp(self, html: str) -> list[BSESMEDocument]:
        records, seen = [], set()
        for m in re.finditer(
            r"<td[^>]*>\s*(\d{2}-[A-Z][a-z]{2}-\d{4})\s*</td>\s*"
            r"<td[^>]*>\s*<a[^>]*href=\"([^\"]*)\"[^>]*>\s*([^<]+)\s*</a>",
            html, re.I,
        ):
            url = m.group(2).replace("&amp;", "&")
            company = m.group(3).strip()
            key = (company, url)
            if key in seen:
                continue
            seen.add(key)
            records.append(BSESMEDocument(
                company_name=company, date=m.group(1),
                document_type="DRHP", document_url=url, is_zip=url.lower().endswith(".zip"),
            ))
        return records

    def _parse_rhp(self, html: str) -> list[BSESMEDocument]:
        records, seen = [], set()
        for m in re.finditer(
            r"<td[^>]*class=\"TTRow_left\"[^>]*>\s*([A-Z][A-Za-z0-9 &.,()/-]+)\s*</td>\s*"
            r"<td[^>]*class=\"TTRow\"[^>]*>\s*<a[^>]*href=\"([^\"]*)\"[^>]*>",
            html, re.I,
        ):
            company = m.group(1).strip()
            url = m.group(2).replace("&amp;", "&")
            key = (company, url)
            if key in seen:
                continue
            seen.add(key)
            doc_type = "Prospectus" if "Prospectus" in url else "RHP"
            records.append(BSESMEDocument(
                company_name=company, document_type=doc_type,
                document_url=url, is_zip=url.lower().endswith(".zip"),
            ))
        return records
