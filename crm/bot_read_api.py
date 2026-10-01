"""Bot-scoped, read-only operational API. Never returns customer records or secret values."""
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, select

from .bot_settings import parse_rules
from .ingest import authenticated_bot
from .models import (Bot, BotIncident, BotModule, BotPaymentProvider, BotSetting,
                     Identity, IngestEvent, Order, Payment, PaymentVerification)

router = APIRouter(prefix='/api/v1/bots', tags=['Bot read API'])


def scoped_bot(request, db, bot_key):
    bot_id = authenticated_bot(request, signed_get=True)
    bot = db.get(Bot, bot_id)
    if not bot or bot.key != bot_key:
        raise HTTPException(404, 'Bot not found')
    return bot


def private_json(payload):
    return JSONResponse(payload, headers={'Cache-Control': 'no-store'})


@router.get('/{bot_key}/overview')
def overview(request: Request, bot_key: str, days: int = 30):
    if not 1 <= days <= 90:
        raise HTTPException(422, 'days must be between 1 and 90')
    with request.app.state.session_factory() as db:
        bot = scoped_bot(request, db, bot_key)
        since = datetime.now(timezone.utc) - timedelta(days=days)
        events = db.execute(select(IngestEvent.event_type, func.count()).where(
            IngestEvent.bot_id == bot.id, IngestEvent.created_at >= since)
            .group_by(IngestEvent.event_type)).all()
        event_days = db.scalars(select(IngestEvent.created_at).where(
            IngestEvent.bot_id == bot.id, IngestEvent.created_at >= since)).all()
        series = {}
        for timestamp in event_days:
            day = timestamp.date().isoformat()
            series[day] = series.get(day, 0) + 1
        proofs = db.execute(select(PaymentVerification.payment_id,
            PaymentVerification.currency, PaymentVerification.amount).join(
            Payment, PaymentVerification.payment_id == Payment.id).where(
            Payment.bot_id == bot.id, PaymentVerification.status == 'verified',
            PaymentVerification.created_at >= since).order_by(PaymentVerification.id)).all()
        totals = defaultdict(lambda: Decimal('0'))
        seen_payments = set()
        for payment_id, currency, amount in proofs:
            if payment_id not in seen_payments:
                seen_payments.add(payment_id)
                totals[currency] += amount
        heartbeat = db.scalar(select(IngestEvent.created_at).where(
            IngestEvent.bot_id == bot.id, IngestEvent.event_type == 'bot.heartbeat')
            .order_by(IngestEvent.id.desc()).limit(1))
        problems = db.scalar(select(func.count()).select_from(BotIncident).where(
            BotIncident.bot_id == bot.id, BotIncident.resolved_at.is_(None)))
        return private_json({
            'bot': {'key': bot.key, 'name': bot.name, 'active': bot.active},
            'period_days': days, 'last_heartbeat_at': heartbeat.isoformat() if heartbeat else None,
            'customers': db.scalar(select(func.count()).select_from(Identity).where(Identity.bot_id == bot.id)),
            'orders': db.scalar(select(func.count()).select_from(Order).where(Order.bot_id == bot.id)),
            'open_incidents': problems,
            'events_by_type': dict(events),
            'event_series': [{'day': (datetime.now(timezone.utc)-timedelta(days=i)).date().isoformat(),
                              'count': series.get((datetime.now(timezone.utc)-timedelta(days=i)).date().isoformat(), 0)}
                             for i in range(days-1, -1, -1)],
            'verified_revenue': {currency: str(amount) for currency, amount in totals.items()},
        })


@router.get('/{bot_key}/settings')
def settings_view(request: Request, bot_key: str):
    with request.app.state.session_factory() as db:
        bot = scoped_bot(request, db, bot_key)
        rows = db.scalars(select(BotSetting).where(BotSetting.bot_id == bot.id)
            .order_by(BotSetting.display_order, BotSetting.id)).all()
        providers = db.scalars(select(BotPaymentProvider).where(
            BotPaymentProvider.bot_id == bot.id).order_by(BotPaymentProvider.provider)).all()
        modules = db.scalars(select(BotModule).where(BotModule.bot_id == bot.id)
            .order_by(BotModule.id)).all()
        return private_json({
            'bot': bot.key,
            'settings': [{'key': row.key, 'label': row.label, 'group': row.group_name,
                          'kind': row.kind, 'configured': row.is_configured,
                          'value': row.value if row.is_configured and row.kind != 'secret' else None,
                          'rules': parse_rules(row.schema_json) if row.kind != 'secret' else {},
                          'updated_at': row.updated_at.isoformat() if row.updated_at else None}
                         for row in rows if row.key not in ('ingest_secret', 'source_database_url')],
            'payments': [{'provider': row.provider, 'enabled': row.enabled} for row in providers],
            'modules': [{'key': row.key, 'name': row.name, 'status': row.status} for row in modules],
        })
