# OmniFactory CRM 1.7 · боты, реклама, платежи и смартфон

## Установка поверх существующей CRM в Windows PowerShell

Остановите `launcher.py start` через `Ctrl+C`. Сохраните прежний `.env` и неизменный `CRM_ENCRYPTION_KEY`. Из корня проекта:

```powershell
Expand-Archive "$HOME\Downloads\OmniFactory_CRM_1_7.zip" -DestinationPath .\_crm_update -Force
.\.venv312\Scripts\python.exe .\_crm_update\omnibot_crm\install_update.py --check
.\.venv312\Scripts\python.exe .\_crm_update\omnibot_crm\install_update.py --apply
.\.venv312\Scripts\python.exe -m pip install -r .\requirements.txt
.\.venv312\Scripts\python.exe -m alembic upgrade head
.\.venv312\Scripts\python.exe .\launcher.py diagnose
.\.venv312\Scripts\python.exe .\launcher.py start
```

Миграция `0008_ads_workspace` добавляет рекламные кабинеты, конверсии и сценарии. Установщик не заменяет `.env`, базу и виртуальную среду; прежние версии заменённых файлов сохраняются в `.crm_update_backups`. Для общей Neon-базы сначала выполните миграцию на отдельной ветке Neon.

## Боты без редактирования файлов CRM

1. В левом меню выберите **Боты → Добавить бота**. Название и уникальный ключ задаются в форме; после сохранения открывается карточка.
2. Примените профили **Telegram · общие параметры** и **Telegram · диалоги и рост**, затем нужные продуктовые профили. Применение создаёт *незаполненные* поля без цен и ключей.
3. Укажите `ingest_secret` (уникальный, не короче 24 символов), `telegram_token` при необходимости рассылки, платёжный кабинет каждого бота и произвольные поля. Они хранятся зашифрованно. Чужие боты их не получают.
4. В разделе **Сценарии и диалоги** создайте триггер (`/start`, ключевое слово, событие), шаги сообщения, вопроса с записью ответа, медиа, задержки и передачи оператору. Черновики отсутствуют в API; **Опубликовать** делает их доступными через шлюз.
5. Код существующего Python-бота один раз подключают через `integrations/bot_config_client.py` и `integrations/send_event.py`. Подписанный `GET /api/v1/bot-config` отдаёт `schema`, `revision`, `settings`, `modules`, `scenarios`, поддерживает ETag/304. События `scenario.enter`, `scenario.complete`, `scenario.handoff` с `scenario_key` наполняют статистику карточки бота. Само сохранение в CRM не меняет обработчики внешнего процесса. Чтобы бот исполнял шаги, ему нужен интерпретатор сценариев и сбор ответов. В CRM нет обещания создать нового работающего Telegram-процесса одной кнопкой.

Пример чтения конфигурации в коде бота:

```python
from integrations.bot_config_client import BotConfigurationGateway

gateway = BotConfigurationGateway("https://crm.example.com", "food_porn", "ВСТАВЬТЕ_СВОЙ_УНИКАЛЬНЫЙ_HMAC_СЕКРЕТ")
config = gateway.refresh()  # Далее бот проверяет и применяет нужные поля и сценарии.
```

