# playwright_manager.py
# Persistent Playwright manager to bypass Cloudflare Managed Challenge
# Designed for FastAPI / main.py usage

import os
import asyncio
import urllib.parse
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
    key: Optional[str] = None
#   download: Optional[str] = None
#   stream: Optional[str] = None


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
            headless=False,
            channel="chrome",
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

        await self.context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
        """)

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
        page.set_default_timeout(8000)

        results: List[Dict] = []

        await page.goto(self.base_url, wait_until="domcontentloaded")
        q = page.locator("#query")
        await q.fill(query)
        await q.press("Enter")

        items = page.locator(f"xpath={self.items_xpath}")

        # Ensure initial results are present before proceeding
        try:
            await items.first.wait_for(state="visible")
        except PlaywrightTimeoutError:
            await page.close()
            return results

        loadmore = page.get_by_role("button", name="Load more")

        try:
            if await loadmore.count() > 0 and await loadmore.first.is_visible():
                for _ in range(2):
                    if await loadmore.count() == 0:
                        break
                    if not await loadmore.first.is_visible():
                        break
                    await loadmore.first.scroll_into_view_if_needed()
                    await loadmore.first.click()
                    await page.wait_for_timeout(1500)
        except Exception:
            pass
                
        items = page.locator(f"xpath={self.items_xpath}")
        total = await items.count()
        print(f"Results = {total}")
        
        for idx in range(total):
            row = items.nth(idx)
            artist = (await row.locator("xpath=./a[2]").text_content()).strip()
            title  = (await row.locator("xpath=./a[3]").text_content()).strip()
            rowattr = row.locator(f"xpath=./div/ul/li[2]/a")
            duration = await rowattr.first.get_attribute("data-duration")
            href_attr = await rowattr.first.get_attribute("href")
            key = None
            if href_attr:
                parts = href_attr.split("/")
                if parts:
                    key = parts[-1].strip()
            
##           download = await rowattr.first.get_attribute("href")
##           stream = await rowattr.first.get_attribute("data-stream")
     

            if not any([artist, title, duration, key]):
                continue

            results.append(
                {
                    "id": len(results) + 1,
                    "artist": artist,
                    "title": title,
                    "duration": duration,
                    "key": key,
 #                   "download": (download or "").strip(),
 #                   "stream": (stream or "").strip(),
                }
            )

        try:
            await self.context.storage_state(path=self.cookie_file)
        except Exception:
            pass

        await page.close()
        return results

    # -----------------------------
    async def search_beatport_track_id(self, artist: str, title: str, mix: str) -> Optional[Dict]:
        query = f"{artist} {title}".strip()
        encoded_query = urllib.parse.quote(query)
        search_url = f"https://www.beatport.com/search/tracks?q={encoded_query}"

        def normalize(text: str) -> str:
            if not text: return ""
            text = text.lower().strip()
            import re
            # Remove common separators and special chars
            text = re.sub(r"\b(and|with|feat\.?|ft\.?|vs\.?|&)\b", " ", text)
            text = re.sub(r"[^\w\s]", " ", text)
            return " ".join(text.split())

        if not self.context:
            await self.start()
        page: Page = await self.context.new_page()
        page.set_default_timeout(5000)

        try:
            await page.goto(search_url, wait_until="domcontentloaded")
            await page.wait_for_timeout(1500)

            rows = page.locator("[data-testid='tracks-list-item'], [data-testid='tracks-table-row']")

            try:
                await rows.first.wait_for(state="visible", timeout=8000)
            except PlaywrightTimeoutError:
                await page.close()
                return None

            total = await rows.count()

            norm_title = normalize(title)
            norm_artist = normalize(artist)
            norm_mix = normalize(mix or "")

            matches = []

            for i in range(total):
                row = rows.nth(i)
                
                # --- 1. Track ID ---
                track_id = None
                overlay_elem = row.locator("[data-overlayfor^='track-']").first
                if await overlay_elem.count() > 0:
                    overlay_val = await overlay_elem.get_attribute("data-overlayfor")
                    if overlay_val:
                        tid_str = overlay_val.replace("track-", "").strip()
                        if tid_str.isdigit():
                            track_id = int(tid_str)

                # --- 2. Track Link & URL ---
                title_anchor = row.locator("a[href*='/track/']").first
                if await title_anchor.count() == 0:
                    continue
                
                href = await title_anchor.get_attribute("href")
                if not href:
                    continue
                
                track_url = f"https://www.beatport.com{href}"
                if not track_id:
                    parts = href.strip("/").split("/")
                    if parts and parts[-1].isdigit():
                        track_id = int(parts[-1])

                if not track_id:
                    continue

                # --- 3. Metadata ---
                row_title = normalize(await title_anchor.get_attribute("title") or "")
                
                row_mix = ""
                mix_span = title_anchor.locator("span span, span").first
                if await mix_span.count() > 0:
                    row_mix = normalize(await mix_span.text_content() or "")
                else:
                    mix_cell = row.locator(".cell.remix, [class*='remix']").first
                    if await mix_cell.count() > 0:
                        row_mix = normalize(await mix_cell.text_content() or "")

                artists_locator = row.locator("a[href*='/artist/']")
                artists_list = []
                for j in range(await artists_locator.count()):
                    txt = await artists_locator.nth(j).text_content()
                    if txt:
                        artists_list.append(txt)
                row_artists = normalize(" ".join(artists_list))

                # --- Matching Rules ---
                # title: exact
                if row_title != norm_title:
                    continue

                # artist: word-based contains
                artist_words = norm_artist.split()
                if not all(word in row_artists for word in artist_words):
                    continue

                # mix: word-based contains (allows "Daxson Remix" to match "Daxson Extended Remix" if query is Daxson Remix)
                # or allows query "Daxson Extended Remix" to match row "Daxson Remix" if all words present?
                # Actually, usually row has MORE detail. So we check if query words are in row.
                if norm_mix:
                    mix_words = norm_mix.split()
                    if not all(word in row_mix for word in mix_words):
                        continue

                # --- 4. Release Date ---
                date_cell = row.locator(".cell.date, [data-testid='released']").first
                release_date = None
                if await date_cell.count() > 0:
                    release_text = await date_cell.text_content()
                    if release_text:
                        release_date = release_text.strip()

                matches.append({
                    "track_id": track_id,
                    "track_url": track_url,
                    "release_date": release_date
                })

            if not matches:
                await page.close()
                return None

            # Sort by earliest release date
            matches = sorted(
                matches,
                key=lambda x: x["release_date"] or "9999-99-99"
            )

            best = matches[0]
            await page.close()
            return {"track_id": best["track_id"], "track_url": best["track_url"]}

        except Exception as e:
            print(f"Beatport search error: {e}")
            await page.close()
            return None

# -----------------------------
# Convenience function
# -----------------------------
_manager = PlaywrightManager()

async def search_tracks_async(query: str) -> List[Dict]:
    return await _manager.search_tracks(query)

async def search_beatport_track_id_async(artist: str, title: str, mix: str) -> Optional[Dict]:
    return await _manager.search_beatport_track_id(artist, title, mix)
