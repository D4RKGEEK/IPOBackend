"""
Cloudflare R2 storage helper.

R2 is S3-compatible. We use boto3 with a Cloudflare-flavored endpoint.

Usage:
    from app.storage.r2 import upload_section, section_url

    url = upload_section(ipo_id=88, doc_type="drhp",
                        section_name="CAPITAL_STRUCTURE",
                        markdown_text="## Capital Structure\\n...")

Config comes from app.config.settings; see .env.example for the variables.
"""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Optional

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from app.config import require_r2, settings

logger = logging.getLogger(__name__)


# ─── Client (cached) ────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _client():
    """Return a boto3 S3 client pointed at Cloudflare R2 (cached)."""
    s = require_r2()
    return boto3.client(
        "s3",
        endpoint_url=s.r2_endpoint,
        aws_access_key_id=s.r2_access_key_id,
        aws_secret_access_key=s.r2_secret_access_key,
        config=Config(signature_version="s3v4", retries={"max_attempts": 3, "mode": "adaptive"}),
        region_name="auto",
    )


# ─── Path / URL conventions ─────────────────────────────────────────

def section_key(ipo_id: int, doc_type: str, section_name: str) -> str:
    """Object key (path inside the bucket) for a section's markdown.

    Path convention: sections/{ipo_id}/{doc_type}/{section_name}.md
    Deterministic — caller can reconstruct without DB lookup.
    """
    safe = section_name.upper().replace(" ", "_").replace("/", "_")
    return f"sections/{ipo_id}/{doc_type}/{safe}.md"


def section_url(ipo_id: int, doc_type: str, section_name: str) -> str:
    """Public URL for a section, given the convention above."""
    base = settings.r2_public_base.rstrip("/")
    return f"{base}/{section_key(ipo_id, doc_type, section_name)}"


# ─── Operations ─────────────────────────────────────────────────────

def upload_section(
    ipo_id: int,
    doc_type: str,
    section_name: str,
    markdown_text: str,
    content_type: str = "text/markdown; charset=utf-8",
) -> str:
    """Upload (or overwrite) a section's markdown to R2. Returns the public URL."""
    body = markdown_text.encode("utf-8") if isinstance(markdown_text, str) else markdown_text
    _client().put_object(
        Bucket=settings.r2_bucket,
        Key=section_key(ipo_id, doc_type, section_name),
        Body=body,
        ContentType=content_type,
        CacheControl="public, max-age=3600",
    )
    return section_url(ipo_id, doc_type, section_name)


def delete_section(ipo_id: int, doc_type: str, section_name: str) -> bool:
    """Delete one section's object. Returns True if deleted, False if it didn't exist."""
    key = section_key(ipo_id, doc_type, section_name)
    try:
        _client().delete_object(Bucket=settings.r2_bucket, Key=key)
        return True
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "NoSuchKey":
            return False
        raise


def delete_ipo_prefix(ipo_id: int) -> int:
    """Delete every object under sections/{ipo_id}/. Returns number of objects deleted."""
    prefix = f"sections/{ipo_id}/"
    client = _client()
    paginator = client.get_paginator("list_objects_v2")
    total = 0
    for page in paginator.paginate(Bucket=settings.r2_bucket, Prefix=prefix):
        objs = [{"Key": o["Key"]} for o in page.get("Contents", [])]
        if not objs:
            continue
        client.delete_objects(Bucket=settings.r2_bucket, Delete={"Objects": objs, "Quiet": True})
        total += len(objs)
    return total


def head_section(ipo_id: int, doc_type: str, section_name: str) -> Optional[dict]:
    """Return object metadata (size, last_modified, etc.) or None if missing."""
    key = section_key(ipo_id, doc_type, section_name)
    try:
        res = _client().head_object(Bucket=settings.r2_bucket, Key=key)
        return {
            "size": res.get("ContentLength"),
            "content_type": res.get("ContentType"),
            "last_modified": res.get("LastModified").isoformat() if res.get("LastModified") else None,
            "etag": res.get("ETag", "").strip('"'),
        }
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
            return None
        raise


def list_sections(ipo_id: int, doc_type: Optional[str] = None) -> list[dict]:
    """List all section objects for an IPO (optionally filtered by doc_type)."""
    prefix = f"sections/{ipo_id}/" + (f"{doc_type}/" if doc_type else "")
    base = settings.r2_public_base.rstrip("/")
    client = _client()
    paginator = client.get_paginator("list_objects_v2")
    out = []
    for page in paginator.paginate(Bucket=settings.r2_bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            out.append({
                "key": obj["Key"],
                "size": obj.get("Size"),
                "last_modified": obj.get("LastModified").isoformat() if obj.get("LastModified") else None,
                "url": f"{base}/{obj['Key']}",
            })
    return out
