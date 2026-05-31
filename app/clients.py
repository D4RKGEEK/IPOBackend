import asyncio
import re
from typing import Any, Optional

import httpx
from bs4 import BeautifulSoup

from .schemas import (
    BSEData,
    BSESMEDocument,
    IPORecord,
    NSEData,
    NSEDocumentAttachment,
    UpstoxData,
)
from .utils import (
    AsyncRateLimiter,
    SEBI_BASE_URL,
    absolutize_url,
    clean_url,
    extract_file_url,
    extract_pdf_from_zip,
    format_date,
    is_zip_url,
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

    def __init__(self, client: httpx.AsyncClient, delay_seconds: float = 0.3):
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
                await self.limiter.wait()
                try:
                    detail = await self.fetch_detail_page(record.document_urls.detail_page)
                except Exception:
                    # Individual detail page fetch failure is non-fatal;
                    # skip this record and continue with the rest.
                    return
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
                from .schemas import DocumentUrls
                urls = DocumentUrls(
                    detail_page=detail_page_url,
                    abridged_prospectus_pdf=absolutize_url(abridged_pdf),
                )
                records.append(
                    IPORecord(
                        company_name=company_name,
                        filing_date=format_date(cells[0].get_text(" ", strip=True)),
                        source="sebi",
                        document_type=document_type,
                        document_urls=urls,
                    )
                )
        return {"records": records, "total_pages": total_pages, "per_page": per_page, "total_records": total_records}

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
        # Strip any document type suffixes: DRHP, RHP, UDRHP (Updated DRHP), etc.
        clean = re.sub(r"\s*-\s*(UDRHP|DRHP|RHP)\b.*$", "", clean, flags=re.I)
        clean = re.sub(r"\s*-\s*(Draft\s+)?Abridged Prospectus.*$", "", clean, flags=re.I)
        # Remove trailing garbage like " 1 Company Name" duplicates
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
            ipo_status = "open" if status == "L" and flag in (1, 2, 3, 15, 17, 23) else "upcoming" if status == "F" and flag == 7 else "other"
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
    OFFER_DOCS_URL = f"{BASE_URL}/api/corporates/offerdocs"
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Referer": f"{BASE_URL}/regulations/offer-documents",
    }

    def __init__(self, client: httpx.AsyncClient, delay_seconds: float = 1.0):
        self.client = client
        self.limiter = AsyncRateLimiter(delay_seconds)

    async def fetch_offer_docs(self, index: str = "equities", from_date: str = "", to_date: str = "") -> list[NSEData]:
        """Fetch offer documents from NSE.

        Args:
            index: 'equities' (MainBoard) or 'sme' (SME IPOs)
            from_date: Start date in DD-MM-YYYY format. Defaults to 1 year ago.
            to_date: End date in DD-MM-YYYY format. Defaults to today.
        """
        await self.limiter.wait()
        from datetime import datetime, timedelta
        today = datetime.now()
        one_year_ago = today - timedelta(days=365)
        from_date = from_date or one_year_ago.strftime("%d-%m-%Y")
        to_date = to_date or today.strftime("%d-%m-%Y")
        params = {"index": index, "from_date": from_date, "to_date": to_date}
        response = await self.client.get(self.OFFER_DOCS_URL, headers=self.HEADERS, params=params, timeout=30)
        response.raise_for_status()
        return self._parse_response(response.json(), index=index)

    async def fetch_all_docs(self, from_date: str = "", to_date: str = "") -> list[NSEData]:
        """Fetch offer documents for both MainBoard and SME markets."""
        equities = await self.fetch_offer_docs(index="equities", from_date=from_date, to_date=to_date)
        sme = await self.fetch_offer_docs(index="sme", from_date=from_date, to_date=to_date)
        return equities + sme

    def _build_attachment(self, url_raw: Optional[str], file_size: Optional[str]) -> Optional[NSEDocumentAttachment]:
        url = clean_url(url_raw)
        if not url:
            return None
        return NSEDocumentAttachment(url=url, file_size=file_size.strip() if file_size else None, is_zip=is_zip_url(url))

    def _parse_response(self, data: Any, index: str = "equities") -> list[NSEData]:
        rows = data if isinstance(data, list) else []
        records = []
        for item in rows if isinstance(rows, list) else []:
            if not isinstance(item, dict):
                continue
            company_name = clean_url(item.get("company") or "")
            if not company_name:
                continue
            records.append(
                NSEData(
                    company_name=company_name,
                    symbol=item.get("symbol"),
                    isin=item.get("isin"),
                    pan_no=item.get("pan_no"),
                    index=index,
                    drhp=item.get("drhp"),
                    drhp_date=item.get("drhpDate"),
                    drhp_status=clean_url(item.get("drhpStatus")),
                    drhp_attach=self._build_attachment(item.get("drhpAttach"), item.get("drhpAttachFileSize")),
                    drhp_sub_date=item.get("drhpSubDate"),
                    drhp_av_link=clean_url(item.get("drhpAvLink")),
                    rhp=item.get("rhp"),
                    rhp_date=item.get("rhpDate"),
                    rhp_attach=self._build_attachment(item.get("rhpAttach"), None),
                    rhp_sub_date=item.get("rhpSubDate"),
                    rhp_av_link=clean_url(item.get("rhpAvLink")),
                    fp=item.get("fp"),
                    fp_date=item.get("fpDate"),
                    fp_attach=self._build_attachment(item.get("fpAttach"), item.get("fpAttachFileSize")),
                    fp_sub_date=item.get("fpSubDate"),
                    fp_av_link=clean_url(item.get("fpAvLink")),
                    adv=item.get("adv"),
                    adv_date=item.get("advDate"),
                    adv_attach=self._build_attachment(item.get("advAttach"), item.get("advAttachFileSize")),
                    iap_sub_date=item.get("iapSubDate"),
                    iap_av_link=clean_url(item.get("iapAvLink")),
                    ic_sub_date=item.get("icSubDate"),
                    ic_av_link=clean_url(item.get("icAvLink")),
                    issue_open_date=item.get("issue_open_date"),
                    issue_close_date=item.get("issue_close_date"),
                    ipo_inprincipal_xbrl_link=item.get("ipo_inprincipal_xbrl_link"),
                    ipo_inlisting_xbrl_link=item.get("ipo_inlisting_xbrl_link"),
                    raw=item,
                )
            )
        return records


