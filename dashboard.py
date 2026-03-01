"""
dashboard.py — CLI stats viewer for Intern Hunter
Usage: python dashboard.py
"""

import json
import os
import sys
from datetime import datetime, timezone

import yaml


CONFIG_PATH = "config.yaml"


def _load_config():
    if not os.path.exists(CONFIG_PATH):
        print("❌ config.yaml not found. Run from the intern_hunter/ directory.")
        sys.exit(1)
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


# ── ANSI colors ───────────────────────────────────────────────────────────────

class C:
    RESET  = "\033[0m"
    BOLD   = "\033[1m"
    DIM    = "\033[2m"
    RED    = "\033[91m"
    GREEN  = "\033[92m"
    YELLOW = "\033[93m"
    BLUE   = "\033[94m"
    CYAN   = "\033[96m"
    WHITE  = "\033[97m"
    BG_DARK = "\033[48;5;236m"


def _color_score(score: int) -> str:
    if score >= 90:
        return f"{C.GREEN}{C.BOLD}{score:3d}{C.RESET}"
    if score >= 75:
        return f"{C.YELLOW}{score:3d}{C.RESET}"
    return f"{C.RED}{score:3d}{C.RESET}"


def _color_status(status: str) -> str:
    colors = {
        "auto_applied": C.GREEN,
        "manual_queue": C.YELLOW,
        "skipped":      C.DIM,
        "error":        C.RED,
        "dry_run":      C.CYAN,
    }
    color = colors.get(status, C.WHITE)
    return f"{color}{status:<14}{C.RESET}"


def _divider(char: str = "─", width: int = 72) -> str:
    return char * width


def _header(title: str, width: int = 72) -> str:
    pad = (width - len(title) - 4) // 2
    return f"{C.BOLD}{C.BLUE}{'═' * pad}  {title}  {'═' * pad}{C.RESET}"


# ── Main dashboard ────────────────────────────────────────────────────────────

def main():
    cfg = _load_config()
    db_path = cfg["agent"]["db_path"]

    if not os.path.exists(db_path):
        print(f"\n  ⚠  Database not found at {db_path}")
        print("  Run `python agent.py --dry-run` first to initialise it.\n")
        sys.exit(0)

    from core.database import (
        get_stats,
        get_recent_applications,
        get_resume_state,
        get_recent_logs,
        init_db,
    )
    init_db(db_path)

    stats = get_stats(db_path)
    recent_apps = get_recent_applications(db_path, limit=10)
    resume_state = get_resume_state(db_path)
    logs = get_recent_logs(db_path, limit=8)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print()
    print(_header("🎯  INTERN HUNTER  •  DASHBOARD"))
    print(f"  {C.DIM}Generated: {now}{C.RESET}")
    print()

    # ── Stats overview ────────────────────────────────────────────────────────
    print(f"  {C.BOLD}OVERVIEW{C.RESET}")
    print(_divider())
    print(
        f"  {C.GREEN}✅ Auto-Applied{C.RESET}  {C.BOLD}{stats['auto_applied']:>4}{C.RESET}"
        f"   {C.YELLOW}📋 Manual Queue{C.RESET} {C.BOLD}{stats['manual_queue']:>4}{C.RESET}"
        f"   {C.DIM}⏭  Skipped{C.RESET}     {C.BOLD}{stats['skipped']:>4}{C.RESET}"
    )
    print(
        f"  {C.CYAN}📅 Today{C.RESET}         {C.BOLD}{stats['today']:>4}{C.RESET}"
        f"   {C.WHITE}🗂  Total{C.RESET}        {C.BOLD}{stats['total']:>4}{C.RESET}"
    )
    print(_divider())
    print()

    # ── Recent applications ───────────────────────────────────────────────────
    print(f"  {C.BOLD}RECENT APPLICATIONS (last 10){C.RESET}")
    print(_divider())
    if not recent_apps:
        print(f"  {C.DIM}No applications yet.{C.RESET}")
    else:
        header_row = (
            f"  {'SCORE':>5}  {'STATUS':<14} {'PLATFORM':<12} "
            f"{'COMPANY':<22} {'ROLE':<28} {'DATE'}"
        )
        print(f"{C.DIM}{header_row}{C.RESET}")
        print("  " + "·" * 68)
        for app in recent_apps:
            score_str = _color_score(app.get("match_score", 0))
            status_str = _color_status(app.get("status", "unknown"))
            platform = (app.get("platform") or "")[:11]
            company  = (app.get("company") or "N/A")[:21]
            role     = (app.get("role") or "N/A")[:27]
            ts_raw   = app.get("applied_at", "")
            ts       = ts_raw[:10] if ts_raw else "N/A"
            print(
                f"  {score_str}/100  {status_str} {C.DIM}{platform:<12}{C.RESET}"
                f" {C.WHITE}{company:<22}{C.RESET} {role:<28} {C.DIM}{ts}{C.RESET}"
            )
    print(_divider())
    print()

    # ── Resume state ──────────────────────────────────────────────────────────
    print(f"  {C.BOLD}RESUME STATE{C.RESET}")
    print(_divider())
    if not resume_state:
        print(f"  {C.DIM}No resume state found. Run the agent to check Google Drive.{C.RESET}")
    else:
        fname = resume_state.get("file_name") or "(unknown)"
        modified = resume_state.get("drive_modified_time") or "N/A"
        last_checked = resume_state.get("last_checked") or "N/A"

        try:
            parsed = json.loads(resume_state.get("parsed_skills") or "{}")
            skills = parsed.get("skills", [])
            skills_str = ", ".join(skills[:12])
            if len(skills) > 12:
                skills_str += f" +{len(skills) - 12} more"
        except Exception:
            skills_str = "(parse error)"

        print(f"  {C.WHITE}File:{C.RESET}          {fname}")
        print(f"  {C.WHITE}Drive modified:{C.RESET} {modified[:19] if modified else 'N/A'}")
        print(f"  {C.WHITE}Last checked:{C.RESET}  {last_checked[:19] if last_checked else 'N/A'}")
        print(f"  {C.WHITE}Parsed skills:{C.RESET} {C.CYAN}{skills_str}{C.RESET}")
    print(_divider())
    print()

    # ── Recent logs ───────────────────────────────────────────────────────────
    print(f"  {C.BOLD}RECENT AGENT LOGS (last 8){C.RESET}")
    print(_divider())
    if not logs:
        print(f"  {C.DIM}No log entries yet.{C.RESET}")
    else:
        for entry in logs:
            level = entry.get("level", "INFO")
            msg   = entry.get("message", "")[:80]
            ts    = (entry.get("ts") or "")[:19]
            level_color = {
                "ERROR": C.RED, "WARNING": C.YELLOW, "INFO": C.GREEN
            }.get(level, C.WHITE)
            print(f"  {C.DIM}{ts}{C.RESET}  {level_color}{level:<7}{C.RESET}  {msg}")
    print(_divider())
    print()

    # ── Quick tips ────────────────────────────────────────────────────────────
    print(f"  {C.DIM}Commands:{C.RESET}")
    print(f"  {C.DIM}  python agent.py            — single run{C.RESET}")
    print(f"  {C.DIM}  python agent.py --schedule — run every 6h{C.RESET}")
    print(f"  {C.DIM}  python agent.py --dry-run  — test without applying{C.RESET}")
    print(f"  {C.DIM}  python setup.py            — re-run setup wizard{C.RESET}")
    print()


if __name__ == "__main__":
    main()
