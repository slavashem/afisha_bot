from dataclasses import dataclass, field


@dataclass
class BotSettings:
    num_events: int = 3


# Global singleton
settings = BotSettings()
