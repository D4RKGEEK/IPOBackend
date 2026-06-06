"""
Per-section JSON schemas for Firecrawl LLM extraction.

Each entry is keyed by the canonical section_name (matching what
section_resolver writes to document_sections.section_name).

Schema design rules:
 - Keep field count manageable (8-15 per section).
 - Use string for amounts/numbers so we can preserve units like "1,234 cr".
 - Use array of strings for lists (years, names, items).
 - `description` in each property guides the LLM to find the right value.
 - All fields are optional. The LLM should return empty string when not found.

Schema versioning:
 - Bump SCHEMA_VERSION when you ADD a new section or CHANGE an existing one's
   field list. Existing parsed_data rows tagged with an older version are
   flagged for re-parse on the next cycle. Old fields stay in unified_data
   until overwritten by a fresh parse.
"""
from __future__ import annotations

from typing import Any


# Bump when you change SECTION_SCHEMAS shape.
SCHEMA_VERSION = 3


# Shared instruction prefix added to every per-section prompt
COMMON_INSTRUCTION = (
    "You are extracting structured data from one section of an Indian IPO prospectus "
    "(DRHP / RHP / Final Prospectus). Return ONLY the requested JSON fields. "
    "Use empty string \"\" for missing text and [] for missing arrays. Never return null. "
    "Preserve units (e.g. 'Rs. 1,234 crore', '12.45%') in their original form. "
    "For PIPE-SEPARATED financial fields, output: 'Period Label: amount | Period Label: amount'. Example: total_revenue: 'Dec 31, 2025: 2675.87 | Mar 31, 2025: 3249.29'. Include ALL periods."
)


