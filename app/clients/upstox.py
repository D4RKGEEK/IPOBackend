"""Upstox client — fetches IPO data from Upstox v2 API."""
import asyncio
import logging
from typing import Any, Optional

import httpx

from app.schemas import UpstoxData
from app.utils import AsyncRateLimiter

logger = logging.getLogger(__name__)


class UpstoxClient:
    BASE_URL = "https://api.upstox.com/v2/ipos"
    HEADERS_TEMPLATE = {
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    }
    ALL_STATUSES = ["upcoming", "open", "closed", "listed"]
    MAX_RETRIES = 3

    def __init__(self, client: httpx.AsyncClient, token: str, delay_seconds: float = 0.1):
        self.client = client
        self.token = token
        self.limiter = AsyncRateLimiter(delay_seconds)

    def _headers(self) -> dict[str, str]:
        return {**self.HEADERS_TEMPLATE, "Authorization": f"Bearer {self.token}"}

    async def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        for attempt in range(self.MAX_RETRIES):
            await self.limiter.wait()
            try:
                resp = await self.client.request(method, url, **kwargs)
            except Exception as exc:
                if attempt < self.MAX_RETRIES - 1:
                    await asyncio.sleep(2 ** attempt)
                    continue
                raise
            if resp.status_code == 429:
                if attempt < self.MAX_RETRIES - 1:
                    await asyncio.sleep(2 ** attempt)
                    continue
                resp.raise_for_status()
            resp.raise_for_status()
            return resp
        raise RuntimeError(f"Upstox request failed after {self.MAX_RETRIES} retries")

    async def _fetch_page(self, status: str, page: int = 1, records: int = 30) -> dict:
        params = {"status": status, "page_number": page, "records": records}
        resp = await self._request("GET", self.BASE_URL, headers=self._headers(), params=params, timeout=30)
        return resp.json()

    async def fetch_all_slugs(self) -> list[dict]:
        all_items = []
        for status in self.ALL_STATUSES:
            page = 1
            while True:
                try:
                    data = await self._fetch_page(status, page=page)
                except Exception:
                    break
                items = (data.get("data") or []) if isinstance(data, dict) else []
                if not items:
                    break
                for item in items:
                    if isinstance(item, dict) and item.get("id"):
                        all_items.append({
                            "id": item["id"], "name": item.get("name", ""),
                            "status": item.get("status", status),
                            "symbol": item.get("symbol"), "issue_type": item.get("issue_type"),
                        })
                meta = data.get("meta_data") or {}
                total_pages = (meta.get("page") or {}).get("total_pages", 0) or 0
                if page >= total_pages:
                    break
                page += 1
        return all_items

    async def fetch_detail(self, slug: str) -> Optional[dict]:
        try:
            resp = await self._request("GET", f"{self.BASE_URL}/{slug}", headers=self._headers(), timeout=30)
            json_data = resp.json()
            return json_data.get("data") if isinstance(json_data, dict) else None
        except Exception:
            return None

    async def fetch_details_batch(self, slugs: list[str]) -> list[UpstoxData]:
        sem = asyncio.Semaphore(5)
        results = []

        async def _fetch_one(slug: str) -> Optional[UpstoxData]:
            async with sem:
                detail = await self.fetch_detail(slug)
                if not detail:
                    return None
                clean = {k: detail.get(k) for k in UpstoxData.model_fields.keys() if k in detail}
                clean.setdefault("id", slug)
                clean.setdefault("name", detail.get("name", slug))
                clean.setdefault("status", detail.get("status", "unknown"))
                return UpstoxData(**clean)

        for slug in slugs:
            result = await _fetch_one(slug)
            if result:
                results.append(result)
        return results
