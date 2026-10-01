"""Generic per-bot administrative workspace."""
import json
import re
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from .acquirers import PROVIDERS, credentials, save_acquirer
from .bot_blueprints import BLUEPRINTS, install_blueprint
from .bot_env_profile import food_porn_coverage
from .bot_settings import KEY_PATTERN
from .models import Bot, BotConfigRelease, BotModule, BotPaymentProvider, BotSetting, BotScenario, BotScenarioStep
from .bot_settings import parse_rules
from .security import audit, csrf, require_admin
from .web import page

router = APIRouter()


def get_bot(db, bot_id):
    bot = db.get(Bot, bot_id)
    if bot is None:
        raise HTTPException(404)
    return bot


@router.get('/bots/{bot_id}/configure')
def configure_bot(request: Request, bot_id: int):
    with request.app.state.session_factory() as db:
        require_admin(request, db)
        bot = get_bot(db, bot_id)
        settings = db.scalars(select(BotSetting).where(BotSetting.bot_id == bot_id)
            .order_by(BotSetting.display_order, BotSetting.id)).all()
        setting_rules = {s.id: parse_rules(s.schema_json) for s in settings}
        releases = db.scalars(select(BotConfigRelease).where(BotConfigRelease.bot_id == bot_id)
            .order_by(BotConfigRelease.version.desc()).limit(15)).all()
        merchants = db.scalars(select(BotPaymentProvider).where(BotPaymentProvider.bot_id == bot_id)
            .order_by(BotPaymentProvider.id)).all()
        merchant_modes = {}
        for merchant in merchants:
            if merchant.provider == 'paypal':
                try:
                    merchant_modes['paypal'] = credentials(merchant, request.app.state.settings.encryption_key).get('environment', '')
                except RuntimeError:
                    merchant_modes['paypal'] = ''
        modules = db.scalars(select(BotModule).where(BotModule.bot_id == bot_id)
            .order_by(BotModule.id)).all()
        scenarios = db.scalars(select(BotScenario).where(BotScenario.bot_id == bot_id)
            .order_by(BotScenario.id.desc())).all()
        return page(request, 'bot_config.html', db, bot=bot, settings=settings, setting_rules=setting_rules,
                    releases=releases, merchants=merchants,
                    env_coverage=food_porn_coverage(settings) if bot.key == 'food_porn' else [],
                    modules=modules, scenarios=scenarios, blueprints=BLUEPRINTS, builtins=PROVIDERS,
                    merchant_modes=merchant_modes,
                    encryption_ready=bool(request.app.state.settings.encryption_key))


@router.post('/bots/{bot_id}/blueprints/{blueprint_key}')
def add_blueprint(request: Request, bot_id: int, blueprint_key: str, token: str = Form()):
    with request.app.state.session_factory() as db:
        admin = require_admin(request, db, owner=True); csrf(request, token)
        get_bot(db, bot_id)
        if blueprint_key not in BLUEPRINTS:
            raise HTTPException(404)
        added, created = install_blueprint(db, bot_id, blueprint_key, admin.id)
        audit(db, 'bot.blueprint.install', f'{bot_id}:{blueprint_key}', admin.id,
              f'{added} declarations; module_created={created}; no values set')
        db.commit()
        return RedirectResponse(f'/bots/{bot_id}/configure#modules', status_code=303)


@router.post('/bots/{bot_id}/scenarios')
def create_scenario(request: Request, bot_id: int, token: str = Form(), key: str = Form(),
                    name: str = Form(), trigger_kind: str = Form('command'),
                    trigger_value: str = Form(''), segment_tags: str = Form('')):
    with request.app.state.session_factory() as db:
        admin = require_admin(request, db, owner=True); csrf(request, token)
        get_bot(db, bot_id)
        if (not KEY_PATTERN.fullmatch(key) or not name.strip() or len(name) > 120 or
                trigger_kind not in ('command', 'keyword', 'event') or
                not trigger_value.strip() or len(trigger_value) > 120 or len(segment_tags) > 500):
            raise HTTPException(422, 'Некорректный сценарий')
        if trigger_kind == 'command' and not re.fullmatch(r'/[a-z][a-z0-9_]{0,31}', trigger_value):
            raise HTTPException(422, 'Команда должна быть вида /start')
        if db.scalar(select(BotScenario.id).where(BotScenario.bot_id == bot_id, BotScenario.key == key)):
            raise HTTPException(409, 'Ключ сценария уже существует')
        scenario = BotScenario(bot_id=bot_id, key=key, name=name.strip(), trigger_kind=trigger_kind,
            trigger_value=trigger_value.strip(), segment_tags=segment_tags.strip(), status='draft')
        db.add(scenario); db.flush()
        audit(db, 'bot.scenario.create', f'{bot_id}:{key}', admin.id)
        db.commit()
        return RedirectResponse(f'/bots/{bot_id}/configure#scenarios', status_code=303)


