"""
SQLAlchemy ORM models for the IPO Aggregation Platform.
SQLite-backed now, swappable to PostgreSQL/Supabase via connection string.
"""
import os
import re
import json
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import (
    Column, String, Float, Integer, BigInteger,
    DateTime, ForeignKey, Text, create_engine, UniqueConstraint, Index,
    JSON as SaJSON,  # SQLAlchemy's JSON works with SQLite (stores as TEXT)
    TypeDecorator,
)
from sqlalchemy.dialects.sqlite import JSON as SqliteJson
from sqlalchemy.orm import DeclarativeBase, relationship
from sqlalchemy.orm import Mapped, mapped_column


def _get_db_path() -> str:
    """Get database path. Configurable via IPOS_DB_PATH env var."""
    env_path = os.getenv("IPOS_DB_PATH")
    if env_path:
        return env_path
    # Default: next to the project directory
    project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(project_dir, "ipos.db")


class Base(DeclarativeBase):
    pass


class IPOMaster(Base):
    """Central IPO record — one row per company."""
    __tablename__ = "ipo_master"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_name: Mapped[str] = mapped_column(String(500), nullable=False, index=True)
    normalized_name: Mapped[str] = mapped_column(String(500), nullable=False, unique=True, index=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="unknown", index=True)
    
    # Key dates
    drhp_filed_date: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    rhp_filed_date: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    fp_filed_date: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    open_date: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    close_date: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    
    # Price & platform
    price_band: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    platform: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # MainBoard, SME
    issue_type: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # IPO, FPO
    
    # Documents (URLs)
    drhp_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    rhp_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    final_prospectus_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    abridged_prospectus_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # Document processing status
    drhp_processed: Mapped[bool] = mapped_column(Integer, default=0)  # Phase 2: PDF parsed?
    rhp_processed: Mapped[bool] = mapped_column(Integer, default=0)
    
    # Confidence and metadata
    data_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    source_count: Mapped[int] = mapped_column(Integer, default=0)  # How many sources found this IPO

    # Lifecycle phase
    phase: Mapped[str] = mapped_column(String(20), default="discovered", index=True)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # ─── Unified extracted data (Phase A/B — the contract shipped to Next.js) ────
    # The single denormalized snapshot, fed by validate→unify after each parse.
    unified_data: Mapped[Optional[dict]] = mapped_column(SaJSON, nullable=True)
    # Per-field {doc_type, parsed_at, schema_version} so we can answer "where did this come from?"
    unified_provenance: Mapped[Optional[dict]] = mapped_column(SaJSON, nullable=True)
    # Bumped every time unified_data changes — Next.js can use this for cache busting.
    unified_version: Mapped[int] = mapped_column(Integer, default=0)
    unified_updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # ─── Publish gating (Phase B — validation outcome) ──────────────────────────
    # pending      — never parsed, nothing to publish yet
    # published    — confidence high enough, webhook fired (or will fire)
    # needs_review — validation flagged issues, do NOT publish
    # rejected     — manual override (user said no)
    publish_status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    confidence_score: Mapped[float] = mapped_column(Float, default=0.0)
    validation_issues: Mapped[Optional[list]] = mapped_column(SaJSON, nullable=True)

    # Full source data (JSON blobs for debugging)
    sebi_data: Mapped[Optional[dict]] = mapped_column(SaJSON, nullable=True)
    bse_data: Mapped[Optional[dict]] = mapped_column(SaJSON, nullable=True)
    nse_data: Mapped[Optional[dict]] = mapped_column(SaJSON, nullable=True)
    bse_sme_data: Mapped[Optional[dict]] = mapped_column(SaJSON, nullable=True)
    
    # Timestamps
    first_seen: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    last_updated: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    last_scraped: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    
    # Status history (one-to-many)
    status_changes = relationship("IPOStatusHistory", back_populates="ipo", cascade="all, delete-orphan",
                                   order_by="IPOStatusHistory.change_date.desc()")
    
    def __repr__(self):
        return f"<IPOMaster(id={self.id}, name='{self.company_name}', status='{self.status}')>"
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "company_name": self.company_name,
            "normalized_name": self.normalized_name,
            "status": self.status,
            "drhp_filed_date": self.drhp_filed_date,
            "rhp_filed_date": self.rhp_filed_date,
            "fp_filed_date": self.fp_filed_date,
            "open_date": self.open_date,
            "close_date": self.close_date,
            "price_band": self.price_band,
            "platform": self.platform,
            "issue_type": self.issue_type,
            "documents": {
                "drhp": self.drhp_url,
                "rhp": self.rhp_url,
                "final_prospectus": self.final_prospectus_url,
                "abridged_prospectus": self.abridged_prospectus_url,
            },
            "drhp_processed": bool(self.drhp_processed),
            "rhp_processed": bool(self.rhp_processed),
            "data_confidence": self.data_confidence,
            "source_count": self.source_count,
            "publish_status": self.publish_status,
            "confidence_score": self.confidence_score,
            "validation_issues": self.validation_issues,
            "unified_version": self.unified_version,
            "unified_updated_at": self.unified_updated_at.isoformat() if self.unified_updated_at else None,
            "first_seen": self.first_seen.isoformat() if self.first_seen else None,
            "last_updated": self.last_updated.isoformat() if self.last_updated else None,
            "last_scraped": self.last_scraped.isoformat() if self.last_scraped else None,
        }


