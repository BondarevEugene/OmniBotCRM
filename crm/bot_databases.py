"""Read-only source database inventory, scoped to one bot.

Only reflected metadata is stored. Neither source rows nor credentials enter a snapshot.
"""
import hashlib
import json
import re
import sqlite3
from pathlib import Path
from time import monotonic
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import MetaData, Table, create_engine, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

from .bot_settings import decrypt, encrypt
from .models import (Bot, BotDataMapping, BotDatabaseConnection, BotDatabaseSnapshot,
                     Customer, Identity, Order, Payment, Product, Project, SourceRecordLink, now)
from .security import audit, csrf, require_admin
from .web import page

router = APIRouter()
ENTITIES = {'customer': 'Клиент', 'order': 'Заказ', 'payment': 'Платёж',
            'product': 'Услуга', 'event': 'Событие'}
FIELDS = ('source_id', 'customer_id', 'telegram_user_id', 'name', 'phone', 'email',
          'title', 'description', 'amount', 'currency', 'status', 'occurred_at')
IDENTIFIER = re.compile(r'^[A-Za-z_][A-Za-z0-9_]{0,119}$')


def normalize_url(raw: str, dialect: str):
    """Accept URL strings only; never put credentials in logs or responses."""
    if dialect not in ('neon', 'postgresql', 'mysql', 'sqlite') or len(raw) > 4096:
        raise HTTPException(422, 'Выберите поддерживаемую СУБД и укажите адрес')
    try:
        url = make_url(raw)
    except (ValueError, TypeError):
        raise HTTPException(422, 'Неверный формат подключения') from None
    if dialect == 'sqlite':
        if url.get_backend_name() != 'sqlite' or url.query or not url.database:
            raise HTTPException(422, 'SQLite: укажите абсолютный путь sqlite:///...')
        path = Path(url.database).resolve(strict=False)
        if not path.is_file() or path.suffix.lower() not in ('.db', '.sqlite', '.sqlite3'):
            raise HTTPException(422, 'Локальный файл SQLite не найден на сервере CRM')
        return f'sqlite:///{path.as_posix()}'
    if not url.host or not url.database or not url.username or not url.password:
        raise HTTPException(422, 'Укажите сервер, базу, пользователя и пароль')
    if dialect == 'mysql':
        if url.get_backend_name() != 'mysql':
            raise HTTPException(422, 'Для MySQL требуется mysql+pymysql://')
        if not url.query.get('ssl_ca'):
            raise HTTPException(422, 'Укажите доверенный SSL CA сертификат для MySQL')
        return url.set(drivername='mysql+pymysql').render_as_string(hide_password=False)
    if url.get_backend_name() != 'postgresql':
        raise HTTPException(422, 'Для Neon/PostgreSQL требуется postgresql://')
    sslmode = url.query.get('sslmode', 'require')
    if sslmode not in ('require', 'verify-ca', 'verify-full'):
        raise HTTPException(422, 'Для PostgreSQL используйте sslmode=require или строже')
    return url.set(drivername='postgresql+psycopg').update_query_dict(
        {'sslmode': sslmode}).render_as_string(hide_password=False)


def validate_schema_name(raw: str, dialect: str):
    value = raw.strip() or ('public' if dialect in ('neon', 'postgresql') else '')
    if value and not IDENTIFIER.fullmatch(value):
        raise HTTPException(422, 'Неверное имя схемы')
    return value


def source_engine(raw_url: str, dialect: str):
    if dialect == 'sqlite':
        path = make_url(raw_url).database
        return create_engine('sqlite://', poolclass=NullPool, creator=lambda: sqlite3.connect(
            Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=5))
    return create_engine(make_url(raw_url), poolclass=NullPool, pool_pre_ping=True,
                         connect_args={'connect_timeout': 5})


def readonly(connection, dialect):
    if dialect in ('neon', 'postgresql'):
        connection.execute(text('SET TRANSACTION READ ONLY'))
        connection.execute(text("SET LOCAL statement_timeout = '8000ms'"))
    elif dialect == 'sqlite':
        connection.execute(text('PRAGMA query_only = ON'))


