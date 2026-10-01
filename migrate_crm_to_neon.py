"""Copy the CRM SQLite database into a freshly migrated, empty Neon database.

Dry run: python migrate_crm_to_neon.py --sqlite-path crm_local.db
Apply:   python migrate_crm_to_neon.py --sqlite-path crm_local.db --apply

Set CRM_NEON_DATABASE_URL in the process environment; never put it in arguments.
Stop the CRM and worker first to avoid changes after the SQLite snapshot.
"""
import argparse
import os
import sqlite3
import tempfile
from pathlib import Path

from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from crm import models  # noqa: F401 - registers every table
from crm.db import Base


def snapshot_sqlite(path):
    if not path.is_file():
        raise ValueError('SQLite file does not exist')
    handle = tempfile.NamedTemporaryFile(prefix='omni_crm_snapshot_', suffix='.sqlite3', delete=False)
    handle.close()
    snapshot = Path(handle.name)
    try:
        with sqlite3.connect(str(path)) as source, sqlite3.connect(str(snapshot)) as destination:
            source.backup(destination)
        return snapshot
    except Exception:
        snapshot.unlink(missing_ok=True)
        raise


def check_revision(connection, revision):
    if not inspect(connection).has_table('alembic_version'):
        raise ValueError('Run Alembic migrations on both databases first')
    rows = connection.execute(text('SELECT version_num FROM alembic_version')).scalars().all()
    if rows != [revision]:
        raise ValueError('Source and destination must both be at migration ' + revision)


def copy_database(source_engine, target_engine, revision, apply=False):
    """Preflight the whole schema and target emptiness before the first write."""
    tables = Base.metadata.sorted_tables
    with source_engine.connect() as source, target_engine.connect() as target:
        check_revision(source, revision)
        check_revision(target, revision)
        source_names = set(inspect(source).get_table_names())
        target_names = set(inspect(target).get_table_names())
        for table in tables:
            if table.name not in source_names or table.name not in target_names:
                raise ValueError('Missing table in source or destination: ' + table.name)
            source_columns = {c['name'] for c in inspect(source).get_columns(table.name)}
            target_columns = {c['name'] for c in inspect(target).get_columns(table.name)}
            expected = set(table.columns.keys())
            if not expected <= source_columns or not expected <= target_columns:
                raise ValueError('Schema columns differ from application models: ' + table.name)
            if target.scalar(select(func.count()).select_from(table)):
                raise ValueError('Destination contains CRM records; refusing to overwrite or merge')
        counts = {table.name: source.scalar(select(func.count()).select_from(table)) for table in tables}
    if not apply:
        return counts
    # Only the target transaction is modified; a failure rolls back inserted rows.
    with source_engine.connect() as source, target_engine.begin() as target:
        for table in tables:
            rows = source.execute(select(table)).mappings()
            for batch in iter(lambda: rows.fetchmany(500), []):
                target.execute(table.insert(), [dict(row) for row in batch])
        if target_engine.dialect.name == 'postgresql':
            for table in tables:
                for pk in table.primary_key.columns:
                    if not pk.autoincrement or pk.type.python_type is not int:
                        continue
                    sequence = target.scalar(text('SELECT pg_get_serial_sequence(:table, :column)'),
                        {'table': table.name, 'column': pk.name})
                    if sequence:
                        maximum = target.scalar(select(func.max(pk)))
                        if maximum is not None:
                            target.execute(text('SELECT setval(CAST(:seq AS regclass), :value, true)'),
                                {'seq': sequence, 'value': maximum})
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sqlite-path', type=Path, default=Path('crm_local.db'))
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    target_url = os.getenv('CRM_NEON_DATABASE_URL', '')
    try:
        destination = make_url(target_url)
    except (ValueError, AttributeError, ArgumentError):
        parser.error('Set CRM_NEON_DATABASE_URL to a valid Postgres URL')
    if (destination.drivername != 'postgresql+psycopg' or
            destination.query.get('sslmode') not in ('require', 'verify-ca', 'verify-full')):
        parser.error('Use postgresql+psycopg:// with sslmode=require (or stronger)')
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    revision = ScriptDirectory.from_config(Config('alembic.ini')).get_current_head()
    snapshot = snapshot_sqlite(args.sqlite_path)
    source_engine = create_engine('sqlite:///' + snapshot.as_posix())
    target_engine = create_engine(target_url, pool_pre_ping=True)
    try:
        counts = copy_database(source_engine, target_engine, revision, apply=args.apply)
        print(('Transferred' if args.apply else 'Dry run') + ': ' +
              ', '.join(f'{name}={amount}' for name, amount in counts.items() if amount))
        if not args.apply:
            print('No data changed. Stop web and worker, then repeat with --apply.')
    except Exception as error:
        raise SystemExit('Migration stopped: ' + type(error).__name__ +
                         (': ' + str(error) if isinstance(error, ValueError) else '')) from None
    finally:
        source_engine.dispose()
        target_engine.dispose()
        snapshot.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