class IPOStatusHistory(Base):
    """Tracks every status change for audit trail."""
    __tablename__ = "ipo_status_history"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ipo_master_id: Mapped[int] = mapped_column(Integer, ForeignKey("ipo_master.id", ondelete="CASCADE"), nullable=False, index=True)
    old_status: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    new_status: Mapped[str] = mapped_column(String(50), nullable=False)
    change_date: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    source: Mapped[str] = mapped_column(String(20), nullable=False)  # sebi, bse, nse, bse_sme, manual
    triggered_by: Mapped[str] = mapped_column(String(20), nullable=False, default="system")  # cron, webhook, manual, system
    details: Mapped[Optional[dict]] = mapped_column(SaJSON, nullable=True)
    
    ipo = relationship("IPOMaster", back_populates="status_changes")
    
    def __repr__(self):
        return f"<StatusChange {self.old_status} → {self.new_status}>"


class ScraperLog(Base):
    """Tracks every scraper run for debugging."""
    __tablename__ = "scraper_logs"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scraper_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)  # sebi, bse, nse, bse_sme, aggregator
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="success", index=True)  # success, error, warning, started
    company_name: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error_details: Mapped[Optional[dict]] = mapped_column(SaJSON, nullable=True)
    execution_time_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    new_ipos_found: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    status_changes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)


class IPODocument(Base):
    """Individual document records for an IPO (drhp, rhp, prospectus)."""
    __tablename__ = "ipo_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ipo_master_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("ipo_master.id", ondelete="CASCADE"),
        nullable=False, index=True
    )
    doc_type: Mapped[str] = mapped_column(String(50), nullable=False)  # drhp, rhp, final_prospectus
    doc_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    phase: Mapped[str] = mapped_column(String(20), nullable=False, default="discovered")  # discovered, downloading, downloaded, parsing, parsed, published
    downloaded_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    parsed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.8)

    # Timestamps
    last_updated: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc)
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    ipo = relationship("IPOMaster", backref="documents")

    def __repr__(self):
        return f"<IPODocument(id={self.id}, ipo={self.ipo_master_id}, type='{self.doc_type}', phase='{self.phase}')>"


class IPOParsedData(Base):
    """Phase 2: Extracted data from PDFs."""
    __tablename__ = "ipo_parsed_data"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ipo_master_id: Mapped[int] = mapped_column(Integer, ForeignKey("ipo_master.id", ondelete="CASCADE"), nullable=False, index=True)
    data_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)  # financial, promoter, business, risk, key_terms
    extracted_data: Mapped[dict] = mapped_column(SaJSON, nullable=False)
    confidence_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    extraction_date: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    extra: Mapped[Optional[dict]] = mapped_column(SaJSON, nullable=True)
    processing_time_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)


