from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


DocumentType = Literal["DRHP", "RHP"]
Source = Literal["sebi", "bse", "nse", "upstox"]


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
    upstox_data: Optional['UpstoxData'] = None


class BSESMEDocument(BaseModel):
    """Document scraped from bsesme.com for SME IPOs."""
    company_name: str
    date: Optional[str] = None
    document_type: str
    document_url: str
    is_zip: bool = False


class UpstoxData(BaseModel):
    """All fields from Upstox IPO Detail API response."""
    id: str = ""
    symbol: Optional[str] = None
    name: str = ""
    status: str = "unknown"
    isin: Optional[str] = None
    issue_type: Optional[str] = None
    issue_size: Optional[float] = None
    industry: Optional[str] = None
    minimum_price: Optional[float] = None
    maximum_price: Optional[float] = None
    bidding_start_date: Optional[str] = None
    bidding_end_date: Optional[str] = None
    total_subscription: Optional[str] = None
    daily_start_time: Optional[str] = None
    daily_end_time: Optional[str] = None
    face_value: Optional[float] = None
    tick_size: Optional[float] = None
    lot_size: Optional[int] = None
    minimum_quantity: Optional[int] = None
    cut_off_price: Optional[float] = None
    listing_price: Optional[float] = None
    listing_exchange: Optional[str] = None
    drhp_url: Optional[str] = None
    rhp_url: Optional[str] = None
    timeline: Optional[dict] = None
    registrar_info: Optional[dict] = None


# ─── Clean Public Response Models ───────────────────────────


class IPOSummarySource(BaseModel):
    sebi: Optional[DocumentUrls] = None
    bse: Optional[BSEData] = None
    nse: Optional[NSEData] = None
    bse_sme: Optional[BSESMEDocument] = None
    upstox: Optional[UpstoxData] = None


class IPOSummary(BaseModel):
    id: int
    company_name: str
    status: str = 'unknown'
    dates: dict[str, Optional[str]] = Field(default_factory=dict)
    documents: dict[str, Optional[str]] = Field(default_factory=dict)
    documents_processed: dict[str, bool] = Field(default_factory=dict)
    price_band: Optional[str] = None
    platform: Optional[str] = None
    issue_type: Optional[str] = None
    upstox_data: Optional[UpstoxData] = None
    raw: Optional[IPOSummarySource] = None


class Pagination(BaseModel):
    total_records: int
    current_page: int
    per_page: int
    total_pages: int


class StatusChangeItem(BaseModel):
    ipo_id: int
    company_name: str
    old_status: Optional[str] = None
    new_status: str
    change_date: str
    source: str
    triggered_by: str


class ScraperLogItem(BaseModel):
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
    status: str
    total_raw: int
    total_unique: int
    new_ipos_found: int
    status_changes_detected: int
    execution_time_ms: int
    errors: list[dict[str, str]] = Field(default_factory=list)


class DocumentTextInfo(BaseModel):
    processed: bool = False
    char_count: int = 0
    source_url: Optional[str] = None
    text_preview: Optional[str] = None
    extraction_date: Optional[str] = None


class StatusHistoryEntry(BaseModel):
    id: int
    old_status: Optional[str] = None
    new_status: str
    change_date: str
    source: str
    triggered_by: str
    details: Optional[dict] = None


class IPODetail(BaseModel):
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