SECTION_SCHEMAS: dict[str, dict[str, Any]] = {
    # ──────────────────────────────────────────────────────────────
    "GENERAL_INFORMATION": {
        "type": "object",
        "properties": {
            # Company identity
            "cin": {"type": "string", "description": "Corporate Identification Number — 21-char alphanumeric like L12345AB1234ABC123456."},
            "registered_address": {"type": "string", "description": "Full registered office address."},
            "telephone": {"type": "string", "description": "Main contact phone number (with country code if shown)."},
            "email": {"type": "string", "description": "Investor relations or general contact email."},
            "website": {"type": "string", "description": "Company website URL."},
            "sector": {"type": "string", "description": "Industry sector / business segment the company operates in (e.g. 'IT - Software', 'Pharmaceuticals', 'Textiles')."},

            # Issue intermediaries
            "brlm_name": {"type": "string", "description": "Book Running Lead Manager(s). Comma-separated if multiple."},
            "brlm_phone": {"type": "string", "description": "BRLM contact phone number."},
            "brlm_email": {"type": "string", "description": "BRLM contact email address."},
            "registrar_name": {"type": "string", "description": "Registrar to the Issue / Registrar and Share Transfer Agent."},
            "registrar_phone": {"type": "string", "description": "Registrar contact phone number."},
            "registrar_email": {"type": "string", "description": "Registrar contact email address."},
            "registrar_website": {"type": "string", "description": "Registrar website URL."},
            "statutory_auditor": {"type": "string", "description": "Name of statutory auditor firm."},
            "legal_advisor": {"type": "string", "description": "Legal counsel to the issuer."},
            "cfo_name": {"type": "string", "description": "Chief Financial Officer's full name."},
            "company_secretary_name": {"type": "string", "description": "Company Secretary's full name."},

            # Issue details
            "listing_exchange": {"type": "string", "description": "Stock exchange where equity shares will be listed (e.g. 'NSE Emerge', 'BSE SME', 'NSE', 'BSE')."},
            "employee_discount": {"type": "string", "description": "Employee discount on the Issue Price, if any (e.g. 'up to 5%', 'Rs. 10 per share')."},
            "sale_type": {"type": "string", "description": "Type of issue: 'Fresh Issue', 'Offer for Sale', or 'Fresh + OFS'."},

            # Capital structure (some RHPs put this inside GENERAL INFORMATION)
            "authorized_shares": {"type": "string", "description": "Total authorized share capital in number of shares."},
            "authorized_amount": {"type": "string", "description": "Total authorized share capital in rupees (e.g. 'Rs. 1,000 crore')."},
            "paid_up_shares": {"type": "string", "description": "Issued, subscribed and paid-up share capital in number of shares."},
            "paid_up_amount": {"type": "string", "description": "Issued, subscribed and paid-up share capital in rupees."},
            "face_value": {"type": "string", "description": "Face value per equity share (e.g. 'Rs. 10', 'Rs. 5', 'Rs. 1')."},
            "pre_issue_shares": {"type": "string", "description": "Total equity shares outstanding BEFORE the offer."},
            "post_issue_shares": {"type": "string", "description": "Total equity shares outstanding AFTER the offer."},
            "fresh_issue_shares": {"type": "string", "description": "Number of equity shares in the fresh issue portion."},
            "offer_for_sale_shares": {"type": "string", "description": "Number of equity shares in the offer-for-sale (OFS) portion."},
            "market_maker_shares": {"type": "string", "description": "Shares reserved for the Market Maker."},
            "employee_shares": {"type": "string", "description": "Shares reserved for the Employee Reservation Portion."},
            "net_issue_to_public": {"type": "string", "description": "Net Issue to Public (total issue minus reservations)."},
            "qib_shares": {"type": "string", "description": "Shares reserved for Qualified Institutional Buyers."},
            "nii_shares": {"type": "string", "description": "Shares reserved for Non-Institutional Investors (NII / HNI)."},
            "retail_shares": {"type": "string", "description": "Shares reserved for Retail Individual Investors (RII)."},
            "anchor_shares": {"type": "string", "description": "Shares reserved for Anchor Investors."},
            "qib_ex_anchor_shares": {"type": "string", "description": "QIB shares remaining after Anchor allocation."},
            "bhnii_shares": {"type": "string", "description": "Big HNI shares (> ₹10 lakh bid, bNII)."},
            "shnii_shares": {"type": "string", "description": "Small HNI shares (₹2L-₹10L bid, sNII)."},
            "qib_percent": {"type": "string", "description": "QIB portion as percentage of net issue."},
            "nii_percent": {"type": "string", "description": "NII/HNI portion as percentage of net issue."},
            "retail_percent": {"type": "string", "description": "Retail portion as percentage of net issue."},
        },
    },

    # ──────────────────────────────────────────────────────────────
    "CAPITAL_STRUCTURE": {
        "type": "object",
        "properties": {
            "authorized_shares": {"type": "string", "description": "Total authorized share capital in number of shares."},
            "authorized_amount": {"type": "string", "description": "Total authorized share capital in rupees (e.g. 'Rs. 1,000 crore')."},
            "paid_up_shares": {"type": "string", "description": "Issued, subscribed and paid-up share capital in number of shares."},
            "paid_up_amount": {"type": "string", "description": "Issued, subscribed and paid-up share capital in rupees."},
            "face_value": {"type": "string", "description": "Face value per equity share (e.g. 'Rs. 10', 'Rs. 5', 'Rs. 1')."},
            "fresh_issue_shares": {"type": "string", "description": "Number of equity shares in the fresh issue portion."},
            "offer_for_sale_shares": {"type": "string", "description": "Number of equity shares in the offer-for-sale (OFS) portion."},
            "pre_issue_shares": {"type": "string", "description": "Total equity shares outstanding BEFORE the offer."},
            "post_issue_shares": {"type": "string", "description": "Total equity shares outstanding AFTER the offer."},
            "market_maker_shares": {"type": "string", "description": "Shares reserved for the Market Maker."},
            "employee_shares": {"type": "string", "description": "Shares reserved for the Employee Reservation Portion."},
            "net_issue_to_public": {"type": "string", "description": "Net Issue to Public (total issue minus reservations)."},
            "qib_shares": {"type": "string", "description": "Shares reserved for Qualified Institutional Buyers."},
            "anchor_shares": {"type": "string", "description": "Shares reserved for Anchor Investors."},
            "qib_ex_anchor_shares": {"type": "string", "description": "QIB shares remaining after Anchor allocation."},
            "nii_shares": {"type": "string", "description": "Shares reserved for Non-Institutional Investors (NII / HNI)."},
            "bhnii_shares": {"type": "string", "description": "Big HNI shares (> ₹10 lakh bid, bNII). Usually 2/3 of NII portion."},
            "shnii_shares": {"type": "string", "description": "Small HNI shares (₹2L-₹10L bid, sNII). Usually 1/3 of NII portion."},
            "retail_shares": {"type": "string", "description": "Shares reserved for Retail Individual Investors (RII)."},
            "anchor_shares": {"type": "string", "description": "Shares reserved for Anchor Investors."},
            "qib_ex_anchor_shares": {"type": "string", "description": "QIB shares remaining after Anchor allocation."},
            "bhnii_shares": {"type": "string", "description": "Big HNI shares (> ₹10 lakh bid, bNII)."},
            "shnii_shares": {"type": "string", "description": "Small HNI shares (₹2L-₹10L bid, sNII)."},
            "qib_percent": {"type": "string", "description": "QIB portion as percentage of net issue."},
            "nii_percent": {"type": "string", "description": "NII/HNI portion as percentage of net issue."},
            "retail_percent": {"type": "string", "description": "Retail portion as percentage of net issue."},
            "qib_percent": {"type": "string", "description": "QIB portion as percentage of net issue."},
            "nii_percent": {"type": "string", "description": "NII/HNI portion as percentage of net issue."},
            "retail_percent": {"type": "string", "description": "Retail portion as percentage of net issue."},
        },
    },

    # ──────────────────────────────────────────────────────────────
    "ISSUE_STRUCTURE": {
        "type": "object",
        "properties": {
            "total_issue_shares": {"type": "string", "description": "Total number of equity shares offered in the Issue."},
            "total_issue_amount": {"type": "string", "description": "Total issue size in rupees (e.g. 'Rs. 55 crore')."},
            "market_lot": {"type": "string", "description": "Minimum bid lot (number of shares per application)."},
            "minimum_application": {"type": "string", "description": "Minimum application amount in rupees (e.g. 'Rs. 2,00,000')."},
            "retail_min_lots": {"type": "string", "description": "Minimum number of lots for retail investors."},
            "retail_max_lots": {"type": "string", "description": "Maximum number of lots for retail investors (usually same as min for RII)."},
            "shni_min_lots": {"type": "string", "description": "Minimum number of lots for Small HNI / sNII (Rs. 2L-10L bid)."},
            "shni_max_lots": {"type": "string", "description": "Maximum number of lots for Small HNI / sNII."},
            "bhni_min_lots": {"type": "string", "description": "Minimum number of lots for Big HNI / bNII (above Rs. 10L bid)."},
            "employee_min_lots": {"type": "string", "description": "Minimum number of lots for Employee Reservation Portion."},
            "employee_max_lots": {"type": "string", "description": "Maximum number of lots for Employee Reservation Portion."},
            "trading_lot": {"type": "string", "description": "Trading lot size (number of equity shares per lot on the exchange)."},
            "retail_min_shares": {"type": "string", "description": "Minimum shares for retail (min lots x lot size)."},
            "retail_max_amount": {"type": "string", "description": "Maximum application amount for retail (in rupees)."},
            "shni_min_shares": {"type": "string", "description": "Minimum shares for Small HNI."},
            "shni_max_shares": {"type": "string", "description": "Maximum shares for Small HNI."},
            "shni_min_amount": {"type": "string", "description": "Minimum application amount for sNII."},
            "shni_max_amount": {"type": "string", "description": "Maximum application amount for sNII."},
            "bhni_min_shares": {"type": "string", "description": "Minimum shares for Big HNI."},
            "bhni_min_amount": {"type": "string", "description": "Minimum application amount for bNII."},
            "employee_min_shares": {"type": "string", "description": "Minimum shares for Employee Portion."},
            "employee_max_shares": {"type": "string", "description": "Maximum shares for Employee Portion."},
            "employee_max_amount": {"type": "string", "description": "Maximum application amount for Employee Portion (e.g. 'Rs. 5,00,000')."},

            # Issue reservation shares by category — values often embedded in ISSUE_STRUCTURE table
            "qib_shares": {"type": "string", "description": "Shares reserved for Qualified Institutional Buyers (from the issue structure table)."},
            "anchor_shares": {"type": "string", "description": "Shares reserved for Anchor Investors (a sub-set of QIB, typically up to 60% of QIB)."},
            "qib_ex_anchor_shares": {"type": "string", "description": "QIB shares remaining after Anchor allocation."},
            "nii_shares": {"type": "string", "description": "Shares reserved for Non-Institutional Investors / HNI (from the issue structure table)."},
            "bhnii_shares": {"type": "string", "description": "Big HNI shares (> ₹10 lakh bid, bNII). Usually 2/3 of NII portion."},
            "shnii_shares": {"type": "string", "description": "Small HNI shares (₹2L-₹10L bid, sNII). Usually 1/3 of NII portion."},
            "retail_shares": {"type": "string", "description": "Shares reserved for Retail Individual Investors (from the issue structure table)."},
            "market_maker_shares": {"type": "string", "description": "Shares reserved for the Market Maker (from the issue structure table)."},
            "employee_shares": {"type": "string", "description": "Shares reserved for the Employee Reservation Portion (from the issue structure table)."},
            "net_issue_to_public": {"type": "string", "description": "Net Issue to Public (total issue minus reservations, from the issue structure table)."},
            "fresh_issue_shares": {"type": "string", "description": "Number of equity shares in the fresh issue portion."},
            "offer_for_sale_shares": {"type": "string", "description": "Number of equity shares in the offer-for-sale (OFS) portion."},
            "total_issue_shares": {"type": "string", "description": "Total number of equity shares offered in the Issue."},
            "total_issue_amount": {"type": "string", "description": "Total issue size in rupees (e.g. 'Rs. 55 crore')."},
            "qib_percent": {"type": "string", "description": "QIB portion as percentage of net issue. e.g. '50%', 'not more than 50%', '50.00%'. Extract even if preceded by 'not more than' / 'not less than' qualifiers."},
            "nii_percent": {"type": "string", "description": "NII/HNI portion as percentage of net issue. e.g. '15%', 'not less than 15%'. Extract even if preceded by 'not more than' / 'not less than' qualifiers."},
            "retail_percent": {"type": "string", "description": "Retail portion as percentage of net issue. e.g. '35%', 'not more than 35%'. Extract even if preceded by 'not more than' / 'not less than' qualifiers."},
        },
    },

    # ──────────────────────────────────────────────────────────────
    "OBJECTS_OF_THE_OFFER": {
        "type": "object",
        "properties": {
            "total_project_cost": {"type": "string", "description": "Total project cost / total objects of the offer (in rupees)."},
            "fund_usage_breakdown": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of fund-usage line items, e.g. ['Repayment of borrowings: Rs. 500 crore', 'Capex: Rs. 200 crore', 'General corporate purposes: Rs. 50 crore'].",
            },
        },
    },

    # ──────────────────────────────────────────────────────────────
    "BASIS_FOR_OFFER_PRICE": {
        "type": "object",
        "properties": {
            "eps_basic": {"type": "string", "description": "Basic earnings per share (most recent year)."},
            "eps_diluted": {"type": "string", "description": "Diluted earnings per share (most recent year)."},
            "eps_pre_ipo": {"type": "string", "description": "Pre-IPO EPS (weighted average used for P/E calculation)."},
            "eps_post_ipo": {"type": "string", "description": "Post-IPO EPS (diluted by fresh issue shares)."},
            "pe_ratio": {"type": "string", "description": "Price-to-Earnings ratio at the offer price (lower or upper band)."},
            "pe_pre_ipo": {"type": "string", "description": "Pre-IPO P/E ratio."},
            "pe_post_ipo": {"type": "string", "description": "Post-IPO P/E ratio."},
            "nav_per_share": {"type": "string", "description": "Net Asset Value per equity share."},
            "roe_percent": {"type": "string", "description": "Return on Equity / RoNW, as a percentage (e.g. '18.5%')."},
            "roce_percent": {"type": "string", "description": "Return on Capital Employed, as a percentage."},
            "price_to_book_value": {"type": "string", "description": "Price-to-Book ratio at the offer price."},
            "price_to_book_pre_ipo": {"type": "string", "description": "Pre-IPO Price-to-Book ratio."},
            "price_to_book_post_ipo": {"type": "string", "description": "Post-IPO Price-to-Book ratio."},
            "debt_equity_ratio": {"type": "string", "description": "Debt-to-Equity ratio (e.g. '0.52')."},
            "revenue_growth_percent": {"type": "string", "description": "Year-on-year revenue growth percentage."},
            "pat_margin_percent": {"type": "string", "description": "Profit-after-tax margin percentage."},
            "ebitda_margin_percent": {"type": "string", "description": "EBITDA margin percentage."},
            "promoter_holding_pre_ipo_percent": {"type": "string", "description": "Promoter holding percentage BEFORE the offer."},
            "promoter_holding_post_ipo_percent": {"type": "string", "description": "Promoter holding percentage AFTER the offer."},
            "market_cap_pre_ipo": {"type": "string", "description": "Market capitalisation before the offer (in rupees, e.g. 'Rs. 208.15 crore')."},
        },
    },

    # ──────────────────────────────────────────────────────────────
    "RESTATED_FINANCIAL_STATEMENTS": {
        "type": "object",
        "properties": {
            "financial_years": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of fiscal periods covered, e.g. ['Dec 31, 2025', 'Mar 31, 2025', 'Mar 31, 2024', 'Mar 31, 2023'].",
            },
            "total_revenue": {"type": "string", "description": "Revenue from Operations per period. Output as pipe-separated: e.g. 'Dec 31, 2025: 2675.87 | Mar 31, 2025: 3249.29 | Mar 31, 2024: 2977.31 | Mar 31, 2023: 2785.01'. Include amount WITHOUT commas. Look for 'Revenue from Operations' row in P&L."},
            "total_income": {"type": "string", "description": "Total Income per period. Output as pipe-separated: e.g. 'Dec 31, 2025: 2755.70 | Mar 31, 2025: 3312.87'. Look for 'Total Income' row."},
            "profit_after_tax": {"type": "string", "description": "Profit After Tax per period. Output as pipe-separated. Look for 'Profit for the Year' or 'Profit After Tax' row in P&L."},
            "ebitda": {"type": "string", "description": "EBITDA per period. Output as pipe-separated. Look for EBITDA row or compute from Profit Before Tax + Depreciation + Finance Costs."},
            "total_assets": {"type": "string", "description": "Total Assets per period. Output as pipe-separated: e.g. 'Dec 31, 2025: 3276.01'. Look for 'Total Assets' row in Balance Sheet."},
            "net_worth": {"type": "string", "description": "Net Worth / Total Equity per period. Output as pipe-separated. Look for 'Total Equity' row."},
            "reserves_and_surplus": {"type": "string", "description": "Reserves and Surplus / Other Equity per period. Output as pipe-separated. Look for 'Other Equity' row."},
            "total_borrowings": {"type": "string", "description": "Total Borrowings per period. Output as pipe-separated. Sum long-term + short-term Borrowings."},
        },
    },

    # ──────────────────────────────────────────────────────────────
    "ISSUE_PROCEDURE": {
        "type": "object",
        "properties": {
            "bid_open_date": {"type": "string", "description": "Bid/Issue opening date (DD/MM/YYYY or as-printed)."},
            "bid_close_date": {"type": "string", "description": "Bid/Issue closing date."},
            "allotment_date": {"type": "string", "description": "Date of allotment / finalisation of basis of allotment."},
            "refund_date": {"type": "string", "description": "Date of initiation of allotment / refunds / unblocking of funds from ASBA."},
            "credit_of_shares_date": {"type": "string", "description": "Date of credit of equity shares to demat accounts of allottees."},
            "listing_date": {"type": "string", "description": "Tentative listing date / commencement of trading on exchanges."},
            "market_lot": {"type": "string", "description": "Minimum bid lot (number of shares per application)."},
            "retail_min_lots": {"type": "string", "description": "Minimum number of lots for retail investors."},
            "minimum_application": {"type": "string", "description": "Minimum application amount in rupees."},
        },
    },

    # ──────────────────────────────────────────────────────────────
    "OUR_PROMOTERS_AND_PROMOTER_GROUP": {
        "type": "object",
        "properties": {
            "promoter_names": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Names of all promoters of the company.",
            },
            "promoter_group_names": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Names of promoter group members (individuals and entities) other than the promoters themselves.",
            },
        },
    },
}


