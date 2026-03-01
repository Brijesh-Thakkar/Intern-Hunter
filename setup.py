"""
setup.py — One-time interactive setup wizard for Intern Hunter
Guides through: API keys, Gmail, college email, Telegram, Google Drive OAuth,
and Internshala/LinkedIn browser session capture.
"""

import asyncio
import getpass
import json
import os
import re
import sys
from pathlib import Path

import yaml


CONFIG_PATH = "config.yaml"
STEPS_TOTAL = 6


def _banner():
    print("\n" + "═" * 60)
    print("   🎯  INTERN HUNTER — Setup Wizard")
    print("   Automated AI Internship Agent for Brijesh Thakkar")
    print("═" * 60)


def _step(n: int, title: str):
    print(f"\n[Step {n}/{STEPS_TOTAL}] {title}")
    print("─" * 50)


def _load_config() -> dict:
    if not os.path.exists(CONFIG_PATH):
        print(f"❌ config.yaml not found at {CONFIG_PATH}. Make sure you're in the intern_hunter/ directory.")
        sys.exit(1)
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def _save_config(cfg: dict) -> None:
    with open(CONFIG_PATH, "w") as f:
        yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
    print(f"  ✓ Configuration saved to {CONFIG_PATH}")


def _prompt(prompt_text: str, default: str = "", secret: bool = False) -> str:
    if default:
        display_default = "****" if secret else default
        prompt_text = f"{prompt_text} [{display_default}]: "
    else:
        prompt_text = f"{prompt_text}: "
    if secret:
        value = getpass.getpass(prompt_text).strip()
    else:
        value = input(prompt_text).strip()
    return value if value else default


def _validate_email(email: str) -> bool:
    return bool(re.match(r"[^@]+@[^@]+\.[^@]+", email))


# ── Step 1: Mistral API key ───────────────────────────────────────────────────

def setup_openai(cfg: dict) -> dict:
    _step(1, "Mistral AI API Key (for job scoring + cover letters)")
    print("  Get your FREE key at: https://console.mistral.ai/api-keys")
    print("  Models: mistral-small-latest (scoring) + mistral-medium-latest (cover letters)")

    existing = cfg.get("mistral", {}).get("api_key", "")
    key = _prompt("Mistral API key", default=existing, secret=True)

    if not key:
        print("  ⚠  No key provided — AI features will not work until this is set.")
    else:
        cfg.setdefault("mistral", {})["api_key"] = key
        print("  ✓ Mistral API key saved.")
    return cfg


# ── Step 2: Gmail + App Password ─────────────────────────────────────────────

def setup_gmail(cfg: dict) -> dict:
    _step(2, "Gmail Address + App Password (for notifications)")
    print("  How to create an App Password:")
    print("  1. Go to: https://myaccount.google.com/apppasswords")
    print("  2. Select 'Mail' + device name, click Generate")
    print("  3. Copy the 16-character password")

    existing_email = cfg.get("email", {}).get("sender_address", "")
    email = _prompt("Your Gmail address", default=existing_email)
    if email and not _validate_email(email):
        print("  ⚠  That doesn't look like a valid email — saved anyway.")

    existing_pw = cfg.get("email", {}).get("app_password", "")
    app_pw = _prompt("Gmail App Password (16 chars)", default=existing_pw, secret=True)

    cfg.setdefault("email", {})
    cfg["email"]["sender_address"] = email
    cfg["email"]["app_password"] = app_pw
    print("  ✓ Gmail credentials saved.")
    return cfg


# ── Step 3: College email for HIGH PRIORITY alerts ────────────────────────────

def setup_college_email(cfg: dict) -> dict:
    _step(3, "College Email (for HIGH PRIORITY duplicate alerts)")
    print("  Your IIT Jodhpur email (e.g. b22cs001@iitj.ac.in)")
    print("  HIGH PRIORITY matches (score ≥ 85) will be sent to BOTH emails.")

    existing = cfg.get("candidate", {}).get("college_email", "")
    email = _prompt("College email address", default=existing)
    if email and not _validate_email(email):
        print("  ⚠  That doesn't look like a valid email — saved anyway.")

    cfg.setdefault("candidate", {})["college_email"] = email
    print("  ✓ College email saved.")
    return cfg


