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

            total = query.count()
            query = query.order_by(IPOMaster.last_updated.desc())
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
                
                # Update fields
                for key, value in ipo_data.items():
                    if key.startswith("_") or key in ("id", "normalized_name", "first_seen"):
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
                record = IPOMaster(normalized_name=normalized_name, **{
                    k: v for k, v in ipo_data.items()
                    if k != "normalized_name" and not k.startswith("_")
                    and hasattr(IPOMaster, k)
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

    # ─── Dashboard Stats ───────────────────────────────────

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

            recent_scrapes = (
                session.query(ScraperLog)
                .order_by(ScraperLog.created_at.desc())
                .limit(5)
                .all()
            )

            # Latest scrape result
            latest_scrape = (
                session.query(ScraperLog)
                .filter(ScraperLog.action == "full_scrape")
                .order_by(ScraperLog.created_at.desc())
                .first()
            )

            # Platform distribution
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