@router.post('/bots/{bot_id}/scenarios/{scenario_id}/steps')
def add_scenario_step(request: Request, bot_id: int, scenario_id: int, token: str = Form(),
                      kind: str = Form(), text: str = Form(''), field_key: str = Form(''),
                      choices: str = Form(''), media_url: str = Form(''),
                      queue: str = Form(''), delay_seconds: int = Form(0)):
    with request.app.state.session_factory() as db:
        admin = require_admin(request, db, owner=True); csrf(request, token)
        scenario = db.get(BotScenario, scenario_id)
        if not scenario or scenario.bot_id != bot_id:
            raise HTTPException(404)
        if kind not in ('message', 'question', 'media', 'handoff', 'wait') or len(text) > 2000:
            raise HTTPException(422, 'Неверный шаг')
        options = {}
        if kind in ('message', 'question') and not text.strip():
            raise HTTPException(422, 'Введите текст сообщения')
        if kind == 'question':
            if not KEY_PATTERN.fullmatch(field_key):
                raise HTTPException(422, 'Укажите ключ поля для ответа')
            options = {'field': field_key, 'choices': [c.strip() for c in choices.split(',') if c.strip()]}
            if len(options['choices']) > 12 or any(len(c) > 64 for c in options['choices']):
                raise HTTPException(422, 'Не более 12 коротких вариантов')
        if kind == 'media':
            if not media_url.startswith('https://') or len(media_url) > 1000:
                raise HTTPException(422, 'Укажите HTTPS адрес изображения/файла')
            options = {'url': media_url}
        if kind == 'handoff':
            if not queue.strip() or len(queue) > 120:
                raise HTTPException(422, 'Укажите команду операторов')
            options = {'queue': queue.strip()}
        if kind == 'wait':
            if not 1 <= delay_seconds <= 86400:
                raise HTTPException(422, 'Задержка от 1 до 86400 секунд')
            options = {'seconds': delay_seconds}
        position = len(scenario.steps) + 1
        if position > 50:
            raise HTTPException(422, 'Максимум 50 шагов')
        db.add(BotScenarioStep(scenario_id=scenario_id, position=position, kind=kind,
                               text=text.strip(), options_json=json.dumps(options, ensure_ascii=False)))
        scenario.status, scenario.version = 'draft', scenario.version + 1
        audit(db, 'bot.scenario.step', f'{bot_id}:{scenario.key}', admin.id, kind)
        db.commit()
        return RedirectResponse(f'/bots/{bot_id}/configure#scenario-{scenario_id}', status_code=303)


@router.post('/bots/{bot_id}/scenarios/{scenario_id}/publish')
def publish_scenario(request: Request, bot_id: int, scenario_id: int, token: str = Form(),
                     status: str = Form()):
    with request.app.state.session_factory() as db:
        admin = require_admin(request, db, owner=True); csrf(request, token)
        scenario = db.get(BotScenario, scenario_id)
        if not scenario or scenario.bot_id != bot_id:
            raise HTTPException(404)
        if status not in ('published', 'paused') or (status == 'published' and not scenario.steps):
            raise HTTPException(422, 'Добавьте хотя бы один шаг')
        scenario.status, scenario.version = status, scenario.version + 1
        audit(db, 'bot.scenario.status', f'{bot_id}:{scenario.key}', admin.id, status)
        db.commit()
        return RedirectResponse(f'/bots/{bot_id}/configure#scenario-{scenario_id}', status_code=303)


@router.post('/bots/{bot_id}/scenarios/{scenario_id}/steps/{step_id}/order')
def change_scenario_step(request: Request, bot_id: int, scenario_id: int, step_id: int,
                         token: str = Form(), action: str = Form()):
    with request.app.state.session_factory() as db:
        admin = require_admin(request, db, owner=True); csrf(request, token)
        scenario = db.get(BotScenario, scenario_id)
        if not scenario or scenario.bot_id != bot_id or action not in ('up', 'down', 'remove'):
            raise HTTPException(404)
        steps = list(scenario.steps)
        index = next((i for i, step in enumerate(steps) if step.id == step_id), None)
        if index is None:
            raise HTTPException(404)
        if action == 'remove':
            db.delete(steps.pop(index))
            db.flush()
        else:
            other = index + (-1 if action == 'up' else 1)
            if 0 <= other < len(steps):
                steps[index], steps[other] = steps[other], steps[index]
        for n, step in enumerate(steps):
            step.position = -(n + 1)
        db.flush()
        for n, step in enumerate(steps):
            step.position = n + 1
        scenario.status, scenario.version = 'draft', scenario.version + 1
        audit(db, 'bot.scenario.order', f'{bot_id}:{scenario.key}', admin.id, action)
        db.commit()
        return RedirectResponse(f'/bots/{bot_id}/configure#scenario-{scenario_id}', status_code=303)


