"""Owner-entered FX, provider defaults and an auditable operational cashbook."""
import csv
import io
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, StreamingResponse
from sqlalchemy import select

from .acquirers import SHARED_FIELDS, credentials
from .bot_settings import encrypt
from .models import (Bot, BotAccountingProfile, ExchangeRate, Expense, Order, Payment, PaymentVerification,
                     SharedPaymentProvider)
from .security import audit, csrf, require_admin
from .web import csv_cell, page

router = APIRouter()
Q = Decimal('0.01')


def published_rates(db):
    """Latest owner-entered rates as of today, for the signed bot configuration."""
    today = datetime.now(timezone.utc).date()
    rates = db.scalars(select(ExchangeRate).where(ExchangeRate.rate_date <= today)
        .order_by(ExchangeRate.rate_date)).all()
    result = {}
    for row in rates:
        result[row.currency] = {'uah_per_unit': str(row.uah_per_unit),
                                'rate_date': row.rate_date.isoformat(), 'source': row.source}
    return result


def cashbook(db, start: date, end: date, bot_id: int = 0):
    if start > end or (end - start).days > 3660:
        raise HTTPException(422, 'Некорректный период')
    proof_rows = db.execute(select(PaymentVerification, Payment, Order)
        .join(Payment, Payment.id == PaymentVerification.payment_id)
        .outerjoin(Order, Order.id == Payment.order_id)
        .where(PaymentVerification.status == 'verified',
               *((Payment.bot_id == bot_id,) if bot_id else ()))
        .order_by(PaymentVerification.created_at, PaymentVerification.id)).all()
    expenses = db.scalars(select(Expense).where(
        *((Expense.bot_id == bot_id,) if bot_id else ())).order_by(Expense.spent_at)).all()
    rates = db.scalars(select(ExchangeRate).where(ExchangeRate.rate_date <= end)
        .order_by(ExchangeRate.rate_date)).all()
    by_currency = {c: [] for c in ('USD', 'EUR')}
    for rate in rates:
        by_currency[rate.currency].append(rate)

    def convert(amount, currency, day):
        if currency == 'UAH':
            return Decimal('1'), amount.quantize(Q)
        matches = [r for r in by_currency.get(currency, ()) if r.rate_date <= day]
        if not matches:
            return None, None
        rate = matches[-1].uah_per_unit
        return rate, (amount * rate).quantize(Q, rounding=ROUND_HALF_UP)

    rows = []
    seen_payments = set()
    for proof, payment, order in proof_rows:
        if payment.id in seen_payments:
            continue
        seen_payments.add(payment.id)
        day = proof.created_at.date()
        if not start <= day <= end:
            continue
        rate, uah = convert(proof.amount, proof.currency, day)
        rows.append(dict(day=day, kind='Поступление', bot_id=payment.bot_id,
            description=order.title if order else payment.external_id,
            reference=proof.provider_reference, counterparty=f'Клиент #{payment.customer_id}',
            amount=proof.amount, currency=proof.currency, rate=rate, uah=uah,
            provider=payment.provider, payment_id=payment.id))
    for expense in expenses:
        day = expense.spent_at.date()
        if not start <= day <= end:
            continue
        rate, uah = convert(expense.amount, expense.currency, day)
        rows.append(dict(day=day, kind='Расход', bot_id=expense.bot_id,
            description=expense.category, reference=f'Расход #{expense.id}',
            counterparty=expense.vendor, amount=expense.amount,
            currency=expense.currency, rate=rate, uah=uah,
            provider='ручная запись', payment_id=None))
    rows.sort(key=lambda r: (r['day'], r['kind'], r['reference']))
    received, spent = {}, {}
    for row in rows:
        totals = received if row['kind'] == 'Поступление' else spent
        totals[row['currency']] = totals.get(row['currency'], Decimal('0')) + row['amount']
    uah_received = sum((r['uah'] for r in rows if r['kind'] == 'Поступление' and r['uah'] is not None), Decimal('0'))
    uah_spent = sum((r['uah'] for r in rows if r['kind'] == 'Расход' and r['uah'] is not None), Decimal('0'))
    return dict(rows=rows, received=received, spent=spent,
        uah_received=uah_received, uah_spent=uah_spent,
        missing=sum(r['uah'] is None for r in rows))


