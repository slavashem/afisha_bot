import asyncio
import re
from typing import TypedDict, Optional
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, Page
from utils.logger import logger


class EventData(TypedDict):
    title: str
    date: str
    place: str
    description: str
    ticket_url: str
    afisha_url: str
    image_url: str


# ─── Источники для сбора ────────────────────────────────────────────────────

HOT_URL = (
    "https://afisha.yandex.ru/kaliningrad/selections/"
    "hot?source=selection-events&city=kaliningrad"
)
STANDUP_URL = "https://afisha.yandex.ru/kaliningrad/standup?source=menu"
SPECTACL_URL = "https://afisha.yandex.ru/kaliningrad/selections/spectacl"

# ─── Вспомогательные утилиты ─────────────────────────────────────────────────


async def _slow_scroll(page: Page, steps: int = 6) -> None:
    for _ in range(steps):
        await page.evaluate("window.scrollBy(0, window.innerHeight * 0.8)")
        await asyncio.sleep(1.2)


async def _get_event_urls(page: Page, base_url: str) -> list[str]:
    logger.info(f"Opening: {base_url}")
    response = await page.goto(base_url, wait_until="domcontentloaded", timeout=45000)

    # Проверка HTTP-статуса
    if response and response.status >= 400:
        logger.warning(
            f"HTTP {response.status} for {base_url}, skipping this source"
        )
        return []

    try:
        await page.wait_for_selector("a[href*='/kaliningrad/']", timeout=15000)
    except Exception:
        logger.warning("Timed out waiting for event links, proceeding anyway")

    await asyncio.sleep(3)
    await _slow_scroll(page)
    await asyncio.sleep(2)

    hrefs: list[str] = await page.eval_on_selector_all(
        "a[href]", "els => els.map(e => e.getAttribute('href'))"
    )

    event_pattern = re.compile(
        r"/kaliningrad/"
        r"(concert|theatre|other|cinema|exhibition|show|sport|standup|kids|party|festival|dance|lecture)"
        r"/[a-z0-9\-]+"
    )

    urls: set[str] = set()
    for href in hrefs:
        if not href:
            continue
        clean = href.split("?")[0].split("#")[0]
        if event_pattern.search(clean):
            if clean.startswith("/"):
                clean = "https://afisha.yandex.ru" + clean
            urls.add(clean)

    logger.info(f"Found {len(urls)} unique event URLs from {base_url}")
    return list(urls)


async def _extract_ticket_url(soup: BeautifulSoup, page_url: str) -> str:
    """Всегда возвращает ссылку на страницу мероприятия на Яндекс Афише
    (вместо попыток найти партнёрскую ссылку на билеты)."""
    return page_url


async def _parse_event_page(page: Page, url: str) -> Optional[EventData]:
    try:
        response = await page.goto(
            url, wait_until="domcontentloaded", timeout=30000
        )

        # Проверка HTTP-статуса на странице события
        if response and response.status >= 400:
            logger.warning(
                f"HTTP {response.status} for event page {url}, skipping"
            )
            return None

        await asyncio.sleep(2)

        html = await page.content()
        soup = BeautifulSoup(html, "lxml")

        title = (
            _text(soup, "h1")
            or _text(soup, "[class*='Title']")
            or _text(soup, "[class*='title']")
        )

        date = (
            _text(soup, "time")
            or _text(soup, "[class*='Date']")
            or _text(soup, "[class*='date']")
            or _text(soup, "[class*='schedule']")
        )

        place = (
            _text(soup, "[class*='PlaceName']")
            or _text(soup, "[class*='VenueName']")
            or _text(soup, "[class*='placeName']")
            or _text(soup, "[class*='venue']")
            or _text(soup, "[class*='Place']")
        )

        description = _best_description(soup)

        image_url = (
            _attr(soup, "meta[property='og:image']", "content")
            or _attr(soup, "picture source", "srcset")
            or _attr(soup, "picture img", "src")
            or _attr(soup, "[class*='poster'] img", "src")
            or _attr(soup, "[class*='Image'] img", "src")
        )

        if image_url:
            image_url = _clean_image_url(image_url)

        ticket_url = await _extract_ticket_url(soup, url)

        if not title:
            return None

        return EventData(
            title=title,
            date=date or "",
            place=place or "",
            description=description or "",
            ticket_url=ticket_url,
            afisha_url=url,
            image_url=image_url or "",
        )

    except Exception as e:
        logger.warning(f"Failed to parse {url}: {e}")
        return None


def _text(soup: BeautifulSoup, selector: str) -> str:
    el = soup.select_one(selector)
    if el:
        t = el.get_text(separator=" ", strip=True)
        if t:
            return t
    return ""