Функции профиля сценариев основаны на сравнении платформ из [обзора Carrot quest](https://www.carrotquest.io/blog/platforms-to-build-telegram-bot/): сбор контактов, сегментация, цепочки, медиа, передача оператору, аналитика. Сам обзор опубликован в 2023 году; он использован как перечень идей, а не как документация Telegram API. Рассылки по согласию уже работают в CRM, диалоги и A/B-тесты требуют подключения исполнения в боте.

## Рекламные кампании

На **Продвижение / SEO** создайте канал `facebook`/`instagram` или `google_ads`, затем заполните появившийся рекламный кабинет. Для Meta нужен ID рекламного аккаунта (цифры без `act_`) и access token с доступом к Insights. Для Google нужны customer ID без дефисов, OAuth Client ID, Client Secret и Refresh Token; ID управляющего аккаунта необязателен. Валюта должна совпадать с валютой рекламного аккаунта. CRM не получает токены сама: выдайте доступ своему приложению у провайдера.

После подключения **Синхронизировать сейчас** читает 30 завершённых дней. Отдельный worker повторяет запрос ежечасно. CRM создаёт/обновляет кампании по внешним ID, загружает дневные показы, клики, конверсии и затраты; Meta action `lead` попадает в лиды платформы, Google `metrics.conversions` — отдельно в конверсии. Реальные лиды CRM учитываются отдельно через связку контакта и UTM. Расходы рекламных кабинетов не суммируются автоматически с бухгалтерскими расходами. Есть Excel-отчёт.

Подключение только **читает статистику**: создание объявлений, изменение ставок и загрузка аудиторий не реализованы. При ошибках старые отчёты сохраняются, статус и безопасное сообщение об ошибке показываются возле кабинета. Доступы зашифрованы одним `CRM_ENCRYPTION_KEY`.

Официальные источники: [Meta Insights API](https://www.postman.com/meta/facebook-marketing-api/folder/zzd6d5p/insights-api), [Google Ads reporting](https://developers.google.com/google-ads/api/docs/reporting/overview), [OAuth для Google Ads](https://developers.google.com/google-ads/api/docs/oauth/overview). По состоянию на сентябрь 2026 доступ к Google Ads API назначается Google Cloud project; developer token для новых подключений не нужен ([изменения Google](https://developers.google.com/google-ads/api/docs/api-policy/developer-token)).

## Платёжные кабинеты каждого бота

| Провайдер | Поля в карточке | Серверная проверка | Важное ограничение |
| --- | --- | --- | --- |
| Portmone | login, password, payee ID | По заказу, магазину, `PAYED`, сумме | Реализована UAH, до 30 дней истории. |
| WayForPay | merchantAccount, secretKey | `CHECK_STATUS`, подпись ответа, `Approved`, сумма и валюта | Сообщения бота сами оплату не подтверждают. |
| LiqPay | publicKey, privateKey | Подписанный запрос `action=status`, `success`, `pay`, магазин, заказ и сумма | `hold`/ожидание не считается продажей. |
| PayPal Orders v2 | Client ID, Client Secret, Merchant ID, live/sandbox | OAuth, GET order, один capture `COMPLETED`, правильный merchant и сумма | В заказ CRM передают именно PayPal Order ID. Sandbox не подтверждает реальную выручку. |

Официальные спецификации: [Portmone Payment Gateway](https://docs.portmone.com.ua/en/docs/en/PaymentGatewayEng/), [WayForPay Check Status](https://wiki.wayforpay.com/en/view/852117), [LiqPay payment status](https://www.liqpay.ua/doc/api/information/status_payment?tab=1), [PayPal Orders v2](https://developer.paypal.com/api/orders/v2/definitions/purchase_unit). CRM не формирует checkout, не создаёт платежи у провайдера и пока не обрабатывает последующие возвраты/chargebacks. Для бухгалтерского закрытия нужна отдельная сверка возвратов.

## Клиенты, таблицы и смартфон

**Клиенты**: поиск по имени/email/телефону, фильтры статуса, источника, тега и бота, сортировка, карточка с заметками и историей. Импорт UTF-8 CSV по `name,email,phone` заполняет отсутствующие контакты и пропускает противоречивые совпадения. Импорт *не назначает* согласие на маркетинг. Excel-выгрузка клиентов включает лист связей с ботами; экспортируется текущая выборка до 10 000 клиентов.

`/m` — мобильное пространство со сводкой, поиском по 45 таблицам, выгрузкой Excel и ссылками на рабочие карточки. В шапке и нижнем меню есть клиенты, боты, реклама и таблицы. Полная CRM адаптирована к узкому экрану: формы и действия доступны через те же адреса и мобильное меню. Системные таблицы `/m/t/...` остаются только для просмотра, чтобы исключить случайную правку технических записей.

Интерфейс и навигация спроектированы для OmniFactory CRM; [SMART CRM](https://ce.smart-it.com/) послужила ориентиром по охвату задач, её оформление и исходный код не копировались.
