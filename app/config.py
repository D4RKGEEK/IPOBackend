"""
Centralized application config.

One pydantic-settings model owns every env var the application reads. Boot
fails fast with a clear message if a *required* variable is missing.

Read order (lower wins, higher overrides):
    1. defaults declared on the model
    2. <repo_root>/.env
    3. process environment

Usage:
    from app.config import settings
    settings.deepseek_api_key
    settings.r2.bucket
    settings.r2.public_base
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Optional

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


# Path to the canonical .env that lives at the repo root
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_ENV_FILE = _PROJECT_ROOT / ".env"


def _detect_ssl_cert() -> Optional[str]:
    """Pick a usable cert bundle without hardcoding macOS-only paths."""
    candidates = [
        os.environ.get("SSL_CERT_FILE"),
        "/opt/homebrew/etc/openssl@3/cert.pem",
        "/usr/local/etc/openssl@3/cert.pem",
        "/etc/ssl/certs/ca-certificates.crt",
        "/etc/pki/tls/certs/ca-bundle.crt",
    ]
    for c in candidates:
        if c and os.path.exists(c):
            return c
    return None


class Settings(BaseSettings):
    """All runtime configuration. Required fields are not Optional."""

    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ─── App ──────────────────────────────────────────────────────────
    app_name: str = Field(default="IPO Aggregation Platform")
    version: str = Field(default="3.0.0")
    debug: bool = Field(default=False)
    log_level: str = Field(default="INFO")

    # ─── Database ─────────────────────────────────────────────────────
    # DATABASE_URL wins if set (postgresql://...). Otherwise we fall back to
    # the local SQLite file at IPOS_DB_PATH.
    database_url: str = Field(default="")
    ipos_db_path: str = Field(default=str(_PROJECT_ROOT / "ipos.db"))

    @computed_field
    @property
    def db_dialect(self) -> str:
        """Return 'postgresql' or 'sqlite' so callers can branch on dialect."""
        if self.database_url.startswith("postgres"):
            return "postgresql"
        return "sqlite"

    @computed_field
    @property
    def db_url(self) -> str:
        """The actual URL handed to SQLAlchemy.

        For Postgres we normalize the scheme to `postgresql+psycopg://` so
        SQLAlchemy uses psycopg v3 (modern, async-capable). Supabase
        connection strings often start with `postgres://` which is fine —
        we rewrite the prefix.
        """
        if self.db_dialect == "postgresql":
            url = self.database_url
            if url.startswith("postgres://"):
                url = "postgresql://" + url[len("postgres://"):]
            if url.startswith("postgresql://"):
                url = "postgresql+psycopg://" + url[len("postgresql://"):]
            return url
        return f"sqlite:///{self.ipos_db_path}"

    # ─── DeepSeek (LLM parsing) ───────────────────────────────────────
    deepseek_api_key: str = Field(default="", description="Required for /parse-sections")
    # Match the model the parser has been running with in production.
    # Override via DEEPSEEK_MODEL=… in .env if you switch tiers (e.g. deepseek-chat).
    deepseek_model: str = Field(default="deepseek-v4-flash")

    # ─── Firecrawl (alternative LLM parser) ───────────────────────────
    firecrawl_api_key: str = Field(default="", description="Required for /parse-firecrawl")

    # ─── Cloudflare R2 (section markdown storage) ─────────────────────
    cf_account_id: str = Field(default="")
    r2_access_key_id: str = Field(default="")
    r2_secret_access_key: str = Field(default="")
    r2_bucket: str = Field(default="")
    r2_public_base: str = Field(default="")

    # ─── Parser provider switch ───────────────────────────────────────
    parser_provider: str = Field(default="deepseek")  # deepseek | firecrawl

    # ─── Internal auth (gates write/cron endpoints from external callers) ────
    # Set to a long random string in production. When unset, the gate is OFF
    # (open API) — convenient for local dev but unsafe for production.
    internal_api_key: str = Field(default="")

    # ─── Scrape tuning ────────────────────────────────────────────────
    sebi_max_pages: int = Field(default=10, ge=1, le=50)
    sebi_delay_seconds: float = Field(default=0.3, ge=0)
    bse_delay_seconds: float = Field(default=0.1, ge=0)
    nse_delay_seconds: float = Field(default=0.1, ge=0)
    bse_sme_delay_seconds: float = Field(default=0.5, ge=0)
    max_document_size_mb: int = Field(default=80, ge=1)

    # ─── Upstox ──────────────────────────────────────────────────────
    upstox_access_token: str = Field(default="", description="Upstox API Bearer token")

    # ─── SSL ─────────────────────────────────────────────────────────
    ssl_cert_file: Optional[str] = Field(default=None)

    # ─── Notifications (graceful degradation: blank = disabled) ──────
    telegram_bot_token: str = Field(default="")
    telegram_chat_id: str = Field(default="")
    gmail_user: str = Field(default="")
    gmail_app_password: str = Field(default="")
    notify_recipient_email: str = Field(default="")

    # ─── Computed ─────────────────────────────────────────────────────
    @computed_field
    @property
    def r2_enabled(self) -> bool:
        return bool(
            self.cf_account_id and self.r2_access_key_id
            and self.r2_secret_access_key and self.r2_bucket
        )

    @computed_field
    @property
    def r2_endpoint(self) -> str:
        return f"https://{self.cf_account_id}.r2.cloudflarestorage.com"

    @computed_field
    @property
    def max_document_size_bytes(self) -> int:
        return self.max_document_size_mb * 1024 * 1024


# ─── Singleton ──────────────────────────────────────────────────────

def _load() -> Settings:
    try:
        s = Settings()
    except Exception as exc:
        sys.stderr.write(
            "FATAL: failed to load settings. Check that .env exists at "
            f"{_ENV_FILE} and matches .env.example.\n  -> {exc}\n"
        )
        raise

    # Side-effect: export SSL cert path into env so libraries that read it
    # (httpx, requests) pick it up. Do this once, here, so the rest of the
    # codebase doesn't have to.
    cert = s.ssl_cert_file or _detect_ssl_cert()
    if cert:
        os.environ.setdefault("SSL_CERT_FILE", cert)
        os.environ.setdefault("REQUESTS_CA_BUNDLE", cert)

    return s


settings = _load()


# ─── Validation helpers (called by features that need specific vars) ─

class MissingConfigError(RuntimeError):
    """Raised when a feature requires env vars that aren't set."""


def require_deepseek() -> str:
    if not settings.deepseek_api_key:
        raise MissingConfigError(
            "DEEPSEEK_API_KEY is not set. Add it to .env or export it. "
            "See .env.example."
        )
    return settings.deepseek_api_key


def require_firecrawl() -> str:
    if not settings.firecrawl_api_key:
        raise MissingConfigError(
            "FIRECRAWL_API_KEY is not set. Add it to .env or export it."
        )
    return settings.firecrawl_api_key


def require_r2() -> Settings:
    missing = [
        name for name, val in (
            ("CF_ACCOUNT_ID", settings.cf_account_id),
            ("R2_ACCESS_KEY_ID", settings.r2_access_key_id),
            ("R2_SECRET_ACCESS_KEY", settings.r2_secret_access_key),
            ("R2_BUCKET", settings.r2_bucket),
            ("R2_PUBLIC_BASE", settings.r2_public_base),
        ) if not val
    ]
    if missing:
        raise MissingConfigError(
            f"R2 storage requires these env vars: {', '.join(missing)}. See .env.example."
        )
    return settings
