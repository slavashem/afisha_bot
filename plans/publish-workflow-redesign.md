# План редизайна workflow публикации

## Контекст

Бот парсит Яндекс Афишу → показывает админу → админ публикует.  
**Проблема:** события сохраняются в БД сразу при `/check` (published=0), даже если админ их потом отклонил. Повторно они уже не обрабатываются (event_exists = True). Админ не может редактировать текст перед публикацией.

**Цель:**
1. Сохранять в БД **только опубликованные и игнорируемые** события
2. Кнопка «Игнорировать» — помечает событие как неинтересное, в историю
3. Игнорированные + опубликованные = не обрабатывать повторно
4. Возможность **редактировать текст поста** до публикации

---

## Шаг 1. Миграция БД: `published` → `status`

**Файл:** `database/db.py`

### 1.1 Обновить `EXPECTED_COLUMNS`

```python
EXPECTED_COLUMNS: dict[str, str] = {
    "id": "INTEGER PRIMARY KEY AUTOINCREMENT",
    "title": "TEXT NOT NULL",
    "date": "TEXT",
    "place": "TEXT",
    "description": "TEXT",
    "ticket_url": "TEXT",
    "afisha_url": "TEXT",
    "image_url": "TEXT",
    "telegram_text": "TEXT",
    "instagram_text": "TEXT",
    "status": "TEXT NOT NULL DEFAULT 'published'",
    "created_at": "TEXT DEFAULT CURRENT_TIMESTAMP",
    "published_at": "TEXT",
    "ignored_at": "TEXT",
}
```

- Удаляем `published INTEGER DEFAULT 0`
- Добавляем `status TEXT NOT NULL DEFAULT 'published'`
- Добавляем `published_at TEXT` и `ignored_at TEXT`

### 1.2 Миграция старых данных

В `_migrate_db()` после добавления колонок:

```python
# Миграция published → status
await db.execute(
    "UPDATE events SET status = 'published' WHERE published = 1 AND status IS NULL"
)
await db.execute(
    "UPDATE events SET status = 'ignored' WHERE published = 0 AND status IS NULL"
)
# Установить published_at для старых опубликованных
await db.execute(
    "UPDATE events SET published_at = created_at WHERE status = 'published' AND published_at IS NULL"
)
```

### 1.3 Обновить `init_db()` — CREATE TABLE

Заменить `published INTEGER DEFAULT 0` на `status TEXT NOT NULL DEFAULT 'published'`, добавить `published_at`, `ignored_at`.

---

## Шаг 2. Новые/обновлённые функции в `database/db.py`

### 2.1 `save_event(event, status='published', db_path)`

Добавить параметр `status`:

```python
async def save_event(event: dict, status: str = "published", db_path: str = DB_PATH) -> int:
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute(
            """
            INSERT INTO events
                (title, date, place, description, ticket_url, afisha_url, image_url,
                 telegram_text, instagram_text, status, created_at, published_at, ignored_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.get("title"),
                event.get("date"),
                event.get("place"),
                event.get("description"),
                event.get("ticket_url"),
                event.get("afisha_url") or event.get("ticket_url"),
                event.get("image_url"),
                event.get("telegram_text", ""),
                event.get("instagram_text", ""),
                status,
                now,
                now if status == "published" else None,
                now if status == "ignored" else None,
            ),
        )
        await db.commit()
        return cursor.lastrowid
```

### 2.2 `event_exists(afisha_url, db_path)` — без изменений

Уже работает корректно: проверяет наличие записи по `afisha_url` независимо от статуса. Поскольку мы больше не сохраняем «новые» события, любое совпадение означает «уже обработано».

### 2.3 `is_event_processed(afisha_url, db_path)` — новая функция

```python
async def is_event_processed(afisha_url: str, db_path: str = DB_PATH) -> bool:
    """Проверяет, было ли событие уже опубликовано или проигнорировано."""
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT id FROM events WHERE afisha_url = ? AND status IN ('published', 'ignored')",
            (afisha_url,),
        ) as cursor:
            return await cursor.fetchone() is not None
```

