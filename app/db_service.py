"""
Database service layer for IPO Aggregation Platform.
All DB operations go through here — the API and scraper both use this.
"""
import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from .db_models import (
    IPOMaster,
    IPOStatusHistory,
    ScraperLog,
    IPOParsedData,
    IPODocument,
    init_db,
    get_session,
)

logger = logging.getLogger(__name__)


class DatabaseService:
    """High-level CRUD for the IPO platform. Thread-safe (each call gets its own session)."""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path
        init_db(db_path)  # Create tables if they don't exist

    def _session(self) -> Session:
        return get_session(self.db_path)

    # ─── IPO CRUD ─────────────────────────────────────────

    def get_ipo_by_id(self, ipo_id: int) -> Optional[IPOMaster]:
        with self._session() as session:
            return session.query(IPOMaster).filter(IPOMaster.id == ipo_id).first()

    def get_ipo_by_normalized_name(self, name: str) -> Optional[IPOMaster]:
        with self._session() as session:
            return session.query(IPOMaster).filter(
                IPOMaster.normalized_name == name
            ).first()

    def get_all_ipos(
        self,
        status: Optional[str] = None,
        platform: Optional[str] = None,
        search: Optional[str] = None,
        year: Optional[int] = None,
        page: int = 1,
        per_page: int = 25,
    ) -> tuple[list[dict[str, Any]], int]:
        """Return (ipos_list, total_count). Filters are applied server-side."""
        with self._session() as session:
            query = session.query(IPOMaster)

            if status and status != "all":
                query = query.filter(IPOMaster.status == status)
            if platform and platform != "all":
                query = query.filter(IPOMaster.platform.ilike(f"%{platform}%"))
            if search:
                query = query.filter(
                    IPOMaster.company_name.ilike(f"%{search}%")
                )
            if year:
                query = query.filter(
                    IPOMaster.drhp_filed_date.like(f"{year}%")
                )

            total = query.count()
            # Sort by most recently filed first. Fallback to discovery date.
            query = query.order_by(
                IPOMaster.drhp_filed_date.desc().nullslast(),
                IPOMaster.rhp_filed_date.desc().nullslast(),
                IPOMaster.id.desc(),
            )
            query = query.offset((page - 1) * per_page).limit(per_page)

            return [row.to_dict() for row in query.all()], total

    def upsert_ipo(self, ipo_data: dict[str, Any]) -> tuple[IPOMaster, bool]:
        """
        Insert or update an IPO record.
        Returns (ipo_record, is_new).
        Detects status changes automatically.
        """
        normalized_name = ipo_data.get("normalized_name", "").strip().upper()
        if not normalized_name:
            raise ValueError("normalized_name is required")
        
        with self._session() as session:
            existing = session.query(IPOMaster).filter(
                IPOMaster.normalized_name == normalized_name
            ).first()

            now = datetime.now(timezone.utc)

            if existing:
                # Detect status change
                new_status = ipo_data.get("status", "unknown")
                if existing.status != new_status and existing.status:
                    self._log_status_change(
                        session, existing.id, existing.status, new_status,
                        source=ipo_data.get("_source", "aggregator"),
                        triggered_by=ipo_data.get("_triggered_by", "system"),
                    )
                
                # Detect document URL changes — reset processed flag if URL changed
                doc_url_fields = {
                    "drhp_url": "drhp_processed",
                    "rhp_url": "rhp_processed",
                    "final_prospectus_url": "rhp_processed",  # reuse rhp_processed for FP
                }
                for url_field, processed_field in doc_url_fields.items():
                    new_url = ipo_data.get(url_field)
                    old_url = getattr(existing, url_field, None)
                    if new_url and new_url != old_url:
                        # URL changed — existing text is stale, reset processed flag
                        setattr(existing, processed_field, 0)
                        # Delete stale extracted text from ipo_parsed_data
                        data_type_lookup = {
                            "drhp_url": "raw_text_drhp",
                            "rhp_url": "raw_text_rhp",
                            "final_prospectus_url": "raw_text_final_prospectus",
                        }
                        dt = data_type_lookup.get(url_field)
                        if dt:
                            session.query(IPOParsedData).filter(
                                IPOParsedData.ipo_master_id == existing.id,
                                IPOParsedData.data_type == dt,
                            ).delete()
                
                # Update fields — skip internal-processed flags (managed by URL change detection)
                processed_fields = {"drhp_processed", "rhp_processed"}
                for key, value in ipo_data.items():
                    if key.startswith("_") or key in ("id", "normalized_name", "first_seen") or key in processed_fields:
                        continue
                    if hasattr(existing, key):
                        setattr(existing, key, value)
                
                existing.last_updated = now
                existing.last_scraped = now
                session.commit()
                session.refresh(existing)
                return existing, False
            else:
                # New IPO
                processed_fields = {"drhp_processed", "rhp_processed"}
                record = IPOMaster(normalized_name=normalized_name, **{
                    k: v for k, v in ipo_data.items()
                    if k != "normalized_name" and not k.startswith("_")
                    and hasattr(IPOMaster, k) and k not in processed_fields
                })
                record.first_seen = now
                record.last_updated = now
                record.last_scraped = now
                session.add(record)
                session.flush()  # Get the ID

                # Log initial creation as status change
                self._log_status_change(
                    session, record.id, None, ipo_data.get("status", "unknown"),
                    source=ipo_data.get("_source", "aggregator"),
                    triggered_by=ipo_data.get("_triggered_by", "system"),
                    details={"action": "first_seen"},
                )
                
                session.commit()
                session.refresh(record)
                return record, True

    def _log_status_change(
        self,
        session: Session,
        ipo_id: int,
        old_status: Optional[str],
        new_status: str,
        source: str = "aggregator",
        triggered_by: str = "system",
        details: Optional[dict] = None,
    ) -> IPOStatusHistory:
        record = IPOStatusHistory(
            ipo_master_id=ipo_id,
            old_status=old_status,
            new_status=new_status,
            source=source,
            triggered_by=triggered_by,
            details=details or {},
        )
        session.add(record)
        return record

    # ─── Status History ────────────────────────────────────

    def get_status_history(
        self, ipo_id: int, limit: int = 20
    ) -> list[dict[str, Any]]:
        with self._session() as session:
            rows = (
                session.query(IPOStatusHistory)
                .filter(IPOStatusHistory.ipo_master_id == ipo_id)
                .order_by(IPOStatusHistory.change_date.desc())
                .limit(limit)
                .all()
            )
            return [
                {
                    "id": r.id,
                    "old_status": r.old_status,
                    "new_status": r.new_status,
                    "change_date": r.change_date.isoformat(),
                    "source": r.source,
                    "triggered_by": r.triggered_by,
                    "details": r.details,
                }
                for r in rows
            ]

    def get_recent_status_changes(
        self, limit: int = 50, since: Optional[str] = None
    ) -> list[dict[str, Any]]:
        """Get recent status changes across all IPOs, with company names."""
        with self._session() as session:
            query = (
                session.query(IPOStatusHistory, IPOMaster.company_name)
                .join(IPOMaster, IPOStatusHistory.ipo_master_id == IPOMaster.id)
                .order_by(IPOStatusHistory.change_date.desc())
                .limit(limit)
            )
            results = []
            for h, company_name in query.all():
                results.append({
                    "ipo_id": h.ipo_master_id,
                    "company_name": company_name,
                    "old_status": h.old_status,
                    "new_status": h.new_status,
                    "change_date": h.change_date.isoformat(),
                    "source": h.source,
                    "triggered_by": h.triggered_by,
                })
            return results

    # ─── Document Processing ─────────────────────────────

    def get_unprocessed_documents(self, limit: int = 50) -> list[dict[str, Any]]:
        """
        Get IPOs with document URLs that haven't been processed yet.
        Prioritizes ZIP URLs first, then recent PDFs.
        """
        from sqlalchemy import or_, case
        with self._session() as session:
            # Priority: ZIPs first, then recent PDFs
            is_zip = case(
                (IPOMaster.drhp_url.ilike('%.zip'), 1),
                (IPOMaster.rhp_url.ilike('%.zip'), 1),
                (IPOMaster.final_prospectus_url.ilike('%.zip'), 1),
                else_=0
            )
            
            rows = (
                session.query(IPOMaster)
                .filter(
                    or_(
                        IPOMaster.drhp_processed == 0,
                        IPOMaster.rhp_processed == 0,
                    ),
                    or_(
                        IPOMaster.drhp_url.isnot(None),
                        IPOMaster.rhp_url.isnot(None),
                        IPOMaster.final_prospectus_url.isnot(None),
                    ),
                )
                .order_by(is_zip.desc(), IPOMaster.drhp_filed_date.desc().nullslast(), IPOMaster.id.desc())
                .limit(limit)
                .all()
            )
            return [row.to_dict() for row in rows]

    def save_document_text(
        self,
        ipo_id: int,
        document_type: str,  # 'drhp', 'rhp', 'final_prospectus'
        text: str,
        source_url: str,
        confidence_score: float = 0.8,
    ) -> int:
        """Save extracted PDF text to ipo_parsed_data."""
        from datetime import datetime, timezone
        with self._session() as session:
            record = IPOParsedData(
                ipo_master_id=ipo_id,
                data_type=f"raw_text_{document_type}",
                extracted_data={
                    "text": text,
                    "source_url": source_url,
                    "document_type": document_type,
                    "char_count": len(text),
                },
                confidence_score=confidence_score,
                extra={
                    "extraction_method": "pymupdf",
                    "version": "1.0",
                },
            )
            session.add(record)
            session.commit()
            return record.id

    def mark_document_processed(
        self,
        ipo_id: int,
        document_type: str,  # 'drhp', 'rhp', 'final_prospectus'
    ):
        """Mark a document as processed (text extracted)."""
        field = f"{document_type}_processed"
        with self._session() as session:
            ipo = session.query(IPOMaster).filter(IPOMaster.id == ipo_id).first()
            if ipo and hasattr(ipo, field):
                setattr(ipo, field, 1)
                session.commit()

    def get_document_text(
        self,
        ipo_id: int,
        document_type: str,  # 'drhp', 'rhp', 'final_prospectus'
    ) -> Optional[str]:
        """Retrieve previously extracted text for a document."""
        data_type = f"raw_text_{document_type}"
        with self._session() as session:
            record = (
                session.query(IPOParsedData)
                .filter(
                    IPOParsedData.ipo_master_id == ipo_id,
                    IPOParsedData.data_type == data_type,
                )
                .order_by(IPOParsedData.extraction_date.desc())
                .first()
            )
            if record and record.extracted_data:
                return record.extracted_data.get("text")
            return None

    # ─── Parsed Data (Phase 2) ──────────────────────────

    def save_parsed_ipo_data(
        self,
        ipo_id: int,
        parsed_data: dict,
        document_type: str = "merged",
        confidence_score: float = 0.0,
        processing_time_ms: int = 0,
    ) -> int:
        """Save structured parsed IPO data to ipo_parsed_data table."""
        with self._session() as session:
            record = IPOParsedData(
                ipo_master_id=ipo_id,
                data_type=f"parsed_{document_type}",
                extracted_data=parsed_data,
                confidence_score=confidence_score,
                processing_time_ms=processing_time_ms,
                extra={
                    "extraction_version": "2.0",
                    "source": "pipeline",
                },
            )
            session.add(record)
            session.commit()
            return record.id

    def get_parsed_ipo_data(
        self,
        ipo_id: int,
        document_type: str = "merged",
    ) -> Optional[dict]:
        """Retrieve parsed IPO data."""
        data_type = f"parsed_{document_type}"
        with self._session() as session:
            record = (
                session.query(IPOParsedData)
                .filter(
                    IPOParsedData.ipo_master_id == ipo_id,
                    IPOParsedData.data_type == data_type,
                )
                .order_by(IPOParsedData.extraction_date.desc())
                .first()
            )
            if record and record.extracted_data:
                return {
                    "data": record.extracted_data,
                    "confidence_score": record.confidence_score,
                    "extraction_date": record.extraction_date.isoformat(),
                    "processing_time_ms": record.processing_time_ms,
                }
            return None

    def get_parsed_data_history(
        self,
        ipo_id: int,
    ) -> list[dict]:
        """Get all parsed data versions for an IPO."""
        with self._session() as session:
            records = (
                session.query(IPOParsedData)
                .filter(
                    IPOParsedData.ipo_master_id == ipo_id,
                    IPOParsedData.data_type.like('parsed_%'),
                )
                .order_by(IPOParsedData.extraction_date.desc())
                .all()
            )
            return [
                {
                    "id": r.id,
                    "data_type": r.data_type,
                    "data": r.extracted_data,
                    "confidence_score": r.confidence_score,
                    "extraction_date": r.extraction_date.isoformat(),
                    "processing_time_ms": r.processing_time_ms,
                }
                for r in records
            ]

    # ─── Dashboard Stats (updated) ────────────────────────

    def get_dashboard_stats(self) -> dict[str, Any]:
        with self._session() as session:
            total = session.query(func.count(IPOMaster.id)).scalar() or 0

            status_counts = {}
            for row in session.query(
                IPOMaster.status, func.count(IPOMaster.id)
            ).group_by(IPOMaster.status).all():
                status_counts[row[0]] = row[1]

            avg_confidence = (
                session.query(func.avg(IPOMaster.data_confidence)).scalar() or 0.0
            )

            total_drhp = (
                session.query(func.count(IPOMaster.id))
                .filter(IPOMaster.drhp_url.isnot(None))
                .scalar() or 0
            )
            total_rhp = (
                session.query(func.count(IPOMaster.id))
                .filter(IPOMaster.rhp_url.isnot(None))
                .scalar() or 0
            )
            
            # Document processing stats
            drhp_processed = (
                session.query(func.count(IPOMaster.id))
                .filter(IPOMaster.drhp_processed == 1)
                .scalar() or 0
            )
            rhp_processed = (
                session.query(func.count(IPOMaster.id))
                .filter(IPOMaster.rhp_processed == 1)
                .scalar() or 0
            )
            unresolved_zips = (
                session.query(func.count(IPOMaster.id))
                .filter(
                    IPOMaster.drhp_processed == 0,
                    IPOMaster.drhp_url.ilike('%.zip'),
                )
                .scalar() or 0
            ) + (
                session.query(func.count(IPOMaster.id))
                .filter(
                    IPOMaster.rhp_processed == 0,
                    IPOMaster.rhp_url.ilike('%.zip'),
                )
                .scalar() or 0
            )

            recent_scrapes = (
                session.query(ScraperLog)
                .order_by(ScraperLog.created_at.desc())
                .limit(5)
                .all()
            )

            latest_scrape = (
                session.query(ScraperLog)
                .filter(ScraperLog.action == "full_scrape")
                .order_by(ScraperLog.created_at.desc())
                .first()
            )

            platform_counts = {}
            for row in session.query(
                IPOMaster.platform, func.count(IPOMaster.id)
            ).filter(IPOMaster.platform.isnot(None)).group_by(IPOMaster.platform).all():
                platform_counts[row[0]] = row[1]

            return {
                "total_ipos": total,
                "ipos_by_status": status_counts,
                "ipos_by_platform": platform_counts,
                "avg_confidence": round(avg_confidence, 2),
                "total_with_drhp": total_drhp,
                "total_with_rhp": total_rhp,
                "drhp_processed": drhp_processed,
                "rhp_processed": rhp_processed,
                "unresolved_zip_links": unresolved_zips,
                "latest_scrape": {
                    "status": latest_scrape.status if latest_scrape else None,
                    "created_at": latest_scrape.created_at.isoformat() if latest_scrape else None,
                    "new_ipos_found": latest_scrape.new_ipos_found if latest_scrape else None,
                    "status_changes": latest_scrape.status_changes if latest_scrape else None,
                    "execution_time_ms": latest_scrape.execution_time_ms if latest_scrape else None,
                } if latest_scrape else None,
                "recent_scrapes": [
                    {"status": log.status, "action": log.action, "created_at": log.created_at.isoformat()}
                    for log in recent_scrapes
                ],
            }

    # ─── Scraper Logs ──────────────────────────────────────

    def log_scrape(
        self,
        scraper_type: str,
        action: str,
        status: str = "success",
        company_name: Optional[str] = None,
        message: Optional[str] = None,
        error_details: Optional[dict] = None,
        execution_time_ms: Optional[int] = None,
        new_ipos_found: Optional[int] = None,
        status_changes: Optional[int] = None,
    ) -> int:
        with self._session() as session:
            log = ScraperLog(
                scraper_type=scraper_type,
                action=action,
                status=status,
                company_name=company_name,
                message=message,
                error_details=error_details or {},
                execution_time_ms=execution_time_ms,
                new_ipos_found=new_ipos_found,
                status_changes=status_changes,
            )
            session.add(log)
            session.commit()
            return log.id

    def get_recent_logs(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._session() as session:
            logs = (
                session.query(ScraperLog)
                .order_by(ScraperLog.created_at.desc())
                .limit(limit)
                .all()
            )
            return [
                {
                    "id": log.id,
                    "scraper_type": log.scraper_type,
                    "action": log.action,
                    "status": log.status,
                    "company_name": log.company_name,
                    "message": log.message,
                    "error_details": log.error_details,
                    "execution_time_ms": log.execution_time_ms,
                    "new_ipos_found": log.new_ipos_found,
                    "status_changes": log.status_changes,
                    "created_at": log.created_at.isoformat(),
                }
                for log in logs
            ]

    # ─── Document CRUD ─────────────────────────────────────

    def upsert_document(
        self,
        ipo_id: int,
        doc_type: str,
        url: str,
        doc_version: int = 1,
    ) -> IPODocument:
        """Create or update a document record for an IPO."""
        with self._session() as session:
            existing = (
                session.query(IPODocument)
                .filter(
                    IPODocument.ipo_master_id == ipo_id,
                    IPODocument.doc_type == doc_type,
                    IPODocument.doc_version == doc_version,
                )
                .first()
            )
            if existing:
                if existing.url != url:
                    existing.url = url
                    existing.phase = "discovered"
                existing.last_updated = datetime.now(timezone.utc)
                doc = existing
            else:
                doc = IPODocument(
                    ipo_master_id=ipo_id,
                    doc_type=doc_type,
                    doc_version=doc_version,
                    url=url,
                    phase="discovered",
                )
                session.add(doc)
            session.commit()
            return doc

    def get_documents(self, ipo_id: int) -> list[dict[str, Any]]:
        """Get all documents for an IPO."""
        with self._session() as session:
            docs = (
                session.query(IPODocument)
                .filter(IPODocument.ipo_master_id == ipo_id)
                .order_by(IPODocument.doc_type, IPODocument.doc_version)
                .all()
            )
            return [
                {
                    "id": d.id,
                    "doc_type": d.doc_type,
                    "doc_version": d.doc_version,
                    "url": d.url,
                    "phase": d.phase,
                    "downloaded_at": d.downloaded_at.isoformat() if d.downloaded_at else None,
                    "parsed_at": d.parsed_at.isoformat() if d.parsed_at else None,
                    "published_at": d.published_at.isoformat() if d.published_at else None,
                    "confidence": d.confidence,
                }
                for d in docs
            ]

    def update_document_phase(
        self,
        doc_id: int,
        phase: str,
    ) -> bool:
        """Update document phase and set the corresponding timestamp."""
        with self._session() as session:
            doc = session.query(IPODocument).filter(IPODocument.id == doc_id).first()
            if not doc:
                return False
            doc.phase = phase
            now = datetime.now(timezone.utc)
            if phase == "downloaded":
                doc.downloaded_at = now
            elif phase == "parsed":
                doc.parsed_at = now
            elif phase == "published":
                doc.published_at = now
            doc.last_updated = now
            session.commit()
            return True

    def update_document_phase_by_ipo(
        self,
        ipo_id: int,
        doc_type: str,
        phase: str,
    ) -> bool:
        """Update document phase by IPO ID and doc type."""
        with self._session() as session:
            doc = (
                session.query(IPODocument)
                .filter(
                    IPODocument.ipo_master_id == ipo_id,
                    IPODocument.doc_type == doc_type,
                )
                .first()
            )
            if not doc:
                return False
            doc.phase = phase
            now = datetime.now(timezone.utc)
            if phase == "downloaded":
                doc.downloaded_at = now
            elif phase == "parsed":
                doc.parsed_at = now
            elif phase == "published":
                doc.published_at = now
            doc.last_updated = now
            session.commit()
            return True

    def delete_document(self, doc_id: int) -> bool:
        """Delete a document record."""
        with self._session() as session:
            doc = session.query(IPODocument).filter(IPODocument.id == doc_id).first()
            if not doc:
                return False
            session.delete(doc)
            session.commit()
            return True
