"""
Smoke-test R2 upload + public-URL round-trip.

Run:  .venv/bin/python scripts/test_r2.py

Uploads a small markdown file, fetches it via the public URL, deletes it, and
verifies each step. Exits 0 on success, non-zero on failure.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

from app.storage.r2 import (
    upload_section,
    section_url,
    head_section,
    delete_section,
    list_sections,
)


def green(s): return f"\033[92m{s}\033[0m"
def red(s):   return f"\033[91m{s}\033[0m"
def dim(s):   return f"\033[2m{s}\033[0m"


def main() -> int:
    TEST_IPO = 999999
    TEST_DOC = "drhp"
    TEST_SEC = "TEST_SECTION"
    body = "# Test Section\n\nThis is a smoke test from scripts/test_r2.py.\n"

    print(dim("R2 smoke test — uploads, fetches, deletes one object.\n"))

    # 1. Upload
    try:
        url = upload_section(TEST_IPO, TEST_DOC, TEST_SEC, body)
        print(green("✓ upload   "), url)
    except Exception as e:
        print(red("✗ upload failed:"), e)
        return 1

    # 2. Head check
    meta = head_section(TEST_IPO, TEST_DOC, TEST_SEC)
    if not meta:
        print(red("✗ head_object returned None right after upload"))
        return 2
    print(green("✓ head     "), f"size={meta['size']}  etag={meta['etag']}  ctype={meta['content_type']}")

    # 3. Fetch public URL (R2 takes a moment to propagate occasionally)
    fetched = None
    last_err = None
    for attempt in range(3):
        try:
            r = httpx.get(url, timeout=15)
            if r.status_code == 200:
                fetched = r.text
                break
            last_err = f"HTTP {r.status_code}: {r.text[:120]}"
        except Exception as e:
            last_err = str(e)
        time.sleep(1.5)

    if fetched is None:
        print(red("✗ public fetch failed:"), last_err)
        print(dim("  Hint: in Cloudflare → R2 → bucket → Settings → Public Access, allow R2.dev subdomain access."))
        return 3

    if fetched.strip() != body.strip():
        print(red("✗ content mismatch"))
        print(dim("  expected:"), repr(body[:60]))
        print(dim("  got:     "), repr(fetched[:60]))
        return 4
    print(green("✓ public GET"), f"{len(fetched)} chars match")

    # 4. List
    items = list_sections(TEST_IPO, TEST_DOC)
    if not any(i["key"].endswith(f"{TEST_SEC}.md") for i in items):
        print(red("✗ list_objects didn't include the test object"))
        return 5
    print(green("✓ list     "), f"{len(items)} object(s) under prefix")

    # 5. Cleanup
    if not delete_section(TEST_IPO, TEST_DOC, TEST_SEC):
        print(red("✗ delete returned False"))
        return 6
    print(green("✓ delete   "), "test object removed")

    if head_section(TEST_IPO, TEST_DOC, TEST_SEC) is not None:
        print(red("✗ head still finds the object after delete"))
        return 7

    print(green("\nAll R2 round-trip checks passed."))
    print(dim(f"URL convention: {section_url(88, 'drhp', 'CAPITAL_STRUCTURE')}"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
