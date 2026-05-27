from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


DocumentType = Literal["DRHP", "RHP"]
Source = Literal["sebi", "bse", "nse"]


# ─── Source-specific Internal Models ────────────────────────


class DocumentUrls(BaseModel):
    """URLs for a specific document from SEBI."""
    detail_page: Optional[str] = None
    drhp_pdf: Optional[str] = None
    rhp_pdf: Optional[str] = None
    abridged_prospectus_pdf: Optional[str] = None


class BSEData(BaseModel):
    """IPO metadata from BSE API."""
    scrip_cd: Optional[int] = None
    company_name: str
    long_name: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    price_band: Optional[str] = None
    face_value: Optional[float] = None
    issue_type: Optional[str] = None
    platform: Optional[str] = None
    status: Optional[str] = None
    ipo_no: Optional[int] = None


class NSEDocumentAttachment(BaseModel):
    """A single document attachment from NSE."""
    url: Optional[str] = None
    file_size: Optional[str] = None
    is_zip: bool = False
    resolved_pdf_url: Optional[str] = None


class NSEData(BaseModel):
    """IPO offer document data from NSE's /api/corporates/offerdocs."""
    company_name: str
    symbol: Optional[str] = None
    isin: Optional[str] = None
    pan_no: Optional[str] = None
    index: Optional[str] = None
    drhp: Optional[str] = None
    drhp_date: Optional[str] = None
    drhp_status: Optional[str] = None
    drhp_attach: Optional[NSEDocumentAttachment] = None
    drhp_sub_date: Optional[str] = None
    drhp_av_link: Optional[str] = None
    rhp: Optional[str] = None
    rhp_date: Optional[str] = None
    rhp_attach: Optional[NSEDocumentAttachment] = None
    rhp_sub_date: Optional[str] = None
    rhp_av_link: Optional[str] = None
    fp: Optional[str] = None
    fp_date: Optional[str] = None
    fp_attach: Optional[NSEDocumentAttachment] = None
    fp_sub_date: Optional[str] = None
    fp_av_link: Optional[str] = None
    adv: Optional[str] = None
    adv_date: Optional[str] = None
    adv_attach: Optional[NSEDocumentAttachment] = None
    iap_sub_date: Optional[str] = None
    iap_av_link: Optional[str] = None
    ic_sub_date: Optional[str] = None
    ic_av_link: Optional[str] = None
    issue_open_date: Optional[str] = None
    issue_close_date: Optional[str] = None
    ipo_inprincipal_xbrl_link: Optional[str] = None
    ipo_inlisting_xbrl_link: Optional[str] = None
    raw: dict[str, Any] = Field(default_factory=dict)


class IPORecord(BaseModel):
    """Internal merged record (combined from SEBI + BSE + NSE)."""
    company_name: str
    filing_date: Optional[str] = None
    source: Source
    document_type: Optional[DocumentType] = None
    document_urls: Optional[DocumentUrls] = None
    bse_data: Optional[BSEData] = None
    nse_data: Optional[NSEData] = None
    bse_sme_doc: Optional['BSESMEDocument'] = None


class BSESMEDocument(BaseModel):
    """Document scraped from bsesme.com for SME IPOs."""
    company_name: str
    date: Optional[str] = None
    document_type: str
    document_url: str
    is_zip: bool = False


# ─── Clean Public Response Models ───────────────────────────


class IPOSummarySource(BaseModel):
    """Raw source data (returned when raw=true)."""
    sebi: Optional[DocumentUrls] = None
    bse: Optional[BSEData] = None
    nse: Optional[NSEData] = None
    bse_sme: Optional[BSESMEDocument] = None


class IPOSummary(BaseModel):
    """Clean, unified IPO record for the frontend."""
    id: int
    company_name: str
    status: str = 'unknown'
    dates: dict[str, Optional[str]] = Field(default_factory=dict)
    documents: dict[str, Optional[str]] = Field(default_factory=dict)
    documents_processed: dict[str, bool] = Field(default_factory=dict)
    price_band: Optional[str] = None
    platform: Optional[str] = None
    issue_type: Optional[str] = None
    # Only present when raw=true
    raw: Optional[IPOSummarySource] = None


class Pagination(BaseModel):
    total_records: int
    current_page: int
    per_page: int
    total_pages: int


class StatusChangeItem(BaseModel):
    """A single status change record."""
    ipo_id: int
    company_name: str
    old_status: Optional[str] = None
    new_status: str
    change_date: str
    source: str
    triggered_by: str


class ScraperLogItem(BaseModel):
    """A single scraper log entry."""
    id: int
    scraper_type: str
    action: str
    status: str
    company_name: Optional[str] = None
    message: Optional[str] = None
    error_details: Optional[dict] = None
    execution_time_ms: Optional[int] = None
    new_ipos_found: Optional[int] = None
    status_changes: Optional[int] = None
    created_at: str


class RefreshResult(BaseModel):
    """Result of a scrape refresh."""
    status: str
    total_raw: int
    total_unique: int
    new_ipos_found: int
    status_changes_detected: int
    execution_time_ms: int
    errors: list[dict[str, str]] = Field(default_factory=list)


class DocumentTextInfo(BaseModel):
    """Extracted text info for a document."""
    processed: bool = False
    char_count: int = 0
    source_url: Optional[str] = None
    text_preview: Optional[str] = None
    extraction_date: Optional[str] = None


class StatusHistoryEntry(BaseModel):
    """A single status change record (without IPO context — used inside IPO detail)."""
    id: int
    old_status: Optional[str] = None
    new_status: str
    change_date: str
    source: str
    triggered_by: str
    details: Optional[dict] = None


class IPODetail(BaseModel):
    """Complete IPO detail including status history and document texts."""
    id: int
    company_name: str
    normalized_name: str
    status: str
    dates: dict[str, Optional[str]] = Field(default_factory=dict)
    documents: dict[str, Optional[str]] = Field(default_factory=dict)
    documents_processed: dict[str, bool] = Field(default_factory=dict)
    price_band: Optional[str] = None
    platform: Optional[str] = None
    issue_type: Optional[str] = None
    data_confidence: float = 0.0
    source_count: int = 0
    first_seen: Optional[str] = None
    last_updated: Optional[str] = None
    last_scraped: Optional[str] = None
    raw: Optional[IPOSummarySource] = None
    status_history: list[StatusHistoryEntry] = Field(default_factory=list)
    document_texts: dict[str, DocumentTextInfo] = Field(default_factory=dict)


class Meta(BaseModel):
    sources_queried: list[str]
    errors: list[dict[str, str]] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class IPOResponse(BaseModel):
    data: list[IPOSummary]
    pagination: Pagination
    meta: Meta