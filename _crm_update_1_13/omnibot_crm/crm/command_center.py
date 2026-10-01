"""Actionable per-bot operating picture with verified revenue only."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, Request
from sqlalchemy import func, select

from .models import (Bot, BotConfigApplied, BotConfigRelease, BotIncident, Customer,
                     Expense, Identity, Order, Payment, PaymentVerification, Ticket)
from .security import require_admin
from .web import page

router = APIRouter()


def picture(db, bot, now):
    latest = db.scalar(select(BotConfigRelease).where(BotConfigRelease.bot_id == bot.id)
        .order_by(BotConfigRelease.version.desc()).limit(1))
    applied = db.scalar(select(BotConfigApplied).where(BotConfigApplied.bot_id == bot.id))
    threshold = now - timedelta(days=30)
    receipts = db.execute(select(PaymentVerification.currency,
        func.sum(PaymentVerification.amount)).join(Payment,
        Payment.id == PaymentVerification.payment_id).where(Payment.bot_id == bot.id,
        PaymentVerification.status == 'verified', PaymentVerification.created_at >= threshold)
        .group_by(PaymentVerification.currency)).all()
    spending = db.execute(select(Expense.currency, func.sum(Expense.amount))
        .where(Expense.bot_id == bot.id, Expense.spent_at >= threshold)
        .group_by(Expense.currency)).all()
    clients = db.scalar(select(func.count(Identity.customer_id.distinct()))
        .where(Identity.bot_id == bot.id)) or 0
    pending = db.scalar(select(func.count(Payment.id)).where(Payment.bot_id == bot.id,
        Payment.status == 'reported_paid',
        ~Payment.id.in_(select(PaymentVerification.payment_id)))) or 0
    incidents = db.scalar(select(func.count(BotIncident.id)).where(BotIncident.bot_id == bot.id,
        BotIncident.resolved_at.is_(None))) or 0
    tickets = db.scalar(select(func.count(Ticket.id)).where(Ticket.bot_id == bot.id,
        Ticket.status == 'open')) or 0
    orders = db.scalar(select(func.count(Order.id)).where(Order.bot_id == bot.id,
        Order.created_at >= threshold)) or 0
    last_seen = bot.last_seen_at
    if last_seen and last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=timezone.utc)
    online = bool(last_seen and last_seen >= now - timedelta(minutes=10))
    config_status = ('Нет публикации' if latest is None else
        'Не применена' if applied is None else
        'Ошибка применения' if applied.status == 'failed' else
        'Актуальна' if applied.status == 'applied' and applied.revision == latest.revision else
        'Ожидает обновления')
    alerts = []
    if bot.active and not online: alerts.append('Нет свежего сигнала бота')
    if config_status != 'Актуальна': alerts.append('Конфигурация: ' + config_status.lower())
    if pending: alerts.append(f'{pending} оплат ожидают проверки')
    if incidents: alerts.append(f'{incidents} открытых инцидентов')
    if tickets: alerts.append(f'{tickets} обращений ждут ответа')
    return dict(bot=bot, online=online, last_seen=last_seen,
        config_status=config_status, applied=applied, latest=latest,
        clients=clients, orders=orders, pending=pending, incidents=incidents,
        tickets=tickets, alerts=alerts, received=dict(receipts), spent=dict(spending))


@router.get('/control-center')
def control_center(request: Request, bot_id: int = 0):
    with request.app.state.session_factory() as db:
        require_admin(request, db)
        bots = db.scalars(select(Bot).order_by(Bot.name)).all()
        selected = bot_id if any(bot.id == bot_id for bot in bots) else 0
        now = datetime.now(timezone.utc)
        rows = [picture(db, bot, now) for bot in bots if not selected or bot.id == selected]
        return page(request, 'control_center.html', db, bots=bots, selected=selected,
                    rows=rows, timestamp=now)
