"""
Парсер Яндекс Афиши — Калининград.

Стратегия: вместо редакционных подборок (hot/spectacl), которые меняются
редко и дают одни и те же события, парсим напрямую категорийные страницы.
Каждая категория отсортирована по дате → всегда свежий контент.

Категории обходятся по очереди, из каждой берём по 1–2 события.
Итого при max_total=10 охватываем 5–8 разных категорий.
"""

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
    category: str  # новое поле — категория события


# ─── Категорийные страницы (приоритет по убыванию) ──────────────────────────
# Берём только те категории, где реально есть события с билетами.
# Порядок важен: первые категории обходятся первыми.

CATEGORIES = [
    ("concert",     "Концерты"),
    ("theatre",     "Театр"),
    ("festival",    "Фестивали"),
    ("standup",     "Стендап"),
    ("show",        "Шоу"),
    ("art",         "Выставки"),
    ("kids",        "Детям"),
    ("musical",     "Мюзиклы"),
    ("excursions",  "Экскурсии"),
    ("masterclass", "Мастер-классы"),
    ("lectures",    "Лекции"),
]

BASE_URL = "https://afisha.yandex.ru/kaliningrad"

# ─── Параметры браузера ──────────────────────────────────────────────────────

BROWSER_OPTIONS = {"headless": True}

CONTEXT_OPTIONS = {
    "user_agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "locale": "ru-RU",
    "viewport": {"width": 1280, "height": 900},
}

# Паттерн URL мероприятия: /kaliningrad/КАТЕГОРИЯ/СЛАГ
EVENT_URL_RE = re.compile(r"/kaliningrad/[a-z]+/[a-z0-9][a-z0-9\-]{2,}$")

# Слаги которые выглядят как события, но на деле являются листинговыми страницами
_SLUG_BLACKLIST = re.compile(
    r"/(all|vse|events|selections|search|cinema|afisha|"
    r"top|new|soon|today|weekend|popular|recommended)$"
)

# Если в title есть эти паттерны — это листинг, а не конкретное мероприятие
_LISTING_TITLE_RE = re.compile(
    r"^(Все|Лучшие|Популярные|Топ|Афиша|Расписание)\s.+(Калининград|калинин)",
    re.IGNORECASE,
)


# ─── Загрузка страницы категории ────────────────────────────────────────────


async def _load_category_page(page: Page, category: str) -> list[str]:
    """Открывает страницу категории и собирает ссылки на мероприятия.

    Выполняет один скролл для подгрузки событий.
    Возвращает список абсолютных URL (дедуплицированных).
    """
    url = f"{BASE_URL}/{category}?source=menu"
    logger.info(f"Loading category [{category}]: {url}")

    try:
        response = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        if response and response.status >= 400:
            logger.warning(f"HTTP {response.status} for [{category}], skipping")
            return []
    except Exception as e:
        logger.warning(f"Failed to load [{category}]: {e}")
        return []

    # Ждём появления карточек событий
    try:
        await page.wait_for_function(
            "document.querySelectorAll('a[href*=\"/kaliningrad/\"]').length > 2",
            timeout=15000,
        )
    except Exception:
        logger.warning(f"[{category}] Timeout waiting for event links")

    # Один скролл — подгружает следующую порцию событий
    await page.evaluate("window.scrollTo(0, document.body.scrollHeight * 0.5)")
    await asyncio.sleep(1.5)
    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    await asyncio.sleep(1.5)

    hrefs: list[str] = await page.eval_on_selector_all(
        "a[href]", "els => els.map(e => e.getAttribute('href'))"
    )

    urls: list[str] = []
    seen: set[str] = set()

    for href in hrefs:
        if not href:
            continue
        clean = href.split("?")[0].split("#")[0].rstrip("/")
        if not EVENT_URL_RE.search(clean):
            continue
        if clean.startswith("/"):
            clean = "https://afisha.yandex.ru" + clean
        if clean not in seen and not _SLUG_BLACKLIST.search(clean):
            seen.add(clean)
            urls.append(clean)

    logger.info(f"[{category}] Found {len(urls)} event URLs")
    return urls


# ─── Парсинг страницы одного события ────────────────────────────────────────


