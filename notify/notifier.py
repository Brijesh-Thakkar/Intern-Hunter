"""
notify/notifier.py — Email (SMTP/Gmail) and Telegram notifications for Intern Hunter
"""

import logging
import smtplib
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import List, Dict, Any, Optional

import requests

from core.database import (
    get_applications_since,
    get_manual_queue,
    mark_notified,
    db_log,
)

logger = logging.getLogger(__name__)


# ── Core email sender ─────────────────────────────────────────────────────────

def _send_email(cfg: Dict[str, Any], to_addresses: List[str], subject: str, html_body: str) -> bool:
    email_cfg = cfg["email"]
    sender = email_cfg["sender_address"]
    password = email_cfg["app_password"]

    if not sender or not password:
        logger.warning("[Notifier] Gmail credentials not configured — skipping email.")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"Intern Hunter <{sender}>"
    msg["To"] = ", ".join(to_addresses)
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP(email_cfg["smtp_host"], email_cfg["smtp_port"]) as server:
            server.ehlo()
            server.starttls()
            server.login(sender, password)
            server.sendmail(sender, to_addresses, msg.as_string())
        logger.info("[Notifier] Email sent to %s | Subject: %s", to_addresses, subject)
        return True
    except Exception as exc:
        logger.error("[Notifier] Email send failed: %s", exc)
        return False


# ── Telegram sender ───────────────────────────────────────────────────────────

def _send_telegram(cfg: Dict[str, Any], text: str) -> bool:
    tg = cfg.get("telegram", {})
    if not tg.get("enabled") or not tg.get("bot_token") or not tg.get("chat_id"):
        return False
    try:
        url = f"https://api.telegram.org/bot{tg['bot_token']}/sendMessage"
        resp = requests.post(url, json={
            "chat_id": tg["chat_id"],
            "text": text,
            "parse_mode": "HTML",
        }, timeout=10)
        ok = resp.status_code == 200
        if not ok:
            logger.warning("[Notifier] Telegram send failed: %s", resp.text)
        return ok
    except Exception as exc:
        logger.error("[Notifier] Telegram error: %s", exc)
        return False


# ── HTML helpers ──────────────────────────────────────────────────────────────

def _score_badge_color(score: int) -> str:
    if score >= 90:
        return "#2ecc71"
    if score >= 75:
        return "#f39c12"
    return "#e74c3c"


def _job_card_html(job: Dict[str, Any], show_apply_btn: bool = False) -> str:
    score = job.get("match_score", 0)
    color = _score_badge_color(score)
    apply_btn = ""
    if show_apply_btn and job.get("apply_url"):
        apply_btn = f"""
        <a href="{job['apply_url']}" style="
            display:inline-block;margin-top:10px;padding:8px 18px;
            background:#2563eb;color:#fff;border-radius:6px;text-decoration:none;
            font-weight:bold;font-size:13px;">Apply Now →</a>"""

    cover_section = ""
    if job.get("cover_letter"):
        cover_section = f"""
        <div style="margin-top:12px;padding:12px;background:#f8f9fa;
                    border-left:3px solid #2563eb;border-radius:4px;
                    font-size:13px;color:#374151;white-space:pre-wrap;">{job['cover_letter']}</div>"""

    return f"""
    <div style="background:#fff;border:1px solid #e5e7eb;border-radius:10px;
                padding:18px;margin-bottom:16px;font-family:sans-serif;">
      <div style="display:flex;justify-content:space-between;align-items:center;">
        <div>
          <div style="font-size:16px;font-weight:bold;color:#111;">{job.get('role', job.get('title','N/A'))}</div>
          <div style="font-size:14px;color:#6b7280;">🏢 {job.get('company','N/A')} &nbsp;|&nbsp;
            💰 {job.get('stipend','N/A')} &nbsp;|&nbsp; 🌐 {job.get('platform','').capitalize()}</div>
        </div>
        <div style="background:{color};color:#fff;padding:6px 12px;
                    border-radius:20px;font-weight:bold;font-size:14px;">
          {score}/100
        </div>
      </div>
      {cover_section}
      {apply_btn}
    </div>"""