def inspect_source(raw_url: str, dialect: str, schema: str):
    """Open one bounded connection and return metadata without querying source rows."""
    url = make_url(raw_url)
    engine = source_engine(raw_url, dialect)
    started = monotonic()
    try:
        with engine.connect() as connection:
            readonly(connection, dialect)
            connection.execute(text('SELECT 1'))
            latency = round((monotonic() - started) * 1000)
            inspector = inspect(connection)
            names = sorted(inspector.get_table_names(schema=schema or None))
            if len(names) > 150:
                raise ValueError('too_many_tables')
            tables = []
            for name in names:
                columns = inspector.get_columns(name, schema=schema or None)
                if len(columns) > 100:
                    raise ValueError('too_many_columns')
                foreign_keys = inspector.get_foreign_keys(name, schema=schema or None)
                indexes = inspector.get_indexes(name, schema=schema or None)
                pk = inspector.get_pk_constraint(name, schema=schema or None)
                tables.append({'name': name,
                    'columns': [{'name': c['name'], 'type': str(c['type'])[:100],
                                 'nullable': bool(c['nullable'])} for c in columns],
                    'primary_key': pk.get('constrained_columns') or [],
                    'foreign_keys': [{'columns': f.get('constrained_columns') or [],
                                      'target': f.get('referred_table') or '',
                                      'target_columns': f.get('referred_columns') or []}
                                     for f in foreign_keys],
                    'indexes': [{'name': i['name'], 'columns': i.get('column_names') or [],
                                 'unique': bool(i.get('unique'))} for i in indexes]})
            return {'tables': tables, 'latency_ms': latency, 'schema': schema,
                    'database': url.database, 'dialect': dialect}
    finally:
        engine.dispose()


def probe_source(raw_url: str, dialect: str):
    """A low-cost availability check for the worker, with no table reads."""
    engine = source_engine(raw_url, dialect)
    try:
        with engine.connect() as connection:
            readonly(connection, dialect)
            connection.execute(text('SELECT 1'))
    finally:
        engine.dispose()


def monitor_connections(factory, config):
    """Check at most 50 least-recently checked sources per worker interval."""
    with factory() as db:
        rows = db.scalars(select(BotDatabaseConnection).order_by(
            BotDatabaseConnection.last_checked_at.asc(), BotDatabaseConnection.id).limit(50)).all()
        for row in rows:
            try:
                probe_source(decrypt(row.encrypted_url, config.encryption_key), row.dialect)
                snapshot_id = db.scalar(select(BotDatabaseSnapshot.id).where(
                    BotDatabaseSnapshot.connection_id == row.id).limit(1))
                row.last_status = 'available' if snapshot_id else 'reachable'
                row.last_error = ''
            except Exception:
                row.last_status = 'failed'
                row.last_error = 'Источник недоступен'
            row.last_checked_at = now()
        db.commit()


def source_connection(db, bot_id, connection_id):
    row = db.get(BotDatabaseConnection, connection_id)
    if not row or row.bot_id != bot_id:
        raise HTTPException(404)
    return row


def source_rows(raw_url, dialect, schema, table_name, columns, limit=25, offset=0):
    """Only a reflected table and requested columns; no user-supplied SQL fragments."""
    if not 1 <= limit <= 200 or not 0 <= offset <= 100000:
        raise ValueError('Invalid batch')
    engine = source_engine(raw_url, dialect)
    try:
        with engine.connect() as connection:
            readonly(connection, dialect)
            table = Table(table_name, MetaData(), schema=schema or None, autoload_with=connection)
            names = [name for name in columns if name in table.c]
            if not names or len(names) != len(columns):
                raise ValueError('Unknown source column')
            query = select(*(table.c[name] for name in names)).order_by(table.c[names[0]]).limit(limit).offset(offset)
            return [dict(row._mapping) for row in connection.execute(query)]
    finally:
        engine.dispose()


def latest_tables(db, connection_id):
    snapshot = db.scalar(select(BotDatabaseSnapshot).where(
        BotDatabaseSnapshot.connection_id == connection_id).order_by(BotDatabaseSnapshot.id.desc()).limit(1))
    return {t['name']: t for t in json.loads(snapshot.schema_json)['tables']} if snapshot else {}


def safe_cell(value, name):
    if any(word in name.lower() for word in ('password', 'secret', 'token', 'key', 'credential', 'hash', 'auth')):
        return '••••'
    return str(value if value is not None else '')[:250]


def imported_money(value):
    try:
        result = Decimal(str(value))
        if not result.is_finite() or result < 0 or result > Decimal('999999999999.99'):
            raise ValueError()
        return result.quantize(Decimal('0.01'))
    except (ValueError, InvalidOperation, TypeError):
        raise ValueError('Invalid monetary value') from None


