from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


DocumentType = Literal["DRHP", "RHP"]
Source = Literal["sebi", "bse", "nse"]


class DocumentUrls(BaseModel):
    detail_page: Optional[str] = None
    drhp_pdf: Optional[str] = None
    rhp_pdf: Optional[str] = None
    abridged_prospectus_pdf: Optional[str] = None


class BSEData(BaseModel):
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


class NSEData(BaseModel):
    company_name: str
    raw: dict[str, Any] = Field(default_factory=dict)


class IPORecord(BaseModel):
    company_name: str
    filing_date: Optional[str] = None
    source: Source
    document_type: Optional[DocumentType] = None
    document_urls: Optional[DocumentUrls] = None
    bse_data: Optional[BSEData] = None
    nse_data: Optional[NSEData] = None


class Pagination(BaseModel):
    total_records: int
    current_page: int
    per_page: int
    total_pages: int


class Meta(BaseModel):
    sources_queried: list[str]
    errors: list[dict[str, str]] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class IPOResponse(BaseModel):
    data: list[IPORecord]
    pagination: Pagination
    meta: Meta
