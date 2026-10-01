"""Data connections, commerce reconciliation and exportable management reports."""
import csv
import io
import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from urllib.parse import quote

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, StreamingResponse
from sqlalchemy import delete, func, select

from .models import (Activity, Bot, BotIncident, BotModule, BotScenario, BotPaymentProvider, BotSetting, Campaign, Customer, Deal, Delivery, Expense, Identity,
                     IngestEvent, Invoice, MarketingChannel, MarketingCampaign, MarketingMetric, MarketingTouch, Milestone, Order, OrderItem, Payment,
                     PaymentVerification, Product, Project, SyncRun, Ticket)
from .payment_providers import VerificationError, verify_payment
from .ingest import field, money
from .bot_settings import encrypt, make_rules, normalize_setting, parse_rules
from .bot_sources import food_porn_source
from .finance_hub import cashbook
from .security import audit, csrf, require_admin
from .web import page, csv_cell

router = APIRouter()


@router.get('/bots/{bot_id}')
def bot_detail(request: Request, bot_id: int, view: str = 'overview'):
    with request.app.state.session_factory() as db:
        require_admin(request, db)
        bot = db.get(Bot, bot_id)
        if bot is None:
            raise HTTPException(404)
        if view not in ('overview', 'customers', 'orders', 'payments', 'services', 'monitor', 'marketing'):
            raise HTTPException(404, 'Раздел не найден')
        since = datetime.now(timezone.utc) - timedelta(days=7)
        counts = dict(db.execute(select(IngestEvent.event_type, func.count())
            .where(IngestEvent.bot_id == bot_id, IngestEvent.created_at >= since)
            .group_by(IngestEvent.event_type)).all())
        incidents = db.scalars(select(BotIncident).where(BotIncident.bot_id == bot_id).order_by(BotIncident.id.desc()).limit(30)).all()
        config = request.app.state.settings
        bot_settings = db.scalars(select(BotSetting).where(BotSetting.bot_id == bot_id).order_by(BotSetting.display_order, BotSetting.id)).all()
        modules = db.scalars(select(BotModule).where(BotModule.bot_id == bot_id).order_by(BotModule.id)).all()
        scenarios = db.scalars(select(BotScenario).where(BotScenario.bot_id == bot_id).order_by(BotScenario.id)).all()
        scenario_counts = {(key, typ): amount for key, typ, amount in db.execute(select(
            IngestEvent.scenario_key, IngestEvent.event_type, func.count())
            .where(IngestEvent.bot_id == bot_id, IngestEvent.created_at >= since,
                   IngestEvent.scenario_key.is_not(None))
            .group_by(IngestEvent.scenario_key, IngestEvent.event_type)).all()}
        module_events = dict(db.execute(select(IngestEvent.module_key, func.count()).where(
            IngestEvent.bot_id == bot_id, IngestEvent.created_at >= since,
            IngestEvent.module_key.is_not(None)).group_by(IngestEvent.module_key)).all())
        module_orders = dict(db.execute(select(Order.module_key, func.count()).where(
            Order.bot_id == bot_id, Order.module_key.is_not(None)).group_by(Order.module_key)).all())
        module_verified = dict(db.execute(select(Payment.module_key, func.count()).join(
            PaymentVerification, PaymentVerification.payment_id == Payment.id).where(
            Payment.bot_id == bot_id, Payment.module_key.is_not(None),
            PaymentVerification.status == 'verified').group_by(Payment.module_key)).all())
        event_days = Counter(e.created_at.date().isoformat() for e in db.scalars(select(IngestEvent)
            .where(IngestEvent.bot_id == bot_id, IngestEvent.created_at >= since)))
        activity_series = [{'day': (datetime.now(timezone.utc)-timedelta(days=n)).date().isoformat(),
                            'count': event_days[(datetime.now(timezone.utc)-timedelta(days=n)).date().isoformat()]}
                           for n in range(6,-1,-1)]
        today = datetime.now(timezone.utc).date()
        cash = cashbook(db, today - timedelta(days=30), today, bot_id)
        customer_rows = db.execute(select(Customer, Identity).join(Identity, Identity.customer_id == Customer.id)
            .where(Identity.bot_id == bot_id).order_by(Identity.id.desc()).limit(80)).all()
        order_rows = db.scalars(select(Order).where(Order.bot_id == bot_id)
            .order_by(Order.id.desc()).limit(80)).all()
        payment_rows = db.scalars(select(Payment).where(Payment.bot_id == bot_id)
            .order_by(Payment.id.desc()).limit(80)).all()
        verified_ids = set(db.scalars(select(PaymentVerification.payment_id).join(Payment,
            Payment.id == PaymentVerification.payment_id).where(Payment.bot_id == bot_id,
            PaymentVerification.status == 'verified')).all())
        heartbeat = db.scalar(select(IngestEvent.created_at).where(IngestEvent.bot_id == bot_id,
            IngestEvent.event_type == 'bot.heartbeat').order_by(IngestEvent.id.desc()).limit(1))
        heartbeat_age = (datetime.now(timezone.utc) - (heartbeat if heartbeat.tzinfo else
            heartbeat.replace(tzinfo=timezone.utc))).total_seconds() if heartbeat else None
        product_rows = db.scalars(select(Product).where(Product.bot_id == bot_id)
            .order_by(Product.id.desc()).limit(80)).all()
        ad_rows = db.execute(select(MarketingCampaign, MarketingChannel).join(
            MarketingChannel, MarketingChannel.id == MarketingCampaign.channel_id).where(
            MarketingChannel.bot_id == bot_id).order_by(MarketingCampaign.id.desc()).limit(80)).all()
        ad_metrics = {}
        for ad_campaign, _ in ad_rows:
            totals = db.execute(select(func.coalesce(func.sum(MarketingMetric.spend), 0),
                func.coalesce(func.sum(MarketingMetric.clicks), 0),
                func.coalesce(func.sum(MarketingMetric.leads), 0)).where(
                MarketingMetric.campaign_id == ad_campaign.id,
                MarketingMetric.day >= since)).one()
            ad_metrics[ad_campaign.id] = totals
        return page(request, 'bot_detail.html', db, bot=bot, counts=counts, incidents=incidents,
            settings=bot_settings, modules=modules, scenario_counts=scenario_counts,
            scenarios=scenarios, module_events=module_events,
            module_orders=module_orders, module_verified=module_verified,
            activity_series=activity_series, view=view, cash=cash, customer_rows=customer_rows,
            order_rows=order_rows, payment_rows=payment_rows, verified_ids=verified_ids,
            product_rows=product_rows, heartbeat=heartbeat, heartbeat_age=heartbeat_age,
            ad_rows=ad_rows, ad_metrics=ad_metrics,
            encryption_ready=bool(config.encryption_key),
            customers=db.scalar(select(func.count()).select_from(Identity).where(Identity.bot_id == bot_id)),
            orders=db.scalar(select(func.count()).select_from(Order).where(Order.bot_id == bot_id)),
            campaigns=db.scalar(select(func.count()).select_from(Campaign).where(Campaign.bot_id == bot_id)),
            signed=any(s.key=='ingest_secret' and s.is_configured for s in bot_settings) or
                   (bot.key in config.ingest_secrets and len(config.ingest_secrets[bot.key]) >= 24),
            token_ready=any(s.key=='telegram_token' and s.is_configured for s in bot_settings) or bool(config.bot_tokens.get(bot.key)))


