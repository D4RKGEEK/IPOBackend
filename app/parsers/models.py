"""
Pydantic models for all extracted IPO data.
Every field has a default so the API never returns null — empty strings/lists/0 instead.
"""
from pydantic import BaseModel, Field
from typing import Optional


class FinancialYearData(BaseModel):
    """Financial data for a single year."""
    year: str = ""
    revenue: float = 0.0
    revenue_label: str = ""
    total_expenses: float = 0.0
    profit_after_tax: float = 0.0
    total_assets: float = 0.0
    net_worth: float = 0.0
    total_borrowings: float = 0.0
    eps_basic: float = 0.0
    eps_diluted: float = 0.0
    nav_per_share: float = 0.0
    revenue_growth_pct: float = 0.0
    pat_margin_pct: float = 0.0


class FinancialSummary(BaseModel):
    """Restated financial information."""
    has_data: bool = False
    years: list[FinancialYearData] = Field(default_factory=list)
    revenue_cagr_pct: float = 0.0
    pat_cagr_pct: float = 0.0
    latest_revenue: float = 0.0
    latest_pat: float = 0.0
    latest_assets: float = 0.0
    latest_net_worth: float = 0.0


class PromoterInfo(BaseModel):
    """A single promoter."""
    name: str = ""
    shareholding_before_pct: float = 0.0
    shareholding_after_pct: float = 0.0


class CapitalStructure(BaseModel):
    """Capital structure details."""
    face_value: float = 0.0
    face_value_currency: str = "₹"
    fresh_issue_shares: int = 0
    fresh_issue_amount_cr: float = 0.0
    offer_for_sale_shares: int = 0
    offer_for_sale_amount_cr: float = 0.0
    total_issue_shares: int = 0
    total_issue_amount_cr: float = 0.0
    pre_issue_shares: int = 0
    pre_issue_capital_cr: float = 0.0
    post_issue_shares: int = 0
    post_issue_capital_cr: float = 0.0
    issue_is_fresh_only: bool = False
    issue_is_ofs_only: bool = False
    is_fixed_price: bool = False
    is_book_built: bool = False


class PriceInfo(BaseModel):
    """Price band and valuation."""
    has_price_band: bool = False
    floor_price: float = 0.0
    cap_price: float = 0.0
    issue_price: float = 0.0
    price_band_lower: float = 0.0
    price_band_upper: float = 0.0
    lot_size: int = 0
    min_lot_amount: float = 0.0
    eps_weighted: float = 0.0
    pe_ratio: float = 0.0
    roe_pct: float = 0.0
    nav_per_share: float = 0.0


class IssueDates(BaseModel):
    """Key dates related to the issue."""
    drhp_date: str = ""
    rhp_date: str = ""
    bid_open_date: str = ""
    bid_close_date: str = ""
    allotment_date: str = ""
    listing_date: str = ""
    pay_in_date: str = ""
    basis_of_allotment_date: str = ""
    upi_mandate_cutoff: str = ""


class Intermediaries(BaseModel):
    """Key intermediaries involved in the issue."""
    brlms: list[str] = Field(default_factory=list)
    registrar: str = ""
    registrar_website: str = ""
    bankers: list[str] = Field(default_factory=list)
    legal_advisors: list[str] = Field(default_factory=list)
    auditors: list[str] = Field(default_factory=list)
    market_maker: str = ""


class ObjectsOfIssue(BaseModel):
    """How the funds will be used."""
    purposes: list[dict[str, str]] = Field(default_factory=list)
    total_project_cost_cr: float = 0.0
    means_of_finance: list[dict[str, str]] = Field(default_factory=list)


class RiskFactors(BaseModel):
    """Key risk factors."""
    risks: list[str] = Field(default_factory=list)


class IndustryInfo(BaseModel):
    """Industry and business overview."""
    industry: str = ""
    business_description: str = ""
    revenue_model: str = ""
    competitors: list[str] = Field(default_factory=list)


class ParsedIPOResult(BaseModel):
    """Complete parsed data for one IPO document."""
    company_name: str = ""
    cin: str = ""
    registered_address: str = ""
    website: str = ""
    email: str = ""
    telephone: str = ""
    year_of_incorporation: str = ""
    status: str = "pending"  # pending, completed, partial, failed
    
    issue: CapitalStructure = Field(default_factory=CapitalStructure)
    price: PriceInfo = Field(default_factory=PriceInfo)
    dates: IssueDates = Field(default_factory=IssueDates)
    intermediaries: Intermediaries = Field(default_factory=Intermediaries)
    financials: FinancialSummary = Field(default_factory=FinancialSummary)
    objects_of_issue: ObjectsOfIssue = Field(default_factory=ObjectsOfIssue)
    
    promoters: list[PromoterInfo] = Field(default_factory=list)
    risk_factors: RiskFactors = Field(default_factory=RiskFactors)
    industry: IndustryInfo = Field(default_factory=IndustryInfo)
    
    confidence_score: float = 0.0
    parsing_time_ms: int = 0
    document_type: str = ""  # drhp, rhp, final_prospectus
    extraction_version: str = "2.0"
