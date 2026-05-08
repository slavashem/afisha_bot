from aiogram import Router, Bot, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
)

from config import Config
from database.db import get_event_by_id, mark_as_published
from ai.text_generator import generate_telegram_post, generate_instagram_post
from services.image_search import search_event_images, build_image_query
from services.publisher import publish_to_channel, send_instagram_text
from utils.logger import logger

router = Router()


class Flow(StatesGroup):
    selecting_photo = State()
    confirming_post = State()
    waiting_ref_url = State()


# ─── Show event list ────────────────────────────────────────────────────────

async def send_events_list(bot: Bot, admin_id: int, events: list[dict]) -> None:
    if not events:
        await bot.send_message(admin_id, "Новых мероприятий не найдено.")
        return

    text = "📋 <b>Найденные мероприятия:</b>\n\n"
    buttons = []
    for i, ev in enumerate(events):
        idx = i + 1
        title = ev.get("title", "—")
        date = ev.get("date", "")
        place = ev.get("place", "")
        ticket = ev.get("ticket_url", "")
        afisha = ev.get("afisha_url", "")

        text += f"{idx}. <b>{title}</b>\n"
        if date:
            text += f"   📅 {date}\n"
        if place:
            text += f"   📍 {place}\n"
        # Show ticket URL status
        if ticket and ticket != afisha:
            text += f"   🎟 <a href='{ticket}'>Ссылка на билеты</a>\n"
        else:
            text += f"   🎟 Билеты: <a href='{afisha}'>страница афиши</a>\n"
        text += "\n"

        buttons.append([InlineKeyboardButton(
            text=f"#{idx} {title[:35]}",
            callback_data=f"select_event:{ev['id']}"
        )])

    await bot.send_message(
        admin_id, text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        disable_web_page_preview=True,
    )


# ─── Register handlers ──────────────────────────────────────────────────────