### 2.4 Удалить `get_unpublished_events()` и `mark_as_published()`

Больше не нужны. Вместо них:

### 2.5 `update_event_status(event_id, status, db_path)`

```python
async def update_event_status(event_id: int, status: str, db_path: str = DB_PATH) -> None:
    now = datetime.utcnow().isoformat()
    if status == "published":
        await db.execute(
            "UPDATE events SET status = ?, published_at = ? WHERE id = ?",
            (status, now, event_id),
        )
    elif status == "ignored":
        await db.execute(
            "UPDATE events SET status = ?, ignored_at = ? WHERE id = ?",
            (status, now, event_id),
        )
    await db.commit()
```

### 2.6 `update_event_text(event_id, telegram_text, instagram_text, db_path)`

```python
async def update_event_text(event_id: int, telegram_text: str, instagram_text: str, db_path: str = DB_PATH) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "UPDATE events SET telegram_text = ?, instagram_text = ? WHERE id = ?",
            (telegram_text, instagram_text, event_id),
        )
        await db.commit()
```

---

## Шаг 3. Переделать `run_check()` в `handlers/admin.py`

**Суть:** не сохранять события сразу, только проверять дубликаты.

```python
async def run_check(bot: Bot, config: Config) -> list[dict]:
    """Парсит события и возвращает только новые (не обработанные ранее)."""
    from database.db import is_event_processed

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
```

**Важно:** возвращаем сырые `EventData` словари **без `id` из БД**.

---

## Шаг 4. Обновить `send_events_list()` — работа без id БД

**Файл:** `handlers/publish.py`

Сейчас `send_events_list` использует `ev['id']` для callback. События ещё не в БД — используем **индекс в списке** как временный идентификатор.

```python
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

        text += f"{idx}. <b>{title}</b>\n"
        if date:
            text += f"   📅 {date}\n"
        if place:
            text += f"   📍 {place}\n"
        text += f"   🎟 <a href='{ev.get('afisha_url', '')}'>страница афиши</a>\n\n"

        buttons.append([InlineKeyboardButton(
            text=f"#{idx} {title[:35]}",
            callback_data=f"select_event:{i}"  # индекс, не id
        )])

    await bot.send_message(
        admin_id, text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        disable_web_page_preview=True,
    )
```

---

## Шаг 5. FSM: добавить состояния и кнопки

**Файл:** `handlers/publish.py`

### 5.1 Новое состояние

```python
class Flow(StatesGroup):
    selecting_photo = State()
    confirming_post = State()
    waiting_ref_url = State()
    editing_text = State()  # ← НОВОЕ
```

### 5.2 Хранение событий в FSM

В `register_publish_handlers()` — при старте (после `/check`) сохраняем список событий в состояние:

```python
# В check_and_notify или cmd_check:
await state.update_data(pending_events=events)
```

### 5.3 Обновлённый `on_select_event`

```python
@router.callback_query(F.data.startswith("select_event:"))
async def on_select_event(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.from_user.id != config.admin_id:
        return

    index = int(callback.data.split(":")[1])
    data = await state.get_data()
    pending_events: list[dict] = data.get("pending_events", [])
    
    if index >= len(pending_events):
        await callback.answer("Событие не найдено")
        return

    event = pending_events[index]
    # ... дальше генерация текста как сейчас ...
    # В конце сохраняем event в состояние:
    await state.update_data(current_event=event, current_event_index=index)
```

### 5.4 Кнопки в `_show_post_preview`

```python
keyboard = InlineKeyboardMarkup(inline_keyboard=[
    [
        InlineKeyboardButton(text="✅ Опубликовать", callback_data="confirm_post"),
        InlineKeyboardButton(text="✏️ Редактировать", callback_data="edit_text"),
    ],
    [
        InlineKeyboardButton(text="🚫 Игнорировать", callback_data="ignore_event"),
    ],
])
```

