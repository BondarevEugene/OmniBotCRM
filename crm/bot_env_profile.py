"""Configuration map for the Food_Porn deployment, without reading its .env values."""
from dataclasses import dataclass


@dataclass(frozen=True)
class EnvItem:
    name: str
    setting: str
    group: str
    note: str


FOOD_PORN_ENV = (
    EnvItem('BOT_TOKEN', 'telegram_token', 'secret', 'Токен Telegram; CRM хранит его зашифрованным.'),
    EnvItem('DATABASE_URL', '', 'bootstrap', 'Подключение бота к его собственной базе Neon; пока остается на хосте бота.'),
    EnvItem('OPENAI_API_KEY', 'openai_api_key', 'secret', 'Ключ модели, отдельно для этого бота.'),
    EnvItem('OPENAI_IMAGE_MODEL', 'openai_image_model', 'runtime', 'Модель генерации.'),
    EnvItem('OPENAI_IMAGE_SIZE', 'openai_image_size', 'runtime', 'Размер иллюстрации.'),
    EnvItem('OPENAI_IMAGE_QUALITY', 'openai_image_quality', 'runtime', 'Качество иллюстрации.'),
    EnvItem('APP_ENV', 'app_env', 'runtime', 'Окружение разработки или production.'),
    EnvItem('LOG_LEVEL', 'log_level', 'runtime', 'Уровень логирования.'),
    EnvItem('STORAGE_ROOT', 'storage_root', 'bootstrap', 'Путь к файлам на хосте бота; не переносит сами файлы в CRM.'),
    EnvItem('MAX_UPLOAD_MB', 'max_upload_mb', 'runtime', 'Лимит загружаемых фотографий.'),
    EnvItem('IMAGE_CONCURRENCY', 'image_concurrency', 'runtime', 'Параллельные генерации.'),
    EnvItem('IMAGE_RETRIES', 'image_retries', 'runtime', 'Повторы генерации.'),
    EnvItem('ADMIN_TELEGRAM_IDS', 'admin_telegram_ids', 'runtime', 'Список Telegram ID администраторов.'),
    EnvItem('OPENAI_QUOTA_COOLDOWN_SECONDS', 'openai_quota_cooldown_seconds', 'runtime', 'Пауза после ограничения API.'),
    EnvItem('ENABLE_IMAGE_CACHE', 'enable_image_cache', 'runtime', 'Кеш иллюстраций.'),
    EnvItem('SPOONACULAR_API_KEY', 'spoonacular_api_key', 'secret', 'Ключ кулинарного API.'),
    EnvItem('USE_MOCK_IMAGES', 'use_mock_images', 'runtime', 'Только для тестовой среды.'),
    EnvItem('REPLICATE_API_TOKEN', 'replicate_api_token', 'secret', 'Ключ сервиса изображений.'),
)


def food_porn_coverage(rows):
    configured = {row.key for row in rows if row.is_configured}
    return [{'name': item.name, 'setting': item.setting, 'group': item.group,
             'note': item.note, 'configured': item.setting in configured if item.setting else False}
            for item in FOOD_PORN_ENV]
