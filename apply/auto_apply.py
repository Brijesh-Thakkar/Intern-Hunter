"""
apply/auto_apply.py — Playwright-based form filler for Internshala and LinkedIn Easy Apply
"""

import asyncio
import json
import logging
import os
import random
import re
from pathlib import Path
from typing import Dict, Any, Optional

from playwright.async_api import async_playwright, BrowserContext, Page

from core.database import db_log

logger = logging.getLogger(__name__)

# Selectors that indicate a "why / cover letter" textarea
COVER_LETTER_HINTS = ["why", "cover", "describe", "motivation", "interest",
                      "tell us", "about yourself", "introduce", "reason"]


async def _random_delay(min_ms: int = 800, max_ms: int = 3000):
    await asyncio.sleep(random.uniform(min_ms / 1000, max_ms / 1000))


async def _load_cookies(context: BrowserContext, session_file: str) -> bool:
    if not os.path.exists(session_file):
        logger.warning("Session file not found: %s", session_file)
        return False
    with open(session_file) as f:
        cookies = json.load(f)
    await context.add_cookies(cookies)
    logger.debug("Cookies loaded from %s", session_file)
    return True


async def _fill_cover_letter_areas(page: Page, cover_letter: str) -> None:
    """Find all textarea/input fields that look like cover letter fields and fill them."""
    textareas = await page.query_selector_all("textarea")
    for ta in textareas:
        placeholder = (await ta.get_attribute("placeholder") or "").lower()
        label_for = await ta.get_attribute("id") or ""
        label_el = await page.query_selector(f"label[for='{label_for}']")
        label_text = ((await label_el.inner_text()).lower() if label_el else "")

        combined = placeholder + " " + label_text
        if any(hint in combined for hint in COVER_LETTER_HINTS):
            await ta.fill(cover_letter)
            logger.debug("Filled cover letter in textarea (hint: %s)", combined[:40])
            await _random_delay(300, 800)


async def _handle_radio_or_select(page: Page) -> None:
    """Handle common Yes/No radio buttons for availability/degree questions."""
    # Handle availability radio buttons — prefer "Yes" / positive options
    radios = await page.query_selector_all("input[type='radio']")
    for radio in radios:
        try:
            value = (await radio.get_attribute("value") or "").lower()
            label_for = await radio.get_attribute("id") or ""
            label_el = await page.query_selector(f"label[for='{label_for}']")
            label_text = ((await label_el.inner_text()).lower() if label_el else "")
            # Select "yes" / "available" / "full-time" options
            if any(pos in value + label_text for pos in ["yes", "available", "full time", "full-time", "immediate"]):
                if not await radio.is_checked():
                    await radio.check()
                    await _random_delay(200, 500)
        except Exception:
            pass

    # Handle dropdowns — select first/best option
    selects = await page.query_selector_all("select")
    for sel in selects:
        try:
            options = await sel.query_selector_all("option")
            if len(options) > 1:
                await options[1].evaluate("el => el.selected = true")
                await sel.dispatch_event("change")
        except Exception:
            pass


# ── Internshala auto-apply ────────────────────────────────────────────────────

