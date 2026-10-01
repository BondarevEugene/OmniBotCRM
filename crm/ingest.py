import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from .models import Activity, Bot, BotConfigApplied, BotIncident, BotModule, BotPaymentProvider, BotScenario, Customer, Deal, Identity, IngestEvent, MarketingCampaign, MarketingTouch, Order, OrderItem, Payment, PaymentVerification, Pipeline, Product, Stage, Ticket
from .acquirers import effective_credentials
from .bot_settings import setting_value, runtime_settings
from .config_releases import latest_release, load_release

router = APIRouter(prefix='/api/v1')
TYPES = {'customer.upsert', 'customer.activity', 'order.upsert', 'payment.upsert', 'customer.consent', 'customer.blocked','lead.upsert','ticket.upsert','bot.heartbeat','bot.error','product.upsert','marketing.touch', 'scenario.enter', 'scenario.complete', 'scenario.handoff'}


def authenticated_bot(request, raw=b'', config_request=False, signed_get=False):
    key = request.headers.get('X-Bot-Key', '')
    stamp = request.headers.get('X-Timestamp', '')
    try:
        timestamp = int(stamp)
    except ValueError:
        raise HTTPException(401, 'Invalid signature')
    with request.app.state.session_factory() as db:
        bot = db.scalar(select(Bot).where(Bot.key == key, Bot.active.is_(True)))
        if not bot:
            raise HTTPException(401, 'Invalid signature')
        try:
            secret = setting_value(db, bot.id, 'ingest_secret', request.app.state.settings)
        except RuntimeError:
            raise HTTPException(503, 'Bot key unavailable') from None
    secret = secret or request.app.state.settings.ingest_secrets.get(key)
    if signed_get:
        path = request.url.path + (('?' + request.url.query) if request.url.query else '')
        signed = stamp.encode() + b'.GET.' + path.encode('utf-8')
    else:
        signed = stamp.encode() + (b'.GET./api/v1/bot-config' if config_request else b'.' + raw)
    expected = hmac.new(secret.encode() if secret else b'', signed, hashlib.sha256).hexdigest()
    if not secret or len(secret) < 24 or abs(time.time() - timestamp) > 300 or not hmac.compare_digest(request.headers.get('X-Signature', ''), expected):
        raise HTTPException(401, 'Invalid signature')
    return bot.id


@router.get('/bot-config')
def get_bot_config(request: Request):
    bot_id = authenticated_bot(request, config_request=True)
    with request.app.state.session_factory() as db:
        release = latest_release(db, bot_id)
        if release:
            try:
                payload = load_release(release, request.app.state.settings.encryption_key)
            except (RuntimeError, ValueError):
                raise HTTPException(503, 'Published bot configuration unavailable') from None
            revision = release.revision
            headers = {'Cache-Control': 'no-store', 'ETag': '"' + revision + '"'}
            if request.headers.get('If-None-Match') == headers['ETag']:
                from fastapi.responses import Response
                return Response(status_code=304, headers=headers)
            payload['revision'] = revision
            payload['release_version'] = release.version
            return JSONResponse(payload, headers=headers)
        try:
            data = runtime_settings(db, bot_id, request.app.state.settings)
        except RuntimeError:
            raise HTTPException(503, 'Bot settings unavailable') from None
        modules = db.scalars(select(BotModule).where(BotModule.bot_id == bot_id)
            .order_by(BotModule.id)).all()
        products = [{'key': row.key, 'name': row.name, 'status': row.status} for row in modules]
        scenarios = db.scalars(select(BotScenario).where(BotScenario.bot_id == bot_id,
            BotScenario.status == 'published').order_by(BotScenario.id)).all()
        flows = [{'key': s.key, 'name': s.name, 'trigger': {'kind': s.trigger_kind, 'value': s.trigger_value},
                  'segment_tags': s.segment_tags, 'version': s.version,
                  'steps': [{'kind': step.kind, 'text': step.text,
                             'options': json.loads(step.options_json)} for step in s.steps]} for s in scenarios]
        accounts = db.scalars(select(BotPaymentProvider).where(BotPaymentProvider.bot_id == bot_id,
            BotPaymentProvider.enabled.is_(True))).all()
        try:
            payment_accounts = {row.provider: effective_credentials(db, bot_id, row.provider,
                request.app.state.settings.encryption_key, row) for row in accounts}
        except RuntimeError:
            raise HTTPException(503, 'Payment account settings unavailable') from None
    from .finance_hub import published_rates
    with request.app.state.session_factory() as db:
        exchange_rates = published_rates(db)
    payload = {'schema': 1, 'settings': data, 'modules': products, 'scenarios': flows,
               'payment_accounts': payment_accounts, 'exchange_rates': exchange_rates}
    revision = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                          separators=(',', ':')).encode()).hexdigest()
    headers = {'Cache-Control': 'no-store', 'ETag': '"' + revision + '"'}
    if request.headers.get('If-None-Match') == headers['ETag']:
        from fastapi.responses import Response
        return Response(status_code=304, headers=headers)
    payload['revision'] = revision
    return JSONResponse(payload, headers=headers)


