# OmniFactory CRM 1.10.2 · исправление запуска

В предыдущем архиве отсутствовал `crm/bot_settings.py`. Из-за этого новый
`crm/config_releases.py` не мог импортировать `parse_rules`, и оба процесса
завершались после успешной проверки launcher. Этот архив содержит полный набор
исходных файлов CRM для релиза 1.10.2, включая миграции 0001–0012.

## Установка в Windows PowerShell

Выполняйте команды **по одной строке** из корня `omnibot_crm`. Используйте
Python из `.venv312`, даже если подсказка терминала показывает `(.venv)`.
Перед установкой остановите launcher клавишами Ctrl+C, если он ещё работает.
Сохраните копию локальной SQLite базы и `.env`.

```powershell
Expand-Archive -LiteralPath "$env:USERPROFILE\Downloads\OmniFactory_CRM_1_10_2_Full_Source_Repair.zip" -DestinationPath ".\_crm_update_1_10_2" -Force
.\.venv312\Scripts\python.exe .\_crm_update_1_10_2\omnibot_crm\install_update.py --check
.\.venv312\Scripts\python.exe .\_crm_update_1_10_2\omnibot_crm\install_update.py --apply
.\.venv312\Scripts\python.exe -m pip install -r .\requirements.txt
.\.venv312\Scripts\python.exe -c "from crm.app import app; from crm.bot_settings import parse_rules; print('Imports OK')"
.\.venv312\Scripts\python.exe -m alembic upgrade head
.\.venv312\Scripts\python.exe .\launcher.py start
```

`--check` показывает список заменяемых файлов. `--apply` сохраняет старые
версии в `.crm_update_backups`. Архив не содержит и не заменяет вашу `.env`,
SQLite базу и данные в `storage`. Уже применённая миграция 0012 останется на
месте: повторный `alembic upgrade head` ничего не меняет.

Если архив сохранён в другом месте, замените путь к ZIP в первой команде.
После применения удалять папку `_crm_update_1_10_2` не обязательно.
