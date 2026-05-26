import asyncio
import re
from typing import Any, Optional

import httpx
from bs4 import BeautifulSoup

from .schemas import BSEData, IPORecord, NSEData
from .utils import (
    AsyncRateLimiter,
    SEBI_BASE_URL,
    absolutize_url,
    extract_file_url,
    format_date,
    normalize_company_name,
    to_sebi_date,
)


class SEBIClient:
    AJAX_URL = f"{SEBI_BASE_URL}/sebiweb/ajax/home/getnewslistinfo.jsp"
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    def __init__(self, client: httpx.AsyncClient, delay_seconds: float = 0.5):
        self.client = client
        self.limiter = AsyncRateLimiter(delay_seconds)

    async def fetch_filings(
        self,
        page: int = 1,
        document_type: str = "DRHP",
        from_date: str = "",
        to_date: str = "",
        search: str = "",
    ) -> dict[str, Any]:
        await self.limiter.wait()
        smid = 10 if document_type == "DRHP" else 11
        sm_text = (
            "DRAFT OFFER DOCUMENTS FILED WITH SEBI"
            if smid == 10
            else "RED HERRING DOCUMENTS FILED WITH ROC"
        )
        data = {
            "nextValue": str(page),
            "next": "s" if page == 1 else "n",
            "search": search,
            "fromDate": to_sebi_date(from_date) if from_date else "",
            "toDate": to_sebi_date(to_date) if to_date else "",
            "fromYear": "",
            "toYear": "",
            "deptId": "",
            "sid": "3",
            "ssid": "15",
            "smid": str(smid),
            "ssidhidden": "15",
            "intmid": "-1",
            "sText": "FILINGS",
            "ssText": "PUBLIC ISSUES",
            "smText": sm_text,
            "doDirect": "-1" if page == 1 else "0",
        }
        headers = {
            **self.HEADERS,
            "Referer": (
                f"{SEBI_BASE_URL}/sebiweb/home/HomeAction.do?"
                f"doListing=yes&sid=3&ssid=15&smid={smid}"
            ),
        }
        response = await self.client.post(self.AJAX_URL, headers=headers, data=data, timeout=30)
        response.raise_for_status()
        return self.parse_listing(response.text, document_type=document_type)

    async def fetch_detail_page(self, detail_url: str) -> dict[str, Optional[str]]:
        await self.limiter.wait()
        detail_url = absolutize_url(detail_url) or detail_url
        response = await self.client.get(
            detail_url,
            headers={"User-Agent": self.HEADERS["User-Agent"]},
            timeout=30,
        )
        response.raise_for_status()
        return self.parse_detail_page(response.text)

    async def attach_pdf_urls(self, records: list[IPORecord], max_concurrency: int = 5) -> None:
        semaphore = asyncio.Semaphore(max_concurrency)

        async def attach(record: IPORecord) -> None:
            if not record.document_urls or not record.document_urls.detail_page:
                return
            async with semaphore:
                detail = await self.fetch_detail_page(record.document_urls.detail_page)
            pdf_url = detail.get("pdf_url")
            if not pdf_url:
                return
            if record.document_type == "DRHP":
                record.document_urls.drhp_pdf = pdf_url
            elif record.document_type == "RHP":
                record.document_urls.rhp_pdf = pdf_url

        await asyncio.gather(*(attach(record) for record in records))

    def parse_listing(self, html: str, document_type: str = "DRHP") -> dict[str, Any]:
        soup = BeautifulSoup(html, "html.parser")
        total_pages = self._int_input_value(soup, "totalpage", default=1)
        per_page = self._int_input_value(soup, "nextDel", default=25)
        total_records = 0

        p_text = soup.find("p")
        if p_text:
            match = re.search(r"of\s+(\d+)\s+records", p_text.get_text(" ", strip=True), re.I)
            if match:
                total_records = int(match.group(1))

        records = []
        table = soup.find("table")
        if not table:
            return {
                "records": records,
                "total_pages": total_pages,
                "per_page": per_page,
                "total_records": total_records,
            }

        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 2:
                continue

            main_link = cells[1].find("a", href=True)
            if not main_link:
                continue

            title_attr = main_link.get("title", "")
            link_text = main_link.get_text(" ", strip=True)
            company_name = self._extract_company_name(title_attr or link_text, document_type)
            if not company_name:
                continue

            abridged_pdf = self._extract_abridged_pdf(title_attr, cells[1])
            detail_page_url = absolutize_url(main_link.get("href"))
            urls = {
                "detail_page": detail_page_url,
                "abridged_prospectus_pdf": absolutize_url(abridged_pdf),
            }
            records.append(
                IPORecord(
                    company_name=company_name,
                    filing_date=format_date(cells[0].get_text(" ", strip=True)),
                    source="sebi",
                    document_type=document_type,
                    document_urls=urls,
                )
            )

        return {
            "records": records,
            "total_pages": total_pages,
            "per_page": per_page,
            "total_records": total_records,
        }

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
        pattern = rf"\s*-\s*{document_type}\b.*$"
        clean = re.sub(pattern, "", clean, flags=re.I)
        clean = re.sub(r"\s*-\s*(Draft\s+)?Abridged Prospectus.*$", "", clean, flags=re.I)
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


