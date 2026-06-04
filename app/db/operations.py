"""Simple database operations — one function per action. No bloated service class."""
import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import func

from .engine import get_session, utcnow
from .models import (
    IPOMaster, IPOStatusHistory, ScraperLog,
    IPODocument, IPOParsedData, BackgroundTask,
)

logger = logging.getLogger(__name__)


# ─── IPO Master ─────────────────────────────────────────────

def get_ipo(ipo_id: int) -> Optional[IPOMaster]:
    with get_session() as s:
        return s.query(IPOMaster).filter(IPOMaster.id == ipo_id).first()


def get_ipo_by_normalized(name: str) -> Optional[IPOMaster]:
    with get_session() as s:
        return s.query(IPOMaster).filter(IPOMaster.normalized_name == name).first()


def upsert_ipo(data: dict) -> tuple[IPOMaster, bool]:
    """Create or update an IPO. Returns (ipo, is_new)."""
    with get_session() as s:
        norm = data.get("normalized_name", "")
        existing = s.query(IPOMaster).filter(IPOMaster.normalized_name == norm).first()

        if existing:
            is_new = False
            ipo = existing
            # Snapshot fields we track for change detection BEFORE applying new data
            old_status = ipo.status
            old_drhp = ipo.drhp_url
            old_rhp = ipo.rhp_url
            for key, val in data.items():
                if key.startswith("_"):
                    continue
                if val is not None:
                    setattr(ipo, key, val)

            # ── Status change tracking ───────────────────────────────────
            new_status = ipo.status
            if new_status and new_status != old_status:
                s.add(IPOStatusHistory(
                    ipo_master_id=ipo.id,
                    old_status=old_status,
                    new_status=new_status,
                    change_date=utcnow(),
                    source="scrape",
                    triggered_by="pipeline",
                ))

            # ── Document URL change handling ─────────────────────────────
            drhp_changed = ipo.drhp_url and ipo.drhp_url != old_drhp
            rhp_changed  = ipo.rhp_url  and ipo.rhp_url  != old_rhp
            # New URL → reset processed flag so audit re-resolves the updated doc
            if drhp_changed:
                ipo.drhp_processed = False
            if rhp_changed:
                ipo.rhp_processed = False
            # Give permanently-failed IPOs another chance when a new URL arrives
            if (drhp_changed or rhp_changed) and ipo.publish_status == "failed":
                ipo.publish_status = "pending"
                prov = dict(ipo.unified_provenance or {})
                prov.pop("_retry_count", None)
                ipo.unified_provenance = prov

            ipo.last_updated = utcnow()
        else:
            is_new = True
            ipo = IPOMaster(**{k: v for k, v in data.items() if not k.startswith("_")})
            ipo.first_seen = utcnow()
            ipo.last_updated = utcnow()
            s.add(ipo)

        s.commit()
        s.refresh(ipo)
        return ipo, is_new


def update_ipo_field(ipo_id: int, field: str, value: Any) -> bool:
    """Update a single field. Returns True if changed."""
    with get_session() as s:
        ipo = s.query(IPOMaster).filter(IPOMaster.id == ipo_id).first()
        if not ipo:
            return False
        setattr(ipo, field, value)
        ipo.last_updated = utcnow()
        s.commit()
        return True


def list_ipos(status: Optional[str] = None, year: Optional[int] = None,
              platform: Optional[str] = None, search: Optional[str] = None,
              page: int = 1, per_page: int = 20) -> tuple[list[IPOMaster], int]:
    """List IPOs with filters. Returns (ipos, total_count)."""
    with get_session() as s:
        q = s.query(IPOMaster)
        if status and status != "all":
            q = q.filter(IPOMaster.status == status)
        if year:
            q = q.filter(
                (IPOMaster.open_date.startswith(str(year))) |
                (IPOMaster.drhp_filed_date.startswith(str(year)))
            )
        if platform:
            q = q.filter(IPOMaster.platform == platform)
        if search:
            q = q.filter(IPOMaster.company_name.ilike(f"%{search}%"))

        total = q.count()
        ipos = q.order_by(IPOMaster.last_updated.desc()).offset((page - 1) * per_page).limit(per_page).all()
        return ipos, total


