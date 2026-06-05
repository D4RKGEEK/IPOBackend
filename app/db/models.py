"""SQLAlchemy ORM models for the IPO Aggregation Platform."""
import json
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import (
    Column, String, Float, Integer, BigInteger,
    DateTime, ForeignKey, Text, UniqueConstraint, Index,
    JSON as SaJSON,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .engine import Base, utcnow


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
    platform: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    issue_type: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)

    # Documents (URLs)
    drhp_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    rhp_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    final_prospectus_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    abridged_prospectus_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Document processing status
    drhp_processed: Mapped[bool] = mapped_column(Integer, default=0)
    rhp_processed: Mapped[bool] = mapped_column(Integer, default=0)

    # Data quality
    data_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    source_count: Mapped[int] = mapped_column(Integer, default=0)
    phase: Mapped[str] = mapped_column(String(20), default="discovered")
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Identity resolution
    source_ids: Mapped[Optional[dict]] = mapped_column(SaJSON, nullable=True)

    # Per-field provenance
    field_provenance: Mapped[Optional[dict]] = mapped_column(SaJSON, nullable=True)

    # Subscription data
    subscription_latest: Mapped[Optional[dict]] = mapped_column(SaJSON, nullable=True)

    # Unified data
    unified_data: Mapped[Optional[dict]] = mapped_column(SaJSON, nullable=True)
    unified_provenance: Mapped[Optional[dict]] = mapped_column(SaJSON, nullable=True)
    unified_version: Mapped[int] = mapped_column(Integer, default=0)
    unified_updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Publishing
    publish_status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    confidence_score: Mapped[float] = mapped_column(Float, default=0.0)
    validation_issues: Mapped[Optional[list]] = mapped_column(SaJSON, nullable=True)

    # Raw source data (JSON blobs)
    sebi_data: Mapped[Optional[dict]] = mapped_column(SaJSON, nullable=True)
    bse_data: Mapped[Optional[dict]] = mapped_column(SaJSON, nullable=True)
    nse_data: Mapped[Optional[dict]] = mapped_column(SaJSON, nullable=True)
    bse_sme_data: Mapped[Optional[dict]] = mapped_column(SaJSON, nullable=True)
    upstox_data: Mapped[Optional[dict]] = mapped_column(SaJSON, nullable=True)

    # Timestamps
    first_seen: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_updated: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    last_scraped: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Relationships
    status_changes = relationship("IPOStatusHistory", back_populates="ipo",
                                   cascade="all, delete-orphan",
                                   order_by="IPOStatusHistory.change_date.desc()")

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
            "publish_status": self.publish_status,
            "unified_version": self.unified_version,
            "unified_updated_at": self.unified_updated_at.isoformat() if self.unified_updated_at else None,
            "upstox_data": self.upstox_data,
            "source_ids": self.source_ids,
            "field_provenance": self.field_provenance,
            "subscription_latest": self.subscription_latest,
            "first_seen": self.first_seen.isoformat() if self.first_seen else None,
            "last_updated": self.last_updated.isoformat() if self.last_updated else None,
        }


class IPOStatusHistory(Base):
    """Tracks every status change for audit trail."""
    __tablename__ = "ipo_status_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ipo_master_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("ipo_master.id", ondelete="CASCADE"), nullable=False, index=True)
    old_status: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    new_status: Mapped[str] = mapped_column(String(50), nullable=False)
    change_date: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    triggered_by: Mapped[str] = mapped_column(String(20), nullable=False, default="system")
    details: Mapped[Optional[dict]] = mapped_column(SaJSON, nullable=True)

    ipo = relationship("IPOMaster", back_populates="status_changes")


class IPODocument(Base):
    """Individual document records for an IPO."""
    __tablename__ = "ipo_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ipo_master_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("ipo_master.id", ondelete="CASCADE"), nullable=False, index=True)
    doc_type: Mapped[str] = mapped_column(String(50), nullable=False)
    doc_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    phase: Mapped[str] = mapped_column(String(20), nullable=False, default="discovered")
    downloaded_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    parsed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.8)
    last_updated: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    ipo = relationship("IPOMaster", backref="documents")


class IPOParsedData(Base):
    """Extracted data from PDFs."""
    __tablename__ = "ipo_parsed_data"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ipo_master_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("ipo_master.id", ondelete="CASCADE"), nullable=False, index=True)
    data_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    extracted_data: Mapped[dict] = mapped_column(SaJSON, nullable=False)
    confidence_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    extraction_date: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    extra: Mapped[Optional[dict]] = mapped_column(SaJSON, nullable=True)
    processing_time_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)


class DocumentSection(Base):
    """Extracted sections from PDFs (ToC-based)."""
    __tablename__ = "document_sections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ipo_master_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("ipo_master.id", ondelete="CASCADE"), nullable=False, index=True)
    doc_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    section_name: Mapped[str] = mapped_column(String(100), nullable=False)
    page_start: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    page_end: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    char_count: Mapped[int] = mapped_column(Integer, default=0)
    raw_md: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    raw_md_sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    parsed_md_sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    parsed_data: Mapped[Optional[dict]] = mapped_column(SaJSON, nullable=True)
    parsed: Mapped[bool] = mapped_column(Integer, default=0)
    parsed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_updated: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class DocumentTable(Base):
    """Tables extracted from PDF pages during resolve."""
    __tablename__ = "document_tables"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ipo_master_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("ipo_master.id", ondelete="CASCADE"), nullable=False, index=True)
    doc_type: Mapped[str] = mapped_column(String(20), nullable=False)
    section_name: Mapped[str] = mapped_column(String(100), nullable=False)
    page_num: Mapped[int] = mapped_column(Integer, nullable=False)
    table_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    table_data: Mapped[dict] = mapped_column(SaJSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class BackgroundTask(Base):
    """In-flight or completed background job."""
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


class ScraperLog(Base):
    """Tracks every scraper run for debugging."""
    __tablename__ = "scraper_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scraper_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="success", index=True)
    company_name: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error_details: Mapped[Optional[dict]] = mapped_column(SaJSON, nullable=True)
    execution_time_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    new_ipos_found: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    status_changes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class IPOHistoricalPrice(Base):
    """Daily historical candle data for listed IPOs — one row per IPO, upserted daily."""
    __tablename__ = "ipo_historical_prices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ipo_master_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("ipo_master.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)

    # Source identification
    isin: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    exchange_type: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)  # NSE_EQ | BSE_EQ

    # Summary — most recent candle snapshot
    open: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    high: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    low: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    close: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    volume: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    prev_close: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    change_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    color: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 1=bullish, -1=bearish, 0=flat
    num_candles: Mapped[int] = mapped_column(Integer, default=0)

    # Full candle array for charting
    candles: Mapped[Optional[list]] = mapped_column(SaJSON, nullable=True)

    # Metadata
    fetch_date: Mapped[str] = mapped_column(String(20), nullable=False)  # YYYY-MM-DD of latest candle
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_updated: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    # Relationship
    ipo = relationship("IPOMaster", backref="historical_price", uselist=False)