# ── Step 4: Telegram (optional) ───────────────────────────────────────────────

def setup_telegram(cfg: dict) -> dict:
    _step(4, "Telegram Bot (optional — for instant HIGH PRIORITY alerts)")
    print("  How to set up:")
    print("  1. Open Telegram, search @BotFather, send /newbot")
    print("  2. Copy the bot token")
    print("  3. Send a message to your bot, then get your chat_id from:")
    print("     https://api.telegram.org/bot<TOKEN>/getUpdates")

    skip = input("  Skip Telegram setup? [Y/n]: ").strip().lower()
    if skip in ("", "y", "yes"):
        print("  → Telegram skipped.")
        return cfg

    existing_token = cfg.get("telegram", {}).get("bot_token", "")
    existing_chat = cfg.get("telegram", {}).get("chat_id", "")
    token = _prompt("Bot token", default=existing_token, secret=True)
    chat_id = _prompt("Chat ID", default=existing_chat)

    cfg.setdefault("telegram", {})
    cfg["telegram"]["enabled"] = bool(token and chat_id)
    cfg["telegram"]["bot_token"] = token
    cfg["telegram"]["chat_id"] = chat_id

    if token and chat_id:
        print("  ✓ Telegram configured.")
    return cfg


# ── Step 5: Google Drive OAuth ────────────────────────────────────────────────

def setup_google_drive(cfg: dict) -> dict:
    _step(5, "Google Drive OAuth (to monitor your resume folder)")
    print("  Requirements:")
    print("  1. Enable Google Drive API: https://console.cloud.google.com/")
    print("  2. Create OAuth2 credentials (type: Desktop app)")
    print("  3. Download as credentials.json → save to credentials/credentials.json")
    print()
    print(f"  Drive folder to monitor: {cfg.get('google_drive', {}).get('folder_id', '(not set)')}")

    creds_path = cfg.get("google_drive", {}).get("credentials_path", "credentials/credentials.json")
    if not os.path.exists(creds_path):
        print(f"\n  ⚠  credentials.json not found at {creds_path}")
        dest = input(f"  Enter the full path to your credentials.json [or press Enter to skip]: ").strip()
        if dest and os.path.exists(dest):
            Path("credentials").mkdir(exist_ok=True)
            import shutil
            shutil.copy(dest, creds_path)
            print(f"  ✓ Copied to {creds_path}")
        else:
            print("  → Drive setup skipped. You can re-run setup.py later.")
            return cfg

    print("\n  Running Google Drive OAuth flow — a browser window will open…")
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request
        import google.oauth2.credentials

        scopes = cfg["google_drive"]["scopes"]
        token_path = cfg["google_drive"]["token_path"]
        Path(token_path).parent.mkdir(parents=True, exist_ok=True)

        flow = InstalledAppFlow.from_client_secrets_file(creds_path, scopes)
        creds = flow.run_local_server(port=0)
        with open(token_path, "w") as tok_f:
            tok_f.write(creds.to_json())
        print(f"  ✓ OAuth token saved to {token_path}")
    except Exception as exc:
        print(f"  ❌ OAuth failed: {exc}")
        print("  You can re-run setup.py to try again.")

    return cfg


# ── Step 6: Browser session setup ─────────────────────────────────────────────

def setup_browser_sessions(cfg: dict) -> dict:
    _step(6, "Browser Sessions (log in to Internshala + LinkedIn)")
    print("  We'll open a real browser so you can log in manually.")
    print("  Your cookies will be saved — the agent uses them to apply automatically.")
    print()

    platforms_to_setup = []
    if cfg["platforms"]["internshala"]["enabled"]:
        platforms_to_setup.append(("Internshala", "https://internshala.com/login/", "sessions/internshala.json"))
    if cfg["platforms"]["linkedin"]["enabled"]:
        platforms_to_setup.append(("LinkedIn", "https://www.linkedin.com/login", "sessions/linkedin.json"))

    for name, url, session_file in platforms_to_setup:
        existing = os.path.exists(session_file)
        existing_note = " (already exists, press Enter to skip)" if existing else ""
        choice = input(f"\n  Setup {name} session{existing_note}? [Y/n]: ").strip().lower()
        if choice in ("n", "no"):
            print(f"  → {name} session skipped.")
            continue
        if existing and choice in ("", "y", "yes"):
            overwrite = input(f"  Session file exists. Overwrite? [y/N]: ").strip().lower()
            if overwrite not in ("y", "yes"):
                print(f"  → Keeping existing {name} session.")
                continue

        Path(session_file).parent.mkdir(parents=True, exist_ok=True)
        try:
            from apply.auto_apply import save_session_interactive
            asyncio.run(save_session_interactive(name, url, session_file))
        except Exception as exc:
            print(f"  ❌ {name} session setup failed: {exc}")

    return cfg


