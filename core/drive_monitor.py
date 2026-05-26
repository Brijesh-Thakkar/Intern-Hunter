"""
core/drive_monitor.py — Google Drive folder watcher + resume parser via Mistral
"""

import io
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, Tuple

import pdfplumber
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
try:
    from mistralai import Mistral
except ImportError:
    from mistralai.client import Mistral

from core.database import get_resume_state, upsert_resume_state, update_resume_last_checked, db_log

logger = logging.getLogger(__name__)


# ── Auth helpers ──────────────────────────────────────────────────────────────

def _get_drive_service(cfg: Dict[str, Any]):
    """Return an authenticated Google Drive v3 service object."""
    creds = None
    scopes = cfg["google_drive"]["scopes"]
    token_path = cfg["google_drive"]["token_path"]
    creds_path = cfg["google_drive"]["credentials_path"]

    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, scopes)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(creds_path, scopes)
            creds = flow.run_local_server(port=0)
        Path(token_path).parent.mkdir(parents=True, exist_ok=True)
        with open(token_path, "w") as tok_f:
            tok_f.write(creds.to_json())

    service = build("drive", "v3", credentials=creds)
    return service


# ── File listing ──────────────────────────────────────────────────────────────

def _list_pdfs_in_folder(service, folder_id: str) -> list:
    """Return list of PDF file metadata from the specified Drive folder."""
    results = service.files().list(
        q=f"'{folder_id}' in parents and mimeType='application/pdf' and trashed=false",
        fields="files(id, name, modifiedTime)",
        orderBy="modifiedTime desc",
        pageSize=10,
    ).execute()
    return results.get("files", [])


# ── PDF download & text extraction ───────────────────────────────────────────

def _download_pdf_bytes(service, file_id: str) -> bytes:
    request = service.files().get_media(fileId=file_id)
    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buffer.getvalue()


def _extract_text_from_pdf(pdf_bytes: bytes) -> str:
    text_parts = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                text_parts.append(text)
    return "\n".join(text_parts)


# ── Mistral resume parser ──────────────────────────────────────────────────────

def _parse_resume_with_gemini(resume_text: str, api_key: str, model: str) -> Dict[str, Any]:
    """Call Mistral to extract structured profile from resume text."""
    client = Mistral(api_key=api_key)

    prompt = f"""You are a resume parser. Extract structured information from this resume text.

Resume:
\"\"\"
{resume_text[:8000]}
\"\"\"

Return ONLY a valid JSON object with these fields:
{{
  "skills": ["list", "of", "technical", "skills"],
  "domains": ["list", "of", "domains", "e.g.", "ml", "backend", "frontend"],
  "target_roles": ["list", "of", "appropriate", "intern", "roles"],
  "education": {{
    "college": "college name",
    "degree": "degree type",
    "year": "current year"
  }},
  "projects": [
    {{"name": "project name", "description": "one line description"}}
  ],
  "experience_summary": "2-3 sentence summary of candidate strengths"
}}

Do not include any explanation. Return only the JSON object."""

    try:
        resp = client.chat.complete(
            model=model,
            messages=[
                {"role": "system", "content": "You are a resume parser. Return only valid JSON."},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0,
            max_tokens=1024,
        )
        raw = resp.choices[0].message.content.strip()
        parsed = json.loads(raw)
        return parsed
    except json.JSONDecodeError as exc:
        logger.error("Mistral returned invalid JSON during resume parse: %s", exc)
        return {"skills": [], "domains": [], "target_roles": [], "projects": []}
    except Exception as exc:
        logger.error("Mistral API error during resume parse: %s", exc)
        return {"skills": [], "domains": [], "target_roles": [], "projects": []}


# ── Main entry point ──────────────────────────────────────────────────────────

def check_and_update_resume(cfg: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
    """
    Check Drive folder for new/updated PDF resume.

    Returns:
        (updated: bool, parsed_skills: Optional[dict])
        updated=True means the resume changed and was re-parsed.
    """
    db_path = cfg["agent"]["db_path"]

    try:
        service = _get_drive_service(cfg)
    except Exception as exc:
        logger.error("Drive auth failed: %s", exc)
        db_log(db_path, "ERROR", f"Drive auth failed: {exc}")
        return False, None

    try:
        files = _list_pdfs_in_folder(service, cfg["google_drive"]["folder_id"])
    except Exception as exc:
        logger.error("Drive list failed: %s", exc)
        db_log(db_path, "ERROR", f"Drive list failed: {exc}")
        return False, None

    if not files:
        logger.info("No PDF files found in Drive folder.")
        update_resume_last_checked(db_path)
        return False, None

    # Use the most recently modified PDF
    latest = files[0]
    latest_file_id = latest["id"]
    latest_name = latest["name"]
    latest_modified = latest["modifiedTime"]

    current_state = get_resume_state(db_path)

    if (
        current_state
        and current_state.get("file_name") == latest_name
        and current_state.get("drive_modified_time") == latest_modified
    ):
        logger.info("Resume unchanged (%s). Skipping re-parse.", latest_name)
        update_resume_last_checked(db_path)
        # Return current parsed skills from DB
        try:
            parsed = json.loads(current_state.get("parsed_skills", "{}"))
        except Exception:
            parsed = {}
        return False, parsed

    # Resume is new or modified — download and re-parse
    logger.info("Resume changed or new: %s (modified: %s). Re-parsing…", latest_name, latest_modified)
    db_log(db_path, "INFO", f"Resume updated: {latest_name}. Downloading and re-parsing.")

    try:
        pdf_bytes = _download_pdf_bytes(service, latest_file_id)
        resume_text = _extract_text_from_pdf(pdf_bytes)
    except Exception as exc:
        logger.error("Failed to download/parse PDF: %s", exc)
        db_log(db_path, "ERROR", f"PDF download/parse failed: {exc}")
        return False, None

    logger.info("Extracted %d characters from resume PDF.", len(resume_text))

    parsed_skills = _parse_resume_with_gemini(
        resume_text,
        cfg["mistral"]["api_key"],
        cfg["mistral"]["scoring_model"],
    )
    logger.info("Resume parsed — skills: %s", parsed_skills.get("skills", []))

    upsert_resume_state(db_path, {
        "file_name": latest_name,
        "drive_modified_time": latest_modified,
        "parsed_skills": parsed_skills,
        "resume_text": resume_text,
    })
    db_log(db_path, "INFO", f"Resume state saved for: {latest_name}")

    return True, parsed_skills
