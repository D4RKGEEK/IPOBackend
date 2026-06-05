"""
IPO Dashboard Companion Server — port 3000

Fully standalone FastAPI server that:
  1. Serves dashboard.html at /
  2. Provides new admin/operational endpoints
     (system usage, publish-status, edit IPO, etc.)
  3. Connects to the same database directly (reads .env)

Does NOT import any files from the main API codebase,
so it can run in ANY venv with basic deps installed.

Run:  uvicorn dashboard.server:app --port 3000 --reload
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# Low dep imports — will fail gracefully if missing
_HAS_FASTAPI = False
try:
    from fastapi import FastAPI, HTTPException, Path, Query, Body
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import HTMLResponse, JSONResponse
    _HAS_FASTAPI = True
except ImportError:
    FastAPI = None

try:
    from pydantic import BaseModel
except ImportError:
    BaseModel = object

# Find project root
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ENV_FILE = _PROJECT_ROOT / ".env"

# Load .env manually (don't depend on pydantic-settings or dotenv)
def _load_env(env_path: Path):
    """Minimal .env loader — no deps needed."""
    if not env_path.exists():
        return {}
    envs = {}
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip("\"'")
        envs[key] = val
        os.environ.setdefault(key, val)
    return envs

_env_vars = _load_env(_ENV_FILE)

# Auto SSL cert
for _cert_path in (
    os.environ.get("SSL_CERT_FILE"),
    "/opt/homebrew/etc/openssl@3/cert.pem",
    "/usr/local/etc/openssl@3/cert.pem",
    "/etc/ssl/certs/ca-certificates.crt",
    "/etc/pki/tls/certs/ca-bundle.crt",
):
    if _cert_path and os.path.exists(_cert_path):
        os.environ.setdefault("SSL_CERT_FILE", _cert_path)
        os.environ.setdefault("REQUESTS_CA_BUNDLE", _cert_path)
        break

# DB connection (lazy, only when first used)
_DB_ENGINE = None
_DB_SESSION = None

def _get_db_url() -> str:
    url = _env_vars.get("DATABASE_URL", "")
    if not url:
        return f"sqlite:///{_PROJECT_ROOT}/ipos.db"
    if url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url[len("postgres://"):]
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url

def _get_db_session():
    global _DB_ENGINE, _DB_SESSION
    if _DB_SESSION is not None:
        return _DB_SESSION()

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    url = _get_db_url()
    kwargs = {}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    if url.startswith("postgresql"):
        kwargs.update({
            "pool_pre_ping": True,
            "pool_size": 2,
            "max_overflow": 2,
        })

    _DB_ENGINE = create_engine(url, echo=False, **kwargs)
    _DB_SESSION = sessionmaker(bind=_DB_ENGINE, expire_on_commit=False)
    return _DB_SESSION()


# ─── FastAPI App ─────────────────────────────────────────────────────

if not _HAS_FASTAPI:
    raise RuntimeError("fastapi not installed. Run: pip install fastapi uvicorn")

app = FastAPI(title="IPO Dashboard Server", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

_DASHBOARD_DIR = Path(__file__).parent

# Cache
_system_metrics_cache: dict[str, Any] = {"data": None, "cached_at": 0}
_CACHE_TTL = 15  # seconds

_MAIN_API_BASE = os.environ.get("MAIN_API_BASE", "http://127.0.0.1:8001")


# ─── Helpers ─────────────────────────────────────────────────────────

def _read_proc(path: str) -> str:
    try:
        return Path(path).read_text().strip()
    except Exception:
        return ""


def _try_exec(cmd: list[str]) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        return r.stdout.strip()
    except Exception:
        return ""


def _get_system_usage() -> dict[str, Any]:
    now = time.time()
    if _system_metrics_cache["data"] and (now - _system_metrics_cache["cached_at"]) < _CACHE_TTL:
        return _system_metrics_cache["data"]

    result: dict[str, Any] = {}

    # ── RAM ──
    try:
        mem = _read_proc("/proc/meminfo")
        mem_total = mem_avail = 0
        for line in mem.splitlines():
            if line.startswith("MemTotal:"):
                mem_total = int(line.split()[1]) // 1024
            elif line.startswith("MemAvailable:"):
                mem_avail = int(line.split()[1]) // 1024
        result["ram"] = {
            "total_mb": mem_total,
            "available_mb": mem_avail,
            "used_mb": mem_total - mem_avail,
            "used_pct": round((mem_total - mem_avail) / mem_total * 100, 1) if mem_total else 0,
        }
    except Exception:
        result["ram"] = {"total_mb": 0, "available_mb": 0, "used_mb": 0, "used_pct": 0}

    # ── CPU ──
    load = _read_proc("/proc/loadavg").split()
    result["cpu"] = {
        "load_1m": float(load[0]) if len(load) > 0 else 0,
        "load_5m": float(load[1]) if len(load) > 1 else 0,
        "load_15m": float(load[2]) if len(load) > 2 else 0,
    }

    result["uptime_seconds"] = float(_read_proc("/proc/uptime").split()[0]) if _read_proc("/proc/uptime") else 0

    # ── Disk ──
    try:
        df = subprocess.run(["df", "-h", "/"], capture_output=True, text=True, timeout=5)
        lines = df.stdout.splitlines()
        if len(lines) >= 2:
            parts = lines[1].split()
            result["disk"] = {
                "filesystem": parts[0], "size": parts[1],
                "used": parts[2], "avail": parts[3], "used_pct": parts[4].rstrip("%"),
            }
    except Exception:
        result["disk"] = {"size": "?", "used": "?", "avail": "?", "used_pct": "?"}

    # ── Database ──
    try:
        sess = _get_db_session()
        from sqlalchemy import text as sa_text

        dialect = "postgresql" if _get_db_url().startswith("postgresql") else "sqlite"
        result["database"] = {"dialect": dialect, "tables": {}}

        if dialect == "postgresql":
            size = sess.execute(sa_text("SELECT pg_database_size(current_database())")).scalar()
            result["database"]["size_bytes"] = size or 0
            result["database"]["size_mb"] = round((size or 0) / 1024 / 1024, 1)
        else:
            import os as _os
            db_path = _PROJECT_ROOT / "ipos.db"
            s = _os.path.getsize(str(db_path)) if db_path.exists() else 0
            result["database"]["size_bytes"] = s
            result["database"]["size_mb"] = round(s / 1024 / 1024, 1)

        for tbl in ("ipo_master", "document_sections", "document_tables",
                     "scraper_logs", "ipo_status_history", "documents"):
            try:
                cnt = sess.execute(sa_text(f"SELECT COUNT(*) FROM {tbl}")).scalar()
                result["database"]["tables"][tbl] = cnt
            except Exception:
                pass
        sess.close()
    except Exception as e:
        result["database"] = {"error": str(e)[:200]}

    # ── R2 ──
    try:
        import boto3
        from botocore.client import Config
        r2 = boto3.client(
            "s3",
            endpoint_url=f"https://{_env_vars.get('CF_ACCOUNT_ID','')}.r2.cloudflarestorage.com",
            aws_access_key_id=_env_vars.get("R2_ACCESS_KEY_ID", ""),
            aws_secret_access_key=_env_vars.get("R2_SECRET_ACCESS_KEY", ""),
            config=Config(signature_version="s3v4"),
            region_name="auto",
        )
        bucket = _env_vars.get("R2_BUCKET", "ipo")
        total_size = 0
        total_objects = 0
        is_truncated = True
        marker = ""
        while is_truncated:
            args: dict = {"Bucket": bucket, "MaxKeys": 1000}
            if marker:
                args["Marker"] = marker
            resp = r2.list_objects_v2(**args)
            contents = resp.get("Contents", [])
            total_objects += len(contents)
            for obj in contents:
                total_size += obj.get("Size", 0)
            is_truncated = resp.get("IsTruncated", False)
            if is_truncated and contents:
                marker = contents[-1]["Key"]
            else:
                break
        result["r2"] = {
            "bucket": bucket,
            "object_count": total_objects,
            "size_bytes": total_size,
            "size_mb": round(total_size / 1024 / 1024, 1),
        }
    except ImportError:
        result["r2"] = {"error": "boto3 not installed"}
    except Exception as e:
        result["r2"] = {"error": str(e)[:200]}

    # ── Firecrawl ──
    fc_key = _env_vars.get("FIRECRAWL_API_KEY", "")
    if fc_key:
        try:
            import httpx
            resp = httpx.get(
                "https://api.firecrawl.dev/v1/team",
                headers={"Authorization": f"Bearer {fc_key}"},
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                result["firecrawl"] = {
                    "credits_used": data.get("creditsUsed", 0),
                    "credits_remaining": data.get("creditsRemaining", 0),
                    "plan": data.get("plan", data.get("name", "unknown")),
                }
            else:
                result["firecrawl"] = {"error": f"HTTP {resp.status_code}"}
        except ImportError:
            result["firecrawl"] = {"error": "httpx not installed"}
        except Exception as e:
            result["firecrawl"] = {"error": str(e)[:200]}
    else:
        result["firecrawl"] = {"error": "not configured"}

    # ── Config info ──
    result["config"] = {
        "parser_provider": _env_vars.get("PARSER_PROVIDER", "deepseek"),
        "r2_configured": bool(_env_vars.get("R2_ACCESS_KEY_ID") and _env_vars.get("CF_ACCOUNT_ID")),
        "deepseek_configured": bool(_env_vars.get("DEEPSEEK_API_KEY")),
        "firecrawl_configured": bool(fc_key),
        "db_dialect": "postgresql" if _get_db_url().startswith("postgresql") else "sqlite",
        "api_version": _env_vars.get("VERSION", "3.0.0"),
        "telegram_configured": bool(_env_vars.get("TELEGRAM_BOT_TOKEN")),
        "gmail_configured": bool(_env_vars.get("GMAIL_USER")),
        "internal_api_key_set": bool(_env_vars.get("INTERNAL_API_KEY")),
    }

    _system_metrics_cache["data"] = result
    _system_metrics_cache["cached_at"] = now
    return result


# ─── Routes ──────────────────────────────────────────────────────────

@app.get("/", include_in_schema=False)
async def root():
    html_path = _DASHBOARD_DIR / "dashboard.html"
    if html_path.exists():
        return HTMLResponse(html_path.read_text(encoding="utf-8"))
    raise HTTPException(404, "dashboard.html not found")


# ─── Health ──────────────────────────────────────────────────────────

@app.get("/health", tags=["System"])
async def health():
    return {"status": "ok", "version": "1.0.0", "service": "dashboard-server"}


# ─── API: System Usage ──────────────────────────────────────────────

@app.get("/api/system/usage", tags=["System"])
async def system_usage():
    return _get_system_usage()


# ─── API: Publish Status Update ─────────────────────────────────────

@app.patch("/api/ipos/{ipo_id}/publish-status", tags=["Admin"])
async def update_publish_status(
    ipo_id: int = Path(...),
    publish_status: str = Query(..., description="published | needs_review | rejected | pending"),
    notes: Optional[str] = Query(None),
):
    if publish_status not in ("published", "needs_review", "rejected", "pending"):
        raise HTTPException(400, f"Invalid publish_status: {publish_status}")

    sess = _get_db_session()
    try:
        from sqlalchemy import text as sa_text
        ipo = sess.execute(
            sa_text("SELECT id, company_name, publish_status, validation_issues FROM ipo_master WHERE id=:id"),
            {"id": ipo_id},
        ).mappings().first()
        if not ipo:
            raise HTTPException(404, "IPO not found")

        sess.execute(
            sa_text("UPDATE ipo_master SET publish_status=:ps, published_at=:pa, last_updated=:lu WHERE id=:id"),
            {
                "ps": publish_status, "id": ipo_id,
                "pa": datetime.now(timezone.utc) if publish_status == "published" else None,
                "lu": datetime.now(timezone.utc),
            },
        )

        if notes:
            issues = ipo.get("validation_issues") or []
            if not isinstance(issues, list):
                issues = []
            issues.append(f"[{publish_status}] {notes}")
            sess.execute(
                sa_text("UPDATE ipo_master SET validation_issues=:vi WHERE id=:id"),
                {"vi": json.dumps(issues), "id": ipo_id},
            )

        sess.commit()
        return {"status": "ok", "ipo_id": ipo_id, "publish_status": publish_status}
    except HTTPException:
        raise
    except Exception as e:
        sess.rollback()
        raise HTTPException(500, str(e))
    finally:
        sess.close()


# ─── API: Advance IPO Status ────────────────────────────────────────

VALID_STATUSES = ["FILED", "DRHP", "RHP", "UPCOMING", "OPEN", "CLOSED", "LISTED"]

@app.patch("/api/ipos/{ipo_id}/status", tags=["Admin"])
async def update_ipo_status(
    ipo_id: int = Path(...),
    new_status: str = Query(...),
):
    ns = new_status.upper()
    if ns not in VALID_STATUSES:
        raise HTTPException(400, f"Invalid status: {new_status}")

    sess = _get_db_session()
    try:
        from sqlalchemy import text as sa_text
        row = sess.execute(
            sa_text("SELECT id, company_name, status FROM ipo_master WHERE id=:id"),
            {"id": ipo_id},
        ).mappings().first()
        if not row:
            raise HTTPException(404, "IPO not found")

        old_status = row["status"]
        now = datetime.now(timezone.utc)
        sess.execute(
            sa_text("UPDATE ipo_master SET status=:s, last_updated=:lu WHERE id=:id"),
            {"s": ns, "lu": now, "id": ipo_id},
        )
        # Log status change
        sess.execute(
            sa_text("""INSERT INTO ipo_status_history
                (ipo_master_id, old_status, new_status, change_date, source, triggered_by)
                VALUES (:mid, :os, :ns, :cd, :src, :trig)"""),
            {"mid": ipo_id, "os": old_status, "ns": ns, "cd": now, "src": "dashboard", "trig": "manual"},
        )
        sess.commit()
        return {"status": "ok", "ipo_id": ipo_id, "old_status": old_status, "new_status": ns}
    except HTTPException:
        raise
    except Exception as e:
        sess.rollback()
        raise HTTPException(500, str(e))
    finally:
        sess.close()


# ─── API: Edit IPO Fields ───────────────────────────────────────────

@app.patch("/api/ipos/{ipo_id}", tags=["Admin"])
async def edit_ipo(
    ipo_id: int = Path(...),
    company_name: Optional[str] = Query(None),
    price_band: Optional[str] = Query(None),
    platform: Optional[str] = Query(None),
    issue_type: Optional[str] = Query(None),
    open_date: Optional[str] = Query(None),
    close_date: Optional[str] = Query(None),
    drhp_filed_date: Optional[str] = Query(None),
    rhp_filed_date: Optional[str] = Query(None),
    fp_filed_date: Optional[str] = Query(None),
):
    fields = {
        "company_name": company_name, "price_band": price_band,
        "platform": platform, "issue_type": issue_type,
        "open_date": open_date, "close_date": close_date,
        "drhp_filed_date": drhp_filed_date, "rhp_filed_date": rhp_filed_date,
        "fp_filed_date": fp_filed_date,
    }
    set_parts = [f"{k}=:{k}" for k, v in fields.items() if v is not None]
    if not set_parts:
        raise HTTPException(400, "No fields to update")

    sess = _get_db_session()
    try:
        from sqlalchemy import text as sa_text
        params = {k: v for k, v in fields.items() if v is not None}
        params["id"] = ipo_id
        params["lu"] = datetime.now(timezone.utc)
        set_parts.append("last_updated=:lu")

        sess.execute(
            sa_text(f"UPDATE ipo_master SET {', '.join(set_parts)} WHERE id=:id"),
            params,
        )
        sess.commit()
        changed = {k: v for k, v in fields.items() if v is not None}
        return {"status": "ok", "ipo_id": ipo_id, "changed": changed}
    except Exception as e:
        sess.rollback()
        raise HTTPException(500, str(e))
    finally:
        sess.close()


# ─── API: Add Document URL ──────────────────────────────────────────

@app.post("/api/ipos/{ipo_id}/documents", tags=["Admin"])
async def add_document_url(
    ipo_id: int = Path(...),
    doc_type: str = Query(...),
    url: str = Query(...),
):
    dt = doc_type.lower()
    field_map = {"drhp": "drhp_url", "rhp": "rhp_url", "fp": "final_prospectus_url", "abridged": "abridged_prospectus_url"}
    if dt not in field_map:
        raise HTTPException(400, "doc_type must be: drhp, rhp, fp, abridged")

    sess = _get_db_session()
    try:
        from sqlalchemy import text as sa_text
        row = sess.execute(sa_text("SELECT id FROM ipo_master WHERE id=:id"), {"id": ipo_id}).first()
        if not row:
            raise HTTPException(404, "IPO not found")

        sess.execute(
            sa_text(f"UPDATE ipo_master SET {field_map[dt]}=:url, last_updated=:lu WHERE id=:id"),
            {"url": url, "lu": datetime.now(timezone.utc), "id": ipo_id},
        )
        sess.commit()
        return {"status": "ok", "ipo_id": ipo_id, "doc_type": dt, "url": url}
    except HTTPException:
        raise
    except Exception as e:
        sess.rollback()
        raise HTTPException(500, str(e))
    finally:
        sess.close()


# ─── API: Delete IPO ────────────────────────────────────────────────

@app.delete("/api/ipos/{ipo_id}", tags=["Admin"])
async def delete_ipo(ipo_id: int = Path(...)):
    sess = _get_db_session()
    try:
        from sqlalchemy import text as sa_text
        row = sess.execute(
            sa_text("SELECT id, company_name FROM ipo_master WHERE id=:id"),
            {"id": ipo_id},
        ).mappings().first()
        if not row:
            raise HTTPException(404, "IPO not found")
        name = row["company_name"]

        # Delete cascading rows
        for tbl in ("ipo_status_history", "document_tables", "document_sections", "documents"):
            sess.execute(sa_text(f"DELETE FROM {tbl} WHERE ipo_master_id=:id"), {"id": ipo_id})
        sess.execute(sa_text("DELETE FROM ipo_master WHERE id=:id"), {"id": ipo_id})
        sess.commit()
        return {"status": "deleted", "ipo_id": ipo_id, "company_name": name}
    except HTTPException:
        raise
    except Exception as e:
        sess.rollback()
        raise HTTPException(500, str(e))
    finally:
        sess.close()


# ─── API: Cancel Task ───────────────────────────────────────────────

@app.post("/api/tasks/{task_id}/cancel", tags=["System"])
async def cancel_task(task_id: str = Path(...)):
    """Cancel via main API proxy."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(f"{_MAIN_API_BASE}/api/tasks/{task_id}/cancel")
        if resp.status_code == 404:
            raise HTTPException(404, "Task not found on main API")
        return {"status": "cancelled", "task_id": task_id}
    except httpx.ConnectError:
        raise HTTPException(502, "Main API not reachable")