def count_ipos_by_status() -> dict[str, int]:
    with get_session() as s:
        rows = s.query(IPOMaster.status, func.count(IPOMaster.id)).group_by(IPOMaster.status).all()
        return {r[0] or "unknown": r[1] for r in rows}


# ─── Status History ─────────────────────────────────────────

def record_status_change(ipo_id: int, old_status: Optional[str], new_status: str,
                         source: str = "system", triggered_by: str = "system",
                         details: Optional[dict] = None):
    with get_session() as s:
        s.add(IPOStatusHistory(
            ipo_master_id=ipo_id, old_status=old_status, new_status=new_status,
            change_date=utcnow(), source=source, triggered_by=triggered_by, details=details,
        ))
        s.commit()


def get_status_history(ipo_id: int, limit: int = 50) -> list[IPOStatusHistory]:
    with get_session() as s:
        return s.query(IPOStatusHistory).filter(
            IPOStatusHistory.ipo_master_id == ipo_id
        ).order_by(IPOStatusHistory.change_date.desc()).limit(limit).all()


# ─── Documents ──────────────────────────────────────────────

def upsert_document(ipo_id: int, doc_type: str, url: str, phase: str = "discovered") -> IPODocument:
    with get_session() as s:
        existing = s.query(IPODocument).filter(
            IPODocument.ipo_master_id == ipo_id,
            IPODocument.doc_type == doc_type,
        ).first()
        if existing:
            if existing.url != url:
                existing.url = url
                existing.doc_version = (existing.doc_version or 1) + 1
            existing.phase = phase
            existing.last_updated = utcnow()
        else:
            existing = IPODocument(ipo_master_id=ipo_id, doc_type=doc_type, url=url, phase=phase)
            s.add(existing)
        s.commit()
        s.refresh(existing)
        return existing


def get_pending_documents() -> list[IPODocument]:
    """Get documents ready for processing (downloading or parsing)."""
    with get_session() as s:
        return s.query(IPODocument).filter(
            IPODocument.phase.in_(["discovered", "downloading"])
        ).order_by(IPODocument.created_at).limit(10).all()


# ─── Document Sections ──────────────────────────────────────

def get_sections(ipo_id: int, doc_type: str) -> list[dict]:
    """Get extracted sections for a document type."""
    from .models import DocumentSection
    with get_session() as s:
        rows = s.query(DocumentSection).filter(
            DocumentSection.ipo_master_id == ipo_id,
            DocumentSection.doc_type == doc_type,
        ).order_by(DocumentSection.page_start).all()
        return [{
            "section_name": r.section_name,
            "doc_type": r.doc_type,
            "id": r.id,
            "page_start": r.page_start,
            "page_end": r.page_end,
            "char_count": r.char_count,
            "parsed": bool(r.parsed),
            "parsed_at": r.parsed_at.isoformat() if r.parsed_at else None,
        } for r in rows]


def get_section_raw_md(ipo_id: int, doc_type: str, section_name: str) -> Optional[str]:
    """Get raw markdown text for a section."""
    from .models import DocumentSection
    with get_session() as s:
        r = s.query(DocumentSection).filter(
            DocumentSection.ipo_master_id == ipo_id,
            DocumentSection.doc_type == doc_type,
            DocumentSection.section_name == section_name,
        ).first()
        return r.raw_md if r else None


def delete_sections(ipo_id: int, doc_type: str) -> None:
    """Delete all sections for a document type (e.g. before re-resolving)."""
    from .models import DocumentSection
    with get_session() as s:
        s.query(DocumentSection).filter(
            DocumentSection.ipo_master_id == ipo_id,
            DocumentSection.doc_type == doc_type,
        ).delete()
        s.commit()


        s.commit()


def upsert_section(ipo_id: int, doc_type: str, section_name: str,
                   page_start: Optional[int] = None, page_end: Optional[int] = None,
                   raw_md: Optional[str] = None) -> None:
    """Create or update a document section."""
    from .models import DocumentSection
    with get_session() as s:
        existing = s.query(DocumentSection).filter(
            DocumentSection.ipo_master_id == ipo_id,
            DocumentSection.doc_type == doc_type,
            DocumentSection.section_name == section_name,
        ).first()
        if existing:
            if page_start is not None:
                existing.page_start = page_start
            if page_end is not None:
                existing.page_end = page_end
            if raw_md is not None:
                existing.raw_md = raw_md
                existing.char_count = len(raw_md)
            existing.last_updated = utcnow()
        else:
            existing = DocumentSection(
                ipo_master_id=ipo_id, doc_type=doc_type,
                section_name=section_name, page_start=page_start,
                page_end=page_end, raw_md=raw_md,
                char_count=len(raw_md) if raw_md else 0,
            )
            s.add(existing)
        s.commit()


