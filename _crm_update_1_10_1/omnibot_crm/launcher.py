"""OmniFactory CRM operations console. Python 3.12+, no extra dependencies.

    python launcher.py diagnose [--deep] [--offline]
    python launcher.py start
    python launcher.py web [--reload]
    python launcher.py worker
    python launcher.py migrate

The launcher never generates or prints credentials and never applies migrations
implicitly. Run it with the Python interpreter belonging to this CRM project.
"""
from __future__ import annotations

import argparse
import ast
import importlib.metadata
import importlib.util
import json
import os
import platform
import shutil
import signal
import socket
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
REPORT = ROOT / 'storage' / 'diagnostics' / 'last_report.json'
REQUIRED = ('crm/app.py', 'crm/worker.py', 'crm/config.py', 'crm/models.py',
            'alembic.ini', 'alembic/env.py', 'requirements.txt', '.env.example')
PACKAGES = {'FastAPI': 'fastapi', 'uvicorn': 'uvicorn', 'SQLAlchemy': 'sqlalchemy',
            'psycopg': 'psycopg', 'PyMySQL': 'pymysql', 'alembic': 'alembic', 'Jinja2': 'jinja2',
            'python-multipart': 'multipart', 'itsdangerous': 'itsdangerous',
            'httpx': 'httpx', 'python-dotenv': 'dotenv',
            'cryptography': 'cryptography', 'reportlab': 'reportlab',
            'openpyxl': 'openpyxl'}


def has_socks_proxy() -> bool:
    return any(os.environ.get(name, '').lower().startswith(('socks5://', 'socks5h://',
                      'socks4://', 'socks4a://')) for name in
               ('ALL_PROXY', 'all_proxy', 'HTTP_PROXY', 'http_proxy', 'HTTPS_PROXY', 'https_proxy'))


class Status(str, Enum):
    PASS = 'PASS'
    WARN = 'WARN'
    FAIL = 'FAIL'
    SKIP = 'SKIP'
    INFO = 'INFO'


@dataclass
class Check:
    section: str
    name: str
    status: Status
    detail: str
    critical: bool = False
    remedy: str = ''
    duration_ms: float = 0.0
    metrics: dict[str, int | str | bool] = field(default_factory=dict)


def result(section: str, name: str, status: Status, detail: str, started: float,
           *, critical: bool = False, remedy: str = '', **metrics: int | str | bool) -> Check:
    return Check(section, name, status, detail, critical, remedy,
                 round((time.perf_counter() - started) * 1000, 1), metrics)


class Console:
    def __init__(self, no_color: bool = False):
        self.color = sys.stdout.isatty() and not no_color and 'NO_COLOR' not in os.environ
        if hasattr(sys.stdout, 'reconfigure'):
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        if os.name == 'nt' and self.color:
            os.system('')

    def paint(self, text: str, color: str) -> str:
        return f'\033[{color}m{text}\033[0m' if self.color else text

    def banner(self, mode: str, deep: bool, offline: bool):
        width = min(112, max(72, shutil.get_terminal_size((94, 24)).columns))
        print(self.paint('═' * width, '96'))
        print(self.paint('OMNIFACTORY CRM  •  OPERATIONS CONTROL CENTER'.center(width), '95;1'))
        print(self.paint(f'{mode.upper()}  •  {"DEEP" if deep else "STANDARD"}  •  '
                         f'{"OFFLINE" if offline else "DATABASE"}'.center(width), '90'))
        print(self.paint('═' * width, '96'))

    def check(self, item: Check):
        icon, color = {Status.PASS: ('✅', '92'), Status.WARN: ('⚠️ ', '93'),
                       Status.FAIL: ('❌', '91'), Status.SKIP: ('⏭️ ', '90'),
                       Status.INFO: ('ℹ️ ', '96')}[item.status]
        print(f'{self.paint(icon + " " + item.status.value.ljust(4), color)}  '
              f'{item.name:<30} {item.detail}')
        if item.status == Status.FAIL and item.remedy:
            print(self.paint('       → ' + item.remedy, '93'))

    def summary(self, checks: list[Check], elapsed: float):
        counted = [r for r in checks if r.status in (Status.PASS, Status.WARN, Status.FAIL)]
        weight = sum(2 if r.critical else 1 for r in counted)
        health = round(sum({Status.PASS: 100, Status.WARN: 60, Status.FAIL: 0}[r.status]
                           * (2 if r.critical else 1) for r in counted) / weight) if weight else 0
        failed = [r.name for r in checks if r.critical and r.status == Status.FAIL]
        print(self.paint('═' * 72, '96'))
        print(self.paint(f'ЗДОРОВЬЕ СИСТЕМЫ {health}%  •  '
                         f'{"ТРЕБУЕТСЯ ДЕЙСТВИЕ" if failed else "ГОТОВО К ЗАПУСКУ"}',
                         '91;1' if failed else '92;1'))
        print(f'Проверок: {len(checks)} · Время: {elapsed:.1f} с · Отчёт: {REPORT.relative_to(ROOT)}')
        return not failed, health