def filters(start, end, bot_id):
    today = datetime.now(timezone.utc).date()
    try:
        beginning = date.fromisoformat(start) if start else today - timedelta(days=30)
        finish = date.fromisoformat(end) if end else today
    except ValueError:
        raise HTTPException(422, 'Дата в формате YYYY-MM-DD') from None
    if not 0 <= bot_id:
        raise HTTPException(422, 'Некорректный бот')
    return beginning, finish


@router.get('/finance')
def finance(request: Request, start: str = '', end: str = '', bot_id: int = 0):
    beginning, finish = filters(start, end, bot_id)
    with request.app.state.session_factory() as db:
        require_admin(request, db)
        bots = db.scalars(select(Bot).order_by(Bot.name)).all()
        if bot_id and not db.get(Bot, bot_id):
            raise HTTPException(404)
        rates = db.scalars(select(ExchangeRate).order_by(
            ExchangeRate.rate_date.desc(), ExchangeRate.currency).limit(30)).all()
        profile = db.scalar(select(BotAccountingProfile).where(BotAccountingProfile.bot_id == bot_id)) if bot_id else None
        return page(request, 'finance.html', db, data=cashbook(db, beginning, finish, bot_id),
            bots=bots, rates=rates, start=beginning, end=finish, bot_id=bot_id, profile=profile)


@router.post('/bots/{bot_id}/accounting')
def save_accounting(request: Request, bot_id: int, token: str = Form(), owner_name: str = Form(''),
                    tax_id: str = Form(''), tax_group: str = Form(''), iban: str = Form(''),
                    vat_status: str = Form(''), note: str = Form('')):
    with request.app.state.session_factory() as db:
        admin = require_admin(request, db, owner=True); csrf(request, token)
        if not db.get(Bot, bot_id):
            raise HTTPException(404)
        values = [owner_name, tax_id, tax_group, iban, vat_status, note]
        if any(len(v) > size for v, size in zip(values, (200, 30, 30, 50, 60, 2000))):
            raise HTTPException(422, 'Поле слишком длинное')
        profile = db.scalar(select(BotAccountingProfile).where(BotAccountingProfile.bot_id == bot_id))
        if profile is None:
            profile = BotAccountingProfile(bot_id=bot_id)
            db.add(profile)
        (profile.owner_name, profile.tax_id, profile.tax_group,
         profile.iban, profile.vat_status, profile.note) = [v.strip() for v in values]
        profile.updated_by = admin.id
        audit(db, 'bot.accounting.update', str(bot_id), admin.id, 'Profile updated')
        db.commit()
    return RedirectResponse(f'/finance?bot_id={bot_id}#accounting', status_code=303)


@router.post('/finance/rates')
def save_rate(request: Request, token: str = Form(), rate_date: str = Form(),
              currency: str = Form(), uah_per_unit: str = Form(), source: str = Form('Внесен вручную')):
    with request.app.state.session_factory() as db:
        admin = require_admin(request, db, owner=True); csrf(request, token)
        try:
            day = date.fromisoformat(rate_date)
            amount = Decimal(uah_per_unit)
        except (ValueError, InvalidOperation):
            raise HTTPException(422, 'Проверьте дату и курс') from None
        if currency not in ('USD', 'EUR') or not amount.is_finite() or not 0 < amount < 100000 or len(source) > 120:
            raise HTTPException(422, 'Допустим курс USD/EUR к UAH')
        if amount.as_tuple().exponent < -6:
            raise HTTPException(422, 'Не более шести знаков после запятой')
        row = db.scalar(select(ExchangeRate).where(ExchangeRate.currency == currency,
            ExchangeRate.rate_date == day))
        if row is None:
            row = ExchangeRate(currency=currency, rate_date=day)
            db.add(row)
        row.uah_per_unit, row.source, row.updated_by = amount, source.strip() or 'Внесен вручную', admin.id
        audit(db, 'finance.fx.save', f'{day}:{currency}', admin.id, f'UAH per unit={amount}')
        db.commit()
    return RedirectResponse('/finance#rates', status_code=303)


