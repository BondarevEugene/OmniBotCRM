# OmniFactory CRM 1.10.1 · полный переход с 0010

Этот пакет объединяет файлы релиза 1.9 и 1.10. Он исправляет ошибку отсутствующей миграции `0011_finance_workspace.py` при установке 1.10 поверх версии с `0010_bot_canvas`.

## Установка на Windows из корня omnibot_crm

Перед применением остановите `launcher.py` сочетанием Ctrl+C и сохраните копию локальной SQLite базы и `.env`. Архив уже содержит оба набора файлов, отдельно распаковывать 1.9 не нужно.

Выполняйте команды по одной, целиком:

```powershell
Expand-Archive -LiteralPath "$env:USERPROFILE\Downloads\OmniFactory_CRM_1_10_1_From_0010_Fix.zip" -DestinationPath ".\_crm_update_1_10_1" -Force
.\.venv312\Scripts\python.exe .\_crm_update_1_10_1\omnibot_crm\install_update.py --check
.\.venv312\Scripts\python.exe .\_crm_update_1_10_1\omnibot_crm\install_update.py --apply
.\.venv312\Scripts\python.exe -m pip install -r .\requirements.txt
.\.venv312\Scripts\python.exe -m alembic heads
.\.venv312\Scripts\python.exe -m alembic upgrade head
.\.venv312\Scripts\python.exe .\launcher.py start
```

`alembic heads` должен показывать `0012_bot_data_connections (head)`, launcher — совпадение `база=0012_bot_data_connections · код=0012_bot_data_connections`. Установщик сохраняет заменяемые файлы в `.crm_update_backups`; `.env` и база не входят в архив и не копируются.

После запуска подробное описание возможностей читайте в `OMNIFACTORY_1_9_GUIDE.md` и `OMNIFACTORY_1_10_GUIDE.md`. Доступ к Neon/рекламным кабинетам настраивается отдельно; пакет не содержит ваших учетных данных.