class DocumentSection(Base):
    """Extracted sections from PDFs (ToC-based)."""
    __tablename__ = "document_sections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ipo_master_id: Mapped[int] = mapped_column(Integer, ForeignKey("ipo_master.id", ondelete="CASCADE"), nullable=False, index=True)
    doc_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    section_name: Mapped[str] = mapped_column(String(100), nullable=False)
    page_start: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    page_end: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    char_count: Mapped[int] = mapped_column(Integer, default=0)
    raw_md: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Content hash of raw_md. Compared against parsed_md_sha256 to gate
    # Firecrawl re-calls when content hasn't changed.
    raw_md_sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    # Hash of the raw_md that produced the current parsed_data.
    parsed_md_sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    parsed_data: Mapped[Optional[dict]] = mapped_column(SaJSON, nullable=True)
    parsed: Mapped[bool] = mapped_column(Integer, default=0)
    parsed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    last_updated: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc),
                                                    onupdate=lambda: datetime.now(timezone.utc))


class BackgroundTask(Base):
    """In-flight or completed background job (scrape, resolve, parse).

    Persisted so client polls survive process restarts. Cleared by
    task_manager when the cache exceeds max_tasks.
    """
    __tablename__ = "background_tasks"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(500), default="")
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    progress_label: Mapped[str] = mapped_column(String(500), default="")
    message: Mapped[str] = mapped_column(Text, default="")
    result_json: Mapped[Optional[dict]] = mapped_column(SaJSON, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[float] = mapped_column(Float, index=True)
    started_at: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    completed_at: Mapped[Optional[float]] = mapped_column(Float, nullable=True)


# ─── Engine and Session ──────────────────────────────────────────

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

_engine = None
_SessionLocal = None


def _resolve_db_url(db_path: Optional[str] = None) -> str:
    """Pick the DB connection URL.

    Precedence:
      1. Explicit db_path arg → sqlite:///<path>   (legacy hook for tests)
      2. app.config.settings.db_url               (DATABASE_URL → Postgres, else SQLite)
    """
    if db_path:
        return f"sqlite:///{db_path}"
    try:
        from app.config import settings
        return settings.db_url
    except Exception:
        # Bootstrap path (config not importable yet, e.g. during alembic init).
        return f"sqlite:///{_get_db_path()}"


def _engine_kwargs(url: str) -> dict:
    """Per-dialect engine tuning."""
    if url.startswith("sqlite"):
        return {"connect_args": {"check_same_thread": False}}
    if url.startswith("postgresql"):
        # `prepare_threshold=None` disables psycopg3's auto-prepared-statement
        # cache. Required for Supabase's transaction-mode pooler (port 6543)
        # which doesn't preserve session state between requests and chokes on
        # re-used prepared statement names ("_pg3_0 already exists").
        return {
            "pool_pre_ping": True,
            "pool_size": 5,
            "max_overflow": 5,
            "connect_args": {"prepare_threshold": None},
        }
    return {}


def get_engine(db_path: Optional[str] = None):
    global _engine
    if _engine is None:
        url = _resolve_db_url(db_path)
        # Ensure psycopg v3 is used (installed as 'psycopg', not 'psycopg2').
        if url.startswith("postgresql://") or url.startswith("postgres://"):
            url = url.replace("postgresql://", "postgresql+psycopg://", 1)
            url = url.replace("postgres://", "postgresql+psycopg://", 1)
        _engine = create_engine(url, echo=False, **_engine_kwargs(url))
    return _engine


def get_session(db_path: Optional[str] = None) -> Session:
    global _SessionLocal
    if _SessionLocal is None:
        engine = get_engine(db_path)
        _SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    return _SessionLocal()


def init_db(db_path: Optional[str] = None):
    """Create all tables. Safe to call multiple times.

    Skipped for PostgreSQL (Supabase) — tables are managed by Alembic migrations
    and calling create_all() via PgBouncer transaction-mode pooler hangs.
    """
    engine = get_engine(db_path)
    url = str(engine.url)
    if "postgresql" in url or "postgres" in url:
        return  # Tables already exist in Supabase; use alembic for migrations
    Base.metadata.create_all(engine)
