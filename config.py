import os
from dataclasses import dataclass

from dotenv import load_dotenv


load_dotenv()


@dataclass(frozen=True)
class Config:
    bot_token: str
    admin_ids: list[int]
    database_url: str
    bot_name: str
    bot_username: str
    channel_id: str | None


def load_config() -> Config:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("BOT_TOKEN .env faylida ko'rsatilmagan")

    raw_admins = os.getenv("ADMIN_IDS", "").replace(" ", "")
    admin_ids = [int(item) for item in raw_admins.split(",") if item]

    return Config(
        bot_token=token,
        admin_ids=admin_ids,
        database_url=os.getenv("DATABASE_URL", "sqlite+aiosqlite:///taxi_bot.db"),
        bot_name=os.getenv("BOT_NAME", "SafarX"),
        bot_username=os.getenv("BOT_USERNAME", "Safarx_bot").lstrip("@"),
        channel_id=os.getenv("CHANNEL_ID") or None,
    )