def save_tables(ipo_id: int, doc_type: str, section_name: str, tables: list[dict]) -> None:
    """Save extracted tables for a section's pages into document_tables."""
    from .models import DocumentTable
    with get_session() as s:
        s.query(DocumentTable).filter(
            DocumentTable.ipo_master_id == ipo_id,
            DocumentTable.doc_type == doc_type,
            DocumentTable.section_name == section_name,
        ).delete()
        if not tables:
            return
        for t in tables:
            s.add(DocumentTable(
                ipo_master_id=ipo_id, doc_type=doc_type,
                section_name=section_name, page_num=t["page_num"],
                table_index=t["table_index"],
                table_data={"headers": t.get("headers", []), "rows": t.get("rows", [])},
            ))
        s.commit()


def get_tables(ipo_id: int, doc_type: Optional[str] = None,
               section_name: Optional[str] = None) -> list[dict]:
    """Return saved tables for an IPO, optionally filtered."""
    from .models import DocumentTable
    with get_session() as s:
        q = s.query(DocumentTable).filter(DocumentTable.ipo_master_id == ipo_id)
        if doc_type:
            q = q.filter(DocumentTable.doc_type == doc_type)
        if section_name:
            q = q.filter(DocumentTable.section_name == section_name)
        q = q.order_by(DocumentTable.page_num, DocumentTable.table_index)
        return [
            {
                "id": r.id, "doc_type": r.doc_type,
                "section_name": r.section_name, "page_num": r.page_num,
                "table_index": r.table_index, "data": r.table_data,
            }
            for r in q.all()
        ]


def mark_section_parsed(section_id: int, parsed_data: dict) -> None:
    """Mark a DocumentSection as parsed with extracted data."""
    from .models import DocumentSection
    with get_session() as s:
        row = s.query(DocumentSection).filter(DocumentSection.id == section_id).first()
        if not row:
            return
        row.parsed_data = parsed_data
        row.parsed = 1
        row.parsed_at = utcnow()
        s.commit()


def get_section_parsed(ipo_id: int, doc_type: str, section_name: str) -> Optional[dict]:
    """Get parsed data for a section."""
    from .models import DocumentSection
    with get_session() as s:
        r = s.query(DocumentSection).filter(
            DocumentSection.ipo_master_id == ipo_id,
            DocumentSection.doc_type == doc_type,
            DocumentSection.section_name == section_name,
        ).first()
        if r and r.parsed and r.parsed_data:
            return {"data": r.parsed_data, "parsed_at": r.parsed_at.isoformat() if r.parsed_at else None}
        return None


# ─── Dashboard ───────────────────────────────────────────────

def get_dashboard_stats() -> dict[str, Any]:
    """Get dashboard statistics."""
    with get_session() as s:
        total = s.query(IPOMaster).count()
        from collections import Counter
        statuses = [r[0] for r in s.query(IPOMaster.status).all() if r[0]]
        c = Counter(statuses)
        return {
            "total_ipos": total,
            "by_status": dict(c.most_common()),
        }


# ─── Deprecated: Backwards-compatible DatabaseService class ──

