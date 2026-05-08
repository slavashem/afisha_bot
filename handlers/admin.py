from aiogram import Router, Bot, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from config import Config
from database.db import event_exists, save_event
from parsers.yandex_afisha import parse_all_events
from services.settings import get_settings, set_parse_count
from utils.logger import logger
from .publish import send_events_list

router = Router()


async def run_check(bot: Bot, config: Config) -> list[dict]:
    settings = get_settings()
    count = settings.parse_count

    logger.info(f"Starting afisha check (max {count} events)...")
    raw_events = await parse_all_events(max_total=count)

    saved: list[dict] = []
    for raw in raw_events:
        ticket_url = raw.get("ticket_url", "")
        if not ticket_url:
            continue
        if await event_exists(ticket_url, config.db_path):
            logger.info(f"Already exists: {raw.get('title')}")
            continue

        event_id = await save_event(
            {**raw, "telegram_text": "", "instagram_text": ""},
            config.db_path,
        )
        saved.append({**raw, "id": event_id})

    logger.info(f"Check done. New: {len(saved)}")
    return saved


async def check_and_notify(bot: Bot, config: Config) -> None:
    try:
        events = await run_check(bot, config)
        if not events:
            await bot.send_message(config.admin_id, "✅ Проверка завершена. Новых мероприятий нет.")
        else:
            await send_events_list(bot, config.admin_id, events)
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
                await send_events_list(bot, config.admin_id, events)
        except Exception as e:
            logger.error(f"Check failed: {e}")
            await message.answer(f"❌ Ошибка: {e}")