def check_environment() -> list[Check]:
    checks = []
    t = time.perf_counter()
    checks.append(result('SYSTEM', 'Python runtime',
        Status.PASS if sys.version_info >= (3, 12) else Status.FAIL,
        f'{platform.python_version()} · {sys.executable}', t, critical=True,
        remedy='Установите Python 3.12 и зависимости из requirements.txt.'))
    t = time.perf_counter()
    in_venv = sys.prefix != sys.base_prefix
    checks.append(result('SYSTEM', 'Virtual environment',
        Status.PASS if in_venv else Status.WARN,
        f'{"активировано" if in_venv else "не активировано"} · {sys.prefix}', t,
        remedy='Используйте .\\.venv312\\Scripts\\python.exe на Windows.'))
    t = time.perf_counter()
    missing = [p for p in REQUIRED if not (ROOT / p).is_file()]
    checks.append(result('PROJECT', 'Project structure',
        Status.PASS if not missing else Status.FAIL,
        f'{len(REQUIRED)-len(missing)}/{len(REQUIRED)} обязательных файлов'
        + (f' · отсутствуют: {", ".join(missing)}' if missing else ''), t,
        critical=True, remedy='Распакуйте полный архив CRM.'))
    t = time.perf_counter()
    packages = dict(PACKAGES)
    if has_socks_proxy(): packages['SOCKS proxy support'] = 'socksio'
    missing_packages = [name for name, module in packages.items()
                        if importlib.util.find_spec(module) is None]
    checks.append(result('PYTHON', 'Dependencies',
        Status.PASS if not missing_packages else Status.FAIL,
        f'{len(packages)-len(missing_packages)}/{len(packages)} пакетов'
        + (f' · нет: {", ".join(missing_packages)}' if missing_packages else ''), t,
        critical=True, remedy='python -m pip install -r requirements.txt'))
    t = time.perf_counter()
    files = [p for part in ('crm', 'alembic', 'integrations')
             for p in (ROOT / part).rglob('*.py')] + [ROOT / 'launcher.py']
    broken = []
    for path in files:
        try:
            ast.parse(path.read_text(encoding='utf-8'), str(path))
        except (SyntaxError, UnicodeError, OSError):
            broken.append(str(path.relative_to(ROOT)))
    checks.append(result('PYTHON', 'Python syntax',
        Status.PASS if not broken else Status.FAIL,
        f'{len(files)} файлов' + (f' · ошибка: {", ".join(broken[:4])}' if broken else ''),
        t, critical=True, remedy='Исправьте указанные файлы.'))
    t = time.perf_counter()
    checks.append(result('CONFIG', '.env file',
        Status.PASS if (ROOT / '.env').is_file() else Status.FAIL,
        'найден' if (ROOT / '.env').is_file() else 'не найден', t, critical=True,
        remedy='Скопируйте .env.example в .env и заполните SESSION_SECRET.'))
    return checks


