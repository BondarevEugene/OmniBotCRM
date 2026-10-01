# OmniFactory CRM 1.13.1 — исправление OpenAI Organization ID

В 1.13 форма и сервер ошибочно принимали только ID вида `org_...`. Реальный OpenAI Organization ID обычно имеет вид `org-...`. Теперь поддерживаются оба варианта, форма объясняет, где найти ID, и заранее отклоняет номер CRM `1`. При первом подключении нужен Admin API key.

## Обновить существующую CRM (PowerShell)

Остановите CRM через Ctrl+C. Откройте PowerShell в корне `omnibot_crm` и выполняйте каждую строку целиком:

```powershell
$archive = Join-Path $env:USERPROFILE 'Downloads\OmniFactory_CRM_1_13_1_OpenAI_Organization_ID_Fix.zip'
Test-Path -LiteralPath $archive
Expand-Archive -LiteralPath $archive -DestinationPath '.\_crm_fix_1_13_1' -Force
Copy-Item .\crm\openai_admin.py .\crm\openai_admin.py.before_1_13_1 -Force
Copy-Item .\crm\templates\openai.html .\crm\templates\openai.html.before_1_13_1 -Force
Copy-Item .\_crm_fix_1_13_1\crm\openai_admin.py .\crm\openai_admin.py -Force
Copy-Item .\_crm_fix_1_13_1\crm\templates\openai.html .\crm\templates\openai.html -Force
.\.venv312\Scripts\python.exe .\launcher.py start
```

Новых зависимостей и миграций нет. Если `Test-Path` выводит `False`, проверьте фактическое имя файла в Downloads.

Откройте `http://127.0.0.1:8000/openai`, скопируйте Organization ID из [OpenAI Platform → Organization → General](https://platform.openai.com/settings/organization/general), придумайте название для CRM и вставьте [Admin API key](https://platform.openai.com/settings/organization/admin-keys). Число `1` — номер внутри CRM, в поле OpenAI Organization ID оно не подходит. Не публикуйте секретный ключ в сообщениях и скриншотах.