@router.get('/finance/export.csv')
def export_cashbook(request: Request, start: str = '', end: str = '', bot_id: int = 0):
    beginning, finish = filters(start, end, bot_id)
    with request.app.state.session_factory() as db:
        require_admin(request, db)
        if bot_id and not db.get(Bot, bot_id):
            raise HTTPException(404)
        data = cashbook(db, beginning, finish, bot_id)
        profile = db.scalar(select(BotAccountingProfile).where(BotAccountingProfile.bot_id == bot_id)) if bot_id else None
    out = io.StringIO()
    writer = csv.writer(out, delimiter=';')
    if profile:
        writer.writerow(['Владелец', csv_cell(profile.owner_name), 'Группа',
                         csv_cell(profile.tax_group), 'Идентификатор', csv_cell(profile.tax_id)])
        writer.writerow([])
    writer.writerow(['Дата', 'Тип', 'Бот ID', 'Операция', 'Контрагент', 'Провайдер',
                     'Подтверждение', 'Сумма', 'Валюта', 'Курс UAH/ед.', 'Эквивалент UAH'])
    for r in data['rows']:
        writer.writerow([csv_cell(r['day']), csv_cell(r['kind']), r['bot_id'] or '',
            csv_cell(r['description']), csv_cell(r['counterparty']), csv_cell(r['provider']),
            csv_cell(r['reference']), str(r['amount']), r['currency'],
            str(r['rate']) if r['rate'] is not None else '',
            str(r['uah']) if r['uah'] is not None else ''])
    return StreamingResponse(iter([('\ufeff' + out.getvalue()).encode('utf-8')]), media_type='text/csv; charset=utf-8',
        headers={'Content-Disposition': 'attachment; filename="omnifactory_cashbook.csv"'})


@router.get('/finance/providers')
def providers(request: Request):
    with request.app.state.session_factory() as db:
        require_admin(request, db)
        rows = {r.provider: r for r in db.scalars(select(SharedPaymentProvider)).all()}
        return page(request, 'shared_providers.html', db, fields=SHARED_FIELDS, rows=rows)


@router.post('/finance/providers')
async def save_provider(request: Request):
    form = await request.form()
    provider = str(form.get('provider', ''))
    if provider not in SHARED_FIELDS:
        raise HTTPException(422, 'Неизвестный провайдер')
    with request.app.state.session_factory() as db:
        admin = require_admin(request, db, owner=True); csrf(request, str(form.get('token', '')))
        row = db.scalar(select(SharedPaymentProvider).where(SharedPaymentProvider.provider == provider))
        try:
            data = credentials(row, request.app.state.settings.encryption_key) if row else {}
        except RuntimeError:
            raise HTTPException(409, 'Восстановите CRM_ENCRYPTION_KEY') from None
        for key in SHARED_FIELDS[provider]:
            value = str(form.get(key, '')).strip()
            if len(value) > 4000:
                raise HTTPException(422, 'Значение слишком длинное')
            if value:
                if key == 'environment' and value not in ('live', 'sandbox'):
                    raise HTTPException(422, 'Выберите live или sandbox')
                data[key] = value
        if not data:
            raise HTTPException(422, 'Заполните хотя бы одно поле')
        if row is None:
            row = SharedPaymentProvider(provider=provider)
            db.add(row)
        row.encrypted_credentials = encrypt(json.dumps(data, ensure_ascii=False),
                                            request.app.state.settings.encryption_key)
        row.updated_by = admin.id
        audit(db, 'finance.provider.defaults', provider, admin.id, 'values hidden')
        db.commit()
    return RedirectResponse('/finance/providers', status_code=303)
