"""
core/database.py — All SQLite operations for Intern Hunter
"""

import sqlite3
import json
import hashlib
import logging
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from pathlib import Path

logger = logging.getLogger(__name__)


def get_connection(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def init_db(db_path: str) -> None:
    """Create all tables if they don't exist."""
    conn = get_connection(db_path)
    cur = conn.cursor()

    cur.executescript("""
        CREATE TABLE IF NOT EXISTS jobs_seen (
            id          TEXT PRIMARY KEY,
            platform    TEXT NOT NULL,
            title       TEXT,
            company     TEXT,
            stipend_min INTEGER,
            stipend_max INTEGER,
            remote      INTEGER DEFAULT 0,
            url         TEXT,
            match_score INTEGER DEFAULT 0,
            raw_json    TEXT,
            seen_at     TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS applications (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id       TEXT NOT NULL,
            platform     TEXT,
            company      TEXT,
            role         TEXT,
            stipend      TEXT,
            apply_url    TEXT,
            status       TEXT NOT NULL,
            match_score  INTEGER DEFAULT 0,
            cover_letter TEXT,
            applied_at   TEXT NOT NULL,
            notified     INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS resume_state (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            file_name           TEXT,
            drive_modified_time TEXT,
            parsed_skills       TEXT,
            resume_text         TEXT,
            last_checked        TEXT
        );

        CREATE TABLE IF NOT EXISTS agent_log (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            level   TEXT NOT NULL,
            message TEXT NOT NULL,
            ts      TEXT NOT NULL
        );
    """)
    conn.commit()
    conn.close()
    logger.debug("Database initialised at %s", db_path)


# ── Job helpers ───────────────────────────────────────────────────────────────

def make_job_id(platform: str, url: str) -> str:
    raw = f"{platform}::{url}"
    return platform + "_" + hashlib.md5(raw.encode()).hexdigest()


def job_seen(db_path: str, job_id: str, ttl_days: int = 14) -> bool:
    """Return True if this job was seen within the last ttl_days days."""
    from datetime import timedelta
    conn = get_connection(db_path)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=ttl_days)).isoformat()
    row = conn.execute(
        "SELECT id FROM jobs_seen WHERE id = ? AND seen_at >= ?", (job_id, cutoff)
    ).fetchone()
    conn.close()
    return row is not None


def clear_seen_jobs(db_path: str, older_than_days: int = 0) -> int:
    """
    Delete jobs from jobs_seen (and their skipped applications) so the agent
    re-evaluates them. If older_than_days=0, clears ALL skipped jobs.
    Returns the number of rows deleted.
    """
    from datetime import timedelta
    conn = get_connection(db_path)
    if older_than_days > 0:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=older_than_days)).isoformat()
        cur = conn.execute(
            "DELETE FROM jobs_seen WHERE seen_at < ? AND match_score = 0", (cutoff,)
        )
    else:
        cur = conn.execute("DELETE FROM jobs_seen WHERE match_score = 0")
    deleted = cur.rowcount
    # Also wipe matching skipped applications
    conn.execute(
        "DELETE FROM applications WHERE status IN ('skipped') AND match_score = 0"
    )
    conn.commit()
    conn.close()
    return deleted


def insert_job(db_path: str, job: Dict[str, Any]) -> None:
    conn = get_connection(db_path)
    conn.execute(
        """
        INSERT OR IGNORE INTO jobs_seen
            (id, platform, title, company, stipend_min, stipend_max,
             remote, url, match_score, raw_json, seen_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            job["id"],
            job.get("platform", ""),
            job.get("title", ""),
            job.get("company", ""),
            job.get("stipend_min", 0),
            job.get("stipend_max", 0),
            1 if job.get("remote") else 0,
            job.get("url", ""),
            job.get("match_score", 0),
            json.dumps(job),
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    conn.commit()
    conn.close()


def update_job_score(db_path: str, job_id: str, score: int) -> None:
    conn = get_connection(db_path)
    conn.execute(
        "UPDATE jobs_seen SET match_score = ? WHERE id = ?", (score, job_id)
    )
    conn.commit()
    conn.close()


# ── Application helpers ───────────────────────────────────────────────────────

def log_application(db_path: str, app: Dict[str, Any]) -> int:
    conn = get_connection(db_path)
    cur = conn.execute(
        """
        INSERT INTO applications
            (job_id, platform, company, role, stipend, apply_url,
             status, match_score, cover_letter, applied_at, notified)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            app["job_id"],
            app.get("platform", ""),
            app.get("company", ""),
            app.get("role", ""),
            app.get("stipend", ""),
            app.get("apply_url", ""),
            app.get("status", "unknown"),
            app.get("match_score", 0),
            app.get("cover_letter", ""),
            datetime.now(timezone.utc).isoformat(),
            0,
        ),
    )
    row_id = cur.lastrowid
    conn.commit()
    conn.close()
    return row_id