@router.post('/bots/{bot_id}/settings')
def save_bot_setting(request: Request, bot_id: int, token: str = Form(), key: str = Form(),
                     label: str = Form(), group_name: str = Form('general'), kind: str = Form('text'),
                     value: str = Form(''), description: str = Form(''), rules_enabled: str = Form(''),
                     choices: str = Form(''), visible_if_key: str = Form(''), visible_if_value: str = Form(''),
                     required: bool = Form(False), min_value: str = Form(''), max_value: str = Form(''),
                     max_length: str = Form('')):
    with request.app.state.session_factory() as db:
        admin = require_admin(request, db, owner=True)
        csrf(request, token)
        if not db.get(Bot, bot_id):
            raise HTTPException(404)
        row = db.scalar(select(BotSetting).where(BotSetting.bot_id == bot_id, BotSetting.key == key))
        rules = make_rules(kind, choices, visible_if_key, visible_if_value,
                           required, min_value, max_value, max_length) if rules_enabled == '1' else (
                               parse_rules(row.schema_json) if row else parse_rules('{}'))
        if row and row.is_configured and (row.kind == 'secret') != (kind == 'secret') and not value:
            raise HTTPException(422, 'При смене секретного типа укажите новое значение')
        if row and row.is_configured and row.kind == 'secret' and kind == 'secret' and not value:
            normalized = None  # Leaving this field empty retains the encrypted value.
        elif row is None and not value:
            normalized = None  # New declaration may be completed before publication.
        else:
            normalized = normalize_setting(key, kind, value, rules)
        if not label.strip() or len(label) > 120 or len(group_name) > 80 or len(description) > 500:
            raise HTTPException(422, 'Invalid setting details')
        if row is None:
            highest = db.scalar(select(func.max(BotSetting.display_order)).where(BotSetting.bot_id == bot_id)) or 0
            row = BotSetting(bot_id=bot_id, key=key, display_order=highest + 10)
            db.add(row)
        if normalized is not None:
            row.value = encrypt(normalized, request.app.state.settings.encryption_key) if kind == 'secret' else normalized
            row.is_configured = True
        row.kind, row.label, row.group_name = kind, label.strip(), group_name.strip() or 'general'
        row.description, row.updated_by = description.strip(), admin.id
        row.schema_json = json.dumps(rules, ensure_ascii=False)
        audit(db, 'bot.setting.save', f'{bot_id}:{key}', admin.id, f'type={kind}; value hidden')
        db.commit()
        return RedirectResponse(f'/bots/{bot_id}/configure#settings', status_code=303)