def load_config() -> tuple[Any | None, Check]:
    t = time.perf_counter()
    try:
        from crm.config import settings
        config = settings()
        return config, result('CONFIG', 'Settings validation', Status.PASS,
                              'SESSION_SECRET и DATABASE_URL корректны; Fernet проверен при наличии', t, critical=True)
    except Exception as exc:
        # Never print exception text: it may contain a URL or a credential.
        return None, result('CONFIG', 'Settings validation', Status.FAIL,
                            f'{type(exc).__name__}: проверьте конфигурацию без вывода секретов',
                            t, critical=True, remedy='Проверьте .env: SESSION_SECRET, DATABASE_URL, CRM_ENCRYPTION_KEY.')


def check_config(config: Any | None) -> list[Check]:
    if config is None:
        return []
    checks = []
    t = time.perf_counter()
    try:
        from sqlalchemy.engine import make_url
        url = make_url(config.database_url)
        driver = url.drivername
        label = ('SQLite · локальная разработка' if driver.startswith('sqlite')
                 else f'PostgreSQL · {url.host or "?"} · TLS={dict(url.query).get("sslmode", "не указан")}')
        good = driver.startswith('sqlite') or (driver == 'postgresql+psycopg'
                and bool(url.host) and dict(url.query).get('sslmode') in ('require', 'verify-ca', 'verify-full'))
        checks.append(result('CONFIG', 'Database target',
            Status.PASS if good else Status.WARN, label, t,
            remedy='Для Neon укажите sslmode=require или verify-full.'))
    except Exception:
        checks.append(result('CONFIG', 'Database target', Status.FAIL,
            'некорректный DATABASE_URL', t, critical=True, remedy='Проверьте формат DATABASE_URL.'))
    t = time.perf_counter()
    checks.append(result('CONFIG', 'Secret encryption',
        Status.PASS if config.encryption_key else Status.WARN,
        'Fernet ключ задан' if config.encryption_key else 'ключ не задан; сохранение секретов недоступно', t,
        remedy='Сгенерируйте ключ один раз; не меняйте уже применяемый CRM_ENCRYPTION_KEY.'))
    t = time.perf_counter()
    checks.append(result('CONFIG', 'Session cookies',
        Status.PASS if config.cookie_secure else Status.WARN,
        'HTTPS-only' if config.cookie_secure else 'локальный HTTP режим; для HTTPS установите COOKIE_SECURE=true', t))
    t = time.perf_counter()
    checks.append(result('PROJECT', 'Project inventory', Status.INFO,
        f'миграций: {len(list((ROOT/"alembic"/"versions").glob("*.py")))} · '
        f'тестов: {len(list((ROOT/"tests").glob("test_*.py")))} · '
        f'шаблонов: {len(list((ROOT/"crm"/"templates").rglob("*.html")))}', t))
    return checks


def local_head() -> tuple[str | None, Check]:
    t = time.perf_counter()
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory
        cfg = Config(str(ROOT / 'alembic.ini'))
        cfg.set_main_option('script_location', str(ROOT / 'alembic'))
        heads = ScriptDirectory.from_config(cfg).get_heads()
        if len(heads) != 1:
            raise ValueError('multiple or missing heads')
        return heads[0], result('DATABASE', 'Migration scripts', Status.PASS,
                                'head=' + heads[0], t, critical=True)
    except ModuleNotFoundError as exc:
        return None, result('DATABASE', 'Migration scripts', Status.FAIL,
                            f'не установлен пакет {exc.name} в текущем Python', t,
                            critical=True,
                            remedy='Запустите launcher через Python 3.12 из .venv312 и установите requirements.txt.')
    except Exception:
        return None, result('DATABASE', 'Migration scripts', Status.FAIL,
                            'цепочка миграций повреждена', t, critical=True,
                            remedy='Проверьте alembic/versions.')


