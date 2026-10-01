# Food_Porn → OmniBot CRM · карта администрирования

Анализ выполнен по публичной ветке `master`, commit [`5ad0f486f36b43cd5688c9472a0d2698e2a38c51`](https://github.com/BondarevEugene/Food_Porn/tree/5ad0f486f36b43cd5688c9472a0d2698e2a38c51). Более свежие файлы на компьютере разработчика могут отличаться. Ниже нет токенов, паролей, клиентских данных и утверждённых тарифов.

## Что на самом деле запущено

[`app/main.py`](https://github.com/BondarevEugene/Food_Porn/blob/5ad0f486f36b43cd5688c9472a0d2698e2a38c51/app/main.py) создаёт **один** экземпляр `aiogram.Bot` с одним `BOT_TOKEN` и регистрирует маршруты меню и обоев. Это два **продукта внутри одного Telegram-бота**, а не два независимых Telegram-аккаунта. В CRM заведите одного бота `food_porn` и примените к нему профили **Telegram · общие параметры**, **Гастрономическое меню** и **Обои-манифестации**. Если продукты позднее разделятся на разные Telegram-аккаунты, создайте отдельные карточки ботов и примените те же профили к нужной карточке.

## Параметры, которые требуется администрировать

| Область | Текущий источник | Ключи профиля CRM | Тип / замечание |
| --- | --- | --- | --- |
| Связь с CRM и рассылки | До интеграции в боте нет; CRM уже поддерживает HMAC и worker | `ingest_secret`, `telegram_token` | Шифрованные секреты на уровне Telegram-бота. |
| Источник данных | [`app/config.py`](https://github.com/BondarevEugene/Food_Porn/blob/5ad0f486f36b43cd5688c9472a0d2698e2a38c51/app/config.py), `DATABASE_URL` | `source_database_url` | Зашифрованный URL на стороне CRM; не выдаётся боту. Запасной вариант — `FOOD_PORN_SOURCE_DATABASE_URL` в `.env` CRM. |
| Внешние API | [`app/config.py`](https://github.com/BondarevEugene/Food_Porn/blob/5ad0f486f36b43cd5688c9472a0d2698e2a38c51/app/config.py) | `openai_api_key`, `spoonacular_api_key` | Шифрованные секреты; существующие ключи не переносятся из GitHub. |
| Администраторы и логи | `ADMIN_TELEGRAM_IDS`, `LOG_LEVEL` | `admin_telegram_ids`, `log_level` | Список ID и строка; правам в CRM не равны автоматически. |
| Цена меню | `MENU_PRICE`, `CURRENCY` в конфигурации и [`menu_pipeline.py`](https://github.com/BondarevEugene/Food_Porn/blob/5ad0f486f36b43cd5688c9472a0d2698e2a38c51/app/workers/menu_pipeline.py) | `menu_price`, `menu_currency` | Черновик: действующую цену вводите после проверки. CRM-каталог не меняет цену бота сам. |
| Меню: генерация | `OPENAI_IMAGE_MODEL/SIZE/QUALITY`, `IMAGE_CONCURRENCY`, `IMAGE_RETRIES`, `OPENAI_QUOTA_COOLDOWN_SECONDS`, `ENABLE_IMAGE_CACHE`, `USE_MOCK_IMAGES`, `MAX_UPLOAD_MB` | `menu_image_*`, `menu_quota_cooldown_seconds`, `menu_max_upload_mb` | Отдельные типы: текст, число, флаг; изменение числа workers требует перезапуска. |
| VIP меню | Список Telegram ID внутри `menu_pipeline.py` | `menu_vip_telegram_ids` | Список числовых ID; обход оплаты не должен создавать фиктивную выручку. |
| Печать и SMTP | `PRINTSHOP_NAME/EMAIL`, использование SMTP в [`printshop.py`](https://github.com/BondarevEugene/Food_Porn/blob/5ad0f486f36b43cd5688c9472a0d2698e2a38c51/app/services/printshop.py) | `menu_printshop_*`, `menu_smtp_*` | Адреса и секрет SMTP; параметры SMTP пока отсутствуют в `Settings` исходного commit. |
| Макет | Значения DPI и bleed внутри [`booklet.py`](https://github.com/BondarevEugene/Food_Porn/blob/5ad0f486f36b43cd5688c9472a0d2698e2a38c51/app/render/booklet.py) | `menu_print_dpi`, `menu_bleed_mm` | Потребуется заменить константы в коде; согласовать с типографией. |
| Обои: генерация | Модель/размер, лимит первых пяти фото/целей, художественный prompt в [`wallpaper.py`](https://github.com/BondarevEugene/Food_Porn/blob/5ad0f486f36b43cd5688c9472a0d2698e2a38c51/app/bot/handlers/wallpaper.py) | `wallpaper_image_model`, `wallpaper_image_size`, `wallpaper_max_photos`, `wallpaper_max_goals`, `wallpaper_style_prompt` | Отдельные параметры продукта; сейчас часть значений зашита в код. |
| Обои: коммерция | VIP по username, Premium ссылка и кнопка проверки в `wallpaper.py` | `wallpaper_price`, `wallpaper_currency`, `wallpaper_vip_telegram_ids`, `wallpaper_preview_watermark`, `wallpaper_daily_limit`, `wallpaper_enabled` | Сначала заказ, сверка оплаты и полноценные данные о генерации; поля пока лишь административные черновики. |
| Эквайеры | Portmone и Redsys в конфигурации/[`payment.py`](https://github.com/BondarevEugene/Food_Porn/blob/5ad0f486f36b43cd5688c9472a0d2698e2a38c51/app/services/payment.py) | Отдельные зашифрованные аккаунты Portmone / Redsys в карточке бота | В CRM есть серверная сверка Portmone; Redsys пока **только реестр реквизитов**. WayForPay доступен для других проектов. |

Профили создают только названия, типы и описания **незаполненных** параметров. Применение повторно не меняет значения и порядок, который установил администратор. Любому боту можно добавить свои продукты и произвольные поля вне этих профилей.

## Что требует исправления в самом Food_Porn перед реальными продажами

1. [`payment.py`](https://github.com/BondarevEugene/Food_Porn/blob/5ad0f486f36b43cd5688c9472a0d2698e2a38c51/app/bot/handlers/payment.py) и [`payment_callback.py`](https://github.com/BondarevEugene/Food_Porn/blob/5ad0f486f36b43cd5688c9472a0d2698e2a38c51/app/bot/handlers/payment_callback.py) считают оплату успешной без ответа эквайера; маршруты оплаты не регистрируются в показанном `app/main.py`. Нельзя выдавать чистый файл или отправлять в типографию по нажатию кнопки. Уточните реальный order reference, валюту, сумму и подписанный callback; сверка CRM требует совпадения заказа и суммы.
2. [`wallpaper.py`](https://github.com/BondarevEugene/Food_Porn/blob/5ad0f486f36b43cd5688c9472a0d2698e2a38c51/app/bot/handlers/wallpaper.py) показывает общую ссылку Portmone и кнопку `check_wp_pay_*`, однако в зарегистрированных маршрутах нет её обработчика. Продукт обоев не сохраняет полноценные заказы, транзакции и генерации в Neon. Данные для аналитики обоев пока неоткуда импортировать.
3. В меню эквивалент EUR вычисляется фиксированным делением на `42.0`; в [`payment.py`](https://github.com/BondarevEugene/Food_Porn/blob/5ad0f486f36b43cd5688c9472a0d2698e2a38c51/app/services/payment.py) строки checkout формируются вручную. До сверки официального API и правил валют/подписей не считать их производственным платёжным сценарием.
4. [`app/admin_app.py`](https://github.com/BondarevEugene/Food_Porn/blob/5ad0f486f36b43cd5688c9472a0d2698e2a38c51/app/admin_app.py) содержит статическую пару администратора, секрет сессии и демонстрационные метрики. Этот интерфейс нельзя считать источником реальной аналитики или публиковать без исправления аутентификации. В `app/config.py` есть встроенное значение URL базы с паролем-подобным фрагментом; удалите значение по умолчанию и используйте секреты среды.
5. Локальное хранилище файлов и память FSM не подходят для горизонтального масштабирования без общего хранилища и общей FSM. Это отдельная задача интеграции/развёртывания.

## Контракт будущей интеграции

CRM уже принимает подписанные события в `/api/v1/events`. Для одного `food_porn` идентификатор клиента остаётся единым, а продукт передаётся в `data.module_key`: `menu` или `wallpaper`. Допускаются и пользовательские ключи, зарегистрированные в карточке этого бота. Неизвестный ключ отклоняется. `GET /api/v1/bot-config` отдаёт заполненные поля и реестр продуктов по HMAC; незаполненные шаблонные поля не отдаются.

| Момент в боте | Событие CRM | Что передать |
| --- | --- | --- |
| Регистрация/обновление | `customer.upsert` | Стабильный Telegram ID, имя, телефон при наличии, язык; `module_key` по происхождению события. |
| Начало продукта/каталог | `product.upsert`, `order.upsert` | SKU, подтверждённую цену/валюту и уникальный номер заказа; `module_key`. |
| Запрос и статус платежа | `payment.upsert` | Реальный provider, order reference, сумму, валюту, transaction ID; сообщение бота `paid` учитывается как `reported_paid`, пока CRM не сверит провайдера. |
| Работа/ошибка | `bot.heartbeat`, `bot.error` | Периодический сигнал и обезличенное описание ошибки; `module_key`, когда событие относится к продукту. |
| Маркетинг | `marketing.touch` | Идентификатор кампании и согласие клиента по отдельному событию `customer.consent`; регистрация не означает согласие. |

Существующий импорт исходных таблиц Food Porn маркирует меню как `menu` и **не** придумывает сумму или подтверждённую оплату. Для обоев нужен адаптер событий или реальные таблицы, которые появятся после изменений в исходном проекте. Возможность вносить параметры в CRM сама по себе не меняет работу бота: в его коде надо реализовать чтение подписанного API, проверку допустимых типов и безопасное применение настроек.