async def apply_internshala(
    job: Dict[str, Any],
    cover_letter: str,
    cfg: Dict[str, Any],
    dry_run: bool = False,
) -> str:
    """
    Apply to an Internshala job listing.

    Returns: status string — 'auto_applied' | 'error'
    """
    session_file = cfg["platforms"]["internshala"]["session_file"]
    screenshots_dir = cfg["agent"]["screenshots_dir"]
    Path(screenshots_dir).mkdir(parents=True, exist_ok=True)
    db_path = cfg["agent"]["db_path"]

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=cfg["scraping"]["user_agent"],
            viewport={"width": 1280, "height": 800},
        )
        cookies_loaded = await _load_cookies(context, session_file)
        if not cookies_loaded:
            await browser.close()
            return "error"

        page = await context.new_page()
        try:
            logger.info("[Apply/Internshala] Opening: %s", job["url"])
            await page.goto(
                job["url"],
                timeout=cfg["scraping"]["page_timeout_ms"],
                wait_until="domcontentloaded",
            )
            await _random_delay(cfg["scraping"]["min_delay_ms"], cfg["scraping"]["max_delay_ms"])

            # Click Apply button
            apply_btn = await page.query_selector(
                "button#continue_button, button.btn-block, button:has-text('Apply Now'), "
                "a.apply_now_btn, [id*='apply'], button:has-text('Apply')"
            )
            if not apply_btn:
                logger.warning("[Apply/Internshala] Apply button not found for %s", job["url"])
                await browser.close()
                return "error"

            if not dry_run:
                await apply_btn.click()
                await _random_delay(1000, 2000)

                # Wait for application form / modal
                try:
                    await page.wait_for_selector(
                        "textarea, .cover_letter_container, #cover_letter",
                        timeout=8000,
                    )
                except Exception:
                    logger.warning("[Apply/Internshala] Form not found after clicking Apply")

                await _fill_cover_letter_areas(page, cover_letter)
                await _handle_radio_or_select(page)
                await _random_delay(800, 1500)

                # Submit
                submit_btn = await page.query_selector(
                    "button[type='submit'], button:has-text('Submit'), "
                    "button:has-text('Send Application'), #submit"
                )
                if submit_btn:
                    await submit_btn.click()
                    await _random_delay(1500, 3000)
                    logger.info("[Apply/Internshala] Submitted application for %s", job.get("company"))
                else:
                    logger.warning("[Apply/Internshala] Submit button not found")

            # Screenshot
            screenshot_path = os.path.join(screenshots_dir, f"{job['id']}.png")
            await page.screenshot(path=screenshot_path, full_page=False)
            logger.info("[Apply/Internshala] Screenshot saved: %s", screenshot_path)

            status = "dry_run" if dry_run else "auto_applied"
            db_log(db_path, "INFO", f"[Internshala] Applied to {job.get('company')} — {job.get('title')} ({status})")
            return status

        except Exception as exc:
            logger.error("[Apply/Internshala] Error: %s", exc)
            db_log(db_path, "ERROR", f"[Internshala] Apply error for {job.get('url')}: {exc}")
            try:
                error_path = os.path.join(screenshots_dir, f"{job['id']}_error.png")
                await page.screenshot(path=error_path)
            except Exception:
                pass
            return "error"
        finally:
            await browser.close()


# ── LinkedIn Easy Apply ───────────────────────────────────────────────────────

