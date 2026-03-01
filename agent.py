"""
agent.py — Main orchestrator for Intern Hunter
Usage:
    python agent.py              → single run
    python agent.py --schedule   → run every 6 hours
    python agent.py --dry-run    → full run, skip form submissions
"""

import argparse
import asyncio
import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional

import yaml

# ── Logging setup ─────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("intern_hunter")


# ── Config loader ─────────────────────────────────────────────────────────────

def load_config(path: str = "config.yaml") -> Dict[str, Any]:
    with open(path) as f:
        return yaml.safe_load(f)


# ── Pre-filter ────────────────────────────────────────────────────────────────

def _parse_stipend_text(text: str):
    """Extract (min, max) from stipend text like '₹15,000 - 20,000/month'."""
    if not text:
        return 0, 0
    nums = re.findall(r"[\d,]+", text)
    nums = [int(n.replace(",", "")) for n in nums if n.replace(",", "").isdigit()]
    if len(nums) >= 2:
        return nums[0], nums[1]
    if len(nums) == 1:
        return nums[0], nums[0]
    return 0, 0


def passes_hard_filter(job: Dict, cfg: Dict) -> tuple:
    """
    Returns (passes: bool, reason: str).
    Applies remote, stipend, and blacklist keyword rules.
    """
    prefs = cfg["preferences"]

    # Remote check
    if prefs.get("remote_only", True):
        text_to_check = " ".join([
            job.get("title", ""),
            job.get("description", ""),
            str(job.get("remote", "")),
        ]).lower()
        remote_match = any(kw in text_to_check for kw in prefs.get("remote_keywords", []))
        explicit_remote = job.get("remote", False)
        # Internshala WFH URLs are always remote
        if job.get("platform") == "internshala":
            explicit_remote = True
        if not remote_match and not explicit_remote:
            return False, "Not remote"

    # Stipend check
    min_stipend = prefs.get("min_stipend", 15000)
    stipend_min = job.get("stipend_min", 0)
    stipend_max = job.get("stipend_max", 0)
    # Re-parse if both are 0 but we have text
    if stipend_min == 0 and stipend_max == 0 and job.get("stipend_text"):
        stipend_min, stipend_max = _parse_stipend_text(job["stipend_text"])
        job["stipend_min"] = stipend_min
        job["stipend_max"] = stipend_max
    # If stipend is truly unknown (both 0 and no text), allow it (might be negotiable)
    if stipend_min > 0 and stipend_min < min_stipend:
        return False, f"Stipend too low ({stipend_min} < {min_stipend})"

    # Blacklist keywords
    blacklist = [kw.lower() for kw in prefs.get("blacklist_keywords", [])]
    combined_text = " ".join([
        job.get("title", ""),
        job.get("description", ""),
    ]).lower()
    for kw in blacklist:
        if kw in combined_text:
            return False, f"Blacklist keyword: '{kw}'"

    return True, "ok"


# ── Stipend display helper ────────────────────────────────────────────────────

def _format_stipend(job: Dict) -> str:
    if job.get("stipend_text"):
        return job["stipend_text"]
    mn, mx = job.get("stipend_min", 0), job.get("stipend_max", 0)
    if mn and mx and mn != mx:
        return f"₹{mn:,}–₹{mx:,}/month"
    if mn:
        return f"₹{mn:,}/month"
    return "Negotiable"


# ── Main orchestration cycle ──────────────────────────────────────────────────

