from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramNetworkError

from .billing import CryptoPayClient, billing_watcher
from .bot import router, setup
from .config import load_config
from .database import Database
from .ml import SpamModel

logger = logging.getLogger(__name__)


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = load_config()
    database = Database(config.database_path)
    database.init_schema(config.owner_ids, config.default_mode)
    database.prune_events(config.log_retention_days)
    billing_client = CryptoPayClient(config)
    spam_model = SpamModel(
        path=config.ml_model_path,
        threshold=config.ml_spam_threshold,
        enabled=config.ml_enabled,
        min_text_length=config.ml_min_text_length,
    )
    setup(database, config, billing_client, spam_model)

    bot = Bot(
        token=config.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    if billing_client.configured:
        asyncio.create_task(billing_watcher(database, billing_client, config))
    allowed_updates = dispatcher.resolve_used_update_types()
    while True:
        try:
            await dispatcher.start_polling(bot, allowed_updates=allowed_updates)
            return
        except (TelegramNetworkError, TimeoutError, OSError) as exc:
            logger.warning("Telegram polling network error, retrying in 15 seconds: %s", exc)
            await asyncio.sleep(15)


if __name__ == "__main__":
    asyncio.run(main())