def imported_currency(value):
    code = str(value or 'UAH').upper()
    if not re.fullmatch(r'[A-Z]{3}', code):
        raise ValueError('Invalid currency')
    return code


def import_mapping(db, source, mapping, rows):
    """Idempotent per source; payments are never promoted to paid by DB imports."""
    columns = json.loads(mapping.columns_json)
    total = 0
    for raw in rows:
        item = {field: raw.get(column) for field, column in columns.items()}
        source_id = str(item['source_id'] if item['source_id'] is not None else '')
        if not source_id or len(source_id) > 90:
            raise ValueError('Invalid source ID')
        link = db.scalar(select(SourceRecordLink).where(
            SourceRecordLink.connection_id == source.id, SourceRecordLink.entity == mapping.entity,
            SourceRecordLink.source_id == source_id))
        target = None
        if link:
            models = {'customer': Customer, 'order': Order, 'product': Product, 'payment': Payment}
            target = db.get(models[mapping.entity], link.crm_id)
            if not target:
                raise ValueError('Linked CRM record no longer exists')
        if mapping.entity == 'customer':
            telegram = str(item.get('telegram_user_id') or '')[:100]
            if not target and telegram:
                identity = db.scalar(select(Identity).where(Identity.bot_id == source.bot_id,
                                                            Identity.external_user_id == telegram))
                if identity:
                    target = db.get(Customer, identity.customer_id)
            if not target:
                target = Customer(source=f'database:{source.id}')
                db.add(target)
                db.flush()
            for field, length in [('name', 200), ('email', 255), ('phone', 40)]:
                if field in columns and item[field] is not None:
                    setattr(target, field, str(item[field])[:length] or ('' if field == 'name' else None))
            if telegram:
                existing_identity = db.scalar(select(Identity).where(Identity.bot_id == source.bot_id,
                    Identity.external_user_id == telegram))
                if existing_identity and existing_identity.customer_id != target.id:
                    raise ValueError('Telegram identity belongs to another customer')
                if not existing_identity:
                    db.add(Identity(bot_id=source.bot_id, customer_id=target.id, external_user_id=telegram))
        else:
            customer = None
            if mapping.entity in ('order', 'payment'):
                customer_source_id = str(item.get('customer_id') or '')
                customer_link = db.scalar(select(SourceRecordLink).where(
                    SourceRecordLink.connection_id == source.id, SourceRecordLink.entity == 'customer',
                    SourceRecordLink.source_id == customer_source_id))
                if not customer_link:
                    raise ValueError('Import mapped customers before orders or payments')
                customer = db.get(Customer, customer_link.crm_id)
                if not customer:
                    raise ValueError('Missing CRM customer')
            if mapping.entity == 'product':
                if not target:
                    target = Product(bot_id=source.bot_id, sku=f'db:{source.id}:{source_id}')
                    db.add(target)
                target.title = str(item.get('title') or f'Услуга {source_id}')[:200]
                target.description = str(item.get('description') or '')[:2000]
                target.price = imported_money(item.get('amount'))
                target.currency = imported_currency(item.get('currency'))
            elif mapping.entity == 'order':
                if not target:
                    target = Order(bot_id=source.bot_id, external_id=f'db:{source.id}:{source_id}')
                    db.add(target)
                target.customer_id = customer.id
                target.title = str(item.get('title') or f'Заказ {source_id}')[:200]
                target.amount = imported_money(item.get('amount'))
                target.currency = imported_currency(item.get('currency'))
                target.status = str(item.get('status') or 'new')[:30]
            elif mapping.entity == 'payment':
                if not target:
                    target = Payment(bot_id=source.bot_id, external_id=f'db:{source.id}:{source_id}',
                                     provider='database_unverified', status='pending')
                    db.add(target)
                target.customer_id = customer.id
                target.amount = imported_money(item.get('amount'))
                target.currency = imported_currency(item.get('currency'))
                # A table flag is not a payment processor verification.
                if target.provider == 'database_unverified':
                    target.status = 'pending'
        db.flush()
        if not link:
            db.add(SourceRecordLink(connection_id=source.id, entity=mapping.entity,
                                    source_id=source_id, crm_id=target.id))
        total += 1
    return total