def inspect_database(config: Any | None, head: str | None, offline: bool,
                     timeout: float) -> list[Check]:
    t = time.perf_counter()
    if offline:
        return [result('DATABASE', 'Database connection', Status.SKIP,
                       'пропущено (--offline)', t)]
    if config is None or head is None:
        return [result('DATABASE', 'Database connection', Status.SKIP,
                       'сначала исправьте настройки и миграции', t, critical=True)]
    engine = None
    try:
        from sqlalchemy import create_engine, inspect, select, text, func
        from sqlalchemy.engine import make_url
        from sqlalchemy.orm import Session
        from sqlalchemy.pool import NullPool
        from crm.models import Admin, Bot, BotSetting, BotPaymentProvider
        url = make_url(config.database_url)
        if url.drivername.startswith('sqlite') and url.database not in (None, ':memory:'):
            path = Path(url.database)
            if not path.is_absolute(): path = ROOT / path
            if not path.is_file():
                return [result('DATABASE', 'Database connection', Status.FAIL,
                    'файл SQLite отсутствует', t, critical=True,
                    remedy='Запустите python launcher.py migrate для создания схемы.')]
        kwargs = {'poolclass': NullPool, 'connect_args': {'timeout': timeout}}
        if url.drivername == 'postgresql+psycopg':
            kwargs['connect_args'] = {'connect_timeout': max(1, int(timeout))}
        engine = create_engine(config.database_url, **kwargs)
        with engine.connect() as conn:
            conn.execute(text('SELECT 1'))
            tables = set(inspect(conn).get_table_names())
            revision = conn.execute(text('SELECT version_num FROM alembic_version')).scalar_one_or_none() \
                       if 'alembic_version' in tables else None
            out = [result('DATABASE', 'Database connection', Status.PASS,
                          'SQLite доступна' if url.drivername.startswith('sqlite') else 'PostgreSQL доступен',
                          t, critical=True)]
            t = time.perf_counter()
            same = revision == head
            out.append(result('DATABASE', 'Schema revision',
                Status.PASS if same else Status.FAIL,
                f'база={revision or "не создана"} · код={head}', t, critical=True,
                remedy='Остановите веб и worker, затем запустите python launcher.py migrate.'))
            if same:
                t = time.perf_counter()
                admins = conn.scalar(select(func.count()).select_from(Admin))
                bots = conn.scalar(select(func.count()).select_from(Bot))
                settings_count = conn.scalar(select(func.count()).select_from(BotSetting))
                merchants = conn.scalar(select(func.count()).select_from(BotPaymentProvider)
                                        .where(BotPaymentProvider.enabled.is_(True)))
                out.append(result('CRM', 'Administration inventory',
                    Status.PASS if admins else Status.WARN,
                    f'администраторов: {admins} · ботов: {bots} · параметров: {settings_count} '
                    f'· активных эквайеров: {merchants}', t,
                    remedy='Создайте владельца: python -m crm.cli create-admin --email адрес.'))
                t = time.perf_counter()
                token_ids = set(conn.scalars(select(BotSetting.bot_id)
                                .where(BotSetting.key == 'telegram_token', BotSetting.is_configured.is_(True))))
                ingest_ids = set(conn.scalars(select(BotSetting.bot_id)
                                 .where(BotSetting.key == 'ingest_secret', BotSetting.is_configured.is_(True))))
                provider_counts = dict(conn.execute(select(BotPaymentProvider.bot_id, func.count())
                    .where(BotPaymentProvider.enabled.is_(True))
                    .group_by(BotPaymentProvider.bot_id)).all())
                with Session(conn) as db:
                    for bot in db.scalars(select(Bot).order_by(Bot.id).limit(20)):
                        signed = bot.id in ingest_ids or bool(config.ingest_secrets.get(bot.key))
                        token = bot.id in token_ids or bool(config.bot_tokens.get(bot.key))
                        heartbeat = bot.last_seen_at.strftime('%Y-%m-%d %H:%M UTC') if bot.last_seen_at else 'нет'
                        out.append(result('BOTS', f'Бот {bot.key[:24]}',
                            Status.PASS if signed else Status.WARN,
                            f'подпись={"да" if signed else "нет"} · рассылка={"да" if token else "нет"} '
                            f'· эквайеров={provider_counts.get(bot.id, 0)} · heartbeat={heartbeat}', t,
                            remedy='Создайте ingest_secret в карточке этого бота.'))
                if bots > 20:
                    out.append(result('BOTS', 'Дополнительные боты', Status.INFO,
                                      f'ещё {bots-20}; доступны в разделе «Боты»', t))
            return out
    except Exception as exc:
        return [result('DATABASE', 'Database connection', Status.FAIL,
                       f'{type(exc).__name__}: подключение или схема недоступны; реквизиты скрыты',
                       t, critical=True, remedy='Проверьте DATABASE_URL, сеть и состояние Neon.')]
    finally:
        if engine is not None: engine.dispose()