# ── Main wizard ───────────────────────────────────────────────────────────────

def main():
    _banner()
    print("\nThis wizard will guide you through setup in ~15 minutes.")
    print("All settings are saved to config.yaml.")
    print("You can re-run this script at any time to update settings.\n")

    input("Press Enter to begin… ")

    cfg = _load_config()

    cfg = setup_openai(cfg)
    _save_config(cfg)

    cfg = setup_gmail(cfg)
    _save_config(cfg)

    cfg = setup_college_email(cfg)
    _save_config(cfg)

    cfg = setup_telegram(cfg)
    _save_config(cfg)

    cfg = setup_google_drive(cfg)
    _save_config(cfg)

    cfg = setup_browser_sessions(cfg)
    _save_config(cfg)

    print("\n" + "═" * 60)
    print("   ✅  SETUP COMPLETE!")
    print("═" * 60)
    print()
    print("  Next steps:")
    print("  1. Single run:    python agent.py")
    print("  2. Scheduled:     python agent.py --schedule")
    print("  3. Dry run test:  python agent.py --dry-run")
    print("  4. Dashboard:     python dashboard.py")
    print()
    print("  Logs → data/agent.db (agent_log table)")
    print("  Screenshots → data/screenshots/")
    print("═" * 60 + "\n")


if __name__ == "__main__":
    if "--internshala-only" in sys.argv:
        # ── Quick session capture mode — no full wizard ───────────────────
        cfg = _load_config()
        name = "Internshala"
        url  = cfg["platforms"]["internshala"]["base_url"]
        session_file = cfg["platforms"]["internshala"]["session_file"]
        Path(session_file).parent.mkdir(parents=True, exist_ok=True)

        print("\n" + "═" * 60)
        print("   🌐  Internshala Session Capture")
        print("═" * 60)
        print(f"\n  A browser will open at: {url}")
        print("  Log in to Internshala manually, then close the tab.")
        print("  Your cookies will be saved for auto-apply.\n")

        from apply.auto_apply import save_session_interactive
        asyncio.run(save_session_interactive(name, url, session_file))
        print(f"\n  ✅  Session saved → {session_file}")

        # ── Offer to SCP session to Oracle VM if .env.deploy exists ──────
        if os.path.exists(".env.deploy"):
            choice = input("\n  Upload session to Oracle Cloud VM? [Y/n]: ").strip().lower()
            if choice not in ("n", "no"):
                env: dict = {}
                with open(".env.deploy") as fh:
                    for raw_line in fh:
                        raw_line = raw_line.strip()
                        if raw_line and not raw_line.startswith("#") and "=" in raw_line:
                            k, v = raw_line.split("=", 1)
                            env[k.strip()] = v.strip().strip('"')
                key_path = env.get("ORACLE_SSH_KEY_PATH", "")
                vm_ip    = env.get("ORACLE_VM_IP", "")
                if key_path and vm_ip:
                    import subprocess
                    result = subprocess.run([
                        "scp", "-i", key_path,
                        "-o", "StrictHostKeyChecking=no",
                        session_file,
                        f"ubuntu@{vm_ip}:~/intern_hunter/{session_file}",
                    ])
                    if result.returncode == 0:
                        print(f"  ✅  Session uploaded to Oracle VM → {vm_ip}")
                    else:
                        print(f"  ❌  SCP failed — upload manually:\n"
                              f"      scp -i {key_path} {session_file} "
                              f"ubuntu@{vm_ip}:~/intern_hunter/{session_file}")
                else:
                    print("  ⚠️   ORACLE_SSH_KEY_PATH or ORACLE_VM_IP missing in .env.deploy")
        sys.exit(0)

    main()
