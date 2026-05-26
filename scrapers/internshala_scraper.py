"""
scrapers/internshala_scraper.py — requests+BS4 scraper for Internshala WFH internships
Bypasses Playwright entirely; uses session cookies with direct HTTP requests.
"""

import logging
import re
import time
import random
import json
import os
from typing import List, Dict, Any

import requests
from bs4 import BeautifulSoup

from core.database import make_job_id

logger = logging.getLogger(__name__)


def _parse_stipend(raw: str) -> tuple:
    if not raw:
        return 0, 0
    nums = re.findall(r"[\d,]+", raw)
    nums = [int(n.replace(",", "")) for n in nums if n.replace(",", "").isdigit()]
    if len(nums) >= 2:
        return nums[0], nums[1]
    if len(nums) == 1:
        return nums[0], nums[0]
    return 0, 0


def _get_session(session_file: str) -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Referer": "https://internshala.com/",
    })
    if os.path.exists(session_file):
        cookies = json.load(open(session_file))
        for c in cookies:
            if "internshala.com" in c.get("domain", ""):
                s.cookies.set(c["name"], c["value"], domain=".internshala.com")
        logger.debug("Internshala: loaded cookies from %s", session_file)
    else:
        logger.warning("No session file at %s — scraping without login", session_file)
    return s


def _scrape_skill(session: requests.Session, skill: str) -> List[Dict]:
    slug = skill.lower().replace(" ", "-").replace("/", "-")
    url = f"https://internshala.com/internships/keywords-{slug}/work-from-home-internships/"
    jobs = []

    try:
        r = session.get(url, timeout=20)
        if r.status_code != 200:
            logger.warning("[Internshala] HTTP %d for skill '%s'", r.status_code, skill)
            return []

        soup = BeautifulSoup(r.text, "html.parser")
        cards = soup.select(".individual_internship")
        logger.info("[Internshala] Found %d cards for skill '%s'", len(cards), skill)

        for card in cards:
            try:
                # Title
                title_el = card.select_one(".job-internship-name, .heading_4_5, h3")
                title = title_el.get_text(strip=True) if title_el else "N/A"

                # Skip promo banners
                if "training" in title.lower() or "offer" in title.lower() or title == "N/A":
                    continue

                # Company
                company_el = card.select_one(".company-name, .link_display_like_text")
                company = company_el.get_text(strip=True) if company_el else "N/A"

                # Stipend
                stipend_el = card.select_one(".stipend")
                stipend_raw = stipend_el.get_text(strip=True) if stipend_el else ""
                stipend_min, stipend_max = _parse_stipend(stipend_raw)

                # URL
                url_el = card.select_one("a.job-title-href, a[href*='/internship/detail']")
                href = url_el.get("href", "") if url_el else ""
                job_url = href if href.startswith("http") else f"https://internshala.com{href}"

                if not href:
                    continue

                job_id = make_job_id("internshala", job_url)
                jobs.append({
                    "id": job_id,
                    "platform": "internshala",
                    "title": title,
                    "company": company,
                    "stipend_min": stipend_min,
                    "stipend_max": stipend_max,
                    "stipend_text": stipend_raw,
                    "remote": True,
                    "url": job_url,
                    "apply_url": job_url,
                    "description": "",
                    "required_skills": [],
                })
            except Exception as exc:
                logger.debug("[Internshala] Card parse error: %s", exc)

    except Exception as exc:
        logger.error("[Internshala] Error scraping skill '%s': %s", skill, exc)

    return jobs


def _fetch_description(session: requests.Session, job: Dict) -> Dict:
    try:
        r = session.get(job["url"], timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")

        desc_el = soup.select_one(".internship_details, #about_internship, .about-internship-section")
        if desc_el:
            job["description"] = desc_el.get_text(strip=True)

        skill_els = soup.select(".round_tabs .round_tab_badge, .skills span")
        job["required_skills"] = [el.get_text(strip=True) for el in skill_els if el.get_text(strip=True)]
    except Exception as exc:
        logger.debug("[Internshala] Description fetch error for %s: %s", job.get("url"), exc)
    return job


async def scrape(cfg: Dict[str, Any]) -> List[Dict]:
    """Main entry point — returns list of job dicts from Internshala."""
    p_cfg = cfg["platforms"]["internshala"]
    if not p_cfg["enabled"]:
        logger.info("[Internshala] Platform disabled — skipping.")
        return []

    skills = p_cfg.get("search_skills", ["python", "react", "backend"])
    session_file = p_cfg.get("session_file", "sessions/internshala.json")

    session = _get_session(session_file)
    all_jobs: List[Dict] = []
    seen_ids: set = set()

    for skill in skills:
        jobs = _scrape_skill(session, skill)
        for job in jobs:
            if job["id"] not in seen_ids:
                seen_ids.add(job["id"])
                all_jobs.append(job)
        time.sleep(random.uniform(0.8, 2.0))

    # Fetch descriptions for first 30 unique jobs
    for job in all_jobs[:30]:
        _fetch_description(session, job)
        time.sleep(random.uniform(0.5, 1.2))

    logger.info("[Internshala] Scraped %d unique jobs.", len(all_jobs))
    return all_jobs
