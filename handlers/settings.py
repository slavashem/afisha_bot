"""Единый синглтон настроек бота.

Все модули импортируют отсюда — гарантирует, что set_parse_count()
и get_settings() работают с одним и тем же объектом в памяти.
"""
from dataclasses import dataclass, field


@dataclass
class BotSettings:
    parse_count: int = 5  # Дефолт — 5 событий


# Единственный экземпляр для всего процесса
_settings = BotSettings()


def get_settings() -> BotSettings:
    return _settings


def set_parse_count(n: int) -> None:
    _settings.parse_count = n
