from pathlib import Path
import aiohttp
from config import Config
from utils.logger import logger

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


def _load_prompt(filename: str) -> str:
    path = PROMPTS_DIR / filename
    return path.read_text(encoding="utf-8")


def _fill_prompt(template: str, event: dict) -> str:
    """Подставляет данные мероприятия в шаблон промпта."""
    try:
        return template.format(
            title=event.get("title", ""),
            date=event.get("date", ""),
            place=event.get("place", ""),
            description=event.get("description", ""),
            ticket_url=event.get("ticket_url", ""),
        )
    except KeyError as e:
        logger.error(f"Ошибка в шаблоне промпта: не хватает плейсхолдера {e}")
        raise


async def _call_ai(prompt: str, config: Config) -> str:
    """Отправляет запрос к OpenAI-совместимому API и возвращает текст ответа."""
    headers = {
        "Authorization": f"Bearer {config.ai_api_key}",
        "Content-Type": "application/json",
    }

    system_instruction = (
        "Ты — копирайтер афиши Калининграда. "
        "Отвечай строго по указанному формату. "
        "Не добавляй лишних символов, Markdown-разметки, тегов или пояснений. "
        "Только готовый текст по шаблону."
    )

    payload = {
        "model": config.ai_model,
        "max_tokens": 1000,
        "messages": [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": prompt},
        ],
    }

    logger.info(f"AI API: {config.ai_api_url} | model: {config.ai_model}")

    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{config.ai_api_url}/chat/completions",
            json=payload,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=60),
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                raise RuntimeError(f"AI API error {resp.status}: {body[:300]}")
            data = await resp.json()
            return data["choices"][0]["message"]["content"].strip()


async def generate_telegram_post(event: dict, config: Config) -> tuple[str, bool]:
    """Генерирует Telegram-пост через AI. Возвращает (текст, флаг успеха)."""
    try:
        template = _load_prompt("telegram_prompt.txt")
        prompt = _fill_prompt(template, event)
        result = await _call_ai(prompt, config)
        logger.info(f"Telegram post generated for: {event.get('title')}")
        return result, True
    except Exception as e:
        logger.error(f"AI call failed for Telegram: {e}")
        return _fallback_telegram(event), False


async def generate_instagram_post(event: dict, config: Config) -> tuple[str, bool]:
    """Генерирует Instagram-пост через AI. Возвращает (текст, флаг успеха)."""
    try:
        template = _load_prompt("instagram_prompt.txt")
        prompt = _fill_prompt(template, event)
        result = await _call_ai(prompt, config)
        logger.info(f"Instagram post generated for: {event.get('title')}")
        return result, True
    except Exception as e:
        logger.error(f"AI call failed for Instagram: {e}")
        return _fallback_instagram(event), False


def _fallback_telegram(event: dict) -> str:
    return (
        f"{event.get('title', '')}\n"
        f"{event.get('place', '')} {event.get('date', '')}\n\n"
        f"{event.get('description', '')}"
    )


def _fallback_instagram(event: dict) -> str:
    return (
        f"В Калининграде планируется мероприятие {event.get('title', '')}.\n"
        f"Вся подробная информация доступна в нашем Telegram-канале. "
        f"Ссылки для перехода — в шапке профиля."
    )
