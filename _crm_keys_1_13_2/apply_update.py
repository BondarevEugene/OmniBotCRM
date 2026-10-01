"""Install OmniFactory CRM 1.13.2 OpenAI key registry from an extracted archive."""
import argparse
import py_compile
import shutil
from datetime import datetime
from pathlib import Path

FILES = (
    'crm/openai_admin.py',
    'crm/openai_sync.py',
    'crm/templates/openai.html',
    'crm/templates/openai_keys.html',
    'crm/templates/base.html',
    'tests/test_openai_observability.py',
)


def run(apply):
    source = Path(__file__).resolve().parent
    target = Path.cwd().resolve()
    if not (target / 'crm/app.py').is_file() or not (target / 'alembic/versions/0014_openai_observability.py').is_file():
        raise SystemExit('Запустите команду из корня OmniFactory CRM версии 1.13.')
    if source == target:
        raise SystemExit('Архив надо распаковать в отдельную папку внутри проекта.')
    for relative in FILES:
        original = source / relative
        destination = target / relative
        if not original.is_file() or original.is_symlink() or destination.is_symlink():
            raise SystemExit('Недоступен файл или обнаружена символическая ссылка: ' + relative)
        if relative != 'crm/templates/openai_keys.html' and not destination.is_file():
            raise SystemExit('Нет целевого файла: ' + relative)
        if original.suffix == '.py':
            py_compile.compile(str(original), doraise=True)
    print('Обновление реестра ключей OpenAI:')
    for relative in FILES:
        print('  ' + relative)
    if not apply:
        print('Проверка пройдена. Для установки повторите с --apply.')
        return
    backup = target / '.crm_update_backups' / ('openai_keys_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
    copied = []
    try:
        for relative in FILES:
            destination = target / relative
            existed = destination.exists()
            if existed:
                saved = backup / relative
                saved.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(destination, saved)
            destination.parent.mkdir(parents=True, exist_ok=True)
            copied.append((destination, relative, existed))
            shutil.copy2(source / relative, destination)
    except Exception:
        for destination, relative, existed in reversed(copied):
            if existed:
                shutil.copy2(backup / relative, destination)
            elif destination.exists():
                destination.unlink()
        raise
    print('Готово. Резервные копии исходных файлов: ' + str(backup))
    print('Миграции и новые зависимости не требуются. Перезапустите CRM и синхронизируйте OpenAI.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--check', action='store_true')
    mode.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    run(args.apply)