@router.post('/config-applied')
async def report_config_applied(request: Request):
    raw = await request.body()
    if len(raw) > 1024:
        raise HTTPException(413, 'Report too large')
    bot_id = authenticated_bot(request, raw)
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(422, 'Invalid JSON') from None
    if not isinstance(payload, dict) or set(payload) - {'revision', 'status', 'reason'}:
        raise HTTPException(422, 'Invalid report')
    revision = payload.get('revision', '')
    status = payload.get('status')
    reason = payload.get('reason', '')
    if (not isinstance(revision, str) or len(revision) != 64 or
            any(c not in '0123456789abcdef' for c in revision) or
            status not in ('applied', 'failed') or
            reason not in ('', 'validation_failed', 'restart_required', 'dependency_unavailable', 'other')):
        raise HTTPException(422, 'Invalid report')
    with request.app.state.session_factory() as db:
        release = latest_release(db, bot_id)
        if not release:
            raise HTTPException(409, 'Publish a configuration first')
        effective = status if release.revision == revision else 'stale'
        row = db.scalar(select(BotConfigApplied).where(BotConfigApplied.bot_id == bot_id))
        if row is None:
            row = BotConfigApplied(bot_id=bot_id)
            db.add(row)
        row.revision = revision
        row.release_version = release.version if release.revision == revision else None
        row.status = effective
        row.error = reason if status == 'failed' else ''
        row.reported_at = datetime.now(timezone.utc)
        db.commit()
        return {'ok': True, 'status': effective, 'expected_version': release.version}


def money(value):
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount < 0 or amount.as_tuple().exponent < -2 or amount > Decimal('999999999999.99'):
            raise ValueError()
        return amount
    except (InvalidOperation, ValueError):
        raise HTTPException(422, 'Invalid monetary amount')


def field(value, max_length=200, required=False):
    if value is None:
        value = ''
    if not isinstance(value, str) or len(value) > max_length or (required and not value.strip()):
        raise HTTPException(422, 'Invalid event field')
    return value.strip()


