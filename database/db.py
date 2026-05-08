import aiosqlite
from datetime import datetime
from typing import Optional
from utils.logger import logger

DB_PATH = "data/events.db"

# Полная схема таблицы events (эталон для миграций)
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


async def _migrate_db(db_path: str) -> None:
    """Добавляет недостающие столбцы в существующую таблицу events."""
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute("PRAGMA table_info(events)")
        existing = {row[1] for row in await cursor.fetchall()}

        missing = [name for name in EXPECTED_COLUMNS if name not in existing]
        if missing:
            logger.warning(f"Missing columns detected: {missing}. Running migration...")
            for col_name in missing:
                col_def = EXPECTED_COLUMNS[col_name]
                try:
                    await db.execute(
                        f"ALTER TABLE events ADD COLUMN {col_name} {col_def}"
                    )
                    logger.info(f"Added missing column: {col_name} {col_def}")
                except Exception as e:
                    logger.error(f"Failed to add column {col_name}: {e}")

        # Миграция: published → status
        if "published" in existing:
            logger.info("Migrating published column → status...")
            # Устанавливаем status для старых записей
            await db.execute(
                "UPDATE events SET status = 'published' WHERE published = 1 AND status IS NULL"
            )
            await db.execute(
                "UPDATE events SET status = 'ignored' WHERE published = 0 AND status IS NULL"
            )
            # Заполняем published_at для старых опубликованных
            await db.execute(
                "UPDATE events SET published_at = created_at WHERE status = 'published' AND published_at IS NULL"
            )

        await db.commit()


async def init_db(db_path: str = DB_PATH) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                date TEXT,
                place TEXT,
                description TEXT,
                ticket_url TEXT,
                afisha_url TEXT,
                image_url TEXT,
                telegram_text TEXT,
                instagram_text TEXT,
                status TEXT NOT NULL DEFAULT 'published',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                published_at TEXT,
                ignored_at TEXT
            )
        """)
        await db.commit()
    # Запускаем миграцию на случай, если таблица уже существовала без части столбцов
    await _migrate_db(db_path)
    logger.info("Database initialized")


async def is_event_processed(afisha_url: str, db_path: str = DB_PATH) -> bool:
    """Проверяет, было ли событие уже опубликовано или проигнорировано."""
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT id FROM events WHERE afisha_url = ? AND status IN ('published', 'ignored')",
            (afisha_url,),
        ) as cursor:
            return await cursor.fetchone() is not None


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


async def update_event_status(event_id: int, status: str, db_path: str = DB_PATH) -> None:
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(db_path) as db:
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
        else:
            await db.execute(
                "UPDATE events SET status = ? WHERE id = ?",
                (status, event_id),
            )
        await db.commit()


async def update_event_text(
    event_id: int, telegram_text: str, instagram_text: str, db_path: str = DB_PATH
) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "UPDATE events SET telegram_text = ?, instagram_text = ? WHERE id = ?",
            (telegram_text, instagram_text, event_id),
        )
        await db.commit()


async def get_event_by_id(event_id: int, db_path: str = DB_PATH) -> Optional[dict]:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM events WHERE id = ?", (event_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None
