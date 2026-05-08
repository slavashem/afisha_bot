# Анализ проекта: Telegram-бот «Афиша Калининграда»

## 1. Общая структура

```
afisha_bot_new/
├── .env                          # Переменные окружения (токены, ключи)
├── .gitignore
├── requirements.txt              # Зависимости Python
├── bot.py                        # Точка входа
├── config.py                     # Конфигурация (dataclass + dotenv)
├── ai/
│   ├── __init__.py               # Пустой
│   └── text_generator.py         # Генерация текстов через AI API
├── data/
│   └── .gitkeep                  # Директория для БД
├── database/
│   ├── __init__.py               # Пустой
│   └── db.py                     # SQLite CRUD (aiosqlite)
├── handlers/
│   ├── __init__.py               # Пустой
│   ├── admin.py                  # /start, /check, /settings
│   ├── publish.py                # Флоу публикации (FSM)
│   └── settings.py               # Дублирующий /settings (НЕ ИСПОЛЬЗУЕТСЯ в bot.py)
├── parsers/
│   ├── __init__.py               # Пустой
│   └── yandex_afisha.py          # Парсер Яндекс.Афиши (Playwright + BS4)
├── prompts/
│   ├── instagram_prompt.txt      # Шаблон промпта для Instagram
│   └── telegram_prompt.txt       # Шаблон промпта для Telegram
├── services/
│   ├── __init__.py               # Пустой
│   ├── bot_settings.py           # Singleton настроек (дубль services/settings.py)
│   ├── image_search.py           # Поиск изображений (Bing Images)
│   ├── publisher.py              # Публикация в канал
│   ├── scheduler.py              # APScheduler
│   └── settings.py               # Singleton настроек (дубль bot_settings.py)
└── utils/
    ├── __init__.py               # Пустой
    └── logger.py                 # Настройка логирования
```

## 2. Технологический стек

| Компонент | Технология | Версия |
|-----------|-----------|--------|
| **Язык** | Python | 3.10+ (aiohttp, asyncio) |
| **Фреймворк** | aiogram | 3.13.1 |
| **Хранилище** | SQLite (aiosqlite) | 0.20.0 |
| **Парсинг** | Playwright + BeautifulSoup4 + lxml | 1.48.0 / 4.12.3 / 5.3.0 |
| **AI** | OpenAI-compatible API (VseGPT) | aiohttp |
| **Планировщик** | APScheduler | 3.10.4 |
| **Конфигурация** | python-dotenv | 1.0.1 |
| **Менеджер пакетов** | pip (requirements.txt) | — |

**Вспомогательные утилиты:** `dataclasses` (stdlib), `pathlib` (stdlib), `asyncio` (stdlib).

## 3. Ключевые файлы и их назначение

| Файл | Назначение | Строк |
|------|-----------|-------|
| [`bot.py`](bot.py) | Точка входа: инициализация бота, FSM, роутеров, планировщика | 47 |
| [`config.py`](config.py) | Dataclass `Config` + загрузка из `.env` через `dotenv` | 42 |
| [`database/db.py`](database/db.py) | CRUD для SQLite: `init_db`, `event_exists`, `save_event`, `get_event_by_id`, `get_unpublished_events`, `mark_as_published`, `skip_event`, `update_event_texts` | 90 |
| [`parsers/yandex_afisha.py`](parsers/yandex_afisha.py) | Парсинг Яндекс.Афиши: сбор URL, скролл, извлечение title/date/place/description/image | 204 |
| [`ai/text_generator.py`](ai/text_generator.py) | Генерация текстов через AI API с fallback-шаблонами | 88 |
| [`handlers/admin.py`](handlers/admin.py) | Команды `/start`, `/check`, `/settings` и обработка `set_count` | 111 |
| [`handlers/publish.py`](handlers/publish.py) | FSM-флоу публикации: выбор фото, предпросмотр, подтверждение, реф. ссылка | 240 |
| [`services/publisher.py`](services/publisher.py) | Отправка поста в Telegram-канал + кнопка «Купить билет» | 58 |
| [`services/image_search.py`](services/image_search.py) | Поиск изображений через Bing Images | 88 |
| [`services/scheduler.py`](services/scheduler.py) | Настройка APScheduler | 18 |
| [`services/settings.py`](services/settings.py) | In-memory singleton настроек (parse_count) | 19 |
| [`services/bot_settings.py`](services/bot_settings.py) | Дубликат in-memory singleton настроек (num_events) | 11 |
| [`utils/logger.py`](utils/logger.py) | Стандартный logging в stdout | 23 |

## 4. Риски и точки улучшения

### 🔴 Критические

1. **Дублирование модулей настроек**
   - [`services/settings.py`](services/settings.py) и [`services/bot_settings.py`](services/bot_settings.py) — два почти идентичных датакласса `BotSettings` с разными именами полей (`parse_count` vs `num_events`).
   - [`handlers/settings.py`](handlers/settings.py) использует `services/bot_settings.py`, но **не подключен** в [`bot.py`](bot.py) (не зарегистрирован в `dp.include_router`). При этом [`handlers/admin.py`](handlers/admin.py:80) содержит свой `/settings` через `services/settings.py`.
   - Итог: мёртвый код (`handlers/settings.py` + `services/bot_settings.py`), путаница.

