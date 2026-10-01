"""Read-only source database inventory, scoped to one bot.

Only reflected metadata is stored. Neither source rows nor credentials enter a snapshot.
"""
import hashlib
import json
import re
from time import monotonic

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

from .bot_settings import decrypt, encrypt
from .models import Bot, BotDataMapping, BotDatabaseConnection, BotDatabaseSnapshot, now
from .security import audit, csrf, require_admin
from .web import page

router = APIRouter()
ENTITIES = {'customer': 'Клиент', 'order': 'Заказ', 'payment': 'Платёж',
            'product': 'Услуга', 'event': 'Событие'}
FIELDS = ('source_id', 'customer_id', 'telegram_user_id', 'name', 'phone', 'email',
          'amount', 'currency', 'status', 'occurred_at')
IDENTIFIER = re.compile(r'^[A-Za-z_][A-Za-z0-9_]{0,119}$')


def normalize_url(raw: str, dialect: str):
    """Accept URL strings only; never put credentials in logs or responses."""
    if dialect not in ('neon', 'postgresql', 'mysql') or len(raw) > 4096:
        raise HTTPException(422, 'Выберите поддерживаемую СУБД и укажите адрес')
    try:
        url = make_url(raw)
    except (ValueError, TypeError):
        raise HTTPException(422, 'Неверный формат подключения') from None
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
    value = raw.strip() or ('public' if dialect != 'mysql' else '')
    if value and not IDENTIFIER.fullmatch(value):
        raise HTTPException(422, 'Неверное имя схемы')
    return value


def inspect_source(raw_url: str, dialect: str, schema: str):
    """Open one bounded connection and return metadata without querying source rows."""
    url = make_url(raw_url)
    engine = create_engine(url, poolclass=NullPool, pool_pre_ping=True,
                           connect_args={'connect_timeout': 5})
    started = monotonic()
    try:
        with engine.connect() as connection:
            if dialect != 'mysql':
                connection.execute(text('SET TRANSACTION READ ONLY'))
                connection.execute(text("SET LOCAL statement_timeout = '8000ms'"))
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
    engine = create_engine(make_url(raw_url), poolclass=NullPool,
                           connect_args={'connect_timeout': 5})
    try:
        with engine.connect() as connection:
            if dialect != 'mysql':
                connection.execute(text('SET TRANSACTION READ ONLY'))
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
        return page(request, 'bot_database.html', db, bot=bot, connections=rows,
                    snapshots=snapshots, mappings=mappings, history=history, entities=ENTITIES, fields=FIELDS,
                    result=result[:100], encryption_ready=bool(request.app.state.settings.encryption_key))


@router.post('/bots/{bot_id}/database')
def save_connection(request: Request, bot_id: int, token: str = Form(), label: str = Form(),
                    dialect: str = Form(), url: str = Form(''), schema_name: str = Form('')):
    with request.app.state.session_factory() as db:
        admin = require_admin(request, db, owner=True); csrf(request, token)
        if not db.get(Bot, bot_id):
            raise HTTPException(404)
        label = label.strip()
        if not label or len(label) > 120 or dialect not in ('neon', 'postgresql', 'mysql'):
            raise HTTPException(422, 'Название или тип подключения неверны')
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
        row.schema_name, row.updated_by = validate_schema_name(schema_name, dialect), admin.id
        row.last_status, row.last_error = 'not_checked', ''
        audit(db, 'bot.database.save', f'{bot_id}:{label}', admin.id, 'Credentials hidden')
        db.commit()
        return RedirectResponse(f'/bots/{bot_id}/database', status_code=303)


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
