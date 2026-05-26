"""
scrapers/linkedin_scraper.py — Playwright async scraper for LinkedIn internship + remote jobs in India
"""

import asyncio
import json
import logging
import os
import re
import random
from typing import List, Dict, Any
from urllib.parse import urlencode

from playwright.async_api import async_playwright, BrowserContext

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


async def _random_delay(min_ms: int = 800, max_ms: int = 3000):
    await asyncio.sleep(random.uniform(min_ms / 1000, max_ms / 1000))


async def _load_cookies(context: BrowserContext, session_file: str) -> None:
    if os.path.exists(session_file):
        with open(session_file) as f:
            cookies = json.load(f)
        await context.add_cookies(cookies)
        logger.debug("LinkedIn cookies loaded from %s", session_file)
    else:
        logger.warning("No LinkedIn session file at %s — scraping as guest (limited results)", session_file)


async def _scrape_skill(context, skill: str, cfg: Dict) -> List[Dict]:
    """Search LinkedIn for internships in India with given keyword, remote filter."""
    params = {
        "keywords": skill,
        "location": "India",
        "f_JT": "I",   # Internship
        "f_WT": "2",   # Remote
        "start": "0",
    }
    url = "https://www.linkedin.com/jobs/search/?" + urlencode(params)
    jobs: List[Dict] = []
    page = await context.new_page()
    await page.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
    """)

    try:
        logger.info("[LinkedIn] Navigating for skill '%s': %s", skill, url)
        await page.goto(url, timeout=cfg["scraping"]["page_timeout_ms"], wait_until="domcontentloaded")
        await _random_delay(cfg["scraping"]["min_delay_ms"], cfg["scraping"]["max_delay_ms"])

        # Scroll to load more results
        for _ in range(4):
            await page.evaluate("window.scrollBy(0, 1200)")
            await asyncio.sleep(0.7)

        # Wait for job cards
        # Wait for job cards — don't bail on timeout, try anyway
        try:
            await page.wait_for_selector(
                ".job-card-container, [data-job-id]", timeout=10000
            )
        except Exception:
            pass  # Cards may still be present, continue

        cards = await page.query_selector_all(".job-card-container, [data-job-id]")
        if not cards:
            logger.warning("[LinkedIn] No job cards found for '%s'", skill)
            await page.close()
            return []
        logger.info("[LinkedIn] Found %d cards for '%s'", len(cards), skill)

        for card in cards:
            try:
                title_el = await card.query_selector(
                    ".job-card-list__title, .job-card-container__link, strong"
                )
                company_el = await card.query_selector(
                    ".job-card-container__primary-description, .job-card-container__company-name, .artdeco-entity-lockup__subtitle"
                )
                url_el = await card.query_selector("a.job-card-container__link, a[href*='/jobs/view/']")
                location_el = await card.query_selector(".job-card-container__metadata-item, .job-card-container__metadata-wrapper li")

                title = (await title_el.inner_text()).strip() if title_el else "N/A"
                company = (await company_el.inner_text()).strip() if company_el else "N/A"
                href = await url_el.get_attribute("href") if url_el else ""
                location_text = (await location_el.inner_text()).strip().lower() if location_el else ""

                # Clean LinkedIn tracking params
                job_url = href.split("?")[0] if href else ""
                if not job_url or "linkedin.com" not in job_url:
                    continue

                # Check for remote signal in location
                is_remote = any(kw in location_text for kw in ["remote", "india", "anywhere"])

                job_id = make_job_id("linkedin", job_url)
                job = {
                    "id": job_id,
                    "platform": "linkedin",
                    "title": title,
                    "company": company,
                    "stipend_min": 0,
                    "stipend_max": 0,
                    "stipend_text": "",
                    "remote": is_remote,
                    "url": job_url,
                    "apply_url": job_url,
                    "description": "",
                    "required_skills": [],
                    "easy_apply": False,
                }
                jobs.append(job)
            except Exception as exc:
                logger.debug("[LinkedIn] Card parse error: %s", exc)

    except Exception as exc:
        logger.error("[LinkedIn] Error scraping '%s': %s", skill, exc)
    finally:
        await page.close()

    return jobs


async def _fetch_job_detail(context, job: Dict, cfg: Dict) -> Dict:
    """Open the LinkedIn job detail page to get description + Easy Apply flag."""
    page = await context.new_page()
    await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    try:
        await page.goto(job["url"], timeout=cfg["scraping"]["page_timeout_ms"], wait_until="domcontentloaded")
        await _random_delay(cfg["scraping"]["min_delay_ms"], cfg["scraping"]["max_delay_ms"])

        # Description
        desc_el = await page.query_selector(
            ".jobs-description__content, .show-more-less-html__markup, .description__text"
        )
        if desc_el:
            job["description"] = (await desc_el.inner_text()).strip()

        # Easy Apply check
        easy_apply_el = await page.query_selector(
            "button.jobs-apply-button[aria-label*='Easy Apply'], .jobs-apply-button--top-card"
        )
        if easy_apply_el:
            btn_text = (await easy_apply_el.inner_text()).lower()
            job["easy_apply"] = "easy apply" in btn_text

        # Stipend / salary info
        salary_el = await page.query_selector(
            ".compensation__salary, .job-details-jobs-unified-top-card__job-insight"
        )
        if salary_el:
            raw = (await salary_el.inner_text()).strip()
            job["stipend_text"] = raw
            mn, mx = _parse_stipend(raw)
            job["stipend_min"] = mn
            job["stipend_max"] = mx

        # Remote confirmation
        remote_sections = await page.query_selector_all(
            ".job-criteria__text, .jobs-unified-top-card__workplace-type"
        )
        for rs in remote_sections:
            text = (await rs.inner_text()).strip().lower()
            if "remote" in text:
                job["remote"] = True

    except Exception as exc:
        logger.debug("[LinkedIn] Detail fetch error for %s: %s", job.get("url"), exc)
    finally:
        await page.close()
    return job


async def scrape(cfg: Dict[str, Any]) -> List[Dict]:
    """Main entry point — returns list of job dicts from LinkedIn."""
    p_cfg = cfg["platforms"]["linkedin"]
    if not p_cfg["enabled"]:
        logger.info("[LinkedIn] Platform disabled — skipping.")
        return []

    skills = p_cfg.get("search_skills", ["software engineer intern", "python developer"])
    session_file = p_cfg.get("session_file", "sessions/linkedin.json")
    all_jobs: List[Dict] = []
    seen_ids: set = set()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--disable-infobars",
                "--window-size=1920,1080",
            ]
        )
        context = await browser.new_context(
            user_agent=cfg["scraping"]["user_agent"],
            viewport={"width": 1920, "height": 1080},
            java_script_enabled=True,
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
        )
        await _load_cookies(context, session_file)

        for skill in skills:
            jobs = await _scrape_skill(context, skill, cfg)
            for job in jobs:
                if job["id"] not in seen_ids:
                    seen_ids.add(job["id"])
                    all_jobs.append(job)
            await _random_delay(cfg["scraping"]["min_delay_ms"], cfg["scraping"]["max_delay_ms"])

        # Fetch details for first 25 unique jobs
        for job in all_jobs[:25]:
            await _fetch_job_detail(context, job, cfg)
            await _random_delay(cfg["scraping"]["min_delay_ms"], cfg["scraping"]["max_delay_ms"])

        await browser.close()

    logger.info("[LinkedIn] Scraped %d unique jobs.", len(all_jobs))
    return all_jobs
