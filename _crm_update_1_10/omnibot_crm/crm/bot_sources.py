"""Bot-specific encrypted source settings with legacy environment fallback."""
from sqlalchemy import select

from .bot_settings import decrypt, setting_value
from .models import Bot, BotDatabaseConnection


def food_porn_source(db, config):
    bot = db.scalar(select(Bot).where(Bot.key == 'food_porn'))
    if bot:
        connection = db.scalar(select(BotDatabaseConnection).where(
            BotDatabaseConnection.bot_id == bot.id,
            BotDatabaseConnection.dialect.in_(('neon', 'postgresql')),
            BotDatabaseConnection.last_status == 'available').order_by(
            BotDatabaseConnection.id).limit(1))
        if connection:
            return decrypt(connection.encrypted_url, config.encryption_key)
        url = setting_value(db, bot.id, 'source_database_url', config)
        if url:
            return url
    return config.food_porn_source_database_url
