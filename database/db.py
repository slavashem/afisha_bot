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
    "published": "INTEGER DEFAULT 0",
    "created_at": "TEXT DEFAULT CURRENT_TIMESTAMP",
}


async def _migrate_db(db_path: str) -> None:
    """Добавляет недостающие столбцы в существующую таблицу events."""
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute("PRAGMA table_info(events)")
        existing = {row[1] for row in await cursor.fetchall()}

        missing = [name for name in EXPECTED_COLUMNS if name not in existing]
        if not missing:
            return

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
                published INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.commit()
    # Запускаем миграцию на случай, если таблица уже существовала без части столбцов
    await _migrate_db(db_path)
    logger.info("Database initialized")


async def event_exists(afisha_url: str, db_path: str = DB_PATH) -> bool:
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT id FROM events WHERE afisha_url = ?", (afisha_url,)
        ) as cursor:
            return await cursor.fetchone() is not None


async def save_event(event: dict, db_path: str = DB_PATH) -> int:
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute(
            """
            INSERT INTO events
                (title, date, place, description, ticket_url, afisha_url, image_url,
                 telegram_text, instagram_text, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                datetime.utcnow().isoformat(),
            ),
        )
        await db.commit()
        return cursor.lastrowid


async def get_unpublished_events(db_path: str = DB_PATH) -> list[dict]:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM events WHERE published = 0 ORDER BY created_at ASC"
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]


async def mark_as_published(event_id: int, db_path: str = DB_PATH) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute("UPDATE events SET published = 1 WHERE id = ?", (event_id,))
        await db.commit()


async def get_event_by_id(event_id: int, db_path: str = DB_PATH) -> Optional[dict]:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM events WHERE id = ?", (event_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None