class BSESmeClient:
    """Scrape BSE SME document links from bsesme.com.

    BSE's main API doesn't expose document PDFs. BSE's SME exchange website
    at bsesme.com publishes DRHP and RHP download links directly in the
    ASP.NET page source. These links work with a simple Referer header.
    """

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
        records = []
        seen = set()
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
        records = []
        seen = set()
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
            doc_type = "Prospectus" if "Prospectus" in url else ("RHP" if "RHP" in url else "RHP")
            records.append(BSESMEDocument(
                company_name=company, document_type=doc_type,
                document_url=url, is_zip=url.lower().endswith(".zip"),
            ))
        return records


class UpstoxClient:
    """Fetch IPO data from Upstox v2 API.
    
    Two endpoints:
      - GET /v2/ipos?status={s}&page_number={p}  → list (pagination)
      - GET /v2/ipos/{id}                        → detail (DRHP/RHP URLs)
    
    Usage:
        upstox = UpstoxClient(client, token="...")
        slugs = await upstox.fetch_all_slugs()  # list, all statuses
        detail = await upstox.fetch_detail("some-ipo-slug")  # detail
    """

    BASE_URL = "https://api.upstox.com/v2/ipos"
    HEADERS_TEMPLATE = {
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    }
    ALL_STATUSES = ["upcoming", "open", "closed", "listed"]

    def __init__(self, client: httpx.AsyncClient, token: str, delay_seconds: float = 0.3):
        self.client = client
        self.token = token
        self.limiter = AsyncRateLimiter(delay_seconds)

    def _headers(self) -> dict[str, str]:
        return {**self.HEADERS_TEMPLATE, "Authorization": f"Bearer {self.token}"}

    async def _fetch_page(self, status: str, page: int = 1, records: int = 30) -> dict:
        """Fetch a single page of the list endpoint."""
        await self.limiter.wait()
        params = {"status": status, "page_number": page, "records": records}
        resp = await self.client.get(
            self.BASE_URL, headers=self._headers(), params=params, timeout=30
        )
        resp.raise_for_status()
        return resp.json()

    async def fetch_all_slugs(self) -> list[dict]:
        """Iterate all statuses + pagination, return slugs (id, name, status).
        
        Returns list of dicts with at least {'id', 'name', 'status'}.
        """
        all_items: list[dict] = []
        for status in self.ALL_STATUSES:
            page = 1
            while True:
                try:
                    data = await self._fetch_page(status, page=page)
                except Exception:
                    break  # non-fatal per status
                
                items = (data.get("data") or []) if isinstance(data, dict) else []
                if not items:
                    break
                
                for item in items:
                    if isinstance(item, dict) and item.get("id"):
                        all_items.append({
                            "id": item["id"],
                            "name": item.get("name", ""),
                            "status": item.get("status", status),
                            "symbol": item.get("symbol"),
                            "issue_type": item.get("issue_type"),
                        })
                
                # Check pagination
                meta = data.get("meta_data") or {}
                total_pages = meta.get("total_pages", 0) or 0
                if page >= total_pages:
                    break
                page += 1
        return all_items

    async def fetch_detail(self, slug: str) -> Optional[dict]:
        """Fetch full detail for a single IPO slug.
        
        Returns the 'data' dict from the response, or None on failure.
        """
        await self.limiter.wait()
        try:
            resp = await self.client.get(
                f"{self.BASE_URL}/{slug}",
                headers=self._headers(),
                timeout=30,
            )
            resp.raise_for_status()
            json_data = resp.json()
            return json_data.get("data") if isinstance(json_data, dict) else None
        except Exception:
            return None

    async def fetch_details_batch(self, slugs: list[str]) -> list[UpstoxData]:
        """Fetch details for multiple slugs (concurrent, rate-limited)."""
        from asyncio import Semaphore, gather
        
        sem = Semaphore(5)  # max 5 concurrent
        results: list[UpstoxData] = []

        async def _fetch_one(slug: str) -> Optional[UpstoxData]:
            async with sem:
                detail = await self.fetch_detail(slug)
                if not detail:
                    return None
                # Build UpstoxData, only passing keys that exist in the response
                clean = {k: detail.get(k) for k in UpstoxData.model_fields.keys() if k in detail}
                # Ensure required fields have values
                clean.setdefault("id", slug)
                clean.setdefault("name", detail.get("name", slug))
                clean.setdefault("status", detail.get("status", "unknown"))
                return UpstoxData(**clean)

        tasks = [_fetch_one(slug) for slug in slugs]
        for coro in tasks:
            result = await coro
            if result:
                results.append(result)
        return results