def _email_wrapper(body_content: str, title: str) -> str:
    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="background:#f3f4f6;padding:24px;font-family:Arial,sans-serif;">
  <div style="max-width:640px;margin:0 auto;">
    <div style="background:#1e293b;padding:20px 24px;border-radius:10px 10px 0 0;text-align:center;">
      <h1 style="color:#fff;margin:0;font-size:20px;">🎯 Intern Hunter</h1>
      <p style="color:#94a3b8;margin:4px 0 0;font-size:13px;">{title}</p>
    </div>
    <div style="background:#fff;padding:24px;border-radius:0 0 10px 10px;border:1px solid #e5e7eb;border-top:none;">
      {body_content}
    </div>
    <p style="text-align:center;color:#9ca3af;font-size:11px;margin-top:12px;">
      Intern Hunter — Automated by AI for Brijesh Thakkar, IIT Jodhpur
    </p>
  </div>
</body>
</html>"""


# ── Notification types ────────────────────────────────────────────────────────

def send_high_priority_alert(
    cfg: Dict[str, Any],
    job: Dict[str, Any],
    app_id: int,
) -> None:
    """
    🔴 HIGH PRIORITY — match score ≥ 85.
    Send immediately to personal + college email AND Telegram.
    """
    db_path = cfg["agent"]["db_path"]
    score = job.get("match_score", 0)
    role = job.get("role", job.get("title", "Intern"))
    company = job.get("company", "Unknown")
    stipend = job.get("stipend", "N/A")
    status = job.get("status", "auto_applied")
    cover = job.get("cover_letter", "")

    # Build recipient list
    recipients = [cfg["candidate"]["personal_email"]]
    college_email = cfg["candidate"].get("college_email", "")
    if college_email and college_email not in recipients:
        recipients.append(college_email)

    subject = f"🔴 HIGH PRIORITY: {role} @ {company} — Apply NOW ({score}% match)"

    action_note = ""
    if status in ("manual_queue", "dry_run"):
        action_note = f"""
        <div style="background:#fef3c7;border:1px solid #f59e0b;border-radius:8px;
                    padding:14px;margin-bottom:16px;font-size:14px;color:#92400e;">
          ⚠️ <strong>Action needed:</strong> This job is in your manual queue.
          Click "Apply Now" below to apply directly.
        </div>"""
    else:
        action_note = f"""
        <div style="background:#d1fae5;border:1px solid #10b981;border-radius:8px;
                    padding:14px;margin-bottom:16px;font-size:14px;color:#065f46;">
          ✅ <strong>Auto-applied!</strong> Application submitted successfully.
        </div>"""

    body = f"""
    <h2 style="color:#dc2626;margin-top:0;">🔴 HIGH PRIORITY MATCH</h2>
    {action_note}
    {_job_card_html({**job, "cover_letter": cover}, show_apply_btn=(status == "manual_queue"))}
        <p style="color:#6b7280;font-size:13px;">Found at {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC</p>
    """

    html = _email_wrapper(body, f"HIGH PRIORITY Alert — {company}")
    _send_email(cfg, recipients, subject, html)

    # Telegram
    tg_text = (
        f"🔴 <b>HIGH PRIORITY MATCH ({score}/100)</b>\n"
        f"<b>{role}</b> @ {company}\n"
        f"💰 {stipend}\n"
        f"Status: {status}\n"
        f"🔗 {job.get('apply_url', '')}"
    )
    _send_telegram(cfg, tg_text)

    mark_notified(db_path, app_id)
    db_log(db_path, "INFO", f"HIGH PRIORITY alert sent for {company} — {role} (score: {score})")


def send_auto_applied_confirmation(cfg: Dict[str, Any], job: Dict[str, Any]) -> None:
    """
    ✅ AUTO-APPLIED CONFIRMATION — fires immediately after every successful application.
    Sent to both personal email AND college email.
    """
    db_path = cfg["agent"]["db_path"]
    score = job.get("match_score", 0)
    role = job.get("role", job.get("title", "Intern"))
    company = job.get("company", "Unknown")
    stipend = job.get("stipend", "N/A")
    platform = job.get("platform", "")
    apply_url = job.get("apply_url", "")
    cover = job.get("cover_letter", "")
    applied_at = datetime.now(timezone.utc).strftime("%d %b %Y, %H:%M UTC")

    # Score badge colour: green ≥ 85, yellow 70-84
    if score >= 85:
        badge_bg, badge_text = "#16a34a", "#ffffff"
    elif score >= 70:
        badge_bg, badge_text = "#d97706", "#ffffff"
    else:
        badge_bg, badge_text = "#6b7280", "#ffffff"

    platform_icon = {"internshala": "🎓", "linkedin": "💼", "wellfound": "🚀"}.get(platform, "🌐")

    view_btn = ""
    if apply_url:
        view_btn = f"""
        <a href="{apply_url}" style="
            display:inline-block;margin-top:14px;padding:10px 22px;
            background:#2563eb;color:#fff;border-radius:8px;
            text-decoration:none;font-weight:bold;font-size:13px;">View Application →</a>"""

    cover_section = ""
    if cover:
        cover_section = f"""
        <div style="margin-top:16px;">
          <div style="font-size:12px;font-weight:bold;color:#6b7280;
                      text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px;"
          >Cover Letter Submitted</div>
          <div style="padding:14px;background:#f8fafc;border-left:4px solid #2563eb;
                      border-radius:4px;font-size:13px;color:#374151;
                      white-space:pre-wrap;line-height:1.6;">{cover}</div>
        </div>"""

    body = f"""
    <div style="background:#d1fae5;border:1px solid #10b981;border-radius:10px;
                padding:16px;margin-bottom:20px;">
      <div style="font-size:15px;font-weight:bold;color:#065f46;">✅ Application submitted successfully</div>
      <div style="font-size:13px;color:#047857;margin-top:4px;">Applied at {applied_at}</div>
    </div>

    <div style="background:#fff;border:1px solid #e5e7eb;border-radius:10px;padding:20px;">
      <div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:10px;">
        <div>
          <div style="font-size:20px;font-weight:bold;color:#111;">{role}</div>
          <div style="font-size:14px;color:#6b7280;margin-top:4px;">
            🏢 {company} &nbsp;|&nbsp; 💰 {stipend} &nbsp;|&nbsp; {platform_icon} {platform.capitalize()}
          </div>
        </div>
        <div style="background:{badge_bg};color:{badge_text};padding:8px 16px;
                    border-radius:20px;font-weight:bold;font-size:15px;white-space:nowrap;">
          {score}/100
        </div>
      </div>
      {cover_section}
      {view_btn}
    </div>
    <p style="color:#9ca3af;font-size:12px;margin-top:16px;">
      This confirmation was sent automatically by Intern Hunter.
    </p>
    """

    subject = f"\u2705 Applied: {role} @ {company} \u2014 Intern Hunter"
    html = _email_wrapper(body, f"Application Confirmed \u2014 {company}")

    recipients = [cfg["candidate"]["personal_email"]]
    college_email = cfg["candidate"].get("college_email", "")
    if college_email and college_email not in recipients:
        recipients.append(college_email)

    _send_email(cfg, recipients, subject, html)

    # Telegram ping
    _send_telegram(
        cfg,
        f"\u2705 <b>Applied:</b> {role} @ {company}\n"
        f"\U0001f4b0 {stipend} | Score: {score}/100\n"
        f"{platform_icon} {platform.capitalize()} | {applied_at}\n"
        f"\U0001f517 {apply_url}",
    )

    db_log(db_path, "INFO", f"Auto-apply confirmation sent: {company} — {role} ({score}/100)")


def send_manual_queue_digest(cfg: Dict[str, Any], jobs: List[Dict]) -> None:
    """
    📋 MANUAL QUEUE — send digest of un-notified manual queue jobs.
    """
    if not jobs:
        return

    db_path = cfg["agent"]["db_path"]
    recipients = [cfg["candidate"]["personal_email"]]
    college_email = cfg["candidate"].get("college_email", "")
    if college_email and college_email not in recipients:
        recipients.append(college_email)

    n = len(jobs)
    subject = f"📋 {n} internship{'s' if n > 1 else ''} waiting for your review | Intern Hunter"

    cards_html = "".join(
        _job_card_html({**j, "role": j.get("role", j.get("title")), "cover_letter": j.get("cover_letter", "")},
                       show_apply_btn=True)
        for j in sorted(jobs, key=lambda x: x.get("match_score", 0), reverse=True)
    )

    body = f"""
    <h2 style="margin-top:0;color:#1e293b;">📋 Your Manual Queue</h2>
    <p style="color:#6b7280;font-size:14px;">
      {n} internship{'s' if n > 1 else ''} matched your profile and are waiting for a quick review.
      Each should take about 30 seconds to apply.
    </p>
    {cards_html}
    """

    html = _email_wrapper(body, f"{n} internship(s) in queue")
    _send_email(cfg, recipients, subject, html)

    # Mark notified
    for j in jobs:
        if j.get("id"):
            mark_notified(db_path, j["id"])

    db_log(db_path, "INFO", f"Manual queue digest sent: {n} jobs")


def send_daily_digest(cfg: Dict[str, Any]) -> None:
    """
    📊 DAILY DIGEST — summary at 8 AM sent to personal email.
    """
    db_path = cfg["agent"]["db_path"]
    recipients = [cfg["candidate"]["personal_email"]]

    since_yesterday = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    auto_applied = get_applications_since(db_path, since_yesterday, status="auto_applied")
    manual_queued = get_applications_since(db_path, since_yesterday, status="manual_queue")
    skipped = get_applications_since(db_path, since_yesterday, status="skipped")

    n_auto = len(auto_applied)
    n_manual = len(manual_queued)
    n_skipped = len(skipped)

    subject = f"📊 Daily Intern Report: {n_auto} auto-applied, {n_manual} in queue"

    auto_applied_cards_html = "".join(
        _job_card_html(
            {
                **j,
                "role": j.get("role", j.get("title")),
                "stipend": j.get("stipend", "N/A"),
                "cover_letter": j.get("cover_letter", ""),
            },
            show_apply_btn=False,
        )
        for j in sorted(auto_applied, key=lambda x: x.get("match_score", 0), reverse=True)[:8]
    )
    manual_list_html = "".join(
        _job_card_html({**j, "role": j.get("role", j.get("title")), "cover_letter": ""}, show_apply_btn=True)
        for j in manual_queued[:5]
    )

    auto_section = ""
    if auto_applied_cards_html:
        auto_section = f"""
        <h3 style="color:#065f46;margin-top:24px;">✅ Already Applied Today ({n_auto} jobs)</h3>
        <p style="color:#6b7280;font-size:13px;margin-top:-8px;margin-bottom:12px;">
          Applications submitted automatically in the last 24 hours.
        </p>
        {auto_applied_cards_html}"""

    body = f"""
    <h2 style="margin-top:0;color:#1e293b;">📊 Daily Internship Report</h2>
    <div style="display:flex;gap:12px;margin-bottom:24px;flex-wrap:wrap;">
      <div style="flex:1;min-width:130px;background:#d1fae5;border-radius:10px;padding:16px;text-align:center;">
        <div style="font-size:28px;font-weight:bold;color:#065f46;">{n_auto}</div>
        <div style="font-size:12px;color:#065f46;">Auto-Applied</div>
      </div>
      <div style="flex:1;min-width:130px;background:#fef3c7;border-radius:10px;padding:16px;text-align:center;">
        <div style="font-size:28px;font-weight:bold;color:#92400e;">{n_manual}</div>
        <div style="font-size:12px;color:#92400e;">In Queue</div>
      </div>
      <div style="flex:1;min-width:130px;background:#f3f4f6;border-radius:10px;padding:16px;text-align:center;">
        <div style="font-size:28px;font-weight:bold;color:#374151;">{n_skipped}</div>
        <div style="font-size:12px;color:#374151;">Skipped</div>
      </div>
    </div>

    {auto_section}

    {"<h3 style='color:#92400e;margin-top:24px;'>📋 Manual Queue (top 5) — Review & Apply</h3>" + manual_list_html if manual_list_html else ""}

    <p style="color:#9ca3af;font-size:12px;margin-top:20px;">
      Generated at {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC by Intern Hunter
    </p>
    """

    html = _email_wrapper(body, "Daily Report")
    _send_email(cfg, recipients, subject, html)
    db_log(db_path, "INFO", f"Daily digest sent: {n_auto} auto, {n_manual} manual, {n_skipped} skipped")


def send_error_alert(cfg: Dict[str, Any], error_message: str) -> None:
    """Send a simple error alert to personal email."""
    recipients = [cfg["candidate"]["personal_email"]]
    subject = "⚠️ Intern Hunter — Agent Error"
    body = f"""
    <h2 style="color:#dc2626;margin-top:0;">⚠️ Agent Error</h2>
    <pre style="background:#f8f9fa;padding:16px;border-radius:8px;
                font-size:13px;overflow-x:auto;white-space:pre-wrap;">{error_message}</pre>
    <p style="color:#6b7280;font-size:13px;">
      Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC
    </p>
    """
    html = _email_wrapper(body, "Error Alert")
    _send_email(cfg, recipients, subject, html)