# Aliases — a section may appear in the DB under a slightly different canonical
# name; this maps lookalikes to the canonical schema key above.
SECTION_ALIASES: dict[str, str] = {
    "OBJECTS_OF_THE_ISSUE":                       "OBJECTS_OF_THE_OFFER",
    "BASIS_FOR_ISSUE_PRICE":                      "BASIS_FOR_OFFER_PRICE",
    "RESTATED_FINANCIAL_STATEMENT":               "RESTATED_FINANCIAL_STATEMENTS",
    "RESTATED_FINANCIAL_INFORMATION":             "RESTATED_FINANCIAL_STATEMENTS",
    "RESTATED_CONSOLIDATED_FINANCIAL_STATEMENTS": "RESTATED_FINANCIAL_STATEMENTS",
    "RESTATED_CONSOLIDATED_FINANCIAL_INFORMATION": "RESTATED_FINANCIAL_STATEMENTS",
    "FINANCIAL_INFORMATION":                      "RESTATED_FINANCIAL_STATEMENTS",
    "FINANCIAL_INFORMATION_OF_THE_COMPANY":       "RESTATED_FINANCIAL_STATEMENTS",
    "FINANCIAL_INDEBTEDNESS":                     "STATEMENT_OF_FINANCIAL_INDEBTEDNESS",
    "TERMS_OF_THE_OFFER":                         "ISSUE_PROCEDURE",
    "TERMS_OF_THE_ISSUE":                         "ISSUE_PROCEDURE",
    # ISSUE_STRUCTURE is NOT aliased to ISSUE_PROCEDURE anymore — it has its own schema
    "OUR_PROMOTERS_AND_PROMOTER_GROUP":           "OUR_PROMOTERS_AND_PROMOTER_GROUP",
    "OUR_PROMOTER_AND_PROMOTER_GROUP":            "OUR_PROMOTERS_AND_PROMOTER_GROUP",
    "ABOUT_OUR_COMPANY":                          "ABOUT_THE_COMPANY",
    "ABOUT_COMPANY":                              "ABOUT_THE_COMPANY",
    "CAPITAL_STRUCTURE_OF_THE_COMPANY":           "CAPITAL_STRUCTURE",
    "OUTSTANDING_LITIGATION_AND_MATERIAL_DEVELOPMENTS": "OUTSTANDING_LITIGATION",
    "OUTSTANDING_LITIGATIONS_AND_MATERIAL_DEVELOPMENTS": "OUTSTANDING_LITIGATION",
    "KEY_INDUSTRY_REGULATIONS_AND_POLICIES":      "KEY_REGULATIONS_AND_POLICIES",
    "KEY_INDUSTRY_REGULATIONS":                   "KEY_REGULATIONS_AND_POLICIES",
    "GOVERNMENT_AND_OTHER_APPROVALS":             "KEY_REGULATIONS_AND_POLICIES",
    "GOVERNMENT_AND_OTHER_STATUTORY_APPROVALS":   "KEY_REGULATIONS_AND_POLICIES",
    "STATEMENT_OF_TAX_BENEFITS":                  "STATEMENT_OF_SPECIAL_TAX_BENEFITS",
    "HISTORY_AND_CORPORATE_STRUCTURE":            "HISTORY_AND_CERTAIN_CORPORATE_MATTERS",
    "OUR_HISTORY_AND_CORPORATE_STRUCTURE":        "HISTORY_AND_CERTAIN_CORPORATE_MATTERS",
    "OUR_HISTORY_AND_CERTAIN_CORPORATE_MATTERS":  "HISTORY_AND_CERTAIN_CORPORATE_MATTERS",
    "OBJECT_OF_THE_ISSUE":                        "OBJECTS_OF_THE_ISSUE",
    "OFFER_STRUCTURE":                            "ISSUE_STRUCTURE",
}


