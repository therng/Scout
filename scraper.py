# playwright_manager.py
# Persistent Playwright manager to bypass Cloudflare Managed Challenge
# Designed for FastAPI / main.py usage

import os
import asyncio
from typing import List, Optional, Dict
import random
from pydantic import BaseModel
from playwright.async_api import (
    async_playwright,
    Browser,
    BrowserContext,
    Page,
    TimeoutError as PlaywrightTimeoutError,
)


# -----------------------------
# Pydantic model (not used in responses; kept for reference)
# -----------------------------
class Track(BaseModel):
    artist: Optional[str] = None
    title: Optional[str] = None
    duration: Optional[int] = None
    download: Optional[str] = None
    stream: Optional[str] = None


# -----------------------------
# Playwright Manager (Singleton)
# -----------------------------
class PlaywrightManager:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, "_initialized"):
            return

        self._initialized = True
        self.playwright = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None

        # Selectors and config from environment
        self.base_url = os.environ.get("BASE_URL")
        self.user_agent = os.environ.get("USER_AGENT")
        self.query_xpath = os.environ.get("QUERY_XPATH")
        self.more_xpath = os.environ.get("MORE_XPATH")
        self.items_xpath = os.environ.get("ITEMS_XPATH")
        self.first_xpath = os.environ.get("FIRST_XPATH")
        self.list_xpath = self.items_xpath

        self.cookie_file = "cookies.json"

    # -----------------------------
    async def start(self):
        if self.browser:
            return

        self.playwright = await async_playwright().start()

        self.browser = await self.playwright.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )

        context_args = {
            "user_agent": self.user_agent,
            "viewport": {"width": 1280, "height": 800},
            "java_script_enabled": True,
            "ignore_https_errors": True,
        }

        if os.path.exists(self.cookie_file):
            context_args["storage_state"] = self.cookie_file

        self.context = await self.browser.new_context(**context_args)

    # -----------------------------
    async def stop(self):
        if self.context:
            await self.context.close()
            self.context = None
        if self.browser:
            await self.browser.close()
            self.browser = None
        if self.playwright:
            await self.playwright.stop()
            self.playwright = None


# -----------------------------
    async def search_tracks(self, query: str) -> List[Dict]:
        if not self.base_url:
            raise RuntimeError("BASE_URL is not set in environment")

        await self.start()

        page: Page = await self.context.new_page()
        page.set_default_timeout(10000)  # 10s default timeout

        results: List[Dict] = []

        await page.goto(self.base_url, wait_until="domcontentloaded")
        q = page.locator("#query")
        await q.fill(query)
        await q.press("Enter")
        
        loadmore = page.get_by_role("button", name="Load more")
        await loadmore.scroll_into_view_if_needed()
        if (await loadmore.is_visible()):
            await loadmore.click()

        items = page.locator(f"xpath={self.items_xpath}")
        total = await items.count()
        print(f"Results = {total}")
        
        for idx in  range (1, total):
            row = items.nth(idx)
            artist = await row.locator(f"xpath=./a[2]").text_content()
            title = await row.locator(f"xpath=./a[3]").text_content()
            rowattr = row.locator(f"xpath=./div/ul/li[2]/a")
            duration = await rowattr.first.get_attribute("data-duration")
            download = await rowattr.first.get_attribute("href")
            stream = await rowattr.first.get_attribute("data-stream")
     

            if not any([artist, title, duration, download, stream]):
                continue

            results.append(
                {
                    "id": len(results) + 1,
                    "artist": (artist or "").strip(),
                    "title": (title or "").strip(),
                    "duration": duration,
                    "download": (download or "").strip(),
                    "stream": (stream or "").strip(),
                }
            )

        try:
            await self.context.storage_state(path=self.cookie_file)
        except Exception:
            pass

        await page.close()
        return results
# -----------------------------
# Convenience function
# -----------------------------
_manager = PlaywrightManager()

async def search_tracks_async(query: str) -> List[Dict]:
    return await _manager.search_tracks(query)