def deep_checks() -> list[Check]:
    checks = []
    for name, module in (('Pytest', 'pytest'), ('Ruff', 'ruff')):
        t = time.perf_counter()
        if importlib.util.find_spec(module) is None:
            checks.append(result('QUALITY', name, Status.SKIP,
                'пакет не установлен; pytest в requirements-dev.txt, ruff необязателен', t))
            continue
        cmd = [sys.executable, '-m', module] + (['-q'] if module == 'pytest' else ['check', '.'])
        try:
            proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                                  timeout=180, check=False)
            checks.append(result('QUALITY', name,
                Status.PASS if proc.returncode == 0 else Status.WARN,
                f'exit={proc.returncode}; полный вывод не записывается в отчёт', t,
                remedy='Запустите инструмент отдельно для подробностей.'))
        except (OSError, subprocess.TimeoutExpired):
            checks.append(result('QUALITY', name, Status.WARN,
                'проверка недоступна или превысила 180 с', t))
    return checks


def save_report(checks: list[Check], health: int) -> None:
    payload = {'application': 'OmniFactory CRM', 'generated_at': datetime.now(timezone.utc).isoformat(),
               'health_score': health, 'checks': [asdict(c) for c in checks]}
    try:
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    except OSError:
        print('⚠️  Отчёт не записан: каталог диагностики недоступен.')


def diagnostics(console: Console, offline: bool, deep: bool, timeout: float,
                *, for_migration: bool = False) -> bool:
    started = time.perf_counter()
    checks = check_environment()
    config, check = load_config()
    checks.append(check)
    checks.extend(check_config(config))
    head, check = local_head()
    checks.append(check)
    if not for_migration:
        checks.extend(inspect_database(config, head, offline, timeout))
    if deep:
        checks.extend(deep_checks())
    for item in checks:
        console.check(item)
    ready, health = console.summary(checks, time.perf_counter()-started)
    save_report(checks, health)
    return ready


