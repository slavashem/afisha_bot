from aiogram import Router, Bot, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from config import Config
from database.db import is_event_processed
from parsers.yandex_afisha import parse_all_events
from services.settings import get_settings, set_parse_count
from utils.logger import logger
from .publish import send_events_list

router = Router()


async def run_check(bot: Bot, config: Config) -> list[dict]:
    """Парсит события и возвращает только новые (не обработанные ранее).

    События НЕ сохраняются в БД — только проверяется, не было ли
    это событие уже опубликовано или проигнорировано.
    """
    settings = get_settings()
    count = settings.parse_count

    logger.info(f"Starting afisha check (max {count} events)...")
    raw_events = await parse_all_events(max_total=count)

    new_events: list[dict] = []
    for raw in raw_events:
        afisha_url = raw.get("afisha_url", "")
        if not afisha_url:
            continue

        if await is_event_processed(afisha_url, config.db_path):
            logger.info(f"Already processed: {raw.get('title')}")
            continue

        new_events.append(raw)

    logger.info(f"Check done. New: {len(new_events)}")
    return new_events


async def check_and_notify(bot: Bot, config: Config) -> None:
    try:
        events = await run_check(bot, config)
        if not events:
            await bot.send_message(config.admin_id, "✅ Проверка завершена. Новых мероприятий нет.")
        else:
            await send_events_list(bot, config.admin_id, events, config)
    except Exception as e:
        logger.error(f"Scheduled check failed: {e}")
        await bot.send_message(config.admin_id, f"❌ Ошибка при проверке: {e}")


def _settings_keyboard() -> InlineKeyboardMarkup:
    settings = get_settings()
    counts = [3, 5, 10]
    buttons = []
    for n in counts:
        label = f"{'✅' if settings.parse_count == n else ''} {n} событий".strip()
        buttons.append(InlineKeyboardButton(text=label, callback_data=f"set_count:{n}"))
    return InlineKeyboardMarkup(inline_keyboard=[buttons])


def register_admin_handlers(router: Router, bot: Bot, config: Config) -> None:

    @router.message(Command("start"))
    async def cmd_start(message: Message) -> None:
        if message.from_user.id != config.admin_id:
            return
        await message.answer(
            "👋 <b>Бот афиши Калининграда</b>\n\n"
            "/check — найти новые мероприятия\n"
            "/settings — настройки",
            parse_mode="HTML",
        )

    @router.message(Command("settings"))
    async def cmd_settings(message: Message) -> None:
        if message.from_user.id != config.admin_id:
            return
        s = get_settings()
        await message.answer(
            f"⚙️ <b>Настройки</b>\n\nКоличество событий за проверку: <b>{s.parse_count}</b>",
            parse_mode="HTML",
            reply_markup=_settings_keyboard(),
        )

    @router.callback_query(F.data.startswith("set_count:"))
    async def on_set_count(callback: CallbackQuery) -> None:
        if callback.from_user.id != config.admin_id:
            return
        n = int(callback.data.split(":")[1])
        set_parse_count(n)
        await callback.message.edit_reply_markup(reply_markup=_settings_keyboard())
        await callback.answer(f"Установлено: {n} событий")

    @router.message(Command("check"))
    async def cmd_check(message: Message) -> None:
        if message.from_user.id != config.admin_id:
            return
        s = get_settings()
        await message.answer(f"🔍 Запускаю парсер (ищу {s.parse_count} событий)...")
        try:
            events = await run_check(bot, config)
            if not events:
                await message.answer("✅ Новых мероприятий не найдено.")
            else:
                await send_events_list(bot, config.admin_id, events, config)
        except Exception as e:
            logger.error(f"Check failed: {e}")
            await message.answer(f"❌ Ошибка: {e}")
