"""Install OmniFactory CRM 1.14 Neon source workspace from an extracted archive."""
import argparse
import json
import py_compile
import shutil
from datetime import datetime
from pathlib import Path

FILES = (
    '.env.example',
    'crm/app.py', 'crm/config.py', 'crm/models.py', 'crm/bot_databases.py',
    'crm/neon_inventory.py', 'crm/templates/base.html',
    'crm/templates/bot_database.html', 'crm/templates/neon.html',
    'crm/templates/source_preview.html',
    'alembic/versions/0015_neon_source_workspace.py',
    'tests/test_bot_operations.py', 'tests/test_neon_source_workspace.py',
)
NEW_FILES = {'crm/neon_inventory.py', 'crm/templates/neon.html',
             'crm/templates/source_preview.html',
             'alembic/versions/0015_neon_source_workspace.py',
             'tests/test_neon_source_workspace.py'}


def check(source, target):
    required = ('crm/app.py', 'crm/templates/openai_keys.html',
                'alembic/versions/0014_openai_observability.py')
    if source == target or any(not (target / name).is_file() for name in required):
        raise SystemExit('Запустите из корня установленной OmniFactory CRM 1.13.2; архив распакуйте отдельно.')
    if (target / 'alembic/versions/0015_neon_source_workspace.py').exists():
        raise SystemExit('Миграция 0015 уже есть. Сверьте установленную версию до повторного применения.')
    for name in FILES:
        original, destination = source / name, target / name
        if not original.is_file() or original.is_symlink() or destination.is_symlink():
            raise SystemExit('Недоступен файл или символическая ссылка: ' + name)
        if name not in NEW_FILES and not destination.is_file():
            raise SystemExit('Нет целевого файла: ' + name)
        if original.suffix == '.py':
            py_compile.compile(str(original), doraise=True)
    print('Проверено файлов:', len(FILES))


def apply(source, target):
    backup = target / '.crm_update_backups' / ('neon_1_14_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
    completed = []
    try:
        for name in FILES:
            destination = target / name
            existed = destination.is_file()
            if existed:
                saved = backup / name
                saved.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(destination, saved)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / name, destination)
            completed.append((name, existed))
        (backup / 'manifest.json').write_text(json.dumps(completed, ensure_ascii=False), encoding='utf-8')
    except Exception:
        for name, existed in reversed(completed):
            destination = target / name
            if existed:
                shutil.copy2(backup / name, destination)
            else:
                destination.unlink(missing_ok=True)
        raise
    print('Файлы установлены. Резервная копия кода:', backup)
    print('Теперь выполните: .\\.venv312\\Scripts\\python.exe -m alembic upgrade head')


def rollback(target, backup):
    manifest = backup / 'manifest.json'
    if not manifest.is_file() or backup.parent.resolve() != (target / '.crm_update_backups').resolve():
        raise SystemExit('Укажите каталог резервной копии от этого установщика.')
    for name, existed in reversed(json.loads(manifest.read_text(encoding='utf-8'))):
        if name == 'alembic/versions/0015_neon_source_workspace.py':
            continue  # Keep Alembic's chain intact; no implicit data deletion.
        destination = target / name
        if destination.is_symlink():
            raise SystemExit('Отказ: символическая ссылка ' + name)
        if existed:
            shutil.copy2(backup / name, destination)
        else:
            destination.unlink(missing_ok=True)
    print('Код восстановлен. Миграция 0015 сохранена для целостности Alembic.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--check', action='store_true')
    mode.add_argument('--apply', action='store_true')
    mode.add_argument('--rollback', type=Path, metavar='BACKUP_DIRECTORY')
    args = parser.parse_args()
    source, target = Path(__file__).resolve().parent, Path.cwd().resolve()
    if args.rollback:
        rollback(target, args.rollback.resolve())
    else:
        check(source, target)
        if args.apply:
            apply(source, target)
        else:
            print('Проверка успешна. Запустите --apply.')
