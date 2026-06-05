"""
Pipeline report — HTML email + Telegram summary after auto-pipeline runs.

Flow:
  1. Pipeline completes with stats dict
  2. _build_html_email(stats) → returns HTML string
  3. _build_telegram_summary(stats) → returns concise telegram text
  4. send_pipeline_report sends both channels

No duplicated content: errors go ONCE to Telegram, summary is separate.
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from typing import Any, Optional

from app.config import settings
from app.notifications import (
    _send_gmail,
    _send_telegram,
    gmail_enabled,
    telegram_enabled,
)

logger = logging.getLogger(__name__)

# ── HTML Email template ─────────────────────────────────────────

_EMAIL_TEMPLATE = """\
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<style>
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: #f5f5f5; margin: 0; padding: 0; color: #1a1a1a;
  }}
  .container {{ max-width: 600px; margin: 20px auto; background: #ffffff;
               border-radius: 12px; overflow: hidden;
               box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
  .header {{ background: #00D09C; padding: 24px 28px; }}
  .header h1 {{ margin: 0; color: #fff; font-size: 22px; font-weight: 700; }}
  .header .sub {{ color: rgba(255,255,255,0.85); font-size: 13px; margin-top: 4px; }}
  .body {{ padding: 24px 28px; }}
  .section {{ margin-bottom: 24px; }}
  .section h2 {{ font-size: 15px; color: #666; text-transform: uppercase;
                 letter-spacing: 1px; margin: 0 0 12px 0; }}
  table.summary {{ width: 100%; border-collapse: collapse; }}
  table.summary td {{ padding: 10px 12px; border-bottom: 1px solid #eee; font-size: 14px; }}
  table.summary td:first-child {{ color: #888; width: 40%; }}
  table.summary td:last-child {{ font-weight: 600; }}
  .stat-grid {{ display: flex; flex-wrap: wrap; gap: 12px; }}
  .stat-card {{ flex: 1; min-width: 120px; background: #f8faf9; border-radius: 8px;
                padding: 16px; text-align: center; }}
  .stat-card .num {{ font-size: 28px; font-weight: 700; color: #00D09C; }}
  .stat-card .label {{ font-size: 11px; color: #888; text-transform: uppercase;
                       letter-spacing: 0.5px; margin-top: 4px; }}
  .stat-card.red .num {{ color: #e74c3c; }}
  .stat-card.orange .num {{ color: #f39c12; }}
  .stat-card.gray .num {{ color: #95a5a6; }}
  .detail-item {{ padding: 8px 0; border-bottom: 1px solid #f0f0f0; font-size: 13px; }}
  .detail-item .name {{ font-weight: 600; }}
  .detail-item .status {{ float: right; padding: 2px 8px; border-radius: 4px;
                          font-size: 11px; font-weight: 600; }}
  .status-ok {{ background: #d4edda; color: #155724; }}
  .status-failed {{ background: #f8d7da; color: #721c24; }}
  .status-skipped {{ background: #fff3cd; color: #856404; }}
  .log-entry {{ font-family: 'SF Mono', Menlo, monospace; font-size: 12px;
                padding: 2px 0; color: #555; }}
  .footer {{ text-align: center; color: #aaa; font-size: 11px; padding: 20px;
             border-top: 1px solid #eee; }}
</style></head>
<body>
<div class="container">
  <div class="header">
    <h1>📊 IPO Pipeline Report</h1>
    <div class="sub">{date}</div>
  </div>
  <div class="body">
    <div class="section">
      <h2>Overview</h2>
      <div class="stat-grid">
        <div class="stat-card">
          <div class="num">{checked}</div>
          <div class="label">IPOs Checked</div>
        </div>
        <div class="stat-card {new_class}">
          <div class="num">{new_ipos}</div>
          <div class="label">New Found</div>
        </div>
        <div class="stat-card {resolve_ok_class}">
          <div class="num">{resolved_ok}</div>
          <div class="label">Resolved</div>
        </div>
        <div class="stat-card {resolve_fail_class}">
          <div class="num">{resolved_fail}</div>
          <div class="label">Resolve Failed</div>
        </div>
        <div class="stat-card {parse_ok_class}">
          <div class="num">{parsed_ok}</div>
          <div class="label">Parsed</div>
        </div>
        <div class="stat-card {parse_fail_class}">
          <div class="num">{parsed_fail}</div>
          <div class="label">Parse Failed</div>
        </div>
      </div>
    </div>

    <div class="section">
      <h2>Details</h2>
      <table class="summary">
        <tr><td>Total IPOs checked</td><td>{checked}</td></tr>
        <tr><td>New IPOs this run</td><td>{new_ipos}</td></tr>
        <tr><td>Status changes detected</td><td>{status_changes}</td></tr>
        <tr><td>Resolved (success)</td><td>{resolved_ok}</td></tr>
        <tr><td>Resolve failed</td><td>{resolved_fail}</td></tr>
        <tr><td>Parsed (success)</td><td>{parsed_ok}</td></tr>
        <tr><td>Parse failed/timeout</td><td>{parsed_fail}</td></tr>
        <tr><td>Skipped (already done)</td><td>{skipped}</td></tr>
        <tr><td>No URL available</td><td>{no_url}</td></tr>
      </table>
    </div>

    {resolve_details_section}

    {parse_details_section}

    <div class="section">
      <h2>Resources</h2>
      <table class="summary">
        <tr><td>Free RAM</td><td>{free_mb} MB / {total_mb} MB</td></tr>
        <tr><td>CPU Load (1m / 5m)</td><td>{load_1m} / {load_5m}</td></tr>
      </table>
    </div>

    {error_section}

    {log_section}
  </div>
  <div class="footer">
    🤖 Hermes Agent · IPO Auto Pipeline<br>
    DO Droplet · {date}
  </div>
</div>
</body>
</html>"""


# ── Builders ────────────────────────────────────────────────────

def _build_html_email(stats: dict) -> str:
    """Render stats dict into the HTML template."""
    scrape = stats.get("scrape_result", {})

    checked = stats.get("total_ipos_checked", 0)
    new_ipos = scrape.get("new_ipos_found", 0)
    status_changes = scrape.get("status_changes_detected", 0)
    resolved_ok = stats.get("resolved_success", 0)
    resolved_fail = stats.get("resolved_failed", 0)
    parsed_ok = stats.get("parsed_success", 0)
    parsed_fail = stats.get("parsed_failed", 0)
    skipped = stats.get("skipped_count", 0)
    no_url = stats.get("no_url_count", 0)

    budget = stats.get("resources", {})
    free_mb = budget.get("free_mb", "?")
    total_mb = budget.get("total_mb", "?")
    load_1m = budget.get("load_1m", "?")
    load_5m = budget.get("load_5m", "?")

    date_str = datetime.now(timezone.utc).strftime("%d %b %Y, %I:%M %p UTC")

    new_class = "orange" if new_ipos > 0 else "gray"
    resolve_ok_class = "red" if resolved_fail > 0 else ("orange" if resolved_ok > 0 else "gray")
    resolve_fail_class = "red"
    parse_ok_class = "red" if parsed_fail > 0 else ("orange" if parsed_ok > 0 else "gray")
    parse_fail_class = "red"

    # Resolve details
    resolve_details = stats.get("resolve_details", [])
    if resolve_details:
        rows = ""
        for r in resolve_details:
            s = r.get("status", "?")
            cls = "status-ok" if s == "ok" else "status-failed"
            docs = r.get("doc_type", "").upper() if r.get("doc_type") else ""
            secs = r.get("sections_found", "")
            extra = f" — {docs}, {secs}sections" if docs else ""
            err = f": {r.get('error', '')}" if r.get("error") else ""
            rows += f'<div class="detail-item"><span class="name">{r.get("name", "?")}</span>{extra}{err}<span class="status {cls}">{s}</span></div>'
        resolve_section = f'<div class="section"><h2>Resolve Details</h2>{rows}</div>'
    else:
        resolve_section = ""

    # Parse details
    parse_details = stats.get("parse_details", [])
    if parse_details:
        rows = ""
        for r in parse_details:
            s = r.get("status", "?")
            cls = "status-ok" if s == "ok" else "status-failed"
            extra = ""
            if r.get("groups_parsed"):
                extra = f" ({r['groups_parsed']} parsed, {r.get('groups_skipped', 0)} skipped)"
            err = f": {r.get('error', '')}" if r.get("error") else ""
            rows += f'<div class="detail-item"><span class="name">{r.get("name", "?")}</span>{extra}{err}<span class="status {cls}">{s}</span></div>'
        parse_section = f'<div class="section"><h2>Parse Details</h2>{rows}</div>'
    else:
        parse_section = ""

    # Errors section
    scrape_errors = scrape.get("errors", [])
    if scrape_errors or resolved_fail > 0 or parsed_fail > 0:
        err_lines = ""
        for e in scrape_errors:
            err_lines += f'<div class="log-entry">⚠️ {e.get("source", "?")}: {e.get("error", "")[:200]}</div>'
        for r in resolve_details:
            if r.get("status") in ("failed", "error"):
                err_lines += f'<div class="log-entry">⚠️ Resolve: {r["name"]} — {r.get("error", "")}</div>'
        for r in parse_details:
            if r.get("status") in ("failed", "error", "timeout"):
                err_lines += f'<div class="log-entry">⚠️ Parse: {r["name"]} — {r.get("error", "timeout")}</div>'
        error_section = f'<div class="section"><h2>⚠️ Errors</h2>{err_lines}</div>'
    else:
        error_section = ""

    # Log
    log_entries = stats.get("log", [])
    if log_entries:
        log_rows = "".join(f'<div class="log-entry">{entry}</div>' for entry in log_entries[-15:])
        log_section = f'<div class="section"><h2>Pipeline Log (last {min(len(log_entries), 15)})</h2>{log_rows}</div>'
    else:
        log_section = ""

    return _EMAIL_TEMPLATE.format(
        date=date_str,
        checked=checked,
        new_ipos=new_ipos,
        new_class=new_class,
        status_changes=status_changes,
        resolved_ok=resolved_ok,
        resolved_fail=resolved_fail,
        resolve_ok_class=resolve_ok_class,
        resolve_fail_class=resolve_fail_class,
        parsed_ok=parsed_ok,
        parsed_fail=parsed_fail,
        parse_ok_class=parse_ok_class,
        parse_fail_class=parse_fail_class,
        skipped=skipped,
        no_url=no_url,
        free_mb=free_mb,
        total_mb=total_mb,
        load_1m=load_1m,
        load_5m=load_5m,
        resolve_details_section=resolve_section,
        parse_details_section=parse_section,
        error_section=error_section,
        log_section=log_section,
    )


def _build_telegram_summary(stats: dict) -> str:
    """Build a concise Telegram-friendly consolidated report."""
    scrape = stats.get("scrape_result", {})
    checked = stats.get("total_ipos_checked", 0)
    new_ipos = scrape.get("new_ipos_found", 0)
    status_changes = scrape.get("status_changes_detected", 0)
    resolved_ok = stats.get("resolved_success", 0)
    resolved_fail = stats.get("resolved_failed", 0)
    parsed_ok = stats.get("parsed_success", 0)
    parsed_fail = stats.get("parsed_failed", 0)

    budget = stats.get("resources", {})
    free_mb = budget.get("free_mb", "?")
    load_1m = budget.get("load_1m", "?")

    lines = [
        "📊 <b>Pipeline Run Complete</b> ✅",
        "━━━━━━━━━━━━━━━━━━━━",
        f"IPOs scanned: {checked}",
        f"New: {new_ipos}  ·  Status changes: {status_changes}",
        f"Resolved: ✅ {resolved_ok}  ❌ {resolved_fail}",
        f"Parsed: ✅ {parsed_ok}  ❌ {parsed_fail}",
        f"RAM: {free_mb}MB free  ·  Load: {load_1m}",
    ]

    # Errors
    errors = []
    for e in scrape.get("errors", []):
        errors.append(f"⚠️ <b>{e.get('source')}</b>: {e.get('error', '')[:120]}")
    for r in stats.get("resolve_details", []):
        if r.get("status") in ("failed", "error"):
            errors.append(f"⚠️ <b>Resolve: {r['name']}</b>: {r.get('error', '')[:120]}")
    for r in stats.get("parse_details", []):
        if r.get("status") in ("failed", "error", "timeout"):
            errors.append(f"⚠️ <b>Parse: {r['name']}</b>: {r.get('error', 'timeout')}")

    if errors:
        lines.append("")
        lines.append(f"<b>Errors ({len(errors)}):</b>")
        for err in errors[:5]:
            lines.append(err)
        if len(errors) > 5:
            lines.append(f"...and {len(errors) - 5} more")

    lines.append("")
    lines.append("🤖 IPO Auto Pipeline")

    return "\n".join(lines)


def _build_error_alerts(stats: dict) -> list[str]:
    """Generate individual error messages for Telegram (one per error type)."""
    alerts = []
    scrape = stats.get("scrape_result", {})
    for e in scrape.get("errors", []):
        alerts.append(
            f"⚠️ <b>Scrape Error</b>\n"
            f"Source: {e.get('source')}\n"
            f"Error: {e.get('error', '')[:200]}"
        )
    for r in stats.get("resolve_details", []):
        if r.get("status") in ("failed", "error"):
            alerts.append(
                f"⚠️ <b>Resolve Failed</b>\n"
                f"IPO: {r['name']}\n"
                f"Error: {r.get('error', '')[:200]}"
            )
    for r in stats.get("parse_details", []):
        if r.get("status") in ("failed", "error", "timeout"):
            alerts.append(
                f"⚠️ <b>Parse {'Timeout' if r['status']=='timeout' else 'Failed'}</b>\n"
                f"IPO: {r['name']}\n"
                f"Error: {r.get('error', 'timeout')}"
            )
    return alerts


# ── Public entry point ─────────────────────────────────────────

def send_pipeline_report(stats: dict) -> None:
    """Send pipeline completion report via email + Telegram.

    Args:
        stats: The pipeline stats dict from main.py pipeline_auto
    """
    def _send():
        # 1. HTML email
        if gmail_enabled():
            try:
                html = _build_html_email(stats)
                _send_gmail(
                    subject=f"📊 IPO Pipeline Report | {stats.get('total_ipos_checked', '?')} IPOs scanned",
                    body=html,
                    html=True,
                )
                logger.info("pipeline_report: email sent")
            except Exception as e:
                logger.error("pipeline_report: email failed: %s", e)
        else:
            logger.debug("pipeline_report: gmail not configured, skipping email")

        # 2. Telegram — consolidated summary
        if telegram_enabled():
            try:
                summary = _build_telegram_summary(stats)
                _send_telegram(summary)
                logger.info("pipeline_report: telegram summary sent")
            except Exception as e:
                logger.error("pipeline_report: telegram summary failed: %s", e)

            # 3. Telegram — individual error alerts (only errors, no spam)
            try:
                alerts = _build_error_alerts(stats)
                for alert in alerts:
                    _send_telegram(alert)
            except Exception as e:
                logger.error("pipeline_report: telegram alerts failed: %s", e)
        else:
            logger.debug("pipeline_report: telegram not configured, skipping")

    threading.Thread(target=_send, daemon=True, name="pipeline-report").start()