@router.get('/bots/{bot_id}/database')
def databases(request: Request, bot_id: int, result: str = ''):
    with request.app.state.session_factory() as db:
        require_admin(request, db)
        bot = db.get(Bot, bot_id)
        if not bot:
            raise HTTPException(404)
        rows = db.scalars(select(BotDatabaseConnection).where(
            BotDatabaseConnection.bot_id == bot_id).order_by(BotDatabaseConnection.id)).all()
        snapshots = {}
        mappings = {}
        history = {}
        for row in rows:
            past = db.scalars(select(BotDatabaseSnapshot).where(
                BotDatabaseSnapshot.connection_id == row.id).order_by(
                BotDatabaseSnapshot.id.desc()).limit(8)).all()
            snapshot = past[0] if past else None
            history[row.id] = [{'at': p.created_at, 'tables': p.table_count,
                                'latency_ms': p.latency_ms, 'hash': p.schema_hash,
                                'changed': p.schema_hash != past[i+1].schema_hash if i+1 < len(past) else False}
                               for i, p in enumerate(past)]
            snapshots[row.id] = {'id': snapshot.id, 'at': snapshot.created_at,
                'hash': snapshot.schema_hash, 'latency_ms': snapshot.latency_ms,
                'tables': json.loads(snapshot.schema_json)['tables']} if snapshot else None
            mappings[row.id] = db.scalars(select(BotDataMapping).where(
                BotDataMapping.connection_id == row.id).order_by(BotDataMapping.entity)).all()
        projects = db.scalars(select(Project).order_by(Project.title).limit(300)).all()
        return page(request, 'bot_database.html', db, bot=bot, projects=projects, connections=rows,
                    snapshots=snapshots, mappings=mappings, history=history, entities=ENTITIES, fields=FIELDS,
                    result=result[:100], encryption_ready=bool(request.app.state.settings.encryption_key))


@router.post('/bots/{bot_id}/database')
def save_connection(request: Request, bot_id: int, token: str = Form(), label: str = Form(),
                    dialect: str = Form(), url: str = Form(''), schema_name: str = Form(''),
                    project_id: str = Form(''), neon_project_id: str = Form(''),
                    neon_branch_id: str = Form(''), neon_database_name: str = Form('')):
    with request.app.state.session_factory() as db:
        admin = require_admin(request, db, owner=True); csrf(request, token)
        if not db.get(Bot, bot_id):
            raise HTTPException(404)
        label = label.strip()
        if not label or len(label) > 120 or dialect not in ('neon', 'postgresql', 'mysql', 'sqlite'):
            raise HTTPException(422, 'Название или тип подключения неверны')
        if project_id and (not project_id.isdigit() or not db.get(Project, int(project_id))):
            raise HTTPException(422, 'Выберите существующий CRM-проект')
        if any(len(v) > 120 or (v and not re.fullmatch(r'[A-Za-z0-9_-]+', v)) for v in
               (neon_project_id, neon_branch_id, neon_database_name)):
            raise HTTPException(422, 'Проверьте идентификаторы Neon')
        if dialect == 'neon' and neon_database_name and url.strip():
            try:
                database_in_url = make_url(url.strip()).database
            except (ValueError, TypeError):
                database_in_url = None
            if database_in_url != neon_database_name:
                raise HTTPException(422, 'База в SQL URL не совпадает с указанной базой Neon')
        if dialect == 'neon' and request.app.state.settings.neon_api_key and neon_project_id:
            if not neon_branch_id or not neon_database_name:
                raise HTTPException(422, 'Укажите проект, ветку и базу Neon вместе')
            from .neon_inventory import neon_get, project_catalog
            config = request.app.state.settings
            try:
                project_ids = {p['id'] for p in project_catalog(config.neon_api_key, config.neon_org_id)}
                branches = neon_get(config.neon_api_key, f'/projects/{neon_project_id}/branches').get('branches') or []
                databases = neon_get(config.neon_api_key,
                    f'/projects/{neon_project_id}/branches/{neon_branch_id}/databases').get('databases') or []
            except Exception:
                raise HTTPException(503, 'Не удалось проверить проект в Neon API') from None
            if neon_project_id not in project_ids or neon_branch_id not in {b['id'] for b in branches} or (
                    neon_database_name not in {database['name'] for database in databases}):
                raise HTTPException(422, 'Такой базы нет в выбранной ветке проекта Neon')
        row = db.scalar(select(BotDatabaseConnection).where(
            BotDatabaseConnection.bot_id == bot_id, BotDatabaseConnection.label == label))
        if not url.strip() and not row:
            raise HTTPException(422, 'Укажите адрес подключения')
        if row and row.dialect != dialect and not url.strip():
            raise HTTPException(422, 'При смене СУБД укажите новый адрес')
        encrypted_url = encrypt(normalize_url(url.strip(), dialect),
                                request.app.state.settings.encryption_key) if url.strip() else row.encrypted_url
        if not row:
            row = BotDatabaseConnection(bot_id=bot_id, label=label)
            db.add(row)
        row.dialect, row.encrypted_url = dialect, encrypted_url
        row.project_id = int(project_id) if project_id else None
        row.neon_project_id = neon_project_id if dialect == 'neon' else ''
        row.neon_branch_id = neon_branch_id if dialect == 'neon' else ''
        row.neon_database_name = neon_database_name if dialect == 'neon' else ''
        row.schema_name, row.updated_by = validate_schema_name(schema_name, dialect), admin.id
        row.last_status, row.last_error = 'not_checked', ''
        audit(db, 'bot.database.save', f'{bot_id}:{label}', admin.id, 'Credentials hidden')
        db.commit()
        return RedirectResponse(f'/bots/{bot_id}/database', status_code=303)


