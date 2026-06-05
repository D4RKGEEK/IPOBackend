"""NSE client — fetches offer documents from NSE corporate actions API."""
from typing import Any, Optional

import httpx

from app.schemas import NSEData, NSEDocumentAttachment
from app.utils import AsyncRateLimiter, clean_url, is_zip_url


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
        equities = await self.fetch_offer_docs(index="equities", from_date=from_date, to_date=to_date)
        sme = await self.fetch_offer_docs(index="sme", from_date=from_date, to_date=to_date)
        return equities + sme

    def _build_attachment(self, url_raw: Optional[str], file_size: Optional[str]) -> Optional[NSEDocumentAttachment]:
        url = clean_url(url_raw)
        if not url:
            return None
        return NSEDocumentAttachment(
            url=url, file_size=file_size.strip() if file_size else None, is_zip=is_zip_url(url),
        )

    def _parse_response(self, data: Any, index: str = "equities") -> list[NSEData]:
        rows = data if isinstance(data, list) else []
        records = []
        for item in rows if isinstance(rows, list) else []:
            if not isinstance(item, dict):
                continue
            company_name = clean_url(item.get("company") or "")
            if not company_name:
                continue
            records.append(NSEData(
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
            ))
        return records
