from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from sqlalchemy import func, select
from .models import Activity, Bot, BotIncident, Campaign, Customer, Deal, Delivery, Expense, Identity, Invoice, MarketingMetric, Order, Payment, PaymentVerification, Project, Stage, Ticket


def summary(db, days=30):
    start = datetime.now(timezone.utc) - timedelta(days=days)
    def count(model, clause=None):
        stmt = select(func.count()).select_from(model)
        return db.scalar(stmt.where(clause)) if clause is not None else db.scalar(stmt)
    revenue = defaultdict(Decimal)
    expenses = defaultdict(Decimal)
    for currency, amount in db.execute(select(Payment.currency, func.sum(Payment.amount)).join(PaymentVerification, PaymentVerification.payment_id == Payment.id).where(PaymentVerification.status == 'verified', Payment.created_at >= start).group_by(Payment.currency)):
        revenue[currency] = amount or Decimal('0')
    for currency, amount in db.execute(select(Expense.currency, func.sum(Expense.amount)).where(Expense.spent_at >= start).group_by(Expense.currency)):
        expenses[currency] = amount or Decimal('0')
    currencies = sorted(set(revenue) | set(expenses))
    finance = [{'currency': c, 'revenue': revenue[c], 'expenses': expenses[c], 'balance': revenue[c]-expenses[c]} for c in currencies]
    pipeline_value = [{'currency': currency, 'amount': amount} for currency,amount in db.execute(
        select(Deal.currency,func.sum(Deal.budget)).join(Stage,Deal.stage_id==Stage.id)
        .where(Stage.terminal=='').group_by(Deal.currency)).all()]
    bot_rows = []
    for bot in db.scalars(select(Bot).order_by(Bot.id)):
        bot_rows.append({'bot': bot,
            'customers': count(Identity, Identity.bot_id == bot.id),
            'orders': count(Order, Order.bot_id == bot.id),
            'payments': db.scalar(select(func.count()).select_from(PaymentVerification).join(Payment,Payment.id==PaymentVerification.payment_id).where(Payment.bot_id==bot.id,PaymentVerification.status=='verified'))})
    daily = {str(day): n for day, n in db.execute(select(func.date(Activity.created_at), func.count()).where(Activity.created_at >= start).group_by(func.date(Activity.created_at))).all()}
    trend = []
    for n in range(min(days, 30)-1, -1, -1):
        day = (datetime.now(timezone.utc)-timedelta(days=n)).date().isoformat()
        trend.append({'day': day, 'count': daily.get(day, 0)})
    peak = max([p['count'] for p in trend] + [1])
    for p in trend:
        p['height'] = max(3, round(p['count'] / peak * 100))
    return {
        'customers': count(Customer), 'new_customers': count(Customer, Customer.created_at >= start),
        'customer_sources': db.execute(select(Customer.source,func.count()).group_by(Customer.source).order_by(func.count().desc()).limit(5)).all(),
        'awaiting_verification': db.scalar(select(func.count()).select_from(Payment).where(~Payment.id.in_(select(PaymentVerification.payment_id)))) or 0,
        'bot_incidents': count(BotIncident, BotIncident.resolved_at.is_(None)),
        'invoices_due': count(Invoice, (Invoice.status == 'issued') & (Invoice.due_at < datetime.now(timezone.utc))),
        'marketing_leads': db.scalar(select(func.sum(MarketingMetric.leads)).where(MarketingMetric.day >= start)) or 0,
        'active_customers': count(Customer, Customer.last_seen_at >= start),
        'orders': count(Order), 'period_orders': count(Order, Order.created_at >= start),
        'paid_orders': db.scalar(select(func.count(func.distinct(Payment.order_id))).select_from(Payment)
            .join(PaymentVerification,PaymentVerification.payment_id==Payment.id)
            .where(PaymentVerification.status=='verified')) or 0,
        'bots': count(Bot), 'campaigns': count(Campaign),
        'sent': count(Delivery, Delivery.status == 'sent'),
        'failed': count(Delivery, Delivery.status.in_(['failed', 'unknown'])),
        'activities': count(Activity, Activity.created_at >= start),
        'deals': count(Deal), 'won_deals': count(Deal, Deal.stage_id.in_(select(Stage.id).where(Stage.terminal=='won'))),
        'projects': count(Project), 'active_projects': count(Project, Project.status.not_in(['completed','paused'])),
        'open_tickets': count(Ticket, Ticket.status!='resolved'), 'pipeline_value': pipeline_value,
        'finance': finance, 'bot_rows': bot_rows, 'trend': trend, 'days': days,
    }
