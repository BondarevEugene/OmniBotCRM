"""Typed per-bot settings with encryption for sensitive values."""
import json
import re
from datetime import date
from decimal import Decimal, InvalidOperation

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException
from sqlalchemy import select

from .models import BotSetting

KEY_PATTERN = re.compile(r'^[a-z][a-z0-9_]{0,79}$')
KINDS = {'text', 'multiline', 'integer', 'number', 'boolean', 'json', 'list', 'choice', 'multi_choice',
         'url', 'email', 'date', 'secret'}


def parse_rules(raw):
    try:
        data = json.loads(raw or '{}')
    except (TypeError, ValueError):
        raise HTTPException(422, 'Invalid field rules') from None
    if not isinstance(data, dict):
        raise HTTPException(422, 'Invalid field rules')
    options = data.get('options', [])
    condition = data.get('visible_if', {})
    if (not isinstance(options, list) or len(options) > 30 or
        any(not isinstance(s, str) or not s or len(s) > 80 for s in options) or
        len(set(options)) != len(options) or not isinstance(condition, dict) or
        (condition and (not KEY_PATTERN.fullmatch(str(condition.get('key', ''))) or
                        not isinstance(condition.get('equals'), str) or len(condition['equals']) > 80)) or
        type(data.get('required', False)) is not bool):
        raise HTTPException(422, 'Invalid field rules')
    for bound in ('min', 'max'):
        if bound in data:
            try:
                if not Decimal(str(data[bound])).is_finite():
                    raise ValueError()
            except (InvalidOperation, ValueError, TypeError):
                raise HTTPException(422, 'Invalid numeric limit') from None
    if 'min' in data and 'max' in data and Decimal(str(data['min'])) > Decimal(str(data['max'])):
        raise HTTPException(422, 'Минимум больше максимума')
    length = data.get('max_length', 64000)
    if type(length) is not int or not 1 <= length <= 64000:
        raise HTTPException(422, 'Invalid length limit')
    return {'options': options, 'visible_if': condition, 'required': data.get('required', False),
            'max_length': length, **{key: data[key] for key in ('min', 'max') if key in data}}


def make_rules(kind, choices='', visible_if_key='', visible_if_value='', required=False,
               min_value='', max_value='', max_length=''):
    options = [s.strip() for s in choices.splitlines() if s.strip()]
    if kind in ('choice', 'multi_choice') and not options:
        raise HTTPException(422, 'Добавьте варианты выбора, по одному на строку')
    data = {'options': options, 'required': bool(required)}
    if visible_if_key or visible_if_value:
        data['visible_if'] = {'key': visible_if_key, 'equals': visible_if_value}
    for key, value in (('min', min_value), ('max', max_value), ('max_length', max_length)):
        if value != '':
            data[key] = int(value) if key == 'max_length' and str(value).isdigit() else value
    return parse_rules(json.dumps(data))


def normalize_setting(key, kind, value, rules=None):
    if not KEY_PATTERN.fullmatch(key) or kind not in KINDS or len(value) > 64000:
        raise HTTPException(422, 'Invalid setting name, type or size')
    if key == 'ingest_secret' and (kind != 'secret' or len(value) < 24):
        raise HTTPException(422, 'The ingest secret needs at least 24 characters')
    if (key in ('telegram_token', 'source_database_url') or key.endswith(('_secret', '_token', '_password', '_api_key', '_private_key'))) and kind != 'secret':
        raise HTTPException(422, 'Sensitive settings must be encrypted')
    rules = parse_rules(json.dumps(rules)) if rules is not None else parse_rules('{}')
    if kind in ('choice', 'multi_choice') and not rules['options']:
        raise HTTPException(422, 'Добавьте варианты выбора')
    if kind == 'number':
        try:
            number = Decimal(value)
            if not number.is_finite():
                raise ValueError()
            value = str(number)
        except (InvalidOperation, ValueError):
            raise HTTPException(422, 'Invalid number') from None
    elif kind == 'integer':
        try:
            value = str(int(value))
        except (TypeError, ValueError):
            raise HTTPException(422, 'Invalid integer') from None
    elif kind == 'boolean':
        if value.lower() not in ('true', 'false'):
            raise HTTPException(422, 'Use true or false')
        value = value.lower()
    elif kind == 'json':
        try:
            json.loads(value)
        except ValueError:
            raise HTTPException(422, 'Invalid JSON') from None
    elif kind == 'list':
        try:
            if not isinstance(json.loads(value), list):
                raise ValueError()
        except ValueError:
            raise HTTPException(422, 'Expected a JSON array') from None
    elif kind == 'url':
        if not value.startswith('https://') or len(value) > 2000:
            raise HTTPException(422, 'Use an HTTPS URL')
    elif kind == 'email':
        if len(value) > 255 or '@' not in value or not value.split('@')[-1].count('.'):
            raise HTTPException(422, 'Invalid email address')
    elif kind == 'date':
        try:
            date.fromisoformat(value)
        except ValueError:
            raise HTTPException(422, 'Use YYYY-MM-DD') from None
    elif kind == 'choice':
        if value not in rules['options']:
            raise HTTPException(422, 'Значение отсутствует среди вариантов')
    elif kind == 'multi_choice':
        try:
            items = json.loads(value)
            if not isinstance(items, list) or any(not isinstance(item, str) or item not in rules['options'] for item in items):
                raise ValueError()
            value = json.dumps(list(dict.fromkeys(items)), ensure_ascii=False)
        except ValueError:
            raise HTTPException(422, 'Нужен список выбранных вариантов') from None
    if len(value) > rules['max_length']:
        raise HTTPException(422, 'Превышена допустимая длина поля')
    if kind in ('integer', 'number'):
        number = Decimal(value)
        if ('min' in rules and number < Decimal(str(rules['min']))) or ('max' in rules and number > Decimal(str(rules['max']))):
            raise HTTPException(422, 'Число вне допустимого диапазона')
    return value


def encrypt(value, key):
    if not key:
        raise HTTPException(409, 'Set CRM_ENCRYPTION_KEY before saving secret settings')
    return Fernet(key.encode()).encrypt(value.encode()).decode()


def decrypt(value, key):
    if not key:
        raise RuntimeError('CRM_ENCRYPTION_KEY is missing')
    try:
        return Fernet(key.encode()).decrypt(value.encode()).decode()
    except InvalidToken:
        raise RuntimeError('The configured encryption key cannot decrypt stored bot settings') from None


def setting_value(db, bot_id, name, config):
    row = db.scalar(select(BotSetting).where(BotSetting.bot_id == bot_id, BotSetting.key == name))
    if row is None or not row.is_configured:
        return None
    return decrypt(row.value, config.encryption_key) if row.kind == 'secret' else row.value


def runtime_settings(db, bot_id, config):
    """Return bot-owned settings only to an authenticated bot process."""
    rows = db.scalars(select(BotSetting).where(BotSetting.bot_id == bot_id,
                                               BotSetting.is_configured.is_(True))).all()
    def unpack(row):
        if row.kind == 'secret':
            return decrypt(row.value, config.encryption_key)
        if row.kind == 'boolean':
            return row.value == 'true'
        if row.kind == 'number':
            return float(row.value)
        if row.kind == 'integer':
            return int(row.value)
        if row.kind in ('json', 'list', 'multi_choice'):
            return json.loads(row.value)
        return row.value
    return {r.key: unpack(r) for r in rows if r.key not in ('ingest_secret', 'source_database_url')}
