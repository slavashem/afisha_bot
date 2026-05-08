from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from utils.logger import logger

_db_path: str = "data/events.db"


def init_publisher(db_path: str) -> None:
    global _db_path
    _db_path = db_path


async def publish_to_channel(
    bot: Bot,
    channel_id: int,
    event_id: int,
    telegram_text: str,
    ref_url: str,
    image_url: str | None,
    db_path: str | None = None,
) -> bool:
    from database.db import update_event_status

    path = db_path or _db_path

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🎟 Купить билет", url=ref_url)
    ]])

    try:
        if image_url:
            await bot.send_photo(
                chat_id=channel_id,
                photo=image_url,
                caption=telegram_text[:1024],
                reply_markup=kb,
            )
        else:
            await bot.send_message(
                chat_id=channel_id,
                text=telegram_text[:4096],
                reply_markup=kb,
            )
        # Обновляем статус: published (уже должен быть published, но на всякий случай)
        await update_event_status(event_id, "published", path)
        logger.info(f"Published event #{event_id}")
        return True
    except Exception as e:
        logger.error(f"Publish failed #{event_id}: {e}")
        return False


async def send_instagram_text(bot: Bot, admin_id: int, ig_text: str, title: str) -> None:
    try:
        await bot.send_message(
            chat_id=admin_id,
            text=f"📸 <b>Instagram для «{title}»:</b>\n\n{ig_text}",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error(f"Failed to send Instagram text: {e}")
