import asyncio
import json
import re
from playwright.async_api import async_playwright
from utils.logger import logger


async def search_event_images(query: str, count: int = 5) -> list[str]:
    """
    Searches Bing Images for clean photos (no logos/flyers).
    Returns up to `count` direct image URLs.
    """
    search_query = f"{query} фото"
    encoded = search_query.replace(" ", "+")
    url = (
        f"https://www.bing.com/images/search"
        f"?q={encoded}&qft=+filterui:photo-photo&form=IRFLTR&first=1"
    )

    urls: list[str] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="ru-RU",
        )
        page = await context.new_page()

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(2)

            # Bing embeds image data in anchor tags with attribute m='{json}'
            m_attrs: list[str] = await page.eval_on_selector_all(
                "a.iusc[m]",
                "els => els.map(e => e.getAttribute('m'))"
            )

            for raw in m_attrs:
                if not raw:
                    continue
                try:
                    data = json.loads(raw)
                    murl = data.get("murl", "")
                    if murl and murl.startswith("http") and _is_image_url(murl):
                        urls.append(murl)
                except Exception:
                    continue
                if len(urls) >= count:
                    break

            # Fallback: try extracting from img tags with src
            if not urls:
                srcs: list[str] = await page.eval_on_selector_all(
                    "img.mimg",
                    "els => els.map(e => e.getAttribute('src') || e.getAttribute('data-src'))"
                )
                for src in srcs:
                    if src and src.startswith("http") and _is_image_url(src):
                        urls.append(src)
                    if len(urls) >= count:
                        break

        except Exception as e:
            logger.error(f"Image search error: {e}")
        finally:
            await browser.close()

    logger.info(f"Found {len(urls)} images for query: {query}")
    return urls


def _is_image_url(url: str) -> bool:
    return bool(re.search(r"\.(jpg|jpeg|png|webp)(\?|$)", url, re.IGNORECASE))


def build_image_query(event: dict) -> str:
    title = event.get("title", "")
    # Try to isolate artist name (first 2-3 words usually)
    words = title.split()
    artist = " ".join(words[:3]) if len(words) >= 3 else title
    return artist
