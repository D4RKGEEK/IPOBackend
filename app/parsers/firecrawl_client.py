"""
Firecrawl /scrape client with JSON-schema extraction.

Sends a hosted markdown URL (typically our R2 section URL) and a JSON Schema,
and Firecrawl runs LLM extraction server-side, returning structured fields.

Docs: https://docs.firecrawl.dev/api-reference/endpoint/scrape

Returns the extracted dict on success. On failure, raises FirecrawlError with
enough context to log/debug. Never returns None — callers can rely on truthy
return or an exception.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

import httpx

from app.config import MissingConfigError, require_firecrawl

logger = logging.getLogger(__name__)

API_URL = "https://api.firecrawl.dev/v1/scrape"
DEFAULT_TIMEOUT = 180.0  # seconds — Firecrawl LLM extraction can be slow
DEFAULT_RETRIES = 2


# ─── Errors ──────────────────────────────────────────────────────────

class FirecrawlError(RuntimeError):
    """Raised when Firecrawl returns an error or non-2xx response."""

    def __init__(self, message: str, status_code: Optional[int] = None, body: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


# ─── Core call ───────────────────────────────────────────────────────

def _api_key() -> str:
    try:
        return require_firecrawl()
    except MissingConfigError as e:
        raise FirecrawlError(str(e))


def extract(
    url: str,
    schema: dict[str, Any],
    prompt: Optional[str] = None,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
) -> dict[str, Any]:
    """
    Send a URL to Firecrawl /scrape and get back the extracted JSON.

    Args:
        url: Publicly fetchable URL — typically an R2 section markdown URL.
        schema: JSON Schema describing the fields to extract.
        prompt: Optional natural-language prompt to guide extraction.
        timeout: Per-attempt HTTP timeout in seconds.
        retries: Number of times to retry on 5xx / network errors (in addition to initial attempt).

    Returns:
        The extracted dict (mapping field_name → value).

    Raises:
        FirecrawlError if the API returns a non-success status or extraction is empty.
    """
    payload: dict[str, Any] = {
        "url": url,
        "formats": ["json"],
        "jsonOptions": {
            "schema": schema,
            **({"prompt": prompt} if prompt else {}),
        },
        "onlyMainContent": True,
    }
    headers = {
        "Authorization": f"Bearer {_api_key()}",
        "Content-Type": "application/json",
    }

    last_err: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            with httpx.Client(timeout=timeout) as client:
                r = client.post(API_URL, headers=headers, json=payload)
            if r.status_code >= 500:
                # Server-side error; worth retrying
                last_err = FirecrawlError(
                    f"Firecrawl 5xx: {r.status_code}", status_code=r.status_code, body=r.text[:500]
                )
                time.sleep(1.5 * (attempt + 1))
                continue
            if r.status_code >= 400:
                raise FirecrawlError(
                    f"Firecrawl {r.status_code}: {r.text[:300]}",
                    status_code=r.status_code, body=r.text[:500],
                )
            body = r.json()
            if not body.get("success", True):
                raise FirecrawlError(
                    f"Firecrawl returned success=false: {body.get('error') or body}",
                    status_code=r.status_code, body=body,
                )
            data = body.get("data") or {}
            extracted = data.get("json")
            if extracted is None:
                # Some responses nest under llm_extraction (older) or extract (newer)
                extracted = data.get("extract") or data.get("llm_extraction")
            if not isinstance(extracted, dict):
                raise FirecrawlError(
                    "Firecrawl returned no JSON extraction payload.",
                    status_code=r.status_code, body=body,
                )
            return extracted

        except httpx.RequestError as e:
            last_err = e
            time.sleep(1.5 * (attempt + 1))
            continue
        except FirecrawlError:
            raise

    raise FirecrawlError(f"Firecrawl failed after {retries + 1} attempts: {last_err}")
