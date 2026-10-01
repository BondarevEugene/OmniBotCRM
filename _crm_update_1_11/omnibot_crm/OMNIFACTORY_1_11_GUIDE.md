# OmniFactory CRM 1.11 · Боты, аналитика, Neon

Этот выпуск расширяет действующий проект. У него **нет новой миграции**: head
остается `0012_bot_data_connections`. CRM пока работает на SQLite, пока вы сами
не создадите отдельную базу CRM в Neon и не переключите `DATABASE_URL`.

## Установка исходников из архива

Скачайте `OmniFactory_CRM_1_11_Bot_Operations.zip` в `Downloads`, остановите
launcher и выполните команды из корня `omnibot_crm` **по одной строке**:

```powershell
Expand-Archive -LiteralPath "$env:USERPROFILE\Downloads\OmniFactory_CRM_1_11_Bot_Operations.zip" -DestinationPath ".\_crm_update_1_11" -Force
.\.venv312\Scripts\python.exe .\_crm_update_1_11\omnibot_crm\install_update.py --check
.\.venv312\Scripts\python.exe .\_crm_update_1_11\omnibot_crm\install_update.py --apply
.\.venv312\Scripts\python.exe -m pip install -r .\requirements.txt
.\.venv312\Scripts\python.exe -c "from crm.app import app; from crm.bot_read_api import router; print('Imports OK')"
.\.venv312\Scripts\python.exe -m alembic upgrade head
.\.venv312\Scripts\python.exe .\launcher.py start
```

Установщик сохраняет заменяемые файлы в `.crm_update_backups`. Архив не
содержит `.env`, SQLite базы или файлов из `storage`.

## Отдельная база CRM на Neon

База Food_Porn и база CRM должны быть **разными**. Загруженный `.env` содержит
конфигурацию Food_Porn. Не копируйте его целиком в CRM. Создайте в Neon
отдельную базу/проект `omnifactory_crm` и получите прямую строку подключения;
установите обязательный TLS (`sslmode=require`). В CRM используется драйвер
`postgresql+psycopg://`, не `postgresql://` и не `asyncpg`.

Перед переносом остановите web и worker, сохраните копию `crm_local.db` и
CRM `.env`. В сессии PowerShell (значение подставьте на своем компьютере):

```powershell
$env:CRM_NEON_DATABASE_URL = 'postgresql+psycopg://USER:PASSWORD@HOST/omnifactory_crm?sslmode=require'
$env:DATABASE_URL = $env:CRM_NEON_DATABASE_URL
.\.venv312\Scripts\python.exe -m alembic upgrade head
Remove-Item Env:DATABASE_URL
.\.venv312\Scripts\python.exe .\migrate_crm_to_neon.py --sqlite-path .\crm_local.db
.\.venv312\Scripts\python.exe .\migrate_crm_to_neon.py --sqlite-path .\crm_local.db --apply
```

Последняя команда переносит существующие данные только если обе базы на `0012`
и целевая CRM пуста. Сначала выполните запуск без `--apply`: он только
показывает количество строк. Скрипт создает согласованный снимок SQLite,
переносит таблицы в порядке внешних ключей и сдвигает последовательности ID
в Postgres. Секреты переносятся как шифротекст; **сохраните прежний
`CRM_ENCRYPTION_KEY`**. После успешного переноса измените только `DATABASE_URL`
в CRM `.env` на URL новой базы, затем запустите launcher и проверьте
администратора, ботов и число клиентов. Не запускайте одновременно старую CRM
на SQLite и новую на Neon: они разойдутся по данным.

Для надежного восстановления включите в Neon доступное вашему тарифу хранение
истории и регулярно проверяйте восстановление в отдельной ветке. Делайте
также независимые резервные копии Postgres и храните ключ шифрования отдельно.
Ветвление Neon не подменяет независимый архив данных.

## API только для чтения

- `GET /api/v1/bots/{bot_key}/overview?days=30`: клиенты, заказы, ошибки,
  heartbeat, события по дням и **только проверенная** выручка по валютам.
- `GET /api/v1/bots/{bot_key}/settings`: список полей, их статусы и значения
  обычных полей; секреты имеют `value: null`. Учетные данные платежных систем
  не раскрываются. Данные других ботов не выдаются.

Для каждого запроса используйте заголовки `X-Bot-Key`, `X-Timestamp`,
`X-Signature`. Подпись: HMAC-SHA256 с ключом `ingest_secret` от строки
`<timestamp>.GET.<path>?<query>`; если параметров нет, знак `?` не нужен.
Временная метка Unix действительна пять минут. Пример клиента:

```python
import hashlib, hmac, time
import httpx

path = '/api/v1/bots/food_porn/overview?days=30'
timestamp = str(int(time.time()))
signature = hmac.new(ingest_secret.encode(), (timestamp + '.GET.' + path).encode(), hashlib.sha256).hexdigest()
response = httpx.get(crm_url + path, headers={
    'X-Bot-Key': 'food_porn', 'X-Timestamp': timestamp, 'X-Signature': signature,
}, timeout=10)
response.raise_for_status()
print(response.json())
```

`crm_url` и `ingest_secret` берутся из защищенного окружения сервиса, не
вставляются в исходный код. В рабочей среде используйте HTTPS. Старый
`GET /api/v1/bot-config` остается отдельным каналом **для самого бота**:
он передает ему опубликованную конфигурацию с секретами под той же подписью.
Он тоже только для чтения, но не является безопасным API для витрины/отчетов.

## Food_Porn: настройки из .env

Откройте `Боты → Food Porn → Настройки → Карта .env`. Кнопка создает
недостающие поля без значений, не перезаписывая существующие. После ввода
параметров создайте версию на вкладке «Версии». Значения из загруженного
`.env` **не импортируются автоматически**. Токены и API ключи вводите только
в закрытом интерфейсе CRM. `DATABASE_URL` Food_Porn и файлы в `STORAGE_ROOT`
пока остаются на хосте бота. Переход на управление работающим Food_Porn требует
встроить клиент `integrations/bot_config_client.py` в его startup/worker и
привязать каждое поле к его `Settings`; одна запись в CRM еще не меняет код бота.

## Что делать дальше для профессионального уровня

1. Подключить Food_Porn к событиям CRM: клиенты, продукты, заказы, оплаты,
   ошибки и heartbeat. Сверять платежи с провайдером до учета выручки.
2. Доделать контракт конфигурации: явно разделить startup, runtime и secrets;
   проверять схему перед публикацией, показывать разницу версий, иметь
   безопасный откат и журнал фактического применения версии ботом.
3. Построить сквозную воронку по ботам и услугам: источники трафика, клиенты,
   заказы, подтвержденная выручка и расходы; отделить валюты и курсы по датам.
4. Для Neon держать отдельную базу/права на каждого бота, CRM подключать к
   ботам ограниченным read-only доступом или через события. Настроить
   резервные копии, проверку восстановления, метрики задержки и ошибок синхронизации.
5. Упорядочить раздел «Боты» как рабочее место: обзор → аналитика → клиенты →
   коммерция → инциденты → конфигурация → история выпусков. Новый экран
   аналитики дает основу, но достоверные графики появятся после передачи
   реальных событий из Food_Porn.