async def run_cycle(cfg: Dict[str, Any], dry_run: bool = False) -> None:
    from core.database import (
        init_db, job_seen, insert_job, update_job_score, log_application,
        get_todays_auto_applied_count, get_manual_queue, db_log,
    )
    from core.drive_monitor import check_and_update_resume
    from core.matcher import score_job, generate_cover_letter
    from scrapers import internshala_scraper, linkedin_scraper, wellfound_scraper
    from apply.auto_apply import apply_internshala, apply_linkedin
    from notify.notifier import send_high_priority_alert, send_manual_queue_digest

    db_path = cfg["agent"]["db_path"]
    init_db(db_path)
    Path(cfg["agent"]["screenshots_dir"]).mkdir(parents=True, exist_ok=True)

    logger.info("═" * 60)
    logger.info("  INTERN HUNTER — Cycle started at %s", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
    if dry_run:
        logger.info("  ⚠  DRY RUN MODE — no real form submissions")
    logger.info("═" * 60)
    db_log(db_path, "INFO", f"Cycle started (dry_run={dry_run})")

    # ── Step 1: Check Drive for resume updates ────────────────────────────────
    logger.info("[1/9] Checking Google Drive for resume updates…")
    resume_updated = False
    parsed_skills: Optional[Dict] = None
    try:
        resume_updated, parsed_skills = check_and_update_resume(cfg)
        if resume_updated:
            logger.info("  ✓ Resume updated and re-parsed.")
        else:
            from core.database import get_resume_state
            state = get_resume_state(db_path)
            if state and state.get("parsed_skills"):
                try:
                    parsed_skills = json.loads(state["parsed_skills"])
                except Exception:
                    pass
    except Exception as exc:
        logger.error("Drive check failed: %s", exc)
        db_log(db_path, "ERROR", f"Drive check failed: {exc}")

    # ── Step 2: Scrape all platforms in parallel ──────────────────────────────
    logger.info("[2/9] Scraping internship platforms in parallel…")
    scrape_tasks = []
    if cfg["platforms"]["internshala"]["enabled"]:
        scrape_tasks.append(("internshala", internshala_scraper.scrape(cfg)))
    if cfg["platforms"]["linkedin"]["enabled"]:
        scrape_tasks.append(("linkedin", linkedin_scraper.scrape(cfg)))
    if cfg["platforms"]["wellfound"]["enabled"]:
        scrape_tasks.append(("wellfound", wellfound_scraper.scrape(cfg)))

    raw_results = await asyncio.gather(
        *[task for _, task in scrape_tasks],
        return_exceptions=True,
    )
    all_jobs: List[Dict] = []
    for (platform, _), result in zip(scrape_tasks, raw_results):
        if isinstance(result, Exception):
            logger.error("[%s] Scrape error: %s", platform, result)
            db_log(db_path, "ERROR", f"{platform} scrape failed: {result}")
        else:
            logger.info("  %s: %d jobs scraped", platform, len(result))
            all_jobs.extend(result)

    logger.info("  Total scraped: %d jobs", len(all_jobs))

    # ── Step 3: Deduplicate against jobs_seen ─────────────────────────────────
    logger.info("[3/9] Deduplicating…")
    new_jobs = [j for j in all_jobs if not job_seen(db_path, j["id"], ttl_days=14)]
    logger.info("  %d new (unseen) jobs after deduplication", len(new_jobs))

    # ── Step 4: Pre-filter (remote, stipend, blacklist) ───────────────────────
    logger.info("[4/9] Applying hard preference filters…")
    qualifying: List[Dict] = []
    for job in new_jobs:
        passes, reason = passes_hard_filter(job, cfg)
        if passes:
            qualifying.append(job)
        else:
            # Log the skip
            job["match_score"] = 0
            insert_job(db_path, job)
            log_application(db_path, {
                "job_id": job["id"],
                "platform": job.get("platform"),
                "company": job.get("company"),
                "role": job.get("title"),
                "stipend": _format_stipend(job),
                "apply_url": job.get("apply_url"),
                "status": "skipped",
                "match_score": 0,
                "cover_letter": "",
            })
            logger.debug("  Skipped '%s' @ %s: %s", job.get("title"), job.get("company"), reason)

    logger.info("  %d jobs pass hard filters", len(qualifying))

    # ── Step 5 & 6: AI score + cover letter ──────────────────────────────────
    logger.info("[5/9] AI scoring and cover letter generation…")
    min_score = cfg["preferences"]["min_ai_match_score"]
    daily_cap = cfg["preferences"]["max_applications_per_day"]
    applied_today = get_todays_auto_applied_count(db_path)

    high_priority: List[Dict] = []
    manual_queue_new: List[Dict] = []
    applied_this_cycle = 0

    for job in qualifying:
        # Score the job
        try:
            score, reason = score_job(cfg, job, parsed_skills)
        except Exception as exc:
            logger.error("Scoring error for %s: %s", job.get("url"), exc)
            score, reason = 0, str(exc)

        job["match_score"] = score
        insert_job(db_path, job)
        update_job_score(db_path, job["id"], score)

        logger.info(
            "  [%3d/100] %s%s @ %s — %s",
            score,
            "🔥 " if score >= 88 else ("✅ " if score >= 70 else "❌ "),
            job.get("title", "N/A"), job.get("company", "N/A"), reason[:70]
        )

        if score < min_score:
            log_application(db_path, {
                "job_id": job["id"],
                "platform": job.get("platform"),
                "company": job.get("company"),
                "role": job.get("title"),
                "stipend": _format_stipend(job),
                "apply_url": job.get("apply_url"),
                "status": "skipped",
                "match_score": score,
                "cover_letter": "",
            })
            continue

        # Generate cover letter
        try:
            cover_letter = generate_cover_letter(cfg, job, parsed_skills)
        except Exception as exc:
            logger.error("Cover letter error: %s", exc)
            cover_letter = ""

        job["cover_letter"] = cover_letter
        job["stipend"] = _format_stipend(job)
        job["role"] = job.get("title", "N/A")

        # ── Step 7: Auto-apply where possible ────────────────────────────────
        platform = job.get("platform", "")
        auto_apply_enabled = cfg["platforms"].get(platform, {}).get("auto_apply", False)
        can_auto = (
            auto_apply_enabled
            and (applied_today + applied_this_cycle) < daily_cap
        )

        # LinkedIn: only Easy Apply jobs
        if platform == "linkedin" and not job.get("easy_apply", False):
            can_auto = False

        status = "manual_queue"
        if can_auto:
            logger.info("  → Auto-applying to %s @ %s…", job.get("title"), job.get("company"))
            try:
                if platform == "internshala":
                    status = await apply_internshala(job, cover_letter, cfg, dry_run=dry_run)
                elif platform == "linkedin":
                    status = await apply_linkedin(job, cover_letter, cfg, dry_run=dry_run)
                if status == "auto_applied":
                    applied_this_cycle += 1
            except Exception as exc:
                logger.error("Auto-apply error: %s", exc)
                status = "error"

        app_id = log_application(db_path, {
            "job_id": job["id"],
            "platform": platform,
            "company": job.get("company"),
            "role": job.get("title"),
            "stipend": job["stipend"],
            "apply_url": job.get("apply_url"),
            "status": status,
            "match_score": score,
            "cover_letter": cover_letter,
        })

        job["status"] = status
        job["id"] = app_id  # Use DB application id for notification tracking

        # Categorise for notifications
        if score >= 85:
            high_priority.append(job)
        elif status == "manual_queue":
            manual_queue_new.append(job)

    logger.info(
        "[7/9] Applied this cycle: %d | High-priority alerts: %d | Manual queue: %d",
        applied_this_cycle, len(high_priority), len(manual_queue_new),
    )

    # ── Step 8: Send notifications ────────────────────────────────────────────
    logger.info("[8/9] Sending notifications…")
    for job in high_priority:
        try:
            send_high_priority_alert(cfg, job, job["id"])
        except Exception as exc:
            logger.error("High-priority notification error: %s", exc)

    if manual_queue_new:
        try:
            send_manual_queue_digest(cfg, manual_queue_new)
        except Exception as exc:
            logger.error("Manual queue notification error: %s", exc)

    # ── Step 9: Log summary ──────────────────────────────────────────────────
    logger.info("[9/9] Cycle complete.")
    summary = (
        f"Cycle complete — scraped: {len(all_jobs)}, new: {len(new_jobs)}, "
        f"qualifying: {len(qualifying)}, applied: {applied_this_cycle}, "
        f"manual_queue: {len(manual_queue_new)}, high_priority: {len(high_priority)}"
    )
    logger.info("  %s", summary)
    db_log(db_path, "INFO", summary)


# ── Scheduler entry point ─────────────────────────────────────────────────────

def run_scheduler(cfg: Dict[str, Any], dry_run: bool = False) -> None:
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger
    from notify.notifier import send_daily_digest

    scheduler = BlockingScheduler(timezone="UTC")
    interval_hours = cfg["agent"].get("run_interval_hours", 6)

    def _sync_run():
        # Reload config fresh on each cycle
        fresh_cfg = load_config()
        fresh_cfg["agent"]["dry_run"] = dry_run
        asyncio.run(run_cycle(fresh_cfg, dry_run=dry_run))

    def _daily_digest():
        fresh_cfg = load_config()
        send_daily_digest(fresh_cfg)

    scheduler.add_job(
        _sync_run,
        trigger=IntervalTrigger(hours=interval_hours),
        id="main_cycle",
        name=f"Main scrape+apply cycle every {interval_hours}h",
        replace_existing=True,
        max_instances=1,
    )

    digest_hour = cfg["agent"].get("daily_digest_hour", 8)
    digest_minute = cfg["agent"].get("daily_digest_minute", 0)
    scheduler.add_job(
        _daily_digest,
        trigger=CronTrigger(hour=digest_hour, minute=digest_minute, timezone="UTC"),
        id="daily_digest",
        name="Daily 8 AM digest email",
        replace_existing=True,
    )

    logger.info(
        "Scheduler started — main cycle every %dh, daily digest at %02d:%02d UTC",
        interval_hours, digest_hour, digest_minute,
    )
    logger.info("Running first cycle immediately…")
    _sync_run()  # Run immediately on start

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped.")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Intern Hunter — Automated AI-powered internship applicant"
    )
    parser.add_argument(
        "--schedule",
        action="store_true",
        help="Run every 6 hours using APScheduler (set in config.yaml)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Full pipeline but skip real form submissions",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Clear all cached/skipped jobs so agent re-evaluates everything fresh",
    )
    args = parser.parse_args()

    cfg = load_config()
    dry_run = args.dry_run or cfg["agent"].get("dry_run", False)

    if args.reset:
        from core.database import init_db, clear_seen_jobs
        db_path = cfg["agent"]["db_path"]
        init_db(db_path)
        n = clear_seen_jobs(db_path, older_than_days=0)
        logger.info("♻️  Reset complete — cleared %d cached jobs. Running fresh cycle now.", n)

    if args.schedule:
        run_scheduler(cfg, dry_run=dry_run)
    else:
        asyncio.run(run_cycle(cfg, dry_run=dry_run))


if __name__ == "__main__":
    main()
