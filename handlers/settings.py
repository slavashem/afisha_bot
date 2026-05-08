from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
)
from config import Config
from services.bot_settings import settings

router = Router()

OPTIONS = [1, 3, 5, 10]


def _settings_kb() -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(
            text=f"{'✅' if settings.num_events == n else '  '} {n}",
            callback_data=f"set_num_events:{n}",
        )
        for n in OPTIONS
    ]
    return InlineKeyboardMarkup(inline_keyboard=[buttons])


def _settings_text() -> str:
    return (
        f"⚙️ <b>Настройки</b>\n\n"
        f"Количество мероприятий при проверке: <b>{settings.num_events}</b>\n\n"
        f"Выбери значение:"
    )


def register_settings_handlers(router: Router, config: Config) -> None:
    @router.message(Command("settings"))
    async def cmd_settings(message: Message) -> None:
        if message.from_user.id != config.admin_id:  # type: ignore
            return
        await message.answer(_settings_text(), reply_markup=_settings_kb(), parse_mode="HTML")

    @router.callback_query(F.data == "open_settings")
    async def cb_open_settings(cb: CallbackQuery) -> None:
        if cb.from_user.id != config.admin_id:  # type: ignore
            return
        await cb.message.answer(_settings_text(), reply_markup=_settings_kb(), parse_mode="HTML")  # type: ignore
        await cb.answer()

    @router.callback_query(F.data.startswith("set_num_events:"))
    async def cb_set_num(cb: CallbackQuery) -> None:
        if cb.from_user.id != config.admin_id:  # type: ignore
            return
        n = int(cb.data.split(":")[1])  # type: ignore
        settings.num_events = n
        await cb.answer(f"Установлено: {n}")
        await cb.message.edit_text(  # type: ignore
            _settings_text(), reply_markup=_settings_kb(), parse_mode="HTML"
        )
