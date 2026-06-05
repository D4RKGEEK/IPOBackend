"""SEBI client — fetches DRHP/RHP filings from SEBI website."""
import re
from typing import Any, Optional

import httpx
from bs4 import BeautifulSoup

from app.schemas import IPORecord
from app.utils import (
    AsyncRateLimiter, SEBI_BASE_URL, absolutize_url,
    extract_file_url, format_date, to_sebi_date,
)


class SEBIClient:
    AJAX_URL = f"{SEBI_BASE_URL}/sebiweb/ajax/home/getnewslistinfo.jsp"
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    def __init__(self, client: httpx.AsyncClient, delay_seconds: float = 0.3):
        self.client = client
        self.limiter = AsyncRateLimiter(delay_seconds)

    async def fetch_filings(self, page: int = 1, document_type: str = "DRHP",
                            from_date: str = "", to_date: str = "", search: str = "") -> dict[str, Any]:
        await self.limiter.wait()
        smid = 10 if document_type == "DRHP" else 11
        sm_text = "DRAFT OFFER DOCUMENTS FILED WITH SEBI" if smid == 10 else "RED HERRING DOCUMENTS FILED WITH ROC"
        data = {
            "nextValue": str(page), "next": "s" if page == 1 else "n",
            "search": search, "fromDate": to_sebi_date(from_date) if from_date else "",
            "toDate": to_sebi_date(to_date) if to_date else "", "fromYear": "", "toYear": "",
            "deptId": "", "sid": "3", "ssid": "15", "smid": str(smid),
            "ssidhidden": "15", "intmid": "-1", "sText": "FILINGS",
            "ssText": "PUBLIC ISSUES", "smText": sm_text,
            "doDirect": "-1" if page == 1 else "0",
        }
        headers = {**self.HEADERS, "Referer": (
            f"{SEBI_BASE_URL}/sebiweb/home/HomeAction.do?"
            f"doListing=yes&sid=3&ssid=15&smid={smid}"
        )}
        response = await self.client.post(self.AJAX_URL, headers=headers, data=data, timeout=30)
        response.raise_for_status()
        return self.parse_listing(response.text, document_type=document_type)

    async def fetch_detail_page(self, detail_url: str) -> dict[str, Optional[str]]:
        await self.limiter.wait()
        detail_url = absolutize_url(detail_url) or detail_url
        response = await self.client.get(detail_url, headers={"User-Agent": self.HEADERS["User-Agent"]}, timeout=30)
        response.raise_for_status()
        return self.parse_detail_page(response.text)

    async def attach_pdf_urls(self, records: list[IPORecord], max_concurrency: int = 5) -> None:
        semaphore = __import__('asyncio').Semaphore(max_concurrency)
        async def attach(record: IPORecord) -> None:
            if not record.document_urls or not record.document_urls.detail_page:
                return
            async with semaphore:
                await self.limiter.wait()
                try:
                    detail = await self.fetch_detail_page(record.document_urls.detail_page)
                except Exception:
                    return
            pdf_url = detail.get("pdf_url")
            if not pdf_url:
                return
            if record.document_type == "DRHP":
                record.document_urls.drhp_pdf = pdf_url
            elif record.document_type == "RHP":
                record.document_urls.rhp_pdf = pdf_url
        await __import__('asyncio').gather(*(attach(record) for record in records))

    def parse_listing(self, html: str, document_type: str = "DRHP") -> dict[str, Any]:
        from app.schemas import DocumentUrls
        soup = BeautifulSoup(html, "html.parser")
        total_pages = self._int_input_value(soup, "totalpage", default=1)
        records = []
        table = soup.find("table")
        if table:
            for row in table.find_all("tr"):
                cells = row.find_all("td")
                if len(cells) < 2:
                    continue
                main_link = cells[1].find("a", href=True)
                if not main_link:
                    continue
                title_attr = str(main_link.get("title", "") or "")
                link_text = main_link.get_text(" ", strip=True)
                company_name = self._extract_company_name(title_attr or link_text, document_type)
                if not company_name:
                    continue
                abridged_pdf = self._extract_abridged_pdf(title_attr, cells[1])
                detail_page_url = absolutize_url(main_link.get("href"))
                urls = DocumentUrls(detail_page=detail_page_url, abridged_prospectus_pdf=absolutize_url(abridged_pdf))
                records.append(IPORecord(
                    company_name=company_name,
                    filing_date=format_date(cells[0].get_text(" ", strip=True)),
                    source="sebi", document_type=document_type, document_urls=urls,
                ))
        return {"records": records, "total_pages": total_pages}

    def parse_detail_page(self, html: str) -> dict[str, Optional[str]]:
        soup = BeautifulSoup(html, "html.parser")
        iframe = soup.find("iframe", src=True)
        if iframe:
            pdf_url = extract_file_url(iframe["src"])
            if pdf_url:
                return {"pdf_url": absolutize_url(pdf_url)}
        for tag in soup.find_all(["a", "iframe"], href=True):
            href = tag.get("href")
            if href and "attachdocs" in href and href.lower().endswith(".pdf"):
                return {"pdf_url": absolutize_url(href)}
        match = re.search(r"https?://www\.sebi\.gov\.in/sebi_data/attachdocs/[^\"'\s<>]+\.pdf", html)
        return {"pdf_url": match.group(0) if match else None}

    def _extract_company_name(self, text: str, document_type: str) -> str:
        clean = BeautifulSoup(text or "", "html.parser").get_text(" ", strip=True)
        clean = re.sub(r"\s*-\s*(UDRHP|DRHP|RHP)\b.*$", "", clean, flags=re.I)
        clean = re.sub(r"\s*-\s*(Draft\s+)?Abridged Prospectus.*$", "", clean, flags=re.I)
        clean = re.sub(r"\s+\d+\s+[A-Z]\w+.*$", "", clean)
        return re.sub(r"\s+", " ", clean).strip()

    def _extract_abridged_pdf(self, title_attr: str, cell: Any) -> Optional[str]:
        match = re.search(r"href=['\"]([^'\"]*commondocs/[^'\"]*\.pdf)['\"]", title_attr or "", re.I)
        if match:
            return match.group(1)
        for link in cell.find_all("a", href=True):
            href = link["href"]
            if "commondocs" in href and href.lower().endswith(".pdf"):
                return href
        return None

    def _int_input_value(self, soup: BeautifulSoup, name: str, default: int) -> int:
        tag = soup.find("input", {"name": name}) or soup.find("input", {"id": name})
        try:
            return int(tag["value"]) if tag and tag.get("value") else default
        except ValueError:
            return default
