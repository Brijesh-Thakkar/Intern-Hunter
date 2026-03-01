"""
scrapers/wellfound_scraper.py — Playwright async scraper for Wellfound (AngelList) internships
"""

import asyncio
import json
import logging
import os
import re
import random
from typing import List, Dict, Any

from playwright.async_api import async_playwright, BrowserContext

from core.database import make_job_id

logger = logging.getLogger(__name__)


def _parse_stipend(raw: str) -> tuple:
    if not raw:
        return 0, 0
    # Wellfound shows ranges like "$1,000 – $2,000/mo" or "₹15k–₹20k/month"
    nums = re.findall(r"[\d,]+", raw)
    nums = [int(n.replace(",", "")) for n in nums if n.replace(",", "").isdigit()]
    # Convert thousands if values are small (k notation)
    if "k" in raw.lower():
        nums = [n * 1000 if n < 1000 else n for n in nums]
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
        logger.debug("Wellfound cookies loaded from %s", session_file)
    else:
        logger.info("No Wellfound session file at %s — scraping as guest", session_file)


async def _scrape_page(page, url: str, cfg: Dict) -> List[Dict]:
    """Scrape a Wellfound jobs listing page."""
    jobs: List[Dict] = []
    try:
        logger.info("[Wellfound] Navigating to: %s", url)
        await page.goto(url, timeout=cfg["scraping"]["page_timeout_ms"], wait_until="networkidle")
        await _random_delay(cfg["scraping"]["min_delay_ms"], cfg["scraping"]["max_delay_ms"])

        # Accept cookies popup if it appears
        try:
            accept_btn = await page.query_selector("button[data-test='accept-cookies'], button:has-text('Accept')")
            if accept_btn:
                await accept_btn.click()
                await asyncio.sleep(0.5)
        except Exception:
            pass

        # Scroll to load all listings
        for _ in range(5):
            await page.evaluate("window.scrollBy(0, 1000)")
            await asyncio.sleep(0.6)

        # Try to find job cards - Wellfound uses various selectors
        cards = await page.query_selector_all(
            "[class*='JobListing'], [data-test='StartupResult'], [class*='job-listing'], .styles_component__"
        )
        if not cards:
            cards = await page.query_selector_all("div[class*='listings'] > div, [class*='JobCard']")

        logger.info("[Wellfound] Found %d cards", len(cards))

        for card in cards:
            try:
                title_el = await card.query_selector(
                    "[class*='title'], h2, [class*='role'], [data-test='job-title']"
                )
                company_el = await card.query_selector(
                    "[class*='company'], [class*='startup-name'], [data-test='startup-name']"
                )
                stipend_el = await card.query_selector(
                    "[class*='compensation'], [class*='salary'], [class*='stipend']"
                )
                url_el = await card.query_selector("a[href*='/jobs/'], a[href*='/l/']")

                title = (await title_el.inner_text()).strip() if title_el else "N/A"
                company = (await company_el.inner_text()).strip() if company_el else "N/A"
                stipend_raw = (await stipend_el.inner_text()).strip() if stipend_el else ""
                href = await url_el.get_attribute("href") if url_el else ""

                job_url = href if href.startswith("http") else f"https://wellfound.com{href}"
                if not href or "wellfound.com" not in job_url and not href.startswith("/"):
                    continue

                stipend_min, stipend_max = _parse_stipend(stipend_raw)
                job_id = make_job_id("wellfound", job_url)

                # Get card text for remote check
                card_text = (await card.inner_text()).lower()
                is_remote = any(kw in card_text for kw in ["remote", "work from home", "anywhere"])

                job = {
                    "id": job_id,
                    "platform": "wellfound",
                    "title": title,
                    "company": company,
                    "stipend_min": stipend_min,
                    "stipend_max": stipend_max,
                    "stipend_text": stipend_raw,
                    "remote": is_remote,
                    "url": job_url,
                    "apply_url": job_url,
                    "description": card_text[:500],
                    "required_skills": [],
                }
                jobs.append(job)
            except Exception as exc:
                logger.debug("[Wellfound] Card parse error: %s", exc)

    except Exception as exc:
        logger.error("[Wellfound] Page scrape error: %s", exc)

    return jobs


async def _fetch_job_detail(page, job: Dict, cfg: Dict) -> Dict:
    """Open job detail page for full description."""
    try:
        await page.goto(job["url"], timeout=cfg["scraping"]["page_timeout_ms"], wait_until="domcontentloaded")
        await _random_delay(cfg["scraping"]["min_delay_ms"], cfg["scraping"]["max_delay_ms"])

        desc_el = await page.query_selector(
            "[class*='description'], [data-test='job-description'], [class*='JobDescription']"
        )
        if desc_el:
            job["description"] = (await desc_el.inner_text()).strip()

        skills_els = await page.query_selector_all(
            "[class*='tag'], [class*='skill'], [data-test='skill-tag']"
        )
        for el in skills_els:
            text = (await el.inner_text()).strip()
            if text and len(text) < 30:
                job["required_skills"].append(text)

        # Check remote in full page
        page_text = (await page.content()).lower()
        if any(k in page_text for k in ["remote", "work from home", "anywhere"]):
            job["remote"] = True

    except Exception as exc:
        logger.debug("[Wellfound] Detail fetch error for %s: %s", job.get("url"), exc)
    return job


async def scrape(cfg: Dict[str, Any]) -> List[Dict]:
    """Main entry point — returns list of job dicts from Wellfound."""
    p_cfg = cfg["platforms"]["wellfound"]
    if not p_cfg["enabled"]:
        logger.info("[Wellfound] Platform disabled — skipping.")
        return []

    session_file = p_cfg.get("session_file", "sessions/wellfound.json")

    urls = [
        "https://wellfound.com/jobs?role=software-engineer&jobType=internship&remote=true",
        "https://wellfound.com/jobs?role=machine-learning-engineer&jobType=internship&remote=true",
        "https://wellfound.com/jobs?role=backend-developer&jobType=internship&remote=true",
        "https://wellfound.com/jobs?role=full-stack-engineer&jobType=internship&remote=true",
    ]

    all_jobs: List[Dict] = []
    seen_ids: set = set()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=cfg["scraping"]["user_agent"],
            viewport={"width": 1280, "height": 900},
            locale="en-US",
        )
        await _load_cookies(context, session_file)
        page = await context.new_page()

        for url in urls:
            jobs = await _scrape_page(page, url, cfg)
            for job in jobs:
                if job["id"] not in seen_ids:
                    seen_ids.add(job["id"])
                    all_jobs.append(job)
            await _random_delay(cfg["scraping"]["min_delay_ms"], cfg["scraping"]["max_delay_ms"])

        # Fetch details for first 20 unique jobs
        for job in all_jobs[:20]:
            await _fetch_job_detail(page, job, cfg)
            await _random_delay(cfg["scraping"]["min_delay_ms"], cfg["scraping"]["max_delay_ms"])

        await browser.close()

    logger.info("[Wellfound] Scraped %d unique jobs.", len(all_jobs))
    return all_jobs
