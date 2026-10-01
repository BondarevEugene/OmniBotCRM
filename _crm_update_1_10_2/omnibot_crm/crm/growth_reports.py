"""Campaign to customer, deal and verified payment attribution."""
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, Request
from sqlalchemy import select

from .models import (Deal, MarketingCampaign, MarketingLead, MarketingMetric, MarketingTouch,
                     Payment, PaymentVerification)
from .security import require_admin
from .web import page
from .excel import workbook_response

router = APIRouter()


def attribution(db, days=30):
    days = days if days in (7, 30, 90, 365) else 30
    since = datetime.now(timezone.utc) - timedelta(days=days)
    leads = db.scalars(select(MarketingLead).where(MarketingLead.created_at >= since)).all()
    deals = {d.id: d for d in db.scalars(select(Deal).where(Deal.id.in_([lead.deal_id for lead in leads])))} if leads else {}
    rows = defaultdict(lambda: {'leads': 0, 'customers': set(), 'won': 0, 'payments': defaultdict(Decimal), 'payment_count': 0})
    for lead in leads:
        key = (lead.channel_id, lead.campaign_id)
        rows[key]['leads'] += 1
        rows[key]['customers'].add(lead.customer_id)
        deal = deals.get(lead.deal_id)
        if deal and deal.stage.terminal == 'won':
            rows[key]['won'] += 1
    # Each customer has one earliest known campaign touch, preventing a single
    # verified payment from being counted repeatedly across channels.
    first = {}
    touches = db.scalars(select(MarketingTouch).order_by(MarketingTouch.created_at, MarketingTouch.id)).all()
    for touch in touches:
        first.setdefault(touch.customer_id, touch)
    proof_rows = db.execute(select(Payment, PaymentVerification).join(
        PaymentVerification, PaymentVerification.payment_id == Payment.id).where(
        PaymentVerification.status == 'verified', Payment.created_at >= since)).all()
    for payment, proof in proof_rows:
        touch = first.get(payment.customer_id)
        if not touch:
            continue
        touch_time = touch.created_at.replace(tzinfo=timezone.utc) if touch.created_at.tzinfo is None else touch.created_at
        payment_time = payment.created_at.replace(tzinfo=timezone.utc) if payment.created_at.tzinfo is None else payment.created_at
        if payment_time < touch_time:
            continue
        campaign = db.get(MarketingCampaign, touch.campaign_id)
        if not campaign:
            continue
        entry = rows[(campaign.channel_id, campaign.id)]
        entry['payments'][payment.currency] += payment.amount
        entry['payment_count'] += 1
    campaigns = {c.id: c for c in db.scalars(select(MarketingCampaign).where(
        MarketingCampaign.id.in_([key[1] for key in rows if key[1]])))} if rows else {}
    spends = defaultdict(Decimal)
    for metric in db.scalars(select(MarketingMetric).where(MarketingMetric.day >= since)):
        spends[metric.campaign_id] += metric.spend
    report = []
    for (channel_id, campaign_id), item in rows.items():
        campaign = campaigns.get(campaign_id)
        spend = spends[campaign_id] if campaign else Decimal('0')
        revenue_same_currency = item['payments'].get(campaign.currency, Decimal('0')) if campaign else Decimal('0')
        report.append({'channel_id': channel_id, 'campaign': campaigns.get(campaign_id),
                       'leads': item['leads'], 'customers': len(item['customers']),
                       'won': item['won'], 'payment_count': item['payment_count'],
                       'payments': dict(item['payments']), 'spend': spend,
                       'cpl': spend / item['leads'] if item['leads'] else None,
                       'roas': revenue_same_currency / spend if spend else None})
    report.sort(key=lambda row: (row['leads'], row['payment_count']), reverse=True)
    return report, days


@router.get('/marketing/attribution')
def attribution_page(request: Request, days: int = 30):
    with request.app.state.session_factory() as db:
        require_admin(request, db)
        rows, days = attribution(db, days)
        return page(request, 'marketing_attribution.html', db, rows=rows, days=days,
                    leads=sum(row['leads'] for row in rows),
                    won=sum(row['won'] for row in rows),
                    payment_count=sum(row['payment_count'] for row in rows))


@router.get('/marketing/attribution.xlsx')
def export_attribution(request: Request, days: int = 30):
    with request.app.state.session_factory() as db:
        require_admin(request, db)
        rows, _ = attribution(db, days)
        return workbook_response('marketing_attribution.xlsx', [
            ('Кампании', ['Канал', 'Кампания', 'Заявок', 'Клиентов', 'Выиграно', 'Оплат',
                          'Расход', 'Валюта', 'CPL', 'ROAS', 'Подтверждённая выручка'],
             ([row['channel_id'], row['campaign'].title if row['campaign'] else '',
               row['leads'], row['customers'], row['won'], row['payment_count'],
               row['spend'], row['campaign'].currency if row['campaign'] else '',
               row['cpl'], row['roas'], ', '.join(f'{amount} {currency}' for currency,amount in row['payments'].items())]
              for row in rows))])