def port_available(host: str, port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind((host, port))
        return True
    except OSError:
        return False


def stop_process(proc: subprocess.Popen, timeout: float = 10.0):
    if proc.poll() is not None: return
    try:
        if os.name == 'nt' and hasattr(signal, 'CTRL_BREAK_EVENT'):
            proc.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            proc.terminate()
        proc.wait(timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def run_processes(mode: str, host: str, port: int, reload: bool, console: Console) -> int:
    if mode in ('start', 'web') and not port_available(host, port):
        print(console.paint(f'❌ Порт {host}:{port} занят. Выберите другой --port.', '91'))
        return 2
    env = os.environ.copy()
    env['PYTHONUNBUFFERED'] = '1'
    commands = []
    if mode in ('start', 'web'):
        web = [sys.executable, '-m', 'uvicorn', 'crm.app:app', '--host', host, '--port', str(port)]
        if reload: web.append('--reload')
        commands.append(('web', web))
        print(console.paint(f'🌐 CRM: http://{host}:{port}/login  ·  таблицы: http://{host}:{port}/m', '96'))
    if mode in ('start', 'worker'):
        commands.append(('worker', [sys.executable, '-m', 'crm.worker']))
        print(console.paint('⚙️  Worker: рассылки, сверка платежей и настроенная синхронизация', '95'))
    print('Нажмите Ctrl+C для остановки. Миграции не выполняются автоматически.\n')
    children = []
    try:
        flags = getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0) if os.name == 'nt' else 0
        for name, cmd in commands:
            children.append((name, subprocess.Popen(cmd, cwd=ROOT, env=env, creationflags=flags)))
        while True:
            for name, proc in children:
                if proc.poll() is not None:
                    print(console.paint(f'⚠️  {name} завершился с кодом {proc.returncode}; останавливаю остальные процессы.', '93'))
                    return proc.returncode or 1
            time.sleep(0.4)
    except KeyboardInterrupt:
        print('\nОстанавливаю процессы…')
        return 130
    except OSError:
        print(console.paint('❌ Не удалось запустить процесс.', '91'))
        return 2
    finally:
        for _, proc in reversed(children): stop_process(proc)


def migrate(console: Console) -> int:
    print(console.paint('🗃️  Применяю Alembic миграции к настроенной базе…', '96'))
    try:
        return subprocess.run([sys.executable, '-m', 'alembic', 'upgrade', 'head'], cwd=ROOT,
                              check=False).returncode
    except OSError:
        print('❌ Не удалось запустить Alembic.')
        return 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='OmniFactory CRM · диагностика и управление процессами')
    parser.add_argument('mode', nargs='?', choices=('diagnose', 'start', 'web', 'worker', 'migrate'),
                        default='diagnose')
    parser.add_argument('--deep', action='store_true', help='Запустить pytest и установленный Ruff')
    parser.add_argument('--offline', action='store_true', help='Диагностика без соединения с базой')
    parser.add_argument('--host', default='127.0.0.1', help='Адрес веб-сервера (по умолчанию локальный)')
    parser.add_argument('--port', type=int, default=8000, help='Порт веб-сервера (по умолчанию 8000)')
    parser.add_argument('--reload', action='store_true', help='Автоперезагрузка в режиме web (разработка)')
    parser.add_argument('--timeout', type=float, default=8.0, help='Тайм-аут соединения с базой, сек')
    parser.add_argument('--no-color', action='store_true', help='Выключить ANSI цвета')
    args = parser.parse_args(argv)
    if args.offline and args.mode != 'diagnose': parser.error('--offline применим только к diagnose')
    if args.reload and args.mode != 'web': parser.error('--reload применим только к web')
    if not 1 <= args.port <= 65535 or not 1 <= args.timeout <= 60:
        parser.error('--port должен быть 1..65535, --timeout должен быть 1..60')
    if args.host not in ('127.0.0.1', '0.0.0.0'):
        parser.error('--host: 127.0.0.1 или 0.0.0.0')
    os.chdir(ROOT)
    console = Console(args.no_color)
    console.banner(args.mode, args.deep, args.offline)
    try:
        ready = diagnostics(console, args.offline, args.deep, args.timeout,
                            for_migration=args.mode == 'migrate')
        if args.mode == 'diagnose': return 0 if ready else 2
        if not ready:
            print(console.paint('⛔ Запуск остановлен: устраните критические ошибки.', '91;1'))
            return 2
        if args.mode == 'migrate': return migrate(console)
        return run_processes(args.mode, args.host, args.port, args.reload, console)
    except KeyboardInterrupt:
        return 130


if __name__ == '__main__':
    raise SystemExit(main())