class BSEClient:
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
            if status == "L" and flag in (1, 2, 3, 15, 17, 23):
                ipo_status = "open"
            elif status == "F" and flag == 7:
                ipo_status = "upcoming"
            else:
                ipo_status = "other"

            records.append(
                BSEData(
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
                )
            )
        return records


class NSEClient:
    BASE_URL = "https://www.nseindia.com"
    UNDER_ISSUE_URL = f"{BASE_URL}/api/equity/under-issue"
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Accept": "application/json,text/plain,*/*",
        "Referer": f"{BASE_URL}/market-data/ipo-under-issue",
    }

    def __init__(self, client: httpx.AsyncClient, delay_seconds: float = 2.0):
        self.client = client
        self.limiter = AsyncRateLimiter(delay_seconds)

    async def fetch_under_issue(self, mode: str = "browser") -> list[NSEData]:
        if mode == "browser":
            return await self.fetch_under_issue_with_browser()
        return await self.fetch_under_issue_with_http_session()

    async def fetch_under_issue_with_http_session(self) -> list[NSEData]:
        await self.limiter.wait()
        await self.client.get(self.BASE_URL, headers=self.HEADERS, timeout=30)
        await self.limiter.wait()
        response = await self.client.get(self.UNDER_ISSUE_URL, headers=self.HEADERS, timeout=30)
        response.raise_for_status()
        return self.parse_under_issue(response.json())

    async def fetch_under_issue_with_browser(self) -> list[NSEData]:
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise RuntimeError(
                "Playwright is required for NSE browser mode. Install requirements and run "
                "`playwright install chromium`, or call with mode=http."
            ) from exc

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent=self.HEADERS["User-Agent"],
                extra_http_headers={"Accept": self.HEADERS["Accept"]},
            )
            page = await context.new_page()
            try:
                await page.goto(self.BASE_URL, wait_until="domcontentloaded", timeout=45000)
                await page.goto(
                    f"{self.BASE_URL}/market-data/ipo-under-issue",
                    wait_until="domcontentloaded",
                    timeout=45000,
                )
                data = await page.evaluate(
                    """async (url) => {
                        const response = await fetch(url, {
                            credentials: 'include',
                            headers: { accept: 'application/json,text/plain,*/*' }
                        });
                        if (!response.ok) {
                            throw new Error(`NSE returned ${response.status}`);
                        }
                        return await response.json();
                    }""",
                    self.UNDER_ISSUE_URL,
                )
            finally:
                await browser.close()
        return self.parse_under_issue(data)

    def parse_under_issue(self, data: Any) -> list[NSEData]:
        rows = data if isinstance(data, list) else data.get("data", data.get("Table", []))
        records = []
        for item in rows if isinstance(rows, list) else []:
            name = (
                item.get("companyName")
                or item.get("company")
                or item.get("symbol")
                or item.get("issueName")
                or ""
            ).strip()
            if name:
                records.append(NSEData(company_name=name, raw=item))
        return records


def merge_bse_into_results(results: list[IPORecord], bse_rows: list[BSEData]) -> None:
    index = {normalize_company_name(record.company_name): record for record in results}
    for bse_row in bse_rows:
        key = normalize_company_name(bse_row.company_name)
        existing = index.get(key)
        if existing:
            existing.bse_data = bse_row
        else:
            record = IPORecord(company_name=bse_row.company_name, source="bse", bse_data=bse_row)
            results.append(record)
            index[key] = record