async def apply_linkedin(
    job: Dict[str, Any],
    cover_letter: str,
    cfg: Dict[str, Any],
    dry_run: bool = False,
) -> str:
    """
    Apply to a LinkedIn job via Easy Apply.

    Returns: status string — 'auto_applied' | 'error'
    """
    session_file = cfg["platforms"]["linkedin"]["session_file"]
    db_path = cfg["agent"]["db_path"]
    phone = cfg["candidate"]["phone"]

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=cfg["scraping"]["user_agent"],
            viewport={"width": 1366, "height": 768},
            locale="en-US",
        )
        cookies_loaded = await _load_cookies(context, session_file)
        if not cookies_loaded:
            await browser.close()
            return "error"

        page = await context.new_page()
        try:
            logger.info("[Apply/LinkedIn] Opening: %s", job["url"])
            await page.goto(
                job["url"],
                timeout=cfg["scraping"]["page_timeout_ms"],
                wait_until="domcontentloaded",
            )
            await _random_delay(cfg["scraping"]["min_delay_ms"], cfg["scraping"]["max_delay_ms"])

            # Look for Easy Apply button
            easy_btn = await page.query_selector(
                "button.jobs-apply-button, button[aria-label*='Easy Apply'], "
                "button:has-text('Easy Apply')"
            )
            if not easy_btn:
                logger.warning("[Apply/LinkedIn] Easy Apply button not found for %s", job["url"])
                await browser.close()
                return "error"

            if dry_run:
                await browser.close()
                return "dry_run"

            await easy_btn.click()
            await _random_delay(1000, 2000)

            # Handle multi-step modal — up to 6 steps
            max_steps = 6
            for step in range(max_steps):
                # Fill phone number if present
                phone_inputs = await page.query_selector_all(
                    "input[name*='phone'], input[id*='phone'], input[type='tel']"
                )
                for inp in phone_inputs:
                    existing_val = await inp.input_value()
                    if not existing_val:
                        await inp.fill(phone)
                        await _random_delay(200, 500)

                # Fill textareas (cover letter / additional questions)
                await _fill_cover_letter_areas(page, cover_letter)

                # Handle radio buttons and Yes/No selects
                await _handle_radio_or_select(page)

                # Also handle standard textareas that might not be cover letter fields
                # but need answers (mark highest education etc.)
                text_inputs = await page.query_selector_all("input[type='text']:not([readonly])")
                for inp in text_inputs:
                    existing_val = await inp.input_value()
                    if not existing_val:
                        # Fill generic text inputs with reasonable defaults
                        placeholder = (await inp.get_attribute("placeholder") or "").lower()
                        if "city" in placeholder or "location" in placeholder:
                            await inp.fill("India")
                        elif "year" in placeholder:
                            await inp.fill("2025")
                        elif "gpa" in placeholder or "cgpa" in placeholder:
                            await inp.fill("8.5")

                await _random_delay(500, 1000)

                # Look for Next / Review / Submit buttons
                next_btn = await page.query_selector(
                    "button[aria-label='Continue to next step'], button:has-text('Next'), "
                    "button[aria-label='Review your application']"
                )
                review_btn = await page.query_selector(
                    "button:has-text('Review'), button[aria-label='Review']"
                )
                submit_btn = await page.query_selector(
                    "button[aria-label='Submit application'], button:has-text('Submit application')"
                )

                if submit_btn:
                    await submit_btn.click()
                    await _random_delay(1500, 2500)
                    logger.info("[Apply/LinkedIn] Application submitted for %s @ %s",
                                job.get("title"), job.get("company"))
                    db_log(db_path, "INFO", f"[LinkedIn] Applied: {job.get('company')} — {job.get('title')}")
                    await browser.close()
                    return "auto_applied"
                elif review_btn:
                    await review_btn.click()
                    await _random_delay(800, 1500)
                elif next_btn:
                    await next_btn.click()
                    await _random_delay(800, 1500)
                else:
                    # No navigation button found — try ESC to close and mark as error
                    logger.warning("[Apply/LinkedIn] Navigation button not found at step %d", step)
                    break

            logger.warning("[Apply/LinkedIn] Could not complete application after %d steps", max_steps)
            return "error"

        except Exception as exc:
            logger.error("[Apply/LinkedIn] Error: %s", exc)
            db_log(db_path, "ERROR", f"[LinkedIn] Apply error for {job.get('url')}: {exc}")
            return "error"
        finally:
            try:
                await browser.close()
            except Exception:
                pass


# ── Session helpers ───────────────────────────────────────────────────────────

async def save_session_interactive(platform: str, url: str, session_file: str) -> None:
    """
    Open a headed browser, let user log in manually, then save cookies to session_file.
    Used by setup.py.
    """
    Path(session_file).parent.mkdir(parents=True, exist_ok=True)
    print(f"\n[Setup] Opening {platform} in a visible browser window.")
    print(f"  → Please log in and complete any CAPTCHAs, then come back here.")
    print(f"  → When you are logged in and on the dashboard/homepage, press Enter.")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            )
        )
        page = await context.new_page()
        await page.goto(url)

        input(f"  → Press Enter once you are fully logged in to {platform}… ")

        cookies = await context.cookies()
        with open(session_file, "w") as f:
            json.dump(cookies, f, indent=2)
        print(f"  ✓ Cookies saved to {session_file}")
        await browser.close()