@router.get('/bots/{bot_id}/database/{connection_id}/preview/{table_name}')
def preview_source(request: Request, bot_id: int, connection_id: int, table_name: str, offset: int = 0):
    with request.app.state.session_factory() as db:
        require_admin(request, db)
        row = source_connection(db, bot_id, connection_id)
        table = latest_tables(db, row.id).get(table_name)
        if not table:
            raise HTTPException(404)
        if not 0 <= offset <= 100000:
            raise HTTPException(422)
        columns = [c['name'] for c in table['columns'][:25]]
        try:
            data = source_rows(decrypt(row.encrypted_url, request.app.state.settings.encryption_key),
                               row.dialect, row.schema_name, table_name, columns, 25, offset)
        except Exception:
            raise HTTPException(503, 'Не удалось прочитать таблицу. Проверьте доступ и структуру.') from None
        cells = [[safe_cell(item.get(name), name) for name in columns] for item in data]
        return page(request, 'source_preview.html', db, bot=db.get(Bot, bot_id), source=row,
                    table=table_name, columns=columns, rows=cells, offset=offset, has_more=len(data) == 25)


@router.post('/bots/{bot_id}/database/{connection_id}/inspect')
def check_connection(request: Request, bot_id: int, connection_id: int, token: str = Form()):
    with request.app.state.session_factory() as db:
        admin = require_admin(request, db, owner=True); csrf(request, token)
        row = source_connection(db, bot_id, connection_id)
        try:
            raw_url = decrypt(row.encrypted_url, request.app.state.settings.encryption_key)
            snapshot = inspect_source(raw_url, row.dialect, row.schema_name)
        except Exception as exc:
            # Driver exceptions can contain hostnames, usernames, passwords, or SQL.
            row.last_status, row.last_error = 'failed', 'Подключение или чтение метаданных не удалось'
            row.last_checked_at = now()
            audit(db, 'bot.database.inspect_failed', f'{bot_id}:{connection_id}', admin.id, type(exc).__name__)
            db.commit()
            return RedirectResponse(f'/bots/{bot_id}/database?result=unavailable', status_code=303)
        payload = json.dumps({'tables': snapshot['tables'], 'schema': snapshot['schema'],
                              'database': snapshot['database'], 'dialect': snapshot['dialect']},
                             ensure_ascii=False, sort_keys=True)
        db.add(BotDatabaseSnapshot(connection_id=row.id, latency_ms=snapshot['latency_ms'],
                                   table_count=len(snapshot['tables']), schema_json=payload,
                                   schema_hash=hashlib.sha256(payload.encode()).hexdigest()))
        row.last_status, row.last_error, row.last_checked_at = 'available', '', now()
        audit(db, 'bot.database.inspect', f'{bot_id}:{connection_id}', admin.id,
              f"tables={len(snapshot['tables'])}; latency_ms={snapshot['latency_ms']}")
        db.commit()
        return RedirectResponse(f'/bots/{bot_id}/database?result=checked', status_code=303)