2. **Хранение секретов в репозитории**
   - Файл [`.env`](.env) содержит **реальные** токены (`TELEGRAM_BOT_TOKEN`, `ADMIN_ID`, `AI_API_KEY`).
   - Несмотря на запись `.env` в [`.gitignore`](.gitignore:1), файл присутствует в репозитории и отображается в VSCode. Риск утечки при `git push`.

3. **Отсутствие обработки ошибок в ключевых точках**
   - [`publish.py:185`](handlers/publish.py:185) — если `publish_to_channel` вернёт `False`, пользователь увидит ошибку, но **не может повторить публикацию без перезапуска флоу**.
   - [`publisher.py:29`](services/publisher.py:29) — обрезка caption до 1024 символов может потерять важную информацию. Telegram поддерживает 1024 для фото, но лучше проверять динамически.
   - В парсере нет ретраев при Network-ошибках.

### 🟡 Значительные

4. **Ограниченное тестирование**
   - **Нет тестов** (unit, integration, e2e) — полное отсутствие файлов `test_*.py`.
   - Нет type hints в некоторых функциях (например, [`publish.py:_show_post_preview`](handlers/publish.py:204): параметр `message` без аннотации).

5. **Отсутствие CI/CD и линтеров**
   - Нет конфигов: `.github/workflows/`, `.eslintrc` (не применимо), `ruff.toml`, `.pre-commit-config.yaml`.
   - Нет форматтера (black/ruff), нет статического анализатора (mypy/pyright).

6. **In-memory настройки без персистентности**
   - [`services/settings.py`](services/settings.py) и [`services/bot_settings.py`](services/bot_settings.py) хранят настройки в памяти. При перезапуске бота значение `parse_count` / `num_events` сбрасывается на 3.

7. **Отсутствие graceful shutdown**
   - В [`bot.py`](bot.py:41) — `scheduler.shutdown()` корректно вызывается, но не обрабатываются сигналы SIGTERM/SIGINT для корректного завершения.

8. **Смешение стилей в админ-проверках**
   - В [`handlers/admin.py`](handlers/admin.py) и [`handlers/publish.py`](handlers/publish.py) админ-проверка `if message.from_user.id != config.admin_id: return` повторяется в каждом хендлере — нарушение DRY.

9. **Магические числа и строки**
   - [`parsers/yandex_afisha.py:18`](parsers/yandex_afisha.py:18) — `steps=6`, `sleep(1.2)` — хардкод.
   - [`parsers/yandex_afisha.py:42`](parsers/yandex_afisha.py:42) — список категорий событий хардкодом в regex.
   - [`parsers/yandex_afisha.py:188`](parsers/yandex_afisha.py:188) — `max_events * 3` — магическое умножение.

### 🟢 Некритические / Рекомендации

10. **Логирование только в stdout**
    - Нет ротации логов, нет записи в файл (хотя `.gitignore` игнорирует `*.log`).

11. **Парсер и поиск изображений используют Playwright**
    - Два экземпляра браузера (парсер + image search). Можно переиспользовать один контекст.
    - Playwright — тяжёлая зависимость (скачивание браузеров). Нет проверки наличия браузеров при старте.

12. **Отсутствие Docker-контейнеризации**
    - Нет `Dockerfile` и `docker-compose.yml`. Затруднён деплой.

13. **Неработающий import в publish.py**
    - [`handlers/publish.py:10`](handlers/publish.py:10) — `InputMediaPhoto` импортирован, но **нигде не используется**.

14. **Дублирование команд `/settings`**
    - Команда `/settings` обрабатывается и в [`admin.py:77`](handlers/admin.py:77), и в [`settings.py:36`](handlers/settings.py:36) (который не подключён). Если подключить `settings.py` — будет конфликт.

15. **Размер файлов**
    - [`handlers/publish.py`](handlers/publish.py) — 240 строк (близко к порогу).
    - [`parsers/yandex_afisha.py`](parsers/yandex_afisha.py) — 204 строки.

## 5. Следующие шаги (рекомендации)

1. **Устранить дублирование настроек**
   - Объединить `services/settings.py` и `services/bot_settings.py` в один модуль. Удалить `handlers/settings.py` (или подключить его правильно). Привести к единому имени поля.

2. **Добавить персистентность настроек**
   - Вынести `parse_count` в таблицу SQLite `settings`, чтобы настройки сохранялись между перезапусками.

3. **Внедрить статический анализ и форматирование**
   - Добавить `ruff.toml` (линтер + форматтер). Настроить `mypy` или `pyright` для type checking. Добавить `pre-commit`.

4. **Покрыть проект тестами**
   - Написать unit-тесты для `database/db.py`, `services/publisher.py`, `parsers/yandex_afisha.py` (с моками Playwright). Написать интеграционный тест для `bot.py`.

5. **CI/CD + Docker**
   - Создать `Dockerfile` и `docker-compose.yml` (бот + SQLite volume). Добавить GitHub Actions: lint → test → build.
   - Защитить `.env` — выдать пример `.env.example` и добавить реальный `.env` в `.gitignore` (фактически он там уже есть, но нужно убедиться, что файл не попадёт в коммит).