@router.post('/bots/{bot_id}/settings/{setting_id}/delete')
def delete_bot_setting(request: Request, bot_id: int, setting_id: int, token: str = Form()):
    with request.app.state.session_factory() as db:
        admin = require_admin(request, db, owner=True)
        csrf(request, token)
        row = db.get(BotSetting, setting_id)
        if row is None or row.bot_id != bot_id:
            raise HTTPException(404)
        audit(db, 'bot.setting.delete', f'{bot_id}:{row.key}', admin.id)
        db.delete(row)
        db.commit()
        return RedirectResponse(f'/bots/{bot_id}/configure#settings', status_code=303)


@router.post('/bots/{bot_id}/incidents/{incident_id}/resolve')
def resolve_incident(request: Request, bot_id: int, incident_id: int, token: str = Form()):
    with request.app.state.session_factory() as db:
        admin = require_admin(request, db)
        csrf(request, token)
        incident = db.get(BotIncident, incident_id)
        if incident is None or incident.bot_id != bot_id:
            raise HTTPException(404)
        incident.resolved_at = datetime.now(timezone.utc)
        audit(db, 'bot.incident.resolve', str(incident_id), admin.id)
        db.commit()
        return RedirectResponse(f'/bots/{bot_id}', status_code=303)


@router.get('/integrations')
def integrations(request: Request, result: str = ''):
    with request.app.state.session_factory() as db:
        require_admin(request, db)
        config = request.app.state.settings
        bots = db.scalars(select(Bot).order_by(Bot.id)).all()
        counts = dict(db.execute(select(Identity.bot_id, func.count()).group_by(Identity.bot_id)).all())
        runs = db.scalars(select(SyncRun).order_by(SyncRun.id.desc()).limit(15)).all()
        configured = db.scalars(select(BotSetting).where(BotSetting.key.in_(['ingest_secret','telegram_token']),
                                                         BotSetting.is_configured.is_(True))).all()
        signed_ids = {row.bot_id for row in configured if row.key == 'ingest_secret'}
        token_ids = {row.bot_id for row in configured if row.key == 'telegram_token'}
        acquirers = db.scalars(select(BotPaymentProvider).where(BotPaymentProvider.enabled.is_(True))).all()
        try:
            source_ready = bool(food_porn_source(db, config))
        except RuntimeError:
            source_ready = False
        return page(request, 'integrations.html', db, bots=bots, counts=counts, runs=runs,
            result=result[:140], source_ready=source_ready,
            secret_keys=set(config.ingest_secrets), token_keys={k for k, v in config.bot_tokens.items() if v},
            signed_ids=signed_ids, token_ids=token_ids,
            merchant_counts=Counter(row.bot_id for row in acquirers),
            providers={'Portmone': all((config.portmone_login, config.portmone_password, config.portmone_payee_id)),
                       'WayForPay': bool(config.wayforpay_merchant_account and config.wayforpay_secret_key)})