def resolve_schema(section_name: str) -> tuple[str | None, dict[str, Any] | None]:
    """Look up the schema for a section name, applying aliases.

    Returns (canonical_section_name, schema_dict) or (None, None) if unknown.
    """
    name = section_name.upper().replace(" ", "_").replace("&", "AND")
    canonical = SECTION_ALIASES.get(name, name)
    schema = SECTION_SCHEMAS.get(canonical)
    return (canonical, schema) if schema else (None, None)


# Convenience for the API/dashboard to advertise which sections we attempt
TARGET_SECTIONS = list(SECTION_SCHEMAS.keys())


# ─── Section groups — used by firecrawl_parser to cut credits ───────────────
#
# Instead of N calls (one per section), we make len(SECTION_GROUPS) calls,
# each one combining a few related sections' markdown into a single Firecrawl
# scrape with a merged schema. The LLM still sees the section headers via
# `## SECTION_NAME` separators in the input text, so accuracy is preserved.
#
# Cost: 8 sections × 5 credits  →  5 groups × 5 credits = ~37% saving.
#
# A section may belong to exactly one group. If a section isn't in any
# group it falls back to a per-section call (rare; e.g. one-off custom ones).

SECTION_GROUPS: dict[str, list[str]] = {
    "company":   ["GENERAL_INFORMATION", "OUR_PROMOTERS_AND_PROMOTER_GROUP"],
    "structure": ["CAPITAL_STRUCTURE", "OBJECTS_OF_THE_OFFER"],
    "financial": ["RESTATED_FINANCIAL_STATEMENTS", "BASIS_FOR_OFFER_PRICE"],
    "issue":     ["ISSUE_PROCEDURE"],
    "offer":     ["ISSUE_STRUCTURE"],
}


def merged_group_schema(group_name: str) -> dict[str, Any]:
    """Return a JSON Schema that's the union of all sections in `group_name`.

    Property descriptions are preserved so the LLM still gets per-field guidance.
    """
    if group_name not in SECTION_GROUPS:
        raise KeyError(f"Unknown section group: {group_name}")
    merged_props: dict[str, Any] = {}
    for section in SECTION_GROUPS[group_name]:
        schema = SECTION_SCHEMAS.get(section)
        if not schema:
            continue
        for k, v in schema.get("properties", {}).items():
            # If two sections claim the same field name, first one wins
            # (currently no collisions, but defensive).
            merged_props.setdefault(k, v)
    return {"type": "object", "properties": merged_props}


def group_for_section(section_name: str) -> str | None:
    """Reverse lookup: which group does this section belong to?"""
    for group, members in SECTION_GROUPS.items():
        if section_name in members:
            return group
    return None