# ─── API: Search Parsed Data ────────────────────────────────────────

@app.get("/api/ipos/search-parsed", tags=["Aggregation"])
async def search_parsed(
    q: str = Query(..., min_length=1),
    field: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
):
    sess = _get_db_session()
    try:
        from sqlalchemy import text as sa_text
        rows = sess.execute(
            sa_text("SELECT id, company_name, status, publish_status, confidence_score, unified_data "
                     "FROM ipo_master WHERE unified_data IS NOT NULL ORDER BY last_updated DESC LIMIT 200")
        ).mappings().all()

        query = q.lower().strip()
        results = []
        for row in rows:
            data = row.get("unified_data") or {}
            if not isinstance(data, dict):
                continue
            score = 0
            matches = {}

            def _search(d: dict, prefix=""):
                nonlocal score
                for k, v in d.items():
                    path = f"{prefix}.{k}" if prefix else k
                    if field and path != field:
                        continue
                    if isinstance(v, dict):
                        _search(v, path)
                    elif isinstance(v, list):
                        if any(query in str(item).lower() for item in v):
                            matches[path] = str(v)[:150]
                            score += 1
                    elif v and query in str(v).lower():
                        matches[path] = str(v)[:150]
                        score += 1

            _search(data)
            if field and field not in matches:
                continue

            if score > 0:
                results.append({
                    "ipo_id": row["id"],
                    "company_name": row["company_name"],
                    "status": row["status"],
                    "confidence_score": row["confidence_score"],
                    "publish_status": row["publish_status"],
                    "match_count": score,
                    "matches": matches,
                })
                if len(results) >= limit:
                    break

        return {"query": q, "total_matches": len(results), "results": results}
    except Exception as e:
        raise HTTPException(500, str(e))
    finally:
        sess.close()


