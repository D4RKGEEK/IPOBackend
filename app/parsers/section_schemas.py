"""
Per-section JSON schemas for Firecrawl LLM extraction.

Each entry is keyed by the canonical section_name (matching what
section_resolver writes to document_sections.section_name).

Schema design rules:
 - Keep field count small (5-12 per section) — bigger schema = more hallucination.
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
SCHEMA_VERSION = 1


# Shared instruction prefix added to every per-section prompt
COMMON_INSTRUCTION = (
    "You are extracting structured data from one section of an Indian IPO prospectus "
    "(DRHP / RHP / Final Prospectus). Return ONLY the requested JSON fields. "
    "Use empty string \"\" for missing text and [] for missing arrays. Never return null. "
    "Preserve units (e.g. 'Rs. 1,234 crore', '12.45%') in their original form."
)


SECTION_SCHEMAS: dict[str, dict[str, Any]] = {
    # ──────────────────────────────────────────────────────────────
    "GENERAL_INFORMATION": {
        "type": "object",
        "properties": {
            "cin": {"type": "string", "description": "Corporate Identification Number — 21-char alphanumeric like L12345AB1234ABC123456."},
            "registered_address": {"type": "string", "description": "Full registered office address."},
            "telephone": {"type": "string", "description": "Main contact phone number (with country code if shown)."},
            "email": {"type": "string", "description": "Investor relations or general contact email."},
            "website": {"type": "string", "description": "Company website URL."},
            "brlm_name": {"type": "string", "description": "Book Running Lead Manager(s). Comma-separated if multiple."},
            "registrar_name": {"type": "string", "description": "Registrar to the Issue / Registrar and Share Transfer Agent."},
            "statutory_auditor": {"type": "string", "description": "Name of statutory auditor firm."},
            "legal_advisor": {"type": "string", "description": "Legal counsel to the issuer."},
            "cfo_name": {"type": "string", "description": "Chief Financial Officer's full name."},
            "company_secretary_name": {"type": "string", "description": "Company Secretary's full name."},
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
            "qib_shares": {"type": "string", "description": "Shares reserved for Qualified Institutional Buyers."},
            "nii_shares": {"type": "string", "description": "Shares reserved for Non-Institutional Investors."},
            "retail_shares": {"type": "string", "description": "Shares reserved for Retail Individual Investors."},
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
            "pe_ratio": {"type": "string", "description": "Price-to-Earnings ratio at the offer price (lower or upper band)."},
            "nav_per_share": {"type": "string", "description": "Net Asset Value per equity share."},
            "roe_percent": {"type": "string", "description": "Return on Equity, as a percentage (e.g. '18.5%')."},
            "roce_percent": {"type": "string", "description": "Return on Capital Employed, as a percentage."},
            "price_to_book_value": {"type": "string", "description": "Price-to-Book ratio at the offer price."},
            "revenue_growth_percent": {"type": "string", "description": "Year-on-year revenue growth percentage."},
            "pat_margin_percent": {"type": "string", "description": "Profit-after-tax margin percentage."},
            "ebitda_margin_percent": {"type": "string", "description": "EBITDA margin percentage."},
        },
    },

    # ──────────────────────────────────────────────────────────────
    "RESTATED_FINANCIAL_STATEMENTS": {
        "type": "object",
        "properties": {
            "financial_years": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of fiscal years covered, e.g. ['FY2023', 'FY2024', 'FY2025'].",
            },
            "total_revenue": {"type": "string", "description": "Total revenue / revenue from operations for the most recent fiscal year."},
            "total_income": {"type": "string", "description": "Total income (revenue + other income) for the most recent year."},
            "profit_after_tax": {"type": "string", "description": "Profit after tax for the most recent year."},
            "ebitda": {"type": "string", "description": "EBITDA for the most recent year."},
            "total_assets": {"type": "string", "description": "Total assets on the balance sheet for the most recent year."},
            "net_worth": {"type": "string", "description": "Net worth / total equity for the most recent year."},
            "reserves_and_surplus": {"type": "string", "description": "Reserves and surplus."},
            "total_borrowings": {"type": "string", "description": "Total borrowings (short + long-term)."},
        },
    },

    # ──────────────────────────────────────────────────────────────
    "ISSUE_PROCEDURE": {
        "type": "object",
        "properties": {
            "bid_open_date": {"type": "string", "description": "Bid/Issue opening date (DD/MM/YYYY or as-printed)."},
            "bid_close_date": {"type": "string", "description": "Bid/Issue closing date."},
            "allotment_date": {"type": "string", "description": "Date of allotment / finalisation of basis of allotment."},
            "listing_date": {"type": "string", "description": "Tentative listing date on exchanges."},
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
    "TERMS_OF_THE_OFFER":                         "ISSUE_PROCEDURE",
    "TERMS_OF_THE_ISSUE":                         "ISSUE_PROCEDURE",
    "ISSUE_STRUCTURE":                            "ISSUE_PROCEDURE",
    "OUR_PROMOTERS_AND_PROMOTER_GROUP":           "OUR_PROMOTERS_AND_PROMOTER_GROUP",
    "OUR_PROMOTER_AND_PROMOTER_GROUP":            "OUR_PROMOTERS_AND_PROMOTER_GROUP",
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
# Cost: 7 sections × 5 credits  →  4 groups × 5 credits = ~43% saving.
#
# A section may belong to exactly one group. If a section isn't in any
# group it falls back to a per-section call (rare; e.g. one-off custom ones).

SECTION_GROUPS: dict[str, list[str]] = {
    "company":   ["GENERAL_INFORMATION", "OUR_PROMOTERS_AND_PROMOTER_GROUP"],
    "structure": ["CAPITAL_STRUCTURE", "OBJECTS_OF_THE_OFFER"],
    "financial": ["RESTATED_FINANCIAL_STATEMENTS", "BASIS_FOR_OFFER_PRICE"],
    "issue":     ["ISSUE_PROCEDURE"],
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
