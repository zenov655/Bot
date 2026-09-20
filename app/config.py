import os
from dataclasses import dataclass

from dotenv import load_dotenv


load_dotenv()


@dataclass
class BotConfig:
    token: str


@dataclass
class Settings:
    bot: BotConfig
    webhook_url: str  # Публичный URL, куда Telegram шлёт обновления (для вебхука)


def get_settings() -> Settings:
    return Settings(
        bot=BotConfig(
            token=os.environ.get("BOT_TOKEN", ""),
        ),
        webhook_url=os.environ.get("WEBHOOK_URL", ""),
    )


settings = get_settings()
