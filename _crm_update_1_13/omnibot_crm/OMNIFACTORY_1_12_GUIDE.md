# OmniFactory CRM 1.12 — пульт бизнеса и исполняемый консультант

## Что появилось

- **Пульт бизнеса** `/control-center`: выбор бота, связь, опубликованная и реально применённая версия настроек, клиенты, заказы, обращения, инциденты и очередь оплат к проверке. Поступления за 30 дней учитываются **только по `PaymentVerification`**, отдельными суммами в исходных валютах. Ручные расходы показываются отдельно. Боты без heartbeat отмечаются как не имеющие свежего сигнала — это не диагностика процесса вне CRM.
- **Шлюз конфигурации**: уже существующий `GET /api/v1/bot-config` отдаёт опубликованную версию; новый подписанный `POST /api/v1/config-applied` позволяет боту сообщить, что он *действительно применил* эту версию. Пока конкретный бот не вызывает этот метод после переключения собственных настроек, на пульте будет «Не применена». Отчёт бота не доказывает семантическую работоспособность каждой настройки.
- **Конструктор**: сохранённую блок схему можно опубликовать. Новый подписанный `POST /api/v1/bot-dialogue` выполняет опубликованные блоки `start`, `message`, `question`, `buttons`, `condition`, `media`, `handoff`, `end`; держит состояние клиента и версию схемы в CRM, возвращает инструкции для отправки самим Telegram ботом, создаёт клиента и обращение при передаче человеку. События с одинаковым `event_id` идемпотентны. Блок `wait` пока запрещён при публикации.
- **Telegram Stars**: при поступлении `payment.upsert` с `provider=telegram_stars`, `currency=XTR`, `external_id=successful_payment.telegram_payment_charge_id` worker проверяет запись через `getStarTransactions` токеном *того же бота*. Сверяются сумма, уникальный charge ID, источник `user/invoice_payment`, `invoice_payload` с ID заказа и Telegram user ID с клиентом. Возврат с тем же ID блокирует подтверждение. Храните Stars отдельно от фиатных валют; CRM не пересчитывает XTR в UAH. История более 5000 операций требует следующего механизма постраничной индексации. Проверка возвратов после ранее подтверждённой оплаты пока не автоматизирована: сверяйте их отдельно перед налоговым учётом.
- Изолированные Python адаптеры: `integrations/bot_config_client.py` и `integrations/bot_dialogue_client.py` для подключения существующих обработчиков без переписывания архитектуры ботов.

## Установка на существующий проект в Windows PowerShell

1. Остановите launcher (`Ctrl+C`) и сделайте копию вашей локальной базы данных / снимок Neon. Не копируйте `.env` в архив с обновлением.
2. Скачайте архив релиза. В Проводнике проверьте точное имя и местоположение. Команды ниже предполагают `OmniFactory_CRM_1_12_Update.zip` в папке `Downloads`, а терминал открыт в **корне** `omnibot_crm`.
3. Выполните команды **по одной строке**:

```powershell
$archive = Join-Path $env:USERPROFILE 'Downloads\OmniFactory_CRM_1_12_Update.zip'
Test-Path -LiteralPath $archive
Expand-Archive -LiteralPath $archive -DestinationPath '.\_crm_update_1_12' -Force
.\.venv312\Scripts\python.exe .\_crm_update_1_12\omnibot_crm\install_update.py --check
.\.venv312\Scripts\python.exe .\_crm_update_1_12\omnibot_crm\install_update.py --apply
.\.venv312\Scripts\python.exe -m pip install -r .\requirements.txt
.\.venv312\Scripts\python.exe -m alembic heads
.\.venv312\Scripts\python.exe -m alembic upgrade head
.\.venv312\Scripts\python.exe -m alembic current
.\.venv312\Scripts\python.exe .\launcher.py start
```

Если `Test-Path` возвращает `False`, проверьте путь через `Get-ChildItem "$env:USERPROFILE\Downloads" -Filter '*1_12*.zip' -File`. Не запускайте миграцию, если `alembic heads` не показывает `0013_bot_operations`. Инсталлятор копирует заменяемые файлы в `.crm_update_backups` и **не** меняет `.env` или существующую базу. Для Neon сначала выставьте свой `DATABASE_URL=postgresql+psycopg://...` в `.env`, выполните отдельную резервную копию и ту же миграцию. Перенос уже накопленных данных SQLite в Neon — самостоятельная операция через `migrate_crm_to_neon.py`, он не выполняется установщиком.

## Подключение бота к CRM

Для каждого бота в CRM заведите собственные `bot_key`, подписывающий `ingest_secret` и `telegram_token`. Запускайте CRM по HTTPS; `.env` бота должен пока хранить *bootstrap* `CRM_URL`, `BOT_KEY` и `BOT_INGEST_SECRET`, иначе бот не сможет аутентифицироваться, чтобы получить остальные настройки. Не публикуйте эти значения. Токен Telegram и параметры эквайера размещаются в настройках CRM; действующий код бота должен явно использовать полученные значения.

1. При старте и периодически вызывайте `BotConfigurationGateway.refresh()`, проверьте обязательные поля и примените в боте атомарно. После **фактического** применения вызовите `acknowledge('applied')`; при ошибке `acknowledge('failed', 'validation_failed')`. Клиентская библиотека сама по себе не подключает параметры к существующему Food_Porn.
2. Создайте бота и сценарий, откройте «Конструктор», соедините блоки, сохраните и нажмите **«Опубликовать»**. Отправляйте входящие сообщения из обработчика Telegram через `BotDialogueGateway.message(event_id=str(update.update_id), scenario_key='consult', external_user_id=str(message.from_user.id), text=message.text or '')`. Стабильный `update_id` защищает от повторной обработки при повторах доставки.
3. Выполните возвращённые `actions` в Telegram обработчике: `text` — текст и необязательная reply клавиатура `buttons`, `media` — HTTPS ресурс, `handoff` — уведомление пользователю и работа операторов в CRM. `/start` начинает новую сессию. Сам CRM **не** получает Telegram обновления и **не** посылает сообщения пользователя через этот метод. Для другого бота повторите шаги с его отдельным ключом и секретом.
4. Для Stars создайте заказ с `external_id` **равным `invoice_payload`** Telegram инвойса; клиента с `external_user_id` его Telegram user ID. Только после `successful_payment` отправьте `payment.upsert`: `external_id` равен `telegram_payment_charge_id`, `order_external_id` равен ID заказа, `currency='XTR'`, целая `amount=total_amount`, `status='paid'`. «Пульт бизнеса» сначала покажет платёж на сверке; worker выставит `paid` лишь после запроса к Telegram. Для фиатных платежей сохранены проверки Portmone, WayForPay, LiqPay и PayPal. Не выдавайте услугу по неподтверждённому флагу из браузера или событию pre_checkout.

CRM не содержит токенов или секретов Food_Porn. Пока обработчики двух существующих ботов не подключены к адаптерам и не отправляют события, боевые метрики, оплаты и применение настроек для них не появятся автоматически. Настройка `food_porn` в списке ботов CRM сама по себе не изменяет код Food_Porn.

## Проверки релиза

Миграция `0012 → 0013` на SQLite с сохранением старого бота; 38 тестов, включая изоляцию ботов, версии диалога, передачу оператору, подтверждение настроек и проверку Stars с возвратом. Для живого Telegram / платёжных систем потребуются отдельные токены, тестовые счета и испытание на вашей инфраструктуре.