def _attr(soup: BeautifulSoup, selector: str, attr: str) -> str:
    el = soup.select_one(selector)
    if el and el.get(attr):
        return str(el[attr])
    return ""


def _best_description(soup: BeautifulSoup) -> str:
    candidates = [
        "[class*='Description']",
        "[class*='description']",
        "[class*='about']",
        "[class*='About']",
        "[class*='content']",
        "article",
        "section p",
    ]
    best = ""
    for sel in candidates:
        for el in soup.select(sel):
            t = el.get_text(separator=" ", strip=True)
            if len(t) > len(best) and len(t) > 30:
                best = t
    if not best:
        meta = (
            soup.select_one("meta[name='description']")
            or soup.select_one("meta[property='og:description']")
        )
        if meta and meta.get("content"):
            best = str(meta["content"])
    return best[:1000]


def _clean_image_url(url: str) -> str:
    if url.startswith("//"):
        url = "https:" + url
    if "," in url and "http" in url:
        url = url.split(",")[0].strip().split(" ")[0]
    return url


def _is_valid(event: EventData) -> bool:
    return bool(event.get("title") and event.get("afisha_url"))


# ─── Парсинг с ОДНОГО источника ─────────────────────────────────────────────


async def parse_events_from_url(
    afisha_url: str, max_events: int = 5
) -> list[EventData]:
    """Парсит события с указанного URL Яндекс Афиши.

    Args:
        afisha_url: URL страницы-подборки на Яндекс Афише.
        max_events: Максимальное количество событий для возврата.

    Returns:
        Список EventData (возможно пустой).
    """
    events: list[EventData] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="ru-RU",
            viewport={"width": 1280, "height": 900},
        )
        page = await context.new_page()

        try:
            urls = await _get_event_urls(page, afisha_url)
            if not urls:
                logger.warning(
                    f"No event URLs found on {afisha_url} — "
                    "check page structure or HTTP status"
                )

            for url in urls[: max_events * 3]:
                await asyncio.sleep(1.5)
                event = await _parse_event_page(page, url)
                if event and _is_valid(event):
                    events.append(event)
                    logger.info(
                        f"Parsed: {event['title']} | "
                        f"билеты: {event['ticket_url']}"
                    )
                if len(events) >= max_events:
                    break

        except Exception as e:
            logger.error(f"Parser error on {afisha_url}: {e}")
        finally:
            await browser.close()

    logger.info(f"Parsed {len(events)} events from {afisha_url}")
    return events


# ─── Оркестратор — сбор со ВСЕХ источников ────────────────────────────────


async def parse_all_events(max_total: int = 5) -> list[EventData]:
    """Собирает события из нескольких источников Яндекс Афиши.

    Логика:
    - hot      (selection) → до 3 событий (или сколько есть, но не больше 3)
    - standup              → 1 событие
    - spectacl             → 1 событие (если 404 — пропускаем)

    Args:
        max_total: Общий лимит событий (не используется напрямую,
                   каждый источник имеет свой фиксированный лимит).

    Returns:
        Список EventData (до 5 штук), дедуплицированный по afisha_url.
    """
    # Лимиты для каждого источника
    limits = {
        "hot": min(3, max_total),
        "standup": 1,
        "spectacl": 1,
    }

    async def _safe_parse(url: str, limit: int, label: str) -> list[EventData]:
        """Безопасный парсинг одного источника с обработкой ошибок."""
        try:
            logger.info(f"Parsing source [{label}]: {url}")
            result = await parse_events_from_url(url, max_events=limit)
            logger.info(
                f"Source [{label}] returned {len(result)} event(s)"
            )
            return result
        except Exception as e:
            logger.warning(
                f"Source [{label}] failed: {e}. Skipping."
            )
            return []

    # Запускаем все источники параллельно
    hot_task = _safe_parse(HOT_URL, limits["hot"], "hot")
    standup_task = _safe_parse(STANDUP_URL, limits["standup"], "standup")
    spectacl_task = _safe_parse(SPECTACL_URL, limits["spectacl"], "spectacl")

    hot_events, standup_events, spectacl_events = await asyncio.gather(
        hot_task, standup_task, spectacl_task
    )

    # Собираем в порядке приоритета: hot → standup → spectacl
    all_events: list[EventData] = []
    seen_urls: set[str] = set()

    for events in [hot_events, standup_events, spectacl_events]:
        for event in events:
            url = event.get("afisha_url", "")
            if url and url not in seen_urls:
                seen_urls.add(url)
                all_events.append(event)

    logger.info(
        f"Total unique events after dedup: {len(all_events)} "
        f"(hot={len(hot_events)}, standup={len(standup_events)}, "
        f"spectacl={len(spectacl_events)})"
    )
    return all_events