# ─── Proxy to Main API ──────────────────────────────────────────────

@app.api_route("/proxy/{path:path}", methods=["GET", "POST"], tags=["Proxy"])
async def proxy_to_main(path: str):
    import httpx
    url = f"{_MAIN_API_BASE}/{path}"
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(url)
            try:
                return JSONResponse(content=resp.json(), status_code=resp.status_code)
            except Exception:
                return HTMLResponse(resp.text, status_code=resp.status_code)
    except httpx.ConnectError:
        raise HTTPException(502, f"Main API not reachable at {_MAIN_API_BASE}")


# ─── Notification History ───────────────────────────────────────────

@app.get("/api/notifications/history", tags=["System"])
async def notification_history(limit: int = Query(50, ge=1, le=200)):
    sess = _get_db_session()
    try:
        from sqlalchemy import text as sa_text
        rows = sess.execute(
            sa_text("SELECT * FROM notification_log ORDER BY created_at DESC LIMIT :limit"),
            {"limit": limit},
        ).mappings().all()
        return {"notifications": [dict(r) for r in rows]}
    except Exception:
        return {"notifications": [], "note": "notification_log table not found"}
    finally:
        sess.close()


# ─── Main ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    print("Starting IPO Dashboard Server on http://127.0.0.1:3000")
    print(f"  Dashboard:  http://127.0.0.1:3000/")
    print(f"  System:     http://127.0.0.1:3000/api/system/usage")
    uvicorn.run(app, host="127.0.0.1", port=3000)