---

## Шаг 6. Обработчик «Игнорировать»

```python
@router.callback_query(F.data == "ignore_event", Flow.confirming_post)
async def on_ignore(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.from_user.id != config.admin_id:
        return

    data = await state.get_data()
    event = data.get("current_event", {})

    # Сохраняем в БД со статусом ignored
    await save_event(
        {**event, "telegram_text": data.get("tg_text", ""),
         "instagram_text": data.get("ig_text", "")},
        status="ignored",
        db_path=config.db_path,
    )

    await state.clear()
    await callback.message.edit_caption(
        caption="🚫 Мероприятие проигнорировано.",
        reply_markup=None,
    )
    await callback.answer("Игнорировано")
```

---

## Шаг 7. Обработчик «Редактировать» + FSM `editing_text`

```python
@router.callback_query(F.data == "edit_text", Flow.confirming_post)
async def on_edit_text(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.from_user.id != config.admin_id:
        return

    data = await state.get_data()
    tg_text = data.get("tg_text", "")
    event = data.get("current_event", {})

    await state.set_state(Flow.editing_text)
    await callback.message.answer(
        f"✏️ <b>Текущий текст:</b>\n\n{tg_text}\n\n"
        f"Отправьте новый текст для поста:",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(Flow.editing_text)
async def on_new_text(message: Message, state: FSMContext) -> None:
    if message.from_user.id != config.admin_id:
        return

    new_text = (message.text or "").strip()
    if not new_text:
        await message.answer("❌ Текст не может быть пустым. Отправьте текст:")
        return

    await state.update_data(tg_text=new_text)
    await state.set_state(Flow.confirming_post)

    data = await state.get_data()
    event = data.get("current_event", {})
    image_url = data.get("selected_image") or event.get("image_url")

    await message.answer("✅ Текст обновлён. Новый предпросмотр:")
    await _show_post_preview(message, event, new_text, image_url, state)
```

---

## Шаг 8. Обновить `on_confirm` — сохранение при публикации

```python
@router.callback_query(F.data == "confirm_post", Flow.confirming_post)
async def on_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.from_user.id != config.admin_id:
        return

    data = await state.get_data()
    event = data.get("current_event", {})

    # Сохраняем в БД со статусом published
    event_id = await save_event(
        {
            **event,
            "telegram_text": data.get("tg_text", ""),
            "instagram_text": data.get("ig_text", ""),
        },
        status="published",
        db_path=config.db_path,
    )
    await state.update_data(event_id=event_id)

    # Дальше существующая логика: запрос реф. ссылки
    # ... (без изменений относительно текущего кода)
```

---

## Шаг 9. Обновить `services/publisher.py`

Заменить `mark_as_published` на `update_event_status`:

```python
from database.db import update_event_status

# В publish_to_channel:
await update_event_status(event["id"], "published", _db_path)
```

---

## Шаг 10. Удалить мёртвый код

- `database/db.py`: удалить `get_unpublished_events()`, `mark_as_published()`
- `services/publisher.py`: убрать импорт `mark_as_published`
- Проверить, что нигде больше не используются удалённые функции

---

## Не меняется

| Компонент | Причина |
|-----------|---------|
| `parsers/yandex_afisha.py` | Без изменений |
| `services/scheduler.py` | Без изменений |
| `services/settings.py` | Без изменений |
| `services/image_search.py` | Без изменений |
| `ai/text_generator.py` | Без изменений |
| `config.py` | Без изменений |

---

## Порядок выполнения

1. `database/db.py` — миграция схемы, новые функции
2. `handlers/admin.py` — `run_check()` без сохранения
3. `handlers/publish.py` — FSM, кнопки, редактирование, игнорирование
4. `services/publisher.py` — замена `mark_as_published`
5. Удаление мёртвого кода + проверка импортов
