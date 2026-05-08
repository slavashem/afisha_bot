from dataclasses import dataclass
from dotenv import load_dotenv
import os

load_dotenv()


@dataclass
class Config:
    telegram_bot_token: str
    admin_id: int
    channel_id: int

    ai_api_url: str
    ai_api_key: str
    ai_model: str

    check_interval_minutes: int

    db_path: str = "data/events.db"


def load_config() -> Config:
    return Config(
        telegram_bot_token=_require("TELEGRAM_BOT_TOKEN"),
        admin_id=int(_require("ADMIN_ID")),
        channel_id=int(_require("CHANNEL_ID")),
        ai_api_url=_require("AI_API_URL"),
        ai_api_key=_require("AI_API_KEY"),
        ai_model=_require("AI_MODEL"),
        check_interval_minutes=int(os.getenv("CHECK_INTERVAL_MINUTES", "60")),
    )


def _require(key: str) -> str:
    value = os.getenv(key)
    if not value:
        raise EnvironmentError(f"Missing required environment variable: {key}")
    return value
