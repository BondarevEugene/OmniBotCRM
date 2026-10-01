# OmniFactory CRM 1.13 — OpenAI: экономика, ключи и использование

## Возможности

В CRM появился раздел **«OpenAI · расходы»** (`/openai`) с несколькими организациями и фильтрами по ботам. Admin API key каждой организации хранится Fernet-зашифрованным с уже используемым `CRM_ENCRYPTION_KEY`; он не выводится в HTML, CSV и API для ботов. На сервере выполняются только GET-запросы к официальному OpenAI Admin API. Автоматическая синхронизация — раз в час при работающем worker; есть ручная кнопка.

| Источник OpenAI | Что получает CRM |
| --- | --- |
| Organization Costs API | Фактические расходы в валюте источника, день UTC, проект, ID ключа, статья, количество и единица; пересчёт исправленных провайдером строк, отчёт CSV. Это источник управленческих сумм. |
| Organization Usage API | Completions, images, audio speeches/transcriptions, embeddings, moderations, vector stores, code interpreter sessions, file search calls и web search calls. Агрегаты по проекту, ID ключа, модели и поддерживаемым измерениям семейства. Метрики токенов/запросов/изображений не выдаются за счёт. |
| Organization Admin API | Реестр проектов, ID и метаданные project/API admin keys, дата последнего использования, лимиты скорости, месячные hard limits и оповещения, если права ключа это позволяют. Значения существующих ключей OpenAI не возвращаются. |
| Привязка CRM | Несколько ботов, ручное связывание проекта или ID ключа с ботом и назначением; спорный или общий расход остается в «не распределено». |

**Остаток предоплаченных кредитов:** документированные Usage/Costs и Spend Limit API не выдают отдельного числового баланса предоплаты. CRM честно показывает «Не доступен через API»; кредитные гранты проверяются в OpenAI Billing. «Осталось до месячного лимита» = опубликованный hard limit минус расход текущего календарного месяца и является оценкой, а не доступными деньгами. Hard limits могут сработать с задержкой. Расходы ChatGPT подписки и API Platform имеют разные контуры. Не записывайте OpenAI Costs автоматически как оплаченный расход ФОП: счёт/списание и курс ещё нужно сверить с первичными документами.

**Точность распределения:** OpenAI группирует организационные расходы по проекту/ключу, не по Telegram пользователю или конкретному API запросу. Для Food_Porn и каждого другого продукта заведите собственный Project/API key либо добавьте в код продукта отдельный журнал запросов. Когда несколько ботов используют один ключ, CRM не сможет доказательно разделить затраты.

## Установка в Windows PowerShell

Остановите `launcher.py` клавишами `Ctrl+C`, сделайте резервную копию SQLite или снимок Neon. Архив содержит **полный проект с цепочкой миграций**, но не ваш `.env` и не базу. Откройте PowerShell в корне существующего `omnibot_crm` и запускайте каждую команду целиком на одной строке:

```powershell
$archive = Join-Path $env:USERPROFILE 'Downloads\OmniFactory_CRM_1_13_OpenAI_Observability.zip'
Test-Path -LiteralPath $archive
Expand-Archive -LiteralPath $archive -DestinationPath '.\_crm_update_1_13' -Force
.\.venv312\Scripts\python.exe .\_crm_update_1_13\omnibot_crm\install_update.py --check
.\.venv312\Scripts\python.exe .\_crm_update_1_13\omnibot_crm\install_update.py --apply
.\.venv312\Scripts\python.exe -m pip install -r .\requirements.txt
.\.venv312\Scripts\python.exe -m alembic heads
.\.venv312\Scripts\python.exe -m alembic upgrade head
.\.venv312\Scripts\python.exe -m alembic current
.\.venv312\Scripts\python.exe .\launcher.py start
```

Ожидаемая ревизия после установки: `0014_openai_observability`. Если `Test-Path` возвращает `False`, найдите архив через `Get-ChildItem "$env:USERPROFILE\Downloads" -Filter '*1_13*.zip' -File` и скорректируйте `$archive`. Работайте с `.venv312\Scripts\python.exe`, чтобы PowerShell случайно не использовал старое окружение `.venv` с Python 3.9. Инсталлятор сохраняет изменяемые файлы в `.crm_update_backups` и не меняет `.env`. Смена `DATABASE_URL` на Neon не переносит прежние данные сама собой.

## Подключение OpenAI

1. Убедитесь, что в `.env` CRM уже задан `CRM_ENCRYPTION_KEY` и вы сохранили его вне Git. Не меняйте существующий Fernet ключ без миграции зашифрованных данных.
2. На [официальной странице API Platform](https://platform.openai.com/settings/organization/admin-keys) создайте **Admin API key** с разрешениями на чтение организации, Usage/Costs, проектов и ключей. Обычный продуктовый `OPENAI_API_KEY` не заменяет Admin API key. Скопируйте ID организации вида `org_...`.
3. В CRM войдите как owner → **Системы → OpenAI · расходы** → «Подключить организацию». Введите ID, название и Admin API key; затем нажмите «Синхронизировать сейчас» или дождитесь цикла worker. При частичном доступе экран покажет статус `partial` и код отсутствующего ресурса. При ошибке расходов статус `failed`; предыдущие цифры не стираются.
4. В блоке «Привязать проект или ключ к боту» выберите бота, Project ID и при необходимости **API Key ID** из реестра. Это ID метаданных, а не секретный ключ. Для одного проекта с несколькими ботами используйте отдельные ключи. Фильтр по боту и CSV покажут приписанные расходы.
5. Для реального запуска продукта через OpenAI секретный ключ продукта храните отдельно в параметре бота `openai_api_key` типа `secret`, опубликуйте конфигурацию и подключите существующий `BotConfigurationGateway` в коде бота. Новая интеграция мониторинга **не раздаёт Admin API key ботам** и не меняет Food_Porn автоматически.

## Ограничения и источники

- Первый запуск загружает до 90 дней, далее каждый час перепроверяет последние 30 дней; старые данные за пределами окна не перезаписываются автоматически.
- Единичная строка Usage может отсутствовать или собираться с задержкой. На экране указан момент последней успешной синхронизации; `partial` означает, что некоторые категории не получены.
- Источник для суммы — Costs, не прайс-лист модели. Organization-level расходы (включая планы, если они есть) могут не иметь project/key и останутся не распределены.
- Метаданные ключа (`last_used_at`) сообщают о наблюдавшемся использовании, а не являются проверкой права данного ключа делать запрос к каждой модели.
- Сейчас нет денежных операций над OpenAI: CRM только читает данные. Автоматическую ротацию, выдачу ключей ботам, изменение лимитов, аудит событий доступа и сверку с инвойсами имеет смысл добавлять отдельно с ролями и журналом изменений.

Официальная документация: [Admin APIs](https://developers.openai.com/api/docs/guides/admin-apis), [Costs](https://developers.openai.com/api/reference/resources/admin/subresources/organization/subresources/usage/methods/costs), [Usage](https://developers.openai.com/api/reference/resources/admin/subresources/organization/subresources/usage/methods/completions), [Spend limits](https://developers.openai.com/api/docs/guides/spend-limits), [проверка credit grants](https://help.openai.com/en/articles/10478918-reviewing-api-usage-and-costs).
