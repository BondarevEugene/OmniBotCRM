"""Immutable encrypted bot releases; edits remain drafts after first publication."""
import hashlib
import json

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select

from .bot_settings import decrypt, encrypt, normalize_setting, parse_rules, runtime_settings
from .models import Bot, BotConfigRelease, BotModule, BotPaymentProvider, BotScenario, BotSetting
from .acquirers import effective_credentials
from .security import audit, csrf, require_admin

router = APIRouter()


def payload_revision(payload):
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


def latest_release(db, bot_id):
    return db.scalar(select(BotConfigRelease).where(BotConfigRelease.bot_id == bot_id)
                     .order_by(BotConfigRelease.version.desc()).limit(1))


def load_release(row, encryption_key):
    return json.loads(decrypt(row.encrypted_payload, encryption_key))


def draft_payload(db, bot_id, config):
    rows = db.scalars(select(BotSetting).where(BotSetting.bot_id == bot_id)
                      .order_by(BotSetting.display_order, BotSetting.id)).all()
    by_key = {row.key: row for row in rows}
    visible = {}
    for row in rows:
        rules = parse_rules(row.schema_json)
        condition = rules['visible_if']
        if condition:
            other = by_key.get(condition['key'])
            if not other or other.key == row.key or other.kind in ('secret', 'json', 'list', 'multi_choice'):
                raise HTTPException(422, f'Проверьте условие видимости поля {row.key}')
            # A one-level condition is easy for operators to predict and test.
            if parse_rules(other.schema_json)['visible_if']:
                raise HTTPException(422, f'Цепочки условий недоступны для {row.key}')
            visible[row.key] = other.is_configured and other.value == condition['equals']
        else:
            visible[row.key] = True
        if visible[row.key] and rules['required'] and (not row.is_configured or
                (row.kind != 'secret' and row.value in ('', '[]'))):
            raise HTTPException(422, f'Заполните обязательное поле {row.key}')
        if visible[row.key] and row.is_configured and row.kind != 'secret':
            normalize_setting(row.key, row.kind, row.value, rules)
    values = runtime_settings(db, bot_id, config)
    values = {key: value for key, value in values.items() if visible.get(key, False)}
    modules = db.scalars(select(BotModule).where(BotModule.bot_id == bot_id).order_by(BotModule.id)).all()
    scenarios = db.scalars(select(BotScenario).where(BotScenario.bot_id == bot_id,
        BotScenario.status == 'published').order_by(BotScenario.id)).all()
    accounts = db.scalars(select(BotPaymentProvider).where(BotPaymentProvider.bot_id == bot_id,
        BotPaymentProvider.enabled.is_(True))).all()
    payment_accounts = {row.provider: effective_credentials(db, bot_id, row.provider,
        config.encryption_key, row) for row in accounts}
    from .finance_hub import published_rates
    return {'schema': 1, 'settings': values, 'payment_accounts': payment_accounts,
        'exchange_rates': published_rates(db),
        'modules': [{'key': row.key, 'name': row.name, 'status': row.status} for row in modules],
        'scenarios': [{'key': s.key, 'name': s.name,
            'trigger': {'kind': s.trigger_kind, 'value': s.trigger_value},
            'segment_tags': s.segment_tags, 'version': s.version,
            'steps': [{'kind': step.kind, 'text': step.text,
                       'options': json.loads(step.options_json)} for step in s.steps]} for s in scenarios]}


def save_release(db, bot_id, payload, admin_id, encryption_key, action='publish', source_version=None):
    version = (db.scalar(select(func.max(BotConfigRelease.version)).where(
        BotConfigRelease.bot_id == bot_id)) or 0) + 1
    release = BotConfigRelease(bot_id=bot_id, version=version, revision=payload_revision(payload),
        encrypted_payload=encrypt(json.dumps(payload, ensure_ascii=False), encryption_key),
        created_by=admin_id, action=action, source_version=source_version)
    db.add(release)
    return release


@router.post('/bots/{bot_id}/releases')
def publish(request: Request, bot_id: int, token: str = Form()):
    with request.app.state.session_factory() as db:
        admin = require_admin(request, db, owner=True); csrf(request, token)
        if not db.get(Bot, bot_id):
            raise HTTPException(404)
        payload = draft_payload(db, bot_id, request.app.state.settings)
        release = save_release(db, bot_id, payload, admin.id, request.app.state.settings.encryption_key)
        db.flush()
        audit(db, 'bot.config.publish', f'{bot_id}:v{release.version}', admin.id, release.revision)
        db.commit()
        return RedirectResponse(f'/bots/{bot_id}/configure#releases', status_code=303)


@router.post('/bots/{bot_id}/releases/{version}/rollback')
def rollback(request: Request, bot_id: int, version: int, token: str = Form()):
    with request.app.state.session_factory() as db:
        admin = require_admin(request, db, owner=True); csrf(request, token)
        source = db.scalar(select(BotConfigRelease).where(BotConfigRelease.bot_id == bot_id,
                                                         BotConfigRelease.version == version))
        if not source:
            raise HTTPException(404)
        try:
            payload = load_release(source, request.app.state.settings.encryption_key)
        except (RuntimeError, ValueError):
            raise HTTPException(503, 'Не удалось открыть эту версию') from None
        release = save_release(db, bot_id, payload, admin.id, request.app.state.settings.encryption_key,
                               action='rollback', source_version=version)
        db.flush()
        audit(db, 'bot.config.rollback', f'{bot_id}:v{release.version}', admin.id, f'from v{version}')
        db.commit()
        return RedirectResponse(f'/bots/{bot_id}/configure#releases', status_code=303)
