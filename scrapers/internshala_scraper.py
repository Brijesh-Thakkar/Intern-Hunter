"""
scrapers/internshala_scraper.py — Playwright async scraper for Internshala WFH internships
"""

import asyncio
import logging
import re
import random
from typing import List, Dict, Any

from playwright.async_api import async_playwright, BrowserContext, Page

from core.database import make_job_id

logger = logging.getLogger(__name__)


def _parse_stipend(raw: str) -> tuple:
    """Extract (min, max) stipend integers from strings like '₹15,000 - 20,000 /month'."""
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
    import json, os
    if os.path.exists(session_file):
        with open(session_file) as f:
            cookies = json.load(f)
        await context.add_cookies(cookies)
        logger.debug("Internshala cookies loaded from %s", session_file)
    else:
        logger.warning("No session file found at %s — scraping without login", session_file)


async def _scrape_skill(page: Page, base_url: str, skill: str, cfg: Dict) -> List[Dict]:
    """Scrape a skill-specific Internshala WFH results page."""
    skill_slug = skill.lower().replace(" ", "-").replace("/", "-")
    url = f"https://internshala.com/internships/{skill_slug}-work-from-home-internship/"
    jobs: List[Dict] = []

    try:
        logger.info("[Internshala] Navigating to: %s", url)
        await page.goto(url, timeout=cfg["scraping"]["page_timeout_ms"], wait_until="domcontentloaded")
        await _random_delay(cfg["scraping"]["min_delay_ms"], cfg["scraping"]["max_delay_ms"])

        # Scroll to trigger lazy-loading
        for _ in range(3):
            await page.evaluate("window.scrollBy(0, 800)")
            await asyncio.sleep(0.5)

        cards = await page.query_selector_all(".internship_meta, .individual_internship")
        if not cards:
            cards = await page.query_selector_all("[id^='internship_']")

        logger.info("[Internshala] Found %d cards for skill '%s'", len(cards), skill)

        for card in cards:
            try:
                title_el = await card.query_selector(".job-internship-name, h3.job-title, .heading_4_5")
                company_el = await card.query_selector(".company-name, .link_display_like_text")
                stipend_el = await card.query_selector(".stipend, .ic-16-money + span, [id^='stipend']")
                url_el = await card.query_selector("a.job-title-href, a[href*='/internship/detail']")

                title = (await title_el.inner_text()).strip() if title_el else "N/A"
                company = (await company_el.inner_text()).strip() if company_el else "N/A"
                stipend_raw = (await stipend_el.inner_text()).strip() if stipend_el else ""
                href = await url_el.get_attribute("href") if url_el else ""

                job_url = href if href.startswith("http") else f"https://internshala.com{href}"
                stipend_min, stipend_max = _parse_stipend(stipend_raw)
                job_id = make_job_id("internshala", job_url)

                job = {
                    "id": job_id,
                    "platform": "internshala",
                    "title": title,
                    "company": company,
                    "stipend_min": stipend_min,
                    "stipend_max": stipend_max,
                    "stipend_text": stipend_raw,
                    "remote": True,  # WFH URL guarantees remote
                    "url": job_url,
                    "apply_url": job_url,
                    "description": "",
                    "required_skills": [],
                }
                jobs.append(job)
            except Exception as exc:
                logger.debug("[Internshala] Card parse error: %s", exc)

    except Exception as exc:
        logger.error("[Internshala] Error scraping skill '%s': %s", skill, exc)

    return jobs


async def _fetch_description(page: Page, job: Dict, cfg: Dict) -> Dict:
    """Visit the job detail page to extract full description."""
    try:
        await page.goto(job["url"], timeout=cfg["scraping"]["page_timeout_ms"], wait_until="domcontentloaded")
        await _random_delay(cfg["scraping"]["min_delay_ms"], cfg["scraping"]["max_delay_ms"])

        desc_el = await page.query_selector(".internship_details, #about_internship, .about-internship-section")
        if desc_el:
            job["description"] = (await desc_el.inner_text()).strip()

        skills_els = await page.query_selector_all(".round_tabs .round_tab_badge, .skills span")
        job["required_skills"] = []
        for el in skills_els:
            text = (await el.inner_text()).strip()
            if text:
                job["required_skills"].append(text)
    except Exception as exc:
        logger.debug("[Internshala] Could not fetch description for %s: %s", job.get("url"), exc)
    return job


async def scrape(cfg: Dict[str, Any]) -> List[Dict]:
    """Main entry point — returns list of job dicts from Internshala."""
    p_cfg = cfg["platforms"]["internshala"]
    if not p_cfg["enabled"]:
        logger.info("[Internshala] Platform disabled — skipping.")
        return []

    skills = p_cfg.get("search_skills", ["python", "react", "backend"])
    session_file = p_cfg.get("session_file", "sessions/internshala.json")
    all_jobs: List[Dict] = []
    seen_ids = set()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=cfg["scraping"]["user_agent"],
            viewport={"width": 1280, "height": 800},
            locale="en-IN",
        )
        await _load_cookies(context, session_file)
        page = await context.new_page()

        for skill in skills:
            jobs = await _scrape_skill(page, p_cfg["base_url"], skill, cfg)
            for job in jobs:
                if job["id"] not in seen_ids:
                    seen_ids.add(job["id"])
                    all_jobs.append(job)
            await _random_delay(cfg["scraping"]["min_delay_ms"], cfg["scraping"]["max_delay_ms"])

        # Fetch descriptions for first 30 unique jobs (rate-limit consideration)
        for job in all_jobs[:30]:
            await _fetch_description(page, job, cfg)
            await _random_delay(cfg["scraping"]["min_delay_ms"], cfg["scraping"]["max_delay_ms"])

        await browser.close()

    logger.info("[Internshala] Scraped %d unique jobs.", len(all_jobs))
    return all_jobs