class DatabaseService:
    """Thin wrapper around operations functions for backwards compat with main.py."""
    def get_ipo_by_id(self, ipo_id: int) -> Optional[IPOMaster]:
        return get_ipo(ipo_id)

    def get_all_ipos(self, status="all", platform="all", search="", year=None,
                     documents="all", page=1, per_page=25):
        return list_ipos(
            status=status if status != "all" else None,
            platform=platform if platform != "all" else None,
            search=search or None,
            year=year,
            page=page,
            per_page=per_page,
        )

    def get_status_history(self, ipo_id: int, limit: int = 50) -> list[dict]:
        rows = __import__('app.db.operations', fromlist=['get_status_history']).get_status_history(ipo_id, limit)
        return [{
            "id": r.id, "old_status": r.old_status, "new_status": r.new_status,
            "change_date": r.change_date.isoformat() if r.change_date else None,
            "source": r.source, "triggered_by": r.triggered_by,
            "details": r.details,
        } for r in rows]

    def get_sections(self, ipo_id: int, doc_type: str) -> list[dict]:
        return get_sections(ipo_id, doc_type)

    def get_section_raw_md(self, ipo_id: int, doc_type: str, section_name: str) -> Optional[str]:
        return get_section_raw_md(ipo_id, doc_type, section_name)

    def get_section_parsed(self, ipo_id: int, doc_type: str, section_name: str) -> Optional[dict]:
        return get_section_parsed(ipo_id, doc_type, section_name)

    def upsert_document(self, ipo_id: int, doc_type: str, url: str, phase: str = "discovered"):
        return upsert_document(ipo_id, doc_type, url, phase)

    def delete_sections(self, ipo_id: int, doc_type: str):
        return delete_sections(ipo_id, doc_type)

    def upsert_section(self, ipo_id: int, doc_type: str, section_name: str,
                       page_start=None, page_end=None, raw_md=None):
        return upsert_section(ipo_id, doc_type, section_name,
                              page_start=page_start, page_end=page_end, raw_md=raw_md)

    def save_tables(self, ipo_id: int, doc_type: str, section_name: str,
                    tables: list[dict]) -> None:
        save_tables(ipo_id, doc_type, section_name, tables)

    def get_tables(self, ipo_id: int, doc_type: Optional[str] = None,
                   section_name: Optional[str] = None) -> list[dict]:
        return get_tables(ipo_id, doc_type, section_name)

    def mark_section_parsed(self, section_id: int, parsed_data: dict):
        return mark_section_parsed(section_id, parsed_data)

    def get_dashboard_stats(self) -> dict:
        return get_dashboard_stats()

    def get_recent_status_changes(self, limit: int = 100) -> list[dict]:
        return get_recent_status_changes(limit)

    def get_recent_logs(self, limit: int = 50) -> list:
        return list_scraper_logs(limit=limit)

# ─── Subscription ───────────────────────────────────────────

def update_subscription(ipo_id: int, subscription_data: dict):
    """Store latest subscription snapshot."""
    from app.db.operations import update_ipo_field
    update_ipo_field(ipo_id, "subscription_latest", subscription_data)


def get_open_ipos() -> list[IPOMaster]:
    """Get IPOs currently accepting bids."""
    with get_session() as s:
        return s.query(IPOMaster).filter(IPOMaster.status == "open").all()


# ─── Scraper Logs ───────────────────────────────────────────

def log_scrape(scraper_type: str, action: str, status: str = "success",
               company_name: Optional[str] = None, message: Optional[str] = None,
               error_details: Optional[dict] = None, execution_time_ms: Optional[int] = None,
               new_ipos_found: Optional[int] = None, status_changes: Optional[int] = None):
    with get_session() as s:
        s.add(ScraperLog(
            scraper_type=scraper_type, action=action, status=status,
            company_name=company_name, message=message, error_details=error_details,
            execution_time_ms=execution_time_ms, new_ipos_found=new_ipos_found,
            status_changes=status_changes,
        ))
        s.commit()


def get_recent_status_changes(limit: int = 100) -> list[dict]:
    with get_session() as s:
        rows = s.query(IPOStatusHistory).order_by(
            IPOStatusHistory.change_date.desc()).limit(limit).all()
        return [{
            "ipo_id": r.ipo_master_id,
            "company_name": r.ipo.company_name if r.ipo else "",
            "old_status": r.old_status,
            "new_status": r.new_status,
            "change_date": r.change_date.isoformat() if r.change_date else None,
            "source": r.source,
            "triggered_by": r.triggered_by,
        } for r in rows]


def list_scraper_logs(scraper_type: Optional[str] = None, limit: int = 50) -> list[ScraperLog]:
    with get_session() as s:
        q = s.query(ScraperLog)
        if scraper_type:
            q = q.filter(ScraperLog.scraper_type == scraper_type)
        return q.order_by(ScraperLog.created_at.desc()).limit(limit).all()