def mark_notified(db_path: str, app_id: int) -> None:
    conn = get_connection(db_path)
    conn.execute("UPDATE applications SET notified = 1 WHERE id = ?", (app_id,))
    conn.commit()
    conn.close()


def get_todays_auto_applied_count(db_path: str) -> int:
    today = datetime.now(timezone.utc).date().isoformat()
    conn = get_connection(db_path)
    row = conn.execute(
        """
        SELECT COUNT(*) AS cnt FROM applications
        WHERE status = 'auto_applied'
          AND applied_at LIKE ?
        """,
        (f"{today}%",),
    ).fetchone()
    conn.close()
    return row["cnt"] if row else 0


def get_applications_since(db_path: str, since_iso: str, status: Optional[str] = None
                           ) -> List[Dict]:
    conn = get_connection(db_path)
    if status:
        rows = conn.execute(
            "SELECT * FROM applications WHERE applied_at >= ? AND status = ? ORDER BY match_score DESC",
            (since_iso, status),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM applications WHERE applied_at >= ? ORDER BY match_score DESC",
            (since_iso,),
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_recent_applications(db_path: str, limit: int = 10) -> List[Dict]:
    conn = get_connection(db_path)
    rows = conn.execute(
        "SELECT * FROM applications ORDER BY applied_at DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_manual_queue(db_path: str) -> List[Dict]:
    conn = get_connection(db_path)
    rows = conn.execute(
        "SELECT * FROM applications WHERE status = 'manual_queue' AND notified = 0 ORDER BY match_score DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Resume state helpers ──────────────────────────────────────────────────────

def get_resume_state(db_path: str) -> Optional[Dict]:
    conn = get_connection(db_path)
    row = conn.execute(
        "SELECT * FROM resume_state ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def upsert_resume_state(db_path: str, state: Dict[str, Any]) -> None:
    conn = get_connection(db_path)
    existing = conn.execute("SELECT id FROM resume_state LIMIT 1").fetchone()
    if existing:
        conn.execute(
            """
            UPDATE resume_state SET
                file_name = ?, drive_modified_time = ?,
                parsed_skills = ?, resume_text = ?, last_checked = ?
            WHERE id = ?
            """,
            (
                state.get("file_name", ""),
                state.get("drive_modified_time", ""),
                json.dumps(state.get("parsed_skills", {})),
                state.get("resume_text", ""),
                datetime.now(timezone.utc).isoformat(),
                existing["id"],
            ),
        )
    else:
        conn.execute(
            """
            INSERT INTO resume_state
                (file_name, drive_modified_time, parsed_skills, resume_text, last_checked)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                state.get("file_name", ""),
                state.get("drive_modified_time", ""),
                json.dumps(state.get("parsed_skills", {})),
                state.get("resume_text", ""),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
    conn.commit()
    conn.close()


def update_resume_last_checked(db_path: str) -> None:
    conn = get_connection(db_path)
    conn.execute(
        "UPDATE resume_state SET last_checked = ? WHERE id = (SELECT id FROM resume_state ORDER BY id DESC LIMIT 1)",
        (datetime.now(timezone.utc).isoformat(),),
    )
    conn.commit()
    conn.close()


# ── Agent log helpers ─────────────────────────────────────────────────────────

def db_log(db_path: str, level: str, message: str) -> None:
    try:
        conn = get_connection(db_path)
        conn.execute(
            "INSERT INTO agent_log (level, message, ts) VALUES (?, ?, ?)",
            (level, message, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        print(f"[db_log error] {exc}")


def get_recent_logs(db_path: str, limit: int = 20) -> List[Dict]:
    conn = get_connection(db_path)
    rows = conn.execute(
        "SELECT * FROM agent_log ORDER BY ts DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Dashboard stats ───────────────────────────────────────────────────────────

def get_stats(db_path: str) -> Dict[str, int]:
    conn = get_connection(db_path)
    total = conn.execute("SELECT COUNT(*) AS c FROM applications").fetchone()["c"]
    auto_applied = conn.execute(
        "SELECT COUNT(*) AS c FROM applications WHERE status = 'auto_applied'"
    ).fetchone()["c"]
    manual_queue = conn.execute(
        "SELECT COUNT(*) AS c FROM applications WHERE status = 'manual_queue'"
    ).fetchone()["c"]
    skipped = conn.execute(
        "SELECT COUNT(*) AS c FROM applications WHERE status = 'skipped'"
    ).fetchone()["c"]
    today = datetime.now(timezone.utc).date().isoformat()
    today_count = conn.execute(
        "SELECT COUNT(*) AS c FROM applications WHERE applied_at LIKE ?",
        (f"{today}%",),
    ).fetchone()["c"]
    conn.close()
    return {
        "total": total,
        "auto_applied": auto_applied,
        "manual_queue": manual_queue,
        "skipped": skipped,
        "today": today_count,
    }