@router.post('/integrations/food-porn/sync')
def sync_food_porn(request: Request, token: str = Form()):
    from integrations.food_porn_import import import_data
    with request.app.state.session_factory() as db:
        admin = require_admin(request, db, owner=True)
        csrf(request, token)
        try:
            source_url = food_porn_source(db, request.app.state.settings)
        except RuntimeError:
            raise HTTPException(409, 'Проверьте CRM_ENCRYPTION_KEY для адреса источника') from None
        if not source_url:
            raise HTTPException(409, 'Укажите адрес исходной базы в карточке бота или FOOD_PORN_SOURCE_DATABASE_URL в .env')
        try:
            customers, orders = import_data(source_url, request.app.state.settings.database_url)
            db.add(SyncRun(source='food_porn', status='success', customers=customers, orders=orders))
            audit(db, 'source.sync', 'food_porn', admin.id, f'{customers} customers, {orders} orders')
            message = 'Синхронизация завершена'
        except Exception:
            db.add(SyncRun(source='food_porn', status='failed', detail='Источник недоступен или схема не совпадает'))
            message = 'Ошибка источника: проверьте соединение и структуру таблиц в журнале сервера'
        db.commit()
        return RedirectResponse('/integrations?result=' + quote(message), status_code=303)


@router.get('/commerce')
def commerce(request: Request, result: str = ''):
    with request.app.state.session_factory() as db:
        require_admin(request, db)
        config = request.app.state.settings
        payments = db.scalars(select(Payment).order_by(Payment.id.desc()).limit(100)).all()
        orders = db.scalars(select(Order).order_by(Order.id.desc()).limit(40)).all()
        verified_ids = set(db.scalars(select(PaymentVerification.payment_id).where(PaymentVerification.status == 'verified')))
        acquirers = db.scalars(select(BotPaymentProvider)).all()
        acquirer_status = {(row.bot_id, row.provider): row.enabled for row in acquirers}
        legacy = {'portmone': all((config.portmone_login, config.portmone_password, config.portmone_payee_id)),
                  'wayforpay': bool(config.wayforpay_merchant_account and config.wayforpay_secret_key)}
        payment_ready = {}
        for payment in payments:
            key = (payment.bot_id, payment.provider)
            bot = db.get(Bot, payment.bot_id)
            payment_ready[payment.id] = acquirer_status[key] if key in acquirer_status else (
                bot is not None and bot.key == 'food_porn' and legacy.get(payment.provider, False))
        return page(request, 'commerce.html', db, payments=payments, orders=orders, verified_ids=verified_ids,
            products=db.scalars(select(Product).order_by(Product.id.desc()).limit(100)).all(),
            bots=db.scalars(select(Bot).order_by(Bot.id)).all(),
            pending=sum(p.id not in verified_ids for p in payments), result=result[:140],
            connected=legacy, acquirer_count=sum(row.enabled for row in acquirers),
            payment_ready=payment_ready)


@router.post('/commerce/products')
def add_product(request: Request, token: str = Form(), bot_id: int = Form(), sku: str = Form(),
                title: str = Form(), price: str = Form(), currency: str = Form('UAH')):
    with request.app.state.session_factory() as db:
        admin = require_admin(request, db)
        csrf(request, token)
        if not db.get(Bot, bot_id):
            raise HTTPException(422, 'Неизвестный бот')
        sku = field(sku, 120, True)
        title = field(title, 200, True)
        currency = field(currency, 3, True).upper()
        if len(currency) != 3 or not currency.isalpha():
            raise HTTPException(422, 'Invalid currency')
        if db.scalar(select(Product).where(Product.bot_id == bot_id, Product.sku == sku)):
            raise HTTPException(409, 'SKU уже существует в этом боте')
        product = Product(bot_id=bot_id, sku=sku, title=title, price=money(price), currency=currency)
        db.add(product); db.flush()
        audit(db, 'product.create', str(product.id), admin.id)
        db.commit()
        return RedirectResponse('/commerce', status_code=303)


