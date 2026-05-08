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
SPECTACL_FALLBACK_URL = "https://afisha.yandex.ru/kaliningrad/theatre?source=menu"

# ─── Параметры ──────────────────────────────────────────────────────────────

BROWSER_OPTIONS = {
    "headless": True,
}

CONTEXT_OPTIONS = {
    "user_agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "locale": "ru-RU",
    "viewport": {"width": 1280, "height": 900},
}

# Сколько «лишних» URL пробуем сверх лимита, чтобы компенсировать
# уже обработанные/невалидные события.
ATTEMPT_BUFFER = 4


# ─── Умный скролл (до стабилизации высоты) ──────────────────────────────────


async def _scroll_until_stable(
    page: Page,
    max_scrolls: int = 15,
    pause_sec: float = 1.3,
) -> int:
    """Скроллит страницу вниз, пока высота документа не перестанет расти.

    Возвращает количество сделанных скролл-шагов.
    Бесконечная лента на Яндекс Афише догружает события при скролле —
    фиксированное число шагов может не добраться до всех событий.
    """
    prev_height = await page.evaluate("document.body.scrollHeight")

    for step in range(1, max_scrolls + 1):
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(pause_sec)

        new_height = await page.evaluate("document.body.scrollHeight")
        logger.debug(f"Scroll step {step}: height {prev_height} → {new_height}")

        if new_height == prev_height:
            logger.info(f"Page height stabilised after {step} scroll(s)")
            return step

        prev_height = new_height

    logger.info(f"Reached max scrolls ({max_scrolls}), stopping")
    return max_scrolls


# ─── Сбор URL событий с одной страницы-источника ────────────────────────────


async def _get_event_urls(page: Page, base_url: str) -> list[str]:
    """Загружает страницу-подборку, скроллит, собирает ссылки на мероприятия.

    Возвращает список абсолютных URL мероприятий (дедуплицированный).
    При HTTP-ошибке (4xx/5xx) возвращает пустой список.
    """
    logger.info(f"Opening source page: {base_url}")
    response = await page.goto(base_url, wait_until="domcontentloaded", timeout=45000)

    # Проверка HTTP-статуса
    if response and response.status >= 400:
        logger.warning(
            f"HTTP {response.status} for {base_url}, skipping this source"
        )
        return []

    # Ждём появления хотя бы 3 ссылок на мероприятия
    try:
        await page.wait_for_function(
            "document.querySelectorAll('a[href*=\"/kaliningrad/\"]').length > 3",
            timeout=20000,
        )
        logger.info("Event links found on the page")
    except Exception:
        logger.warning(
            "Fewer than 3 event links detected, but proceeding anyway"
        )

    await asyncio.sleep(2)

    # Скроллим, пока подгружаются новые события
    await _scroll_until_stable(page)
    await asyncio.sleep(1.5)

    # Собираем все ссылки
    hrefs: list[str] = await page.eval_on_selector_all(
        "a[href]", "els => els.map(e => e.getAttribute('href'))"
    )

    # Универсальный паттерн: /kaliningrad/ЛЮБАЯ_КАТЕГОРИЯ/слаг
    event_pattern = re.compile(r"/kaliningrad/[a-z]+/[a-z0-9\-]+")

    urls: set[str] = set()
    for href in hrefs:
        if not href:
            continue
        clean = href.split("?")[0].split("#")[0]
        if event_pattern.search(clean):
            if clean.startswith("/"):
                clean = "https://afisha.yandex.ru" + clean
            urls.add(clean)

    logger.info(
        f"Source [{base_url.split('/')[-1].split('?')[0]}]: "
        f"found {len(urls)} unique event URLs"
    )
    return list(urls)


# ─── Парсинг одной страницы мероприятия ─────────────────────────────────────


async def _extract_ticket_url(soup: BeautifulSoup, page_url: str) -> str:
    """Всегда возвращает ссылку на страницу мероприятия на Яндекс Афише."""
    return page_url


async def _parse_event_page(page: Page, url: str) -> Optional[EventData]:
    """Открывает страницу одного мероприятия и извлекает данные.

    Использует BeautifulSoup для парсинга HTML, полученного через Playwright.
    """
    try:
        response = await page.goto(
            url, wait_until="domcontentloaded", timeout=30000
        )

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
            logger.info(f"No title found at {url}, skipping")
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


# ─── Вспомогательные утилиты для BS4 ────────────────────────────────────────


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


# ─── Парсинг с ОДНОГО источника (обратная совместимость) ────────────────────


async def parse_events_from_url(
    afisha_url: str, max_events: int = 5
) -> list[EventData]:
    """Парсит события с указанного URL Яндекс Афиши.

    Создаёт отдельный браузер (удобно для одиночного вызова).
    Для многократного вызова используйте parse_all_events() —
    он переиспользует один браузер для всех источников.
    """
    events: list[EventData] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(**BROWSER_OPTIONS)
        context = await browser.new_context(**CONTEXT_OPTIONS)
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


# ─── Оркестратор — сбор со ВСЕХ источников (ОДИН браузер) ──────────────────