async def _parse_event_page(page: Page, url: str, category: str = "") -> Optional[EventData]:
    """Открывает страницу мероприятия и извлекает данные."""
    try:
        response = await page.goto(url, wait_until="domcontentloaded", timeout=25000)
        if response and response.status >= 400:
            logger.warning(f"HTTP {response.status} for {url}, skipping")
            return None

        await asyncio.sleep(1.5)

        html = await page.content()
        soup = BeautifulSoup(html, "lxml")

        title = (
            _text(soup, "h1")
            or _text(soup, "[class*='Title']")
            or _text(soup, "[class*='title']")
        )
        if not title:
            logger.debug(f"No title at {url}, skipping")
            return None

        # Фильтруем листинговые страницы по заголовку
        if _LISTING_TITLE_RE.match(title):
            logger.info(f"Skipping listing page: '{title}' ({url})")
            return None

        date = (
            _text(soup, "time")
            or _text(soup, "[class*='Date']")
            or _text(soup, "[class*='date']")
            or _text(soup, "[class*='Schedule']")
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

        return EventData(
            title=title,
            date=date or "",
            place=place or "",
            description=description or "",
            ticket_url=url,
            afisha_url=url,
            image_url=image_url or "",
            category=category,
        )

    except Exception as e:
        logger.warning(f"Failed to parse {url}: {e}")
        return None


# ─── Вспомогательные утилиты ────────────────────────────────────────────────


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
    if not event.get("title") or not event.get("afisha_url"):
        return False
    # Листинговые страницы часто не имеют ни места ни нормальной даты
    has_date = bool(event.get("date") and len(event["date"]) > 3)
    has_place = bool(event.get("place"))
    return has_date or has_place


# ─── Главная функция ─────────────────────────────────────────────────────────


async def parse_all_events(max_total: int = 5) -> list[EventData]:
    """Собирает события из категорийных страниц Яндекс Афиши.

    Алгоритм:
    1. Вычисляем сколько событий берём из каждой категории (per_category).
    2. Обходим категории по очереди, из каждой парсим per_category событий.
    3. Если одна категория не даёт нужного числа — берём что есть и идём дальше.
    4. Дедупликация по afisha_url.

    Args:
        max_total: Желаемое суммарное число событий (из settings.parse_count).

    Returns:
        Список EventData, не более max_total штук.
    """
    # Сколько категорий задействовать и сколько событий с каждой
    # При max_total=5  → 5 категорий по 1
    # При max_total=10 → 5 категорий по 2
    # При max_total=3  → 3 категории по 1
    per_category = max(1, max_total // min(max_total, len(CATEGORIES)))
    num_categories = (max_total + per_category - 1) // per_category  # ceil
    active_categories = CATEGORIES[:num_categories]

    logger.info(
        f"parse_all_events: max_total={max_total}, "
        f"categories={num_categories}, per_category={per_category}"
    )

    all_events: list[EventData] = []
    seen_urls: set[str] = set()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(**BROWSER_OPTIONS)
        context = await browser.new_context(**CONTEXT_OPTIONS)
        page = await context.new_page()

        for cat_slug, cat_label in active_categories:
            if len(all_events) >= max_total:
                break

            # Шаг 1: получаем список URL из категории
            event_urls = await _load_category_page(page, cat_slug)
            if not event_urls:
                logger.warning(f"[{cat_slug}] No URLs, skipping category")
                continue

            # Шаг 2: парсим по per_category событий из категории
            # Пробуем до per_category * 3 URL чтобы найти per_category валидных
            cat_parsed = 0
            for url in event_urls[: per_category * 3]:
                if cat_parsed >= per_category:
                    break
                if url in seen_urls:
                    continue

                await asyncio.sleep(1.0)
                event = await _parse_event_page(page, url, category=cat_label)

                if event and _is_valid(event):
                    seen_urls.add(url)
                    all_events.append(event)
                    cat_parsed += 1
                    logger.info(
                        f"[{cat_slug}] #{cat_parsed}: {event['title']} | {event['date']}"
                    )

                if len(all_events) >= max_total:
                    break

            logger.info(f"[{cat_slug}] Done: {cat_parsed}/{per_category}")

        await browser.close()

    logger.info(
        f"Total parsed: {len(all_events)} events from "
        f"{len(set(e['category'] for e in all_events))} categories"
    )
    return all_events


# ─── Обратная совместимость ──────────────────────────────────────────────────


async def parse_events_from_url(
    afisha_url: str, max_events: int = 5
) -> list[EventData]:
    """Парсит события с произвольного URL категории.

    Оставлен для обратной совместимости.
    Для регулярного сбора используйте parse_all_events().
    """
    events: list[EventData] = []

    # Определяем slug категории из URL
    m = re.search(r"/kaliningrad/([a-z]+)", afisha_url)
    cat_label = m.group(1) if m else ""

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(**BROWSER_OPTIONS)
        context = await browser.new_context(**CONTEXT_OPTIONS)
        page = await context.new_page()

        try:
            urls = await _load_category_page(page, cat_label or afisha_url)
            for url in urls[: max_events * 3]:
                await asyncio.sleep(1.0)
                event = await _parse_event_page(page, url, category=cat_label)
                if event and _is_valid(event):
                    events.append(event)
                if len(events) >= max_events:
                    break
        except Exception as e:
            logger.error(f"Parser error: {e}")
        finally:
            await browser.close()

    return events