@router.post('/events')
async def receive_event(request: Request):
    raw = await request.body()
    if len(raw) > 65536:
        raise HTTPException(413, 'Event too large')
    bot_id = authenticated_bot(request, raw)
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(422, 'Invalid JSON')
    if not isinstance(payload, dict) or payload.get('type') not in TYPES or not isinstance(payload.get('data'), dict):
        raise HTTPException(422, 'Invalid event')
    event_id = field(payload.get('event_id'), 120, True)
    data = payload['data']
    typ = payload['type']
    external_user_id = field(data.get('external_user_id'), 100, typ not in ('bot.heartbeat', 'bot.error', 'product.upsert'))
    factory = request.app.state.session_factory
    with factory() as db:
        bot = db.get(Bot, bot_id)
        if db.scalar(select(IngestEvent).where(IngestEvent.bot_id == bot.id, IngestEvent.event_id == event_id)):
            return {'ok': True, 'duplicate': True}
        module_key = None
        if 'module_key' in data:
            module_key = field(data['module_key'], 80, True)
            if not db.scalar(select(BotModule.id).where(BotModule.bot_id == bot.id,
                                                        BotModule.key == module_key)):
                raise HTTPException(422, 'Unknown module for this bot')
        scenario_key = None
        if typ.startswith('scenario.'):
            scenario_key = field(data.get('scenario_key'), 80, True)
            if not db.scalar(select(BotScenario.id).where(BotScenario.bot_id == bot.id,
                BotScenario.key == scenario_key)):
                raise HTTPException(422, 'Unknown scenario for this bot')
        if typ in ('bot.heartbeat', 'bot.error', 'product.upsert'):
            bot.last_seen_at = datetime.now(timezone.utc)
            if typ == 'bot.error':
                severity = field(data.get('severity', 'error'), 20, True)
                if severity not in ('warning', 'error', 'critical'):
                    raise HTTPException(422, 'Invalid severity')
                db.add(BotIncident(bot_id=bot.id, severity=severity,
                    component=field(data.get('component'), 120, True),
                    message=field(data.get('message'), 500, True)))
            if typ == 'product.upsert':
                sku = field(data.get('sku'), 120, True)
                product = db.scalar(select(Product).where(Product.bot_id == bot.id, Product.sku == sku))
                if not product:
                    product = Product(bot_id=bot.id, sku=sku)
                    db.add(product)
                product.title = field(data.get('title'), 200, True)
                product.description = field(data.get('description'), 2000)
                product.price = money(data.get('price'))
                product.currency = field(data.get('currency', 'UAH'), 3, True).upper()
                product.active = data.get('active', True) is True
                if module_key:
                    product.module_key = module_key
            db.add(IngestEvent(bot_id=bot.id, module_key=module_key,
                               event_id=event_id, event_type=typ))
            db.commit()
            return {'ok': True, 'duplicate': False}
        identity = db.scalar(select(Identity).where(Identity.bot_id == bot.id, Identity.external_user_id == external_user_id))
        if not identity:
            # Identity is bot-scoped: no automatic cross-bot merge by phone/email.
            customer = Customer(source=bot.key)
            db.add(customer)
            db.flush()
            identity = Identity(bot_id=bot.id, customer_id=customer.id, external_user_id=external_user_id)
            db.add(identity)
        else:
            customer = db.get(Customer, identity.customer_id)
        if 'name' in data:
            customer.name = field(data['name'], 200)
        if 'email' in data:
            customer.email = field(data['email'], 255) or None
        if 'phone' in data:
            customer.phone = field(data['phone'], 40) or None
        if 'language' in data:
            customer.language = field(data['language'], 10) or 'ru'
        if 'chat_id' in data:
            identity.chat_id = field(str(data['chat_id']), 100) or None
        if 'blocked' in data and payload['type'] == 'customer.blocked':
            identity.blocked = bool(data['blocked'])
        if payload['type'] == 'customer.consent':
            customer.marketing_consent = data.get('consent') is True
            customer.consent_at = datetime.now(timezone.utc) if customer.marketing_consent else None
        customer.last_seen_at = datetime.now(timezone.utc)
        bot.last_seen_at = customer.last_seen_at
        if typ == 'customer.activity':
            db.add(Activity(customer_id=customer.id, bot_id=bot.id, kind=field(data.get('kind'), 60, True), summary=field(data.get('summary'), 2000)))
        if typ.startswith('scenario.'):
            db.add(Activity(customer_id=customer.id, bot_id=bot.id, kind=typ,
                            summary=f'Сценарий {scenario_key}'))
        if typ == 'marketing.touch':
            utm = field(data.get('utm_campaign'), 120, True)
            campaign = db.scalar(select(MarketingCampaign).where(MarketingCampaign.utm_campaign == utm))
            if not campaign:
                raise HTTPException(422, 'Unknown marketing campaign')
            existing = db.scalar(select(MarketingTouch).where(MarketingTouch.customer_id == customer.id,
                                                              MarketingTouch.campaign_id == campaign.id))
            if not existing:
                db.add(MarketingTouch(customer_id=customer.id, campaign_id=campaign.id, bot_id=bot.id,
                                      medium=field(data.get('medium'), 80)))
        if typ == 'lead.upsert':
            external_id=field(data.get('external_id'),120,True)
            deal=db.scalar(select(Deal).where(Deal.bot_id==bot.id,Deal.external_id==external_id))
            if not deal:
                pipeline=db.scalar(select(Pipeline).where(Pipeline.active.is_(True)).order_by(Pipeline.id))
                stage=db.scalar(select(Stage).where(Stage.pipeline_id==pipeline.id).order_by(Stage.position)) if pipeline else None
                if not stage:raise HTTPException(409,'Create a CRM sales pipeline first')
                deal=Deal(bot_id=bot.id,external_id=external_id,customer_id=customer.id,
                    pipeline_id=pipeline.id,stage_id=stage.id,source=bot.key)
                db.add(deal)
            elif deal.customer_id!=customer.id:raise HTTPException(409,'Lead belongs to another customer')
            service=field(data.get('service','website'),80,True)
            if service not in {'website','web_app','portal','bot','support','integration','other'}:raise HTTPException(422,'Invalid service')
            deal.title=field(data.get('title'),200,True)
            deal.service=service
            deal.budget=money(data.get('budget','0'))
            deal.currency=field(data.get('currency','UAH'),3,True).upper()
            db.add(Activity(customer_id=customer.id,bot_id=bot.id,kind='lead',summary=deal.title))
        if typ == 'ticket.upsert':
            external_id=field(data.get('external_id'),120,True)
            ticket=db.scalar(select(Ticket).where(Ticket.bot_id==bot.id,Ticket.external_id==external_id))
            if not ticket:
                ticket=Ticket(bot_id=bot.id,external_id=external_id,customer_id=customer.id)
                db.add(ticket)
            elif ticket.customer_id!=customer.id:raise HTTPException(409,'Ticket belongs to another customer')
            ticket.title=field(data.get('title'),200,True)
            ticket.description=field(data.get('description'),5000)
            status=field(data.get('status','open'),20,True)
            if status not in {'open','in_progress','waiting','resolved'}:raise HTTPException(422,'Invalid ticket status')
            ticket.status=status
        if typ == 'order.upsert':
            external_id = field(data.get('external_id'), 120, True)
            order = db.scalar(select(Order).where(Order.bot_id == bot.id, Order.external_id == external_id))
            if not order:
                order = Order(bot_id=bot.id, customer_id=customer.id, external_id=external_id)
                db.add(order)
            elif order.customer_id != customer.id:
                raise HTTPException(409, 'Order belongs to another customer')
            new_amount = money(data.get('amount', '0'))
            new_currency = field(data.get('currency', 'UAH'), 3, True).upper()
            if order.status == 'paid' and (order.amount != new_amount or order.currency != new_currency):
                raise HTTPException(409, 'Verified order amount is immutable')
            if module_key:
                if order.status == 'paid' and order.module_key and order.module_key != module_key:
                    raise HTTPException(409, 'Verified order product is immutable')
                order.module_key = module_key
            order.title = field(data.get('title'), 200, True)
            reported_status = field(data.get('status', 'new'), 30, True)
            order.status = ('reported_paid' if reported_status == 'paid' else reported_status) if order.status != 'paid' else 'paid'
            order.amount = new_amount
            order.currency = new_currency
            if 'items' in data:
                items = data['items']
                if not isinstance(items, list) or not 1 <= len(items) <= 50 or order.status == 'paid':
                    raise HTTPException(422, 'Invalid or immutable order items')
                parsed, seen = [], set()
                for entry in items:
                    if not isinstance(entry, dict):
                        raise HTTPException(422, 'Invalid order item')
                    sku = field(entry.get('sku'), 120, True)
                    quantity = entry.get('quantity')
                    if sku in seen or type(quantity) is not int or not 1 <= quantity <= 1000:
                        raise HTTPException(422, 'Invalid order item quantity or duplicate SKU')
                    seen.add(sku)
                    product = db.scalar(select(Product).where(Product.bot_id == bot.id, Product.sku == sku))
                    if not product or product.currency != new_currency:
                        raise HTTPException(422, 'Unknown product or mismatched currency')
                    unit_price = money(entry.get('unit_price'))
                    parsed.append((product.id, quantity, unit_price))
                if sum((quantity * unit_price for _, quantity, unit_price in parsed), Decimal('0')) != new_amount:
                    raise HTTPException(422, 'Item total differs from order amount')
                db.flush()
                db.execute(delete(OrderItem).where(OrderItem.order_id == order.id))
                db.add_all(OrderItem(order_id=order.id, product_id=pid, quantity=qty, unit_price=price)
                           for pid, qty, price in parsed)
        if typ == 'payment.upsert':
            external_id = field(data.get('external_id'), 120, True)
            payment = db.scalar(select(Payment).where(Payment.bot_id == bot.id, Payment.external_id == external_id))
            if not payment:
                payment = Payment(bot_id=bot.id, customer_id=customer.id, external_id=external_id)
                db.add(payment)
            elif payment.customer_id != customer.id:
                raise HTTPException(409, 'Payment belongs to another customer')
            reported_status = field(data.get('status', 'pending'), 30, True)
            provider = field(data.get('provider', 'manual'), 80, True).lower()
            if provider not in {'manual', 'portmone', 'wayforpay', 'paypal', 'redsys', 'liqpay', 'stripe', 'telegram_stars'}:
                raise HTTPException(422, 'Unknown payment provider')
            new_amount = money(data.get('amount'))
            new_currency = field(data.get('currency', 'UAH'), 3, True).upper()
            if payment.id and db.scalar(select(PaymentVerification.id).where(PaymentVerification.payment_id == payment.id)):
                if payment.provider != provider or payment.amount != new_amount or payment.currency != new_currency:
                    raise HTTPException(409, 'Verified payment is immutable')
                if data.get('order_external_id'):
                    original_order = db.get(Order, payment.order_id) if payment.order_id else None
                    if not original_order or original_order.external_id != data['order_external_id']:
                        raise HTTPException(409, 'Verified payment order is immutable')
                if module_key and payment.module_key != module_key:
                    raise HTTPException(409, 'Verified payment product is immutable')
                db.add(IngestEvent(bot_id=bot.id, module_key=module_key,
                                   event_id=event_id, event_type=typ))
                db.commit()
                return {'ok': True, 'duplicate': False, 'customer_id': customer.id}
            payment.provider = provider
            payment.status = ('reported_paid' if reported_status == 'paid' else reported_status) if payment.status != 'paid' else 'paid'
            payment.amount = new_amount
            payment.currency = new_currency
            if data.get('order_external_id'):
                order = db.scalar(select(Order).where(Order.bot_id == bot.id, Order.external_id == field(data['order_external_id'], 120)))
                payment.order_id = order.id if order and order.customer_id == customer.id else None
                if order and order.customer_id == customer.id and order.module_key:
                    if module_key and module_key != order.module_key:
                        raise HTTPException(409, 'Payment product differs from order')
                    module_key = order.module_key
            if module_key:
                payment.module_key = module_key
        db.add(IngestEvent(bot_id=bot.id, module_key=module_key, scenario_key=scenario_key,
                           event_id=event_id, event_type=typ))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            if db.scalar(select(IngestEvent).where(IngestEvent.bot_id == bot.id, IngestEvent.event_id == event_id)):
                return {'ok': True, 'duplicate': True}
            raise
        return {'ok': True, 'duplicate': False, 'customer_id': customer.id}