async def parse_all_events(max_total: int = 5) -> list[EventData]:
    """Собирает события из нескольких источников Яндекс Афиши.

    Распределение лимитов по источникам:
    - hot (selection)  → max_total - 2 (минимум 3)
    - standup          → 1
    - spectacl         → 1
    - fallback на theatre, если spectacl пустой

    Для каждого источника пробуем limit * ATTEMPT_BUFFER URL-адресов,
    чтобы заполнить квоту даже при наличии невалидных страниц.
    Дедупликация по afisha_url.

    Args:
        max_total: Суммарное желаемое количество событий.
                   Прокидывается из settings.parse_count.

    Returns:
        Список EventData (до max_total штук).
    """
    # ── Распределяем лимиты ──────────────────────────────────────────────
    # standup и spectacl всегда по 1; остаток отдаём hot (минимум 3)
    standup_limit = 1
    spectacl_limit = 1
    hot_limit = max(3, max_total - standup_limit - spectacl_limit)

    limits = {
        "hot": hot_limit,
        "standup": standup_limit,
        "spectacl": spectacl_limit,
    }

    sources: list[tuple[str, str]] = [
        (HOT_URL, "hot"),
        (STANDUP_URL, "standup"),
        (SPECTACL_URL, "spectacl"),
    ]

    logger.info(
        f"parse_all_events: max_total={max_total}, "
        f"limits=hot:{hot_limit} standup:{standup_limit} spectacl:{spectacl_limit}"
    )

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(**BROWSER_OPTIONS)
        context = await browser.new_context(**CONTEXT_OPTIONS)

        # ── Шаг 1: собираем URL со всех источников ─────────────────────────
        collected: dict[str, list[str]] = {}

        for source_url, label in sources:
            logger.info(f"Collecting URLs from [{label}]: {source_url}")
            page = await context.new_page()
            try:
                urls = await _get_event_urls(page, source_url)
                collected[label] = urls
            except Exception as e:
                logger.warning(f"Failed to collect [{label}]: {e}")
                collected[label] = []
            finally:
                await page.close()

        # ── Fallback: если spectacl пустой, пробуем theatre ────────────────
        if not collected.get("spectacl"):
            logger.info(
                "spectacl returned 0 events, "
                f"trying fallback: {SPECTACL_FALLBACK_URL}"
            )
            page = await context.new_page()
            try:
                urls = await _get_event_urls(page, SPECTACL_FALLBACK_URL)
                collected["spectacl"] = urls
            except Exception as e:
                logger.warning(f"Fallback [spectacl] also failed: {e}")
                collected["spectacl"] = []
            finally:
                await page.close()

        # ── Шаг 2: парсим каждое мероприятие ───────────────────────────────
        page = await context.new_page()
        all_events: list[EventData] = []
        seen_urls: set[str] = set()

        for label in ["hot", "standup", "spectacl"]:
            source_urls = collected.get(label, [])
            limit = limits[label]
            # Пробуем limit * ATTEMPT_BUFFER URL-ов, чтобы набрать limit валидных
            attempt_cap = limit * ATTEMPT_BUFFER
            parsed_count = 0
            attempt_count = 0

            logger.info(
                f"Parsing [{label}]: {len(source_urls)} URLs available, "
                f"limit={limit}, attempt_cap={attempt_cap}"
            )

            for url in source_urls:
                if parsed_count >= limit:
                    break
                if attempt_count >= attempt_cap:
                    logger.info(
                        f"[{label}] Reached attempt cap ({attempt_cap}), stopping"
                    )
                    break

                attempt_count += 1
                await asyncio.sleep(1.2)
                event = await _parse_event_page(page, url)

                if event and _is_valid(event):
                    afisha_url = event.get("afisha_url", "")
                    if afisha_url and afisha_url not in seen_urls:
                        seen_urls.add(afisha_url)
                        all_events.append(event)
                        parsed_count += 1
                        logger.info(
                            f"[{label}] Parsed #{parsed_count}: {event['title']}"
                        )

            logger.info(
                f"[{label}] Done: {parsed_count}/{limit} events "
                f"after {attempt_count} attempts"
            )

        await browser.close()

    # ── Итоговая статистика ─────────────────────────────────────────────────
    hot_count = sum(
        1 for e in all_events
        if e["afisha_url"].startswith(HOT_URL.split("/selections")[0])
    )
    standup_count = sum(1 for e in all_events if "standup" in e["afisha_url"])
    spectacl_count = sum(
        1 for e in all_events
        if "spectacl" in e["afisha_url"] or "theatre" in e["afisha_url"]
    )

    logger.info(
        f"Parsed successfully: {len(all_events)} unique events "
        f"(hot={hot_count}/{limits['hot']}, "
        f"standup={standup_count}/{limits['standup']}, "
        f"spectacl={spectacl_count}/{limits['spectacl']})"
    )
    logger.info(
        f"URLs collected: "
        f"hot={len(collected.get('hot', []))}, "
        f"standup={len(collected.get('standup', []))}, "
        f"spectacl={len(collected.get('spectacl', []))}"
    )

    return all_events
