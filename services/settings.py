from dataclasses import dataclass, field


@dataclass
class BotSettings:
    parse_count: int = 3


# Global in-memory settings instance
_settings = BotSettings()


def get_settings() -> BotSettings:
    return _settings


def set_parse_count(n: int) -> None:
    _settings.parse_count = n
