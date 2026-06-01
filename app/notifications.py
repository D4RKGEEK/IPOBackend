"""
Notifications — Telegram (instant) + Gmail (digest/errors).

Severity routing:
    info   → Telegram only          (new IPO, parse success)
    warn   → Telegram only          (parse failure, source down)
    error  → Telegram + Gmail       (system error, requires action)
    digest → Gmail only             (daily summary)

Graceful degradation: missing env vars don't crash the app. If
TELEGRAM_BOT_TOKEN/CHAT_ID is unset, Telegram is silently skipped (logged
at DEBUG). Same for Gmail. Configure when ready; until then, nothing breaks.

Fire-and-forget: notifications run in a background thread so the calling
hot path is never blocked by a slow Telegram API.

Usage:
    from app.notifications import notify
    notify("📥 New IPO: Acme Ltd", level="info")
    notify("🚨 Parse failed", level="error",
           details={"ipo_id": 88, "error": "DeepSeek timeout"})
"""
from __future__ import annotations

import json
import logging
import smtplib
import threading
from email.message import EmailMessage
from typing import Any, Literal, Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

Level = Literal["info", "warn", "error", "digest"]


# ─── Channel availability ────────────────────────────────────────

def telegram_enabled() -> bool:
    return bool(settings.telegram_bot_token and settings.telegram_chat_id)


def gmail_enabled() -> bool:
    return bool(settings.gmail_user and settings.gmail_app_password and settings.notify_recipient_email)


# ─── Telegram ───────────────────────────────────────────────────

_TELEGRAM_URL = "https://api.telegram.org/bot{token}/sendMessage"


def _send_telegram(text: str, parse_mode: str = "HTML") -> None:
    """Synchronous Telegram send. Caller wraps in a thread."""
    if not telegram_enabled():
        logger.debug("telegram skipped (not configured)")
        return
    try:
        with httpx.Client(timeout=10) as c:
            r = c.post(
                _TELEGRAM_URL.format(token=settings.telegram_bot_token),
                json={
                    "chat_id": settings.telegram_chat_id,
                    "text": text[:4096],   # Telegram message size cap
                    "parse_mode": parse_mode,
                    "disable_web_page_preview": True,
                },
            )
            if r.status_code != 200:
                logger.warning("telegram returned %d: %s", r.status_code, r.text[:200])
    except Exception as e:
        logger.warning("telegram send failed: %s", e)


# ─── Gmail (SMTP) ───────────────────────────────────────────────

def _send_gmail(subject: str, body: str, html: bool = False) -> None:
    """Synchronous Gmail send via SMTP. Caller wraps in a thread."""
    if not gmail_enabled():
        logger.debug("gmail skipped (not configured)")
        return
    try:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = settings.gmail_user
        msg["To"] = settings.notify_recipient_email
        if html:
            msg.set_content("(HTML-only message)")
            msg.add_alternative(body, subtype="html")
        else:
            msg.set_content(body)

        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=15) as smtp:
            smtp.login(settings.gmail_user, settings.gmail_app_password)
            smtp.send_message(msg)
    except Exception as e:
        logger.warning("gmail send failed: %s", e)


# ─── Public entry point ────────────────────────────────────────

def notify(
    message: str,
    level: Level = "info",
    details: Optional[dict[str, Any]] = None,
    subject: Optional[str] = None,
) -> None:
    """Fire-and-forget notification. Returns immediately; delivery happens in a thread.

    Args:
        message: short summary (used as Telegram body + Gmail subject)
        level: routing key — see module docstring
        details: optional dict appended to Telegram (as <code>) and Gmail body
        subject: optional Gmail subject override (defaults to message)

    Always safe to call: if no channels are configured, this is a no-op
    (logged at DEBUG).
    """
    # Temp mute: global kill switch (NOTIFICATIONS_DISABLED=true in .env)
    if settings.notifications_disabled:
        logger.debug("notifications globally disabled, skipping: %s", message)
        return

    # Always log locally for the structured-log paper trail
    log_fn = {"info": logger.info, "warn": logger.warning,
              "error": logger.error, "digest": logger.info}.get(level, logger.info)
    if details:
        log_fn("notify[%s] %s details=%s", level, message, json.dumps(details, default=str)[:500])
    else:
        log_fn("notify[%s] %s", level, message)

    # Decide channels
    to_telegram = level in ("info", "warn", "error")
    to_gmail = level in ("error", "digest")

    if not to_telegram and not to_gmail:
        return

    # Build payloads
    tg_text = message
    if details:
        snippet = json.dumps(details, default=str, indent=2)[:1500]
        tg_text = f"{message}\n<pre>{_html_escape(snippet)}</pre>"

    gmail_subject = subject or f"[ipo-scraper:{level}] {message[:80]}"
    gmail_body_lines = [message]
    if details:
        gmail_body_lines.append("")
        gmail_body_lines.append("Details:")
        gmail_body_lines.append(json.dumps(details, default=str, indent=2))
    gmail_body = "\n".join(gmail_body_lines)

    # Fire async (one thread does both channels, sequential is fine)
    def _run():
        if to_telegram:
            _send_telegram(tg_text)
        if to_gmail:
            _send_gmail(gmail_subject, gmail_body)

    threading.Thread(target=_run, daemon=True, name=f"notify-{level}").start()


def _html_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
         .replace("<", "&lt;")
         .replace(">", "&gt;")
    )


# ─── Setup self-test (called by the API test endpoint) ─────────

def test_channels() -> dict[str, Any]:
    """Synchronously try each enabled channel. Returns per-channel status."""
    result: dict[str, Any] = {
        "telegram": {"enabled": telegram_enabled(), "ok": None, "error": None},
        "gmail": {"enabled": gmail_enabled(), "ok": None, "error": None},
    }

    if telegram_enabled():
        try:
            with httpx.Client(timeout=10) as c:
                r = c.post(
                    _TELEGRAM_URL.format(token=settings.telegram_bot_token),
                    json={
                        "chat_id": settings.telegram_chat_id,
                        "text": "✅ IPO Scraper notification test from /api/internal/notify/test",
                    },
                )
                result["telegram"]["ok"] = r.status_code == 200
                if r.status_code != 200:
                    result["telegram"]["error"] = r.text[:300]
        except Exception as e:
            result["telegram"]["ok"] = False
            result["telegram"]["error"] = str(e)[:300]

    if gmail_enabled():
        try:
            _send_gmail(
                "[ipo-scraper:test] notification setup OK",
                "If you got this email, Gmail SMTP is configured correctly.\n\n"
                "This is a test from POST /api/internal/notify/test.",
            )
            result["gmail"]["ok"] = True
        except Exception as e:
            result["gmail"]["ok"] = False
            result["gmail"]["error"] = str(e)[:300]

    return result