@router.post('/bots/{bot_id}/modules')
def save_module(request: Request, bot_id: int, token: str = Form(), key: str = Form(),
                name: str = Form(), description: str = Form(''), status: str = Form('draft')):
    with request.app.state.session_factory() as db:
        admin = require_admin(request, db, owner=True); csrf(request, token)
        get_bot(db, bot_id)
        if (not KEY_PATTERN.fullmatch(key) or not name.strip() or len(name) > 120 or
                len(description) > 2000 or status not in ('draft', 'enabled', 'paused')):
            raise HTTPException(422, 'Invalid module')
        module = db.scalar(select(BotModule).where(BotModule.bot_id == bot_id, BotModule.key == key))
        if module is None:
            module = BotModule(bot_id=bot_id, key=key)
            db.add(module)
        module.name, module.description = name.strip(), description.strip()
        module.status, module.updated_by = status, admin.id
        audit(db, 'bot.module.save', f'{bot_id}:{key}', admin.id, f'status={status}')
        db.commit()
        return RedirectResponse(f'/bots/{bot_id}/configure#modules', status_code=303)


@router.post('/bots/{bot_id}/profile')
def save_bot_profile(request: Request, bot_id: int, token: str = Form(), name: str = Form(),
                     description: str = Form(''), active: bool = Form(False)):
    with request.app.state.session_factory() as db:
        admin = require_admin(request, db, owner=True); csrf(request, token)
        bot = get_bot(db, bot_id)
        if not name.strip() or len(name) > 120 or len(description) > 4000:
            raise HTTPException(422, 'Invalid bot profile')
        bot.name, bot.description, bot.active = name.strip(), description.strip(), active
        audit(db, 'bot.profile.update', str(bot_id), admin.id)
        db.commit()
        return RedirectResponse(f'/bots/{bot_id}/configure', status_code=303)


@router.post('/bots/{bot_id}/acquirers')
def save_payment_provider(request: Request, bot_id: int, token: str = Form(), provider: str = Form(),
                          label: str = Form(''), enabled: bool = Form(False), login: str = Form(''),
                          password: str = Form(''), payee_id: str = Form(''),
                          merchant_account: str = Form(''), secret_key: str = Form(''),
                          merchant_code: str = Form(''), terminal: str = Form(''),
                          currency: str = Form(''),
                          public_key: str = Form(''), private_key: str = Form(''),
                          client_id: str = Form(''), client_secret: str = Form(''),
                          merchant_id: str = Form(''), environment: str = Form(''),
                          custom_json: str = Form('')):
    with request.app.state.session_factory() as db:
        admin = require_admin(request, db, owner=True); csrf(request, token)
        get_bot(db, bot_id)
        if provider == 'paypal' and environment and environment not in ('live', 'sandbox'):
            raise HTTPException(422, 'Выберите live или sandbox')
        row = save_acquirer(db, bot_id, provider, label, {
            'login': login, 'password': password, 'payee_id': payee_id,
            'merchant_account': merchant_account, 'secret_key': secret_key,
            'merchant_code': merchant_code, 'terminal': terminal, 'currency': currency,
            'public_key': public_key, 'private_key': private_key,
            'client_id': client_id, 'client_secret': client_secret,
            'merchant_id': merchant_id, 'environment': environment,
        }, custom_json, enabled, request.app.state.settings.encryption_key, admin.id)
        db.flush()
        audit(db, 'bot.acquirer.save', f'{bot_id}:{provider}', admin.id,
              f'enabled={enabled}; credential values hidden')
        db.commit()
        return RedirectResponse(f'/bots/{bot_id}/configure#acquirers', status_code=303)


@router.post('/bots/{bot_id}/settings/{setting_id}/move')
def move_setting(request: Request, bot_id: int, setting_id: int, token: str = Form(), direction: str = Form()):
    with request.app.state.session_factory() as db:
        admin = require_admin(request, db, owner=True); csrf(request, token)
        get_bot(db, bot_id)
        rows = db.scalars(select(BotSetting).where(BotSetting.bot_id == bot_id)
            .order_by(BotSetting.display_order, BotSetting.id)).all()
        positions = {row.id: index for index, row in enumerate(rows)}
        if setting_id not in positions or direction not in ('up', 'down'):
            raise HTTPException(422, 'Unknown setting or direction')
        index = positions[setting_id]
        other = index + (-1 if direction == 'up' else 1)
        if 0 <= other < len(rows):
            rows[index], rows[other] = rows[other], rows[index]
            for n, row in enumerate(rows):
                row.display_order = (n + 1) * 10
            audit(db, 'bot.setting.move', f'{bot_id}:{setting_id}', admin.id, direction)
            db.commit()
        return RedirectResponse(f'/bots/{bot_id}/configure#settings', status_code=303)
