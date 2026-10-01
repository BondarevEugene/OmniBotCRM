r"""Install an extracted OmniFactory CRM update over an existing project safely.

Windows PowerShell: .\.venv312\Scripts\python.exe .\_crm_update\omnibot_crm\install_update.py --check
"""
import argparse
import hashlib
import shutil
from datetime import datetime
from pathlib import Path

ALLOWED_DIRS = {'crm', 'alembic', 'integrations', 'tests'}
ALLOWED_FILES = {'requirements.txt', 'requirements-dev.txt', 'README.md', 'FOOD_PORN_SETUP.md', 'FOOD_PORN_REPOSITORY_ANALYSIS.md', 'BOT_ADMINISTRATION.md', 'OMNIFACTORY_1_7_GUIDE.md', 'OMNIFACTORY_1_8_GUIDE.md', 'OMNIFACTORY_1_10_GUIDE.md', 'LAUNCHER_GUIDE.md',
                 '.env.example', '.gitignore', 'Dockerfile', 'compose.yaml', 'alembic.ini', 'install_update.py', 'launcher.py'}


def digest(path):
    return hashlib.sha256(path.read_bytes()).digest()


def run(source, target, apply):
    source, target = source.resolve(), target.resolve()
    if source == target or not (source/'crm'/'app.py').is_file() or not (target/'crm'/'app.py').is_file():
        raise SystemExit('Use an extracted update and run this command inside the existing CRM project.')
    changes = []
    for file in sorted(source.rglob('*')):
        if not file.is_file() or file.is_symlink():
            continue
        rel = file.relative_to(source)
        if rel.parts[0] not in ALLOWED_DIRS and rel.as_posix() not in ALLOWED_FILES:
            continue
        if '__pycache__' in rel.parts or '.pytest_cache' in rel.parts or file.suffix in ('.pyc', '.db'):
            continue
        dest = target/rel
        if dest.is_symlink():
            raise SystemExit(f'Cannot overwrite a symlink: {rel}')
        if dest.is_file() and digest(dest) == digest(file):
            continue
        changes.append((file, dest, rel))
    print(f'Files to install or update: {len(changes)}')
    for _, _, rel in changes:
        print(f'  {rel}')
    if not apply:
        print('Preview only. Run again with --apply to install.')
        return
    if not changes:
        return
    backup = target/'.crm_update_backups'/datetime.now().strftime('%Y%m%d_%H%M%S')
    for file, dest, rel in changes:
        if dest.exists():
            old = backup/rel
            old.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dest, old)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(file, dest)
    print(f'Update complete. Replaced files backed up under {backup.name} when present.')
    print('Next: install requirements, run migrations, restart web and worker.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--check', action='store_true')
    mode.add_argument('--apply', action='store_true')
    parser.add_argument('--target', default='.', help='Existing CRM project (default: current directory)')
    args = parser.parse_args()
    run(Path(__file__).resolve().parent, Path(args.target), args.apply)