@router.post('/bots/{bot_id}/products/{product_id}')
def edit_bot_product(request: Request, bot_id: int, product_id: int,
                     token: str = Form(), title: str = Form(), price: str = Form(),
                     currency: str = Form(), active: bool = Form(False)):
    with request.app.state.session_factory() as db:
        admin = require_admin(request, db, owner=True); csrf(request, token)
        product = db.get(Product, product_id)
        if not product or product.bot_id != bot_id:
            raise HTTPException(404)
        currency = field(currency, 3, True).upper()
        if currency not in ('UAH', 'USD', 'EUR'):
            raise HTTPException(422, 'Поддерживаются UAH, USD, EUR')
        title = field(title, 200, True)
        amount = money(price)
        if amount <= 0:
            raise HTTPException(422, 'Цена должна быть положительной')
        product.title, product.price, product.currency, product.active = title, amount, currency, active
        audit(db, 'product.update', str(product.id), admin.id, 'Future catalog; historical order prices untouched')
        db.commit()
        return RedirectResponse(f'/bots/{bot_id}?view=services', status_code=303)


@router.get('/commerce/orders/{order_id}')
def order_detail(request: Request, order_id: int):
    with request.app.state.session_factory() as db:
        require_admin(request, db)
        order = db.get(Order, order_id)
        if order is None:
            raise HTTPException(404)
        return page(request, 'order_detail.html', db, order=order,
            items=db.scalars(select(OrderItem).where(OrderItem.order_id == order.id)).all(),
            payments=db.scalars(select(Payment).where(Payment.order_id == order.id)).all())


@router.post('/commerce/payments/{payment_id}/verify')
def reconcile(request: Request, payment_id: int, token: str = Form()):
    with request.app.state.session_factory() as db:
        admin = require_admin(request, db)
        csrf(request, token)
        payment = db.get(Payment, payment_id)
        if payment is None:
            raise HTTPException(404)
        try:
            reference = verify_payment(db, payment, request.app.state.settings)
            audit(db, 'payment.verified', str(payment_id), admin.id, f'{payment.provider}:{reference}')
            db.commit()
            message = 'Платеж подтвержден провайдером'
        except VerificationError as exc:
            db.rollback()
            message = str(exc)
        return RedirectResponse('/commerce?result=' + quote(message), status_code=303)