# ─── Merge Logic ──────────────────────────────────────────────


def merge_upstox_into_results(results: list[IPORecord], upstox_rows: list[UpstoxData]) -> None:
    """Merge Upstox detail data into the results list (by normalized name)."""
    index = {normalize_company_name(r.company_name): r for r in results}
    for row in upstox_rows:
        key = normalize_company_name(row.name)
        existing = index.get(key)
        if existing:
            existing.upstox_data = row
        else:
            record = IPORecord(
                company_name=row.name,
                source="upstox",
                upstox_data=row,
            )
            results.append(record)
            index[key] = record


def merge_bse_into_results(results: list[IPORecord], bse_rows: list[BSEData]) -> None:
    index = {normalize_company_name(r.company_name): r for r in results}
    for row in bse_rows:
        key = normalize_company_name(row.company_name)
        existing = index.get(key)
        if existing:
            existing.bse_data = row
        else:
            record = IPORecord(company_name=row.company_name, source="bse", bse_data=row)
            results.append(record)
            index[key] = record


def merge_bse_sme_docs(results: list[IPORecord], sme_docs: list[BSESMEDocument]) -> None:
    index = {normalize_company_name(r.company_name): r for r in results}
    for doc in sme_docs:
        key = normalize_company_name(doc.company_name)
        existing = index.get(key)
        if existing:
            existing.bse_sme_doc = doc
        else:
            doc_type = "DRHP" if doc.document_type == "DRHP" else "RHP"
            record = IPORecord(company_name=doc.company_name, source="bse", document_type=doc_type, bse_sme_doc=doc)
            results.append(record)
            index[key] = record


def merge_nse_into_results(results: list[IPORecord], nse_rows: list[NSEData]) -> None:
    index = {normalize_company_name(r.company_name): r for r in results}
    for nse_row in nse_rows:
        key = normalize_company_name(nse_row.company_name)
        existing = index.get(key)
        if existing:
            existing.nse_data = nse_row
        else:
            doc_type = None
            urls = None
            if nse_row.drhp:
                doc_type = "DRHP"
                from .schemas import DocumentUrls
                urls = DocumentUrls(drhp_pdf=nse_row.drhp_attach.url if nse_row.drhp_attach else None)
            elif nse_row.rhp:
                doc_type = "RHP"
                from .schemas import DocumentUrls
                urls = DocumentUrls(rhp_pdf=nse_row.rhp_attach.url if nse_row.rhp_attach else None)
            record = IPORecord(
                company_name=nse_row.company_name, source="nse",
                document_type=doc_type, document_urls=urls, nse_data=nse_row,
            )
            results.append(record)
            index[key] = record