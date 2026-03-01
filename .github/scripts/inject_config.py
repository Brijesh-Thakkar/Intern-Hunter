"""
.github/scripts/inject_config.py
─────────────────────────────────
Runs inside GitHub Actions BEFORE agent.py.

Reads:  config.template.yaml  (committed to repo, no real secrets)
Writes: config.yaml            (generated at runtime, gitignored)
        sessions/internshala.json  (decoded from INTERNSHALA_SESSION secret)
        sessions/linkedin.json     (decoded from LINKEDIN_SESSION secret)
        data/                      (directory scaffolding)
        data/screenshots/

All secret values come from environment variables injected by the workflow step.
Missing optional secrets are handled gracefully (empty string / disabled).
"""

import base64
import os
import sys
from pathlib import Path

import yaml

# ── Resolve repo root ──────────────────────────────────────────────────────────
# This script lives at .github/scripts/inject_config.py
# Repo root is two levels up.
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


def _mask(value: str) -> str:
    """Show first 4 chars only for logging."""
    if not value:
        return "(not set)"
    return value[:4] + "*" * max(0, len(value) - 4)


def load_template() -> dict:
    template_path = REPO_ROOT / "config.template.yaml"
    if not template_path.exists():
        print(f"[inject_config] ERROR: {template_path} not found!", file=sys.stderr)
        sys.exit(1)
    with open(template_path) as f:
        return yaml.safe_load(f)


def inject_secrets(cfg: dict) -> dict:
    """Overwrite secret fields in cfg with values from environment variables."""

    mistral_key = _env("MISTRAL_API_KEY")
    gmail_sender = _env("GMAIL_SENDER")
    gmail_password = _env("GMAIL_APP_PASSWORD")
    college_email = _env("COLLEGE_EMAIL")
    tg_token = _env("TELEGRAM_BOT_TOKEN")
    tg_chat = _env("TELEGRAM_CHAT_ID")

    # Mistral
    cfg.setdefault("mistral", {})["api_key"] = mistral_key

    # Email
    cfg.setdefault("email", {})["sender_address"] = gmail_sender
    cfg["email"]["app_password"] = gmail_password

    # Candidate
    cfg.setdefault("candidate", {})["college_email"] = college_email

    # Telegram — auto-enable only if token is provided
    cfg.setdefault("telegram", {})["bot_token"] = tg_token
    cfg["telegram"]["chat_id"] = tg_chat
    cfg["telegram"]["enabled"] = bool(tg_token)

    # Summary (masked)
    print("[inject_config] Injected secrets:")
    print(f"  MISTRAL_API_KEY      → {_mask(mistral_key)}")
    print(f"  GMAIL_SENDER         → {_mask(gmail_sender)}")
    print(f"  GMAIL_APP_PASSWORD   → {_mask(gmail_password)}")
    print(f"  COLLEGE_EMAIL        → {_mask(college_email)}")
    print(f"  TELEGRAM_BOT_TOKEN   → {_mask(tg_token)}")
    print(f"  TELEGRAM_CHAT_ID     → {_mask(tg_chat)}")
    print(f"  Telegram enabled     → {cfg['telegram']['enabled']}")

    return cfg


def write_config(cfg: dict) -> None:
    config_path = REPO_ROOT / "config.yaml"
    with open(config_path, "w") as f:
        yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
    print(f"[inject_config] config.yaml written → {config_path}")


def decode_session(secret_name: str, output_path: Path) -> bool:
    """
    Decode a base64-encoded session JSON from an env var and write it to disk.
    Returns True if the session was written, False if the secret was missing.
    """
    b64_value = _env(secret_name)
    if not b64_value:
        print(f"[inject_config] {secret_name} not set — skipping session file")
        return False

    try:
        decoded = base64.b64decode(b64_value)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(decoded)
        print(f"[inject_config] {secret_name} → decoded and written to {output_path}")
        return True
    except Exception as exc:
        print(
            f"[inject_config] WARNING: Failed to decode {secret_name}: {exc}",
            file=sys.stderr,
        )
        return False


def create_directories() -> None:
    dirs = [
        REPO_ROOT / "data",
        REPO_ROOT / "data" / "screenshots",
        REPO_ROOT / "sessions",
        REPO_ROOT / "credentials",
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)
    print(f"[inject_config] Ensured directories: {[str(d) for d in dirs]}")


def check_required_secrets() -> None:
    """Warn but don't fail on missing optional secrets; fail on truly required ones."""
    required = {
        "MISTRAL_API_KEY": _env("MISTRAL_API_KEY"),
        "GMAIL_SENDER": _env("GMAIL_SENDER"),
        "GMAIL_APP_PASSWORD": _env("GMAIL_APP_PASSWORD"),
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        print(
            f"[inject_config] ERROR: Required secrets missing: {missing}\n"
            "Add them in: Repo → Settings → Secrets and variables → Actions",
            file=sys.stderr,
        )
        sys.exit(1)

    optional = {
        "COLLEGE_EMAIL": _env("COLLEGE_EMAIL"),
        "INTERNSHALA_SESSION": _env("INTERNSHALA_SESSION"),
        "LINKEDIN_SESSION": _env("LINKEDIN_SESSION"),
        "TELEGRAM_BOT_TOKEN": _env("TELEGRAM_BOT_TOKEN"),
        "TELEGRAM_CHAT_ID": _env("TELEGRAM_CHAT_ID"),
    }
    for k, v in optional.items():
        if not v:
            print(f"[inject_config] Optional secret {k} is not set (skipping)")


def main() -> None:
    print("[inject_config] ── Starting config injection ──────────────────────")

    # 0. Create all needed directories first
    create_directories()

    # 1. Check required secrets are present
    check_required_secrets()

    # 2. Load template and inject secrets
    cfg = load_template()
    cfg = inject_secrets(cfg)

    # 3. Write config.yaml
    write_config(cfg)

    # 4. Write session files
    internshala_ok = decode_session(
        "INTERNSHALA_SESSION",
        REPO_ROOT / "sessions" / "internshala.json",
    )
    linkedin_ok = decode_session(
        "LINKEDIN_SESSION",
        REPO_ROOT / "sessions" / "linkedin.json",
    )

    # 5. If sessions are missing, disable auto_apply for that platform in config
    if not internshala_ok or not linkedin_ok:
        # Re-read the written config, patch, re-write
        config_path = REPO_ROOT / "config.yaml"
        with open(config_path) as f:
            live_cfg = yaml.safe_load(f)
        if not internshala_ok:
            live_cfg.setdefault("platforms", {}).setdefault(
                "internshala", {}
            )["auto_apply"] = False
            print("[inject_config] internshala auto_apply → disabled (no session)")
        if not linkedin_ok:
            live_cfg.setdefault("platforms", {}).setdefault(
                "linkedin", {}
            )["auto_apply"] = False
            print("[inject_config] linkedin auto_apply → disabled (no session)")
        with open(config_path, "w") as f:
            yaml.dump(
                live_cfg, f, default_flow_style=False, allow_unicode=True, sort_keys=False
            )

    print("[inject_config] ── Injection complete ──────────────────────────────")


if __name__ == "__main__":
    main()