def register_publish_handlers(router: Router, bot: Bot, config: Config) -> None:

    @router.callback_query(F.data.startswith("select_event:"))
    async def on_select_event(callback: CallbackQuery, state: FSMContext) -> None:
        if callback.from_user.id != config.admin_id:
            return

        event_id = int(callback.data.split(":")[1])
        event = await get_event_by_id(event_id, config.db_path)
        if not event:
            await callback.answer("Мероприятие не найдено")
            return

        await callback.message.answer(
            f"⏳ Генерирую текст через нейросеть для <b>{event['title']}</b>...",
            parse_mode="HTML",
        )
        await callback.answer()

        # Generate Telegram text
        tg_text, tg_ok = await generate_telegram_post(event, config)
        ig_text, ig_ok = await generate_instagram_post(event, config)

        if not tg_ok:
            await callback.message.answer(
                "⚠️ <b>Нейросеть не ответила</b> — показываю текст-заглушку.\n"
                "Проверь AI_API_KEY, AI_API_URL и AI_MODEL в .env",
                parse_mode="HTML",
            )
        else:
            await callback.message.answer("✅ Текст сгенерирован нейросетью")

        await state.update_data(
            event_id=event_id,
            tg_text=tg_text,
            ig_text=ig_text,
        )

        # Search for images
        await callback.message.answer("🔍 Ищу фото в интернете...")
        query = build_image_query(event)
        image_urls = await search_event_images(query, count=5)

        if not image_urls:
            await callback.message.answer(
                "⚠️ Фото не найдены — буду использовать изображение с Афиши"
            )
            await state.update_data(selected_image=event.get("image_url", ""))
            await _show_post_preview(callback.message, event, tg_text, event.get("image_url"), state)
            return

        await state.update_data(image_urls=image_urls)
        await state.set_state(Flow.selecting_photo)

        await callback.message.answer(
            f"📸 Найдено {len(image_urls)} фото. Нажмите кнопку под нужным:"
        )
        for i, img_url in enumerate(image_urls):
            try:
                await callback.message.answer_photo(
                    photo=img_url,
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                        InlineKeyboardButton(
                            text=f"✅ Выбрать это фото",
                            callback_data=f"pick_photo:{i}"
                        )
                    ]])
                )
            except Exception as e:
                logger.warning(f"Could not send photo {i} ({img_url}): {e}")

    @router.callback_query(F.data.startswith("pick_photo:"), Flow.selecting_photo)
    async def on_pick_photo(callback: CallbackQuery, state: FSMContext) -> None:
        if callback.from_user.id != config.admin_id:
            return

        photo_idx = int(callback.data.split(":")[1])
        data = await state.get_data()
        image_urls: list[str] = data.get("image_urls", [])
        selected_url = image_urls[photo_idx] if photo_idx < len(image_urls) else ""

        await state.update_data(selected_image=selected_url)
        await callback.answer("Фото выбрано ✅")

        event_id = data.get("event_id")
        event = await get_event_by_id(event_id, config.db_path)
        tg_text = data.get("tg_text", "")

        await _show_post_preview(callback.message, event, tg_text, selected_url, state)

    @router.callback_query(F.data == "confirm_post", Flow.confirming_post)
    async def on_confirm(callback: CallbackQuery, state: FSMContext) -> None:
        if callback.from_user.id != config.admin_id:
            return
        data = await state.get_data()
        event_id = data.get("event_id")
        event = await get_event_by_id(event_id, config.db_path)

        ticket_url = event.get("ticket_url", "") if event else ""
        afisha_url = event.get("afisha_url", "") if event else ""

        hint = ""
        if ticket_url and ticket_url != afisha_url:
            hint = f"\n\nСсылка на билеты с сайта: <code>{ticket_url}</code>"
        else:
            hint = f"\n\nСтраница на Афише: <code>{afisha_url}</code>"

        await state.set_state(Flow.waiting_ref_url)
        await callback.message.answer(
            f"🔗 Отправьте реферальную ссылку для кнопки «Купить билет»{hint}",
            parse_mode="HTML",
        )
        await callback.answer()

    @router.callback_query(F.data == "reject_post", Flow.confirming_post)
    async def on_reject(callback: CallbackQuery, state: FSMContext) -> None:
        if callback.from_user.id != config.admin_id:
            return
        await state.clear()
        await callback.message.answer("❌ Пост отклонён.")
        await callback.answer()

    @router.message(Flow.waiting_ref_url)
    async def on_ref_url(message: Message, state: FSMContext) -> None:
        if message.from_user.id != config.admin_id:
            return

        ref_url = (message.text or "").strip()
        if not ref_url.startswith("http"):
            await message.answer("❌ Некорректная ссылка. Попробуйте ещё раз:")
            return

        data = await state.get_data()
        await state.clear()

        event_id = data.get("event_id")
        event = await get_event_by_id(event_id, config.db_path)
        if not event:
            await message.answer("❌ Мероприятие не найдено.")
            return

        tg_text = data.get("tg_text", event.get("telegram_text", ""))
        ig_text = data.get("ig_text", event.get("instagram_text", ""))
        image_url = data.get("selected_image") or event.get("image_url")

        success = await publish_to_channel(
            bot=bot,
            channel_id=config.channel_id,
            event=event,
            telegram_text=tg_text,
            ref_url=ref_url,
            image_url=image_url,
        )

        if success:
            await message.answer(f"✅ Опубликовано: «{event.get('title')}»")
            await send_instagram_text(bot, config.admin_id, ig_text, event.get("title", ""))
        else:
            await message.answer("❌ Ошибка публикации. Проверь логи.")


async def _show_post_preview(
    message,
    event: dict,
    tg_text: str,
    image_url: str | None,
    state: FSMContext,
) -> None:
    await state.set_state(Flow.confirming_post)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Опубликовать", callback_data="confirm_post"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data="reject_post"),
    ]])

    caption = f"<b>Предпросмотр поста:</b>\n\n{tg_text}"

    try:
        if image_url:
            await message.answer_photo(
                photo=image_url,
                caption=caption[:1024],
                reply_markup=keyboard,
                parse_mode="HTML",
            )
        else:
            await message.answer(caption[:4096], reply_markup=keyboard, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Preview send error: {e}")
        await message.answer(caption[:4096], reply_markup=keyboard, parse_mode="HTML")
