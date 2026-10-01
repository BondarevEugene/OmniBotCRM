"""Opt-in, generic configuration outlines; no credentials or prices are populated.

Food_Porn upstream currently runs menu and wallpaper products inside one
Telegram bot. These outlines may be applied to *any* CRM bot independently.
"""
from dataclasses import dataclass
from sqlalchemy import func, select

from .models import BotModule, BotSetting


@dataclass(frozen=True)
class Parameter:
    key: str
    label: str
    kind: str
    description: str


@dataclass(frozen=True)
class Blueprint:
    title: str
    description: str
    module_key: str | None
    parameters: tuple[Parameter, ...]


BLUEPRINTS = {
    'food_porn_runtime': Blueprint('Food Porn · параметры config.py',
        'Названия ключей совпадают с Settings в приложенном config.py; значения заполните и опубликуйте самостоятельно.', None, (
        Parameter('price_electronic_uah', 'Электронный продукт · UAH', 'integer', 'Тариф из PRICE_ELECTRONIC_UAH.'),
        Parameter('price_print_uah', 'Печатный продукт · UAH', 'integer', 'Тариф из PRICE_PRINT_UAH.'),
        Parameter('price_electronic_usd', 'Электронный продукт · USD', 'integer', 'Тариф из PRICE_ELECTRONIC_USD.'),
        Parameter('price_print_usd', 'Печатный продукт · USD', 'integer', 'Тариф из PRICE_PRINT_USD.'),
        Parameter('currency', 'Основная валюта меню', 'text', 'ISO код: UAH, USD или EUR.'),
        Parameter('openai_image_model', 'Модель меню', 'text', 'Из OPENAI_IMAGE_MODEL.'),
        Parameter('openai_image_size', 'Размер иллюстрации', 'text', 'Из OPENAI_IMAGE_SIZE.'),
        Parameter('openai_image_quality', 'Качество иллюстрации', 'text', 'Из OPENAI_IMAGE_QUALITY.'),
        Parameter('wallpaper_image_quality', 'Качество обоев', 'text', 'Из WALLPAPER_IMAGE_QUALITY.'),
        Parameter('image_concurrency', 'Параллельные генерации', 'integer', 'Из IMAGE_CONCURRENCY.'),
        Parameter('image_retries', 'Повторы изображений', 'integer', 'Из IMAGE_RETRIES.'),
        Parameter('openai_quota_cooldown_seconds', 'Пауза квоты', 'integer', 'Из OPENAI_QUOTA_COOLDOWN_SECONDS.'),
        Parameter('enable_image_cache', 'Кеш изображений', 'boolean', 'Из ENABLE_IMAGE_CACHE.'),
        Parameter('use_mock_images', 'Тестовые изображения', 'boolean', 'Из USE_MOCK_IMAGES.'),
        Parameter('miniapp_url', 'Публичная ссылка Mini App', 'url', 'Из MINIAPP_URL; укажите HTTPS.'),
        Parameter('printshop_name', 'Типография', 'text', 'Из PRINTSHOP_NAME.'),
        Parameter('printshop_email', 'Email типографии', 'email', 'Из PRINTSHOP_EMAIL.'),
        Parameter('print_poster_width_mm', 'Ширина постера, мм', 'integer', 'Из PRINT_POSTER_WIDTH_MM.'),
        Parameter('print_poster_height_mm', 'Высота постера, мм', 'integer', 'Из PRINT_POSTER_HEIGHT_MM.'),
        Parameter('print_dpi', 'Разрешение печати', 'integer', 'Из PRINT_DPI.'),
        Parameter('smtp_host', 'SMTP сервер', 'text', 'Из SMTP_HOST.'),
        Parameter('smtp_port', 'SMTP порт', 'integer', 'Из SMTP_PORT.'),
        Parameter('smtp_user', 'SMTP пользователь', 'text', 'Из SMTP_USER.'),
        Parameter('smtp_password', 'SMTP пароль', 'secret', 'Из SMTP_PASSWORD; шифруется.'),
        Parameter('smtp_use_tls', 'SMTP TLS', 'boolean', 'Из SMTP_USE_TLS.'),
        Parameter('printshop_email_template', 'Письмо в типографию', 'multiline', 'Из PRINTSHOP_EMAIL_TEMPLATE.'),
        Parameter('default_vip_users', 'VIP пользователи', 'list', 'Список username из DEFAULT_VIP_USERS; использовать только для не финансовых льгот.'),
    )),
    'telegram_growth': Blueprint('Telegram · диалоги и рост',
        'Триггеры, квалификация, контент, оператор и аналитика: поля заполняются вручную.', None, (
        Parameter('welcome_message', 'Приветствие', 'multiline', 'Первый контакт по /start; требуется чтение настройкой ботом.'),
        Parameter('fallback_message', 'Ответ вне сценария', 'multiline', 'Ответ при неизвестном вводе; не отправлять персональные данные.'),
        Parameter('support_queue', 'Команда операторов', 'text', 'Имя команды для передачи диалога менеджеру.'),
        Parameter('support_hours', 'График операторов', 'json', 'Часы и часовой пояс работы команды.'),
        Parameter('lead_fields', 'Поля квалификации', 'list', 'Названия полей для сохранения в карточку клиента.'),
        Parameter('audience_segments', 'Сегменты аудитории', 'json', 'Условия для сегментации по тэгам и событиям.'),
        Parameter('media_policy', 'Отправка медиа', 'json', 'Разрешённые типы и ограничения файла.'),
        Parameter('daily_message_limit', 'Лимит сообщений за сутки', 'integer', 'Лимит отправки на клиента в коде бота.'),
        Parameter('quiet_hours', 'Тихие часы', 'json', 'Интервалы, когда бот не должен отправлять маркетинговые сообщения.'),
        Parameter('default_language', 'Язык по умолчанию', 'text', 'Код языка ответов.'),
        Parameter('handoff_enabled', 'Передача оператору', 'boolean', 'Для выполнения нужен обработчик в исходном боте.'),
        Parameter('dialogue_retention_days', 'Хранение истории, дни', 'integer', 'Политика хранения диалогов после внедрения их записи.'),
        Parameter('ab_variant_rules', 'Правила A/B эксперимента', 'json', 'Доли вариантов и назначения; потребует кода сценария.'),
        Parameter('collect_consent_text', 'Текст согласия', 'multiline', 'Явное согласие для будущих рассылок.'),
    )),
    'telegram_core': Blueprint('Telegram · общие параметры',
        'Один токен Telegram может обслуживать несколько продуктов. Секреты заполняются вручную.', None, (
        Parameter('ingest_secret', 'Подпись событий CRM', 'secret', 'HMAC ключ для событий и чтения конфигурации; минимум 24 символа.'),
        Parameter('telegram_token', 'Токен Telegram', 'secret', 'Токен для разрешённых рассылок CRM; не создавать второй токен для продукта внутри одного бота.'),
        Parameter('source_database_url', 'Адрес исходной базы', 'secret', 'Секрет CRM для импорта данных Food Porn; URL с правом чтения. Не выдаётся боту через API.'),
        Parameter('openai_api_key', 'Ключ OpenAI', 'secret', 'Общий ключ внешнего API; бот должен отдельно читать конфигурацию CRM.'),
        Parameter('spoonacular_api_key', 'Ключ Spoonacular', 'secret', 'Поиск кулинарных рецептов/иллюстраций.'),
        Parameter('admin_telegram_ids', 'Telegram ID администраторов', 'list', 'Массив числовых Telegram ID; не использовать публичные usernames как право доступа.'),
        Parameter('log_level', 'Уровень логов', 'text', 'DEBUG/INFO/WARNING/ERROR после валидации в коде бота.'),
    )),
    'menu': Blueprint('Гастрономическое меню',
        'Меню, рецепты, иллюстрации, печатный PDF, типография и коммерческий цикл.', 'menu', (
        Parameter('menu_price', 'Цена меню', 'number', 'Введите реальный тариф в UAH; значение в GitHub не подтверждает действующую цену.'),
        Parameter('menu_currency', 'Валюта меню', 'text', 'Трёхбуквенный код валюты; в исходниках используется общий currency.'),
        Parameter('menu_image_model', 'Модель изображений меню', 'text', 'Исходный OPENAI_IMAGE_MODEL; понадобится чтение из CRM в коде бота.'),
        Parameter('menu_image_size', 'Размер изображений меню', 'text', 'Исходный OPENAI_IMAGE_SIZE.'),
        Parameter('menu_image_quality', 'Качество изображений меню', 'text', 'Исходный OPENAI_IMAGE_QUALITY.'),
        Parameter('menu_image_concurrency', 'Параллельные задачи', 'integer', 'Исходный IMAGE_CONCURRENCY; изменять только после перезапуска воркеров.'),
        Parameter('menu_image_retries', 'Повторы генерации', 'integer', 'Исходный IMAGE_RETRIES.'),
        Parameter('menu_quota_cooldown_seconds', 'Пауза после квоты', 'integer', 'Исходный OPENAI_QUOTA_COOLDOWN_SECONDS.'),
        Parameter('menu_image_cache_enabled', 'Кеш иллюстраций', 'boolean', 'Исходный ENABLE_IMAGE_CACHE.'),
        Parameter('menu_mock_images_enabled', 'Тестовые изображения', 'boolean', 'Исходный USE_MOCK_IMAGES; включать только в тесте.'),
        Parameter('menu_max_upload_mb', 'Максимум загрузки, MB', 'integer', 'Исходный MAX_UPLOAD_MB.'),
        Parameter('menu_vip_telegram_ids', 'VIP Telegram ID', 'list', 'Вынести ID из кода; VIP не равен оплаченной транзакции.'),
        Parameter('menu_printshop_name', 'Название типографии', 'text', 'Исходный PRINTSHOP_NAME.'),
        Parameter('menu_printshop_email', 'Email типографии', 'email', 'Исходный PRINTSHOP_EMAIL; проверьте рабочий адрес.'),
        Parameter('menu_smtp_host', 'SMTP хост', 'text', 'Отсутствует в Settings текущего commit, но используется PrintshopService.'),
        Parameter('menu_smtp_port', 'SMTP порт', 'integer', 'Добавить в код бота вместе с SMTP хостом.'),
        Parameter('menu_smtp_user', 'SMTP пользователь', 'text', 'Отправитель заказа на печать.'),
        Parameter('menu_smtp_password', 'SMTP пароль', 'secret', 'Отдельный секрет; не передавать в браузер или события.'),
        Parameter('menu_smtp_use_tls', 'SMTP TLS', 'boolean', 'Режим TLS нуждается в проверке с почтовым провайдером.'),
        Parameter('menu_print_dpi', 'DPI для печати', 'integer', 'Сейчас 300 жёстко задано в BookletRenderer.'),
        Parameter('menu_bleed_mm', 'Вылеты, мм', 'number', 'Сейчас 3 мм жёстко задано в BookletRenderer.'),
    )),
    'wallpaper': Blueprint('Обои-манифестации',
        'Фото и цели пользователя, AI изображение, превью, VIP и Premium.', 'wallpaper', (
        Parameter('wallpaper_price', 'Цена Premium', 'number', 'Тариф отсутствует в действующей модели заказа; заполнить после проверки продукта.'),
        Parameter('wallpaper_currency', 'Валюта Premium', 'text', 'Трёхбуквенный код; перед оплатой нужен реальный заказ.'),
        Parameter('wallpaper_image_model', 'Модель AI', 'text', 'Сейчас используется OPENAI_IMAGE_MODEL; нужен отдельный override в коде.'),
        Parameter('wallpaper_image_size', 'Размер результата', 'text', 'Сейчас используется OPENAI_IMAGE_SIZE; желаемый формат проверить на модели.'),
        Parameter('wallpaper_max_photos', 'Максимум фотографий', 'integer', 'Сейчас подготовка учитывает до пяти фото; требуется валидация загрузки.'),
        Parameter('wallpaper_max_goals', 'Максимум целей', 'integer', 'В prompt сейчас берутся первые пять целей.'),
        Parameter('wallpaper_style_prompt', 'Художественное направление', 'multiline', 'Вынести Old Money prompt из обработчика; редактор без вставки чужих секретов.'),
        Parameter('wallpaper_vip_telegram_ids', 'VIP Telegram ID', 'list', 'Отдельный список ID; в GitHub право VIP сейчас определяется по username.'),
        Parameter('wallpaper_preview_watermark', 'Водяной знак превью', 'boolean', 'Изменение потребует логики отдельного превью в боте.'),
        Parameter('wallpaper_daily_limit', 'Лимит генераций в сутки', 'integer', 'Нужен счётчик по клиенту и продукту в источнике.'),
        Parameter('wallpaper_enabled', 'Показывать продукт', 'boolean', 'Применится после чтения конфигурации в обработчике бота.'),
    )),
}


def install_blueprint(db, bot_id: int, key: str, admin_id: int) -> tuple[int, bool]:
    """Declare missing fields only; preserve existing values, order and modules."""
    blueprint = BLUEPRINTS[key]
    module_created = False
    if blueprint.module_key:
        module = db.scalar(select(BotModule).where(BotModule.bot_id == bot_id,
                                BotModule.key == blueprint.module_key))
        if module is None:
            db.add(BotModule(bot_id=bot_id, key=blueprint.module_key,
                             name=blueprint.title, description=blueprint.description,
                             status='draft', updated_by=admin_id))
            module_created = True
    current = {row.key for row in db.scalars(select(BotSetting).where(BotSetting.bot_id == bot_id))}
    order = db.scalar(select(func.max(BotSetting.display_order)).where(BotSetting.bot_id == bot_id)) or 0
    added = 0
    for spec in blueprint.parameters:
        if spec.key in current:
            continue
        order += 10
        db.add(BotSetting(bot_id=bot_id, key=spec.key, label=spec.label, kind=spec.kind,
                          value='', is_configured=False, group_name=key,
                          description=spec.description, display_order=order,
                          updated_by=admin_id))
        current.add(spec.key)
        added += 1
    return added, module_created