@router.post('/bots/{bot_id}/database/{connection_id}/mapping')
def save_mapping(request: Request, bot_id: int, connection_id: int, token: str = Form(),
                 entity: str = Form(), table_name: str = Form(), source_id: str = Form(''),
                 customer_id: str = Form(''), telegram_user_id: str = Form(''),
                 name: str = Form(''), phone: str = Form(''), email: str = Form(''),
                 amount: str = Form(''), currency: str = Form(''), status: str = Form(''),
                 occurred_at: str = Form('')):
    with request.app.state.session_factory() as db:
        admin = require_admin(request, db, owner=True); csrf(request, token)
        row = source_connection(db, bot_id, connection_id)
        snapshot = db.scalar(select(BotDatabaseSnapshot).where(
            BotDatabaseSnapshot.connection_id == row.id).order_by(BotDatabaseSnapshot.id.desc()).limit(1))
        if entity not in ENTITIES or not snapshot:
            raise HTTPException(422, 'Сначала проверьте базу и выберите тип данных')
        tables = {t['name']: t for t in json.loads(snapshot.schema_json)['tables']}
        if table_name not in tables:
            raise HTTPException(422, 'Таблица отсутствует в последнем снимке')
        available = {c['name'] for c in tables[table_name]['columns']}
        choices = dict(source_id=source_id, customer_id=customer_id, telegram_user_id=telegram_user_id,
                       name=name, phone=phone, email=email, amount=amount, currency=currency,
                       status=status, occurred_at=occurred_at)
        chosen = {key: value for key, value in choices.items() if value}
        if not source_id or any(value not in available for value in chosen.values()):
            raise HTTPException(422, 'Выберите существующие колонки и идентификатор записи')
        mapping = db.scalar(select(BotDataMapping).where(BotDataMapping.connection_id == row.id,
                                                        BotDataMapping.entity == entity))
        if not mapping:
            mapping = BotDataMapping(connection_id=row.id, entity=entity)
            db.add(mapping)
        mapping.table_name, mapping.columns_json, mapping.updated_by = (
            table_name, json.dumps(chosen, ensure_ascii=False), admin.id)
        audit(db, 'bot.database.mapping', f'{bot_id}:{connection_id}:{entity}', admin.id,
              f'table={table_name}; fields={",".join(chosen)}')
        db.commit()
        return RedirectResponse(f'/bots/{bot_id}/database#connection-{connection_id}', status_code=303)


@router.post('/bots/{bot_id}/database/{connection_id}/sync/{entity}')
def sync_mapping(request: Request, bot_id: int, connection_id: int, entity: str,
                 token: str = Form(), offset: int = Form(0)):
    with request.app.state.session_factory() as db:
        admin = require_admin(request, db, owner=True); csrf(request, token)
        row = source_connection(db, bot_id, connection_id)
        mapping = db.scalar(select(BotDataMapping).where(BotDataMapping.connection_id == row.id,
                                                         BotDataMapping.entity == entity))
        if entity not in ('customer', 'order', 'product', 'payment') or not mapping:
            raise HTTPException(404)
        table = latest_tables(db, row.id).get(mapping.table_name)
        if not table or not 0 <= offset <= 100000:
            raise HTTPException(422, 'Сначала обновите схему источника')
        columns = json.loads(mapping.columns_json)
        available = {c['name'] for c in table['columns']}
        key = columns.get('source_id')
        unique = key in table['primary_key'] and len(table['primary_key']) == 1
        unique |= any(i['unique'] and i['columns'] == [key] for i in table['indexes'])
        required = {'customer': (), 'product': ('amount',),
                    'order': ('customer_id', 'amount'), 'payment': ('customer_id', 'amount')}
        if not unique or any(field not in columns for field in required[entity]) or any(
                value not in available for value in columns.values()):
            raise HTTPException(422, 'Для импорта нужен одиночный уникальный ID и обязательные поля')
        try:
            source_data = source_rows(decrypt(row.encrypted_url, request.app.state.settings.encryption_key),
                                      row.dialect, row.schema_name, mapping.table_name,
                                      list(dict.fromkeys(columns.values())), 200, offset)
            total = import_mapping(db, row, mapping, source_data)
        except Exception as exc:
            db.rollback()
            audit(db, 'bot.database.sync_failed', f'{bot_id}:{connection_id}:{entity}', admin.id,
                  type(exc).__name__)
            db.commit()
            raise HTTPException(422, 'Импорт остановлен: проверьте карту полей и порядок загрузки клиентов.') from None
        audit(db, 'bot.database.sync', f'{bot_id}:{connection_id}:{entity}', admin.id,
              f'offset={offset}; rows={total}')
        db.commit()
        return RedirectResponse(f'/bots/{bot_id}/database?result=imported&rows={total}#connection-{connection_id}',
                                status_code=303)
