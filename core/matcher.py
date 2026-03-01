"""
core/matcher.py — Mistral-powered job scoring and cover letter generation.
Uses JSON mode for 100% parse reliability and separate fast/quality models.
"""

import json
import logging
from typing import Dict, Any, Tuple, Optional

from mistralai import Mistral

logger = logging.getLogger(__name__)


def _get_client(cfg: Dict[str, Any]) -> Mistral:
    return Mistral(api_key=cfg["mistral"]["api_key"])


def _build_candidate_summary(cfg: Dict[str, Any], parsed_skills: Optional[Dict] = None) -> str:
    """Build a rich candidate profile string for AI prompts."""
    c = cfg["candidate"]
    skills = list(c["skills"])
    roles = list(c["target_roles"])
    projects = c.get("projects", [])

    # Merge in any freshly-parsed skills from resume
    if parsed_skills:
        for s in parsed_skills.get("skills", []):
            if s not in skills:
                skills.append(s)
        for r in parsed_skills.get("target_roles", []):
            if r not in roles:
                roles.append(r)
        extra_projects = parsed_skills.get("projects", [])
        if extra_projects:
            projects = extra_projects

    project_lines = "\n".join(
        f"  - {p['name']}: {p['description']}" for p in projects
    )

    return f"""Candidate: {c['name']}
College: {c['college']} (IIT) — {c['degree']}, Year {c['year']} of 4 (graduating {c.get('graduation_year', 2028)})
Strong at: {', '.join(skills[:12])}
Also knows: {', '.join(skills[12:]) if len(skills) > 12 else 'N/A'}
Target roles: {', '.join(roles)}
Flagship projects:
{project_lines}"""


def score_job(
    cfg: Dict[str, Any],
    job: Dict[str, Any],
    parsed_skills: Optional[Dict] = None,
) -> Tuple[int, str]:
    """
    Score the job (0–100) using Mistral with JSON mode — zero parse failures.
    Returns (score, reason).
    """
    client = _get_client(cfg)
    model = cfg["mistral"]["scoring_model"]
    candidate_summary = _build_candidate_summary(cfg, parsed_skills)
    description = (job.get("description") or job.get("title") or "")[:3000]

    system_prompt = (
        "You are a senior technical recruiter scoring internship-candidate fit. "
        "You always return valid JSON with exactly two keys: score (integer 0-100) "
        "and reason (one sentence under 20 words)."
    )

    user_prompt = f"""Score how well this internship matches the candidate. Return JSON only.

=== CANDIDATE ===
{candidate_summary}

=== JOB ===
Title: {job.get('title', 'N/A')}
Company: {job.get('company', 'N/A')}
Stipend: {job.get('stipend_text', 'Not stated')}
Platform: {job.get('platform', 'N/A')}
Description:
{description}

=== SCORING RUBRIC ===
90-100 → Perfect fit: all core skills match, role aligns exactly with target roles.
75-89  → Strong fit: 70%+ skills match, role is directly relevant.
60-74  → Decent fit: half the skills match, role partially overlaps.
0-59   → Weak fit: unrelated role or skill mismatch.

BONUS +5 if the company is a known startup/product company (not generic outsourcing).
BONUS +3 if role involves building real systems (not just testing/documentation).
PENALTY -10 if role is primarily sales, HR, marketing, or non-technical.

Return: {{"score": <int>, "reason": "<≤20 words>"}}"""

    try:
        resp = client.chat.complete(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
            max_tokens=128,
        )
        result = json.loads(resp.choices[0].message.content)
        score = max(0, min(100, int(result.get("score", 0))))
        reason = result.get("reason", "").strip()
        return score, reason
    except Exception as exc:
        logger.error("Mistral scoring error: %s", exc)
        return 0, f"Scoring failed — {exc}"


def generate_cover_letter(
    cfg: Dict[str, Any],
    job: Dict[str, Any],
    parsed_skills: Optional[Dict] = None,
) -> str:
    """
    Write a punchy tailored cover letter using the higher-quality model.
    Returns plain text.
    """
    client = _get_client(cfg)
    model = cfg["mistral"]["cover_letter_model"]
    max_words = cfg["mistral"].get("max_cover_letter_words", 150)

    c = cfg["candidate"]
    candidate_summary = _build_candidate_summary(cfg, parsed_skills)
    description = (job.get("description") or job.get("title") or "")[:2000]
    projects = c.get("projects", [])
    project_names = [p["name"] for p in projects] if projects else ["SentinelDB", "NeuroLearn"]

    system_prompt = (
        "You are an expert career coach. You write punchy, confident internship cover letters "
        "that stand out. You NEVER use filler phrases like 'I am writing to express my interest'. "
        "You write like a sharp IIT student, not a robot."
    )

    user_prompt = f"""Write a tailored internship cover letter. STRICT LIMIT: {max_words} words.

=== CANDIDATE ===
{candidate_summary}

=== JOB ===
Title: {job.get('title', 'N/A')}
Company: {job.get('company', 'N/A')}
Description:
{description}

=== RULES ===
1. Open with "Hi [Company] team," — use the actual company name.
2. First sentence: directly state the role + your STRONGEST relevant qualification.
3. Mention 1-2 projects ({', '.join(project_names[:2])}) ONLY if they're relevant; tie them to impact numbers.
4. Show you understand what the company ACTUALLY does — be specific, not generic.
5. Confident IIT student tone — self-assured, direct, zero fluff.
6. Final line EXACTLY: "Looking forward to contributing — Brijesh Thakkar, IIT Jodhpur."
7. NO subject line. NO markdown. NO filler. Under {max_words} words.

Return only the cover letter text."""

    try:
        resp = client.chat.complete(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.65,
            max_tokens=512,
        )
        return resp.choices[0].message.content.strip()
    except Exception as exc:
        logger.error("Mistral cover letter error: %s", exc)
        return (
            f"Hi {job.get('company', 'Team')} team,\n\n"
            f"I'm Brijesh Thakkar, a 2nd-year B.Tech CSE+EE student at IIT Jodhpur applying "
            f"for the {job.get('title', 'intern')} role. My work on SentinelDB (distributed DB, "
            f"50k TPS with Raft consensus) and NeuroLearn (ML platform, 2k+ users) demonstrates "
            f"the exact skills you need. I build production-grade systems fast.\n\n"
            f"Looking forward to contributing — Brijesh Thakkar, IIT Jodhpur."
        )