def build_report(db, days=30, bot_id=0):
    days = days if days in (7, 30, 90, 365) else 30
    start = datetime.now(timezone.utc) - timedelta(days=days)
    bot = db.get(Bot, bot_id) if bot_id else None
    if bot_id and not bot:
        raise HTTPException(404, 'Бот не найден')
    identities = db.scalars(select(Identity).where(Identity.bot_id == bot_id) if bot else select(Identity)).all()
    customer_ids = {i.customer_id for i in identities} if bot else None
    customers = db.scalars(select(Customer).where(Customer.created_at >= start)).all()
    if bot:
        customers = [c for c in customers if c.id in customer_ids]
    orders = db.scalars(select(Order).where(Order.created_at >= start, *((Order.bot_id == bot_id,) if bot else ()))).all()
    payments = db.scalars(select(Payment).where(Payment.created_at >= start, *((Payment.bot_id == bot_id,) if bot else ()))).all()
    proofs = {p.payment_id for p in db.scalars(select(PaymentVerification).where(PaymentVerification.status == 'verified'))}
    confirmed = [p for p in payments if p.id in proofs]
    expenses = db.scalars(select(Expense).where(Expense.spent_at >= start, *((Expense.bot_id == bot_id,) if bot else ()))).all()
    projects = db.scalars(select(Project)).all() if not bot else db.scalars(select(Project).join(Deal, Project.deal_id == Deal.id).where(Deal.bot_id == bot_id)).all()
    project_ids = {p.id for p in projects}
    milestones = db.scalars(select(Milestone)).all()
    milestones = [m for m in milestones if m.project_id in project_ids]
    overdue = [p for p in projects if p.due_at and (p.due_at if p.due_at.tzinfo else p.due_at.replace(tzinfo=timezone.utc)) < datetime.now(timezone.utc)
               and p.status not in ('completed', 'paused')]
    activities = db.scalars(select(Activity).where(Activity.created_at >= start, *((Activity.bot_id == bot_id,) if bot else ()))).all()
    campaigns = db.scalars(select(Campaign).where(Campaign.created_at >= start, *((Campaign.bot_id == bot_id,) if bot else ()))).all()
    campaign_ids = [c.id for c in campaigns]
    deliveries = db.scalars(select(Delivery).where(Delivery.campaign_id.in_(campaign_ids))).all() if campaign_ids else []
    tickets = db.scalars(select(Ticket).where(Ticket.status != 'resolved', *((Ticket.bot_id == bot_id,) if bot else ()))).all()
    invoices = db.scalars(select(Invoice).where(Invoice.created_at >= start)).all()
    if bot:
        invoices = [row for row in invoices if row.customer_id in customer_ids]
    touches = db.scalars(select(MarketingTouch).where(MarketingTouch.created_at >= start,
        *((MarketingTouch.bot_id == bot_id,) if bot else ()))).all()
    sources = Counter(c.source for c in customers)
    providers = Counter(p.provider for p in payments)
    revenue, cost = defaultdict(Decimal), defaultdict(Decimal)
    for p in confirmed:
        revenue[p.currency] += p.amount
    for e in expenses:
        cost[e.currency] += e.amount
    finance = [{'currency': c, 'revenue': revenue[c], 'expenses': cost[c], 'balance': revenue[c] - cost[c]}
               for c in sorted(set(revenue) | set(cost))]
    series = []
    daily_customers = Counter(c.created_at.date().isoformat() for c in customers)
    daily_orders = Counter(o.created_at.date().isoformat() for o in orders)
    daily_revenue = defaultdict(Decimal)
    daily_expenses = defaultdict(Decimal)
    for p in confirmed:
        if p.currency == 'UAH':
            daily_revenue[p.created_at.date().isoformat()] += p.amount
    for expense in expenses:
        if expense.currency == 'UAH':
            daily_expenses[expense.spent_at.date().isoformat()] += expense.amount
    for delta in range(min(days, 30) - 1, -1, -1):
        day = (datetime.now(timezone.utc) - timedelta(days=delta)).date().isoformat()
        series.append({'day': day, 'customers': daily_customers[day], 'orders': daily_orders[day],
                       'uah': daily_revenue[day], 'expenses_uah': daily_expenses[day]})
    return dict(days=days, bot=bot, customers=len(customers), orders=len(orders), confirmed=len(confirmed),
        pending=len(payments) - len(confirmed), sources=sources.most_common(), providers=providers.most_common(),
        finance=finance, projects=len(projects), overdue=overdue,
        milestones_done=sum(m.status == 'completed' for m in milestones), milestones_total=len(milestones),
        tickets=len(tickets), activities=len(activities), campaigns=len(campaigns),
        invoices=len(invoices), invoices_overdue=sum(i.status=='issued' and i.due_at is not None and
            (i.due_at if i.due_at.tzinfo else i.due_at.replace(tzinfo=timezone.utc)) < datetime.now(timezone.utc)
            for i in invoices), marketing_touches=len(touches),
        sent=sum(d.status == 'sent' for d in deliveries), failed=sum(d.status in ('failed', 'unknown') for d in deliveries),
        series=series)


@router.get('/reports')
def reports(request: Request, days: int = 30, bot_id: int = 0):
    with request.app.state.session_factory() as db:
        require_admin(request, db)
        return page(request, 'reports.html', db, data=build_report(db, days, bot_id),
                    bots=db.scalars(select(Bot).order_by(Bot.id)).all())


@router.get('/reports/export.csv')
def report_export(request: Request, days: int = 30, bot_id: int = 0):
    with request.app.state.session_factory() as db:
        require_admin(request, db)
        data = build_report(db, days, bot_id)
        stream = io.StringIO()
        writer = csv.writer(stream)
        writer.writerow(['date', 'new_customers', 'orders', 'verified_revenue_UAH'])
        for row in data['series']:
            writer.writerow([csv_cell(row['day']), row['customers'], row['orders'], row['uah']])
        writer.writerow([])
        writer.writerow(['currency', 'verified_revenue', 'expenses', 'balance'])
        for row in data['finance']:
            writer.writerow([row['currency'], row['revenue'], row['expenses'], row['balance']])
        stream.seek(0)
        return StreamingResponse(iter([stream.getvalue().encode('utf-8-sig')]), media_type='text/csv',
                                 headers={'Content-Disposition': 'attachment; filename="crm_report.csv"'})
