"""Merchant initiated payment lookups. Never trust browser or bot payment flags."""
import hashlib
import hmac
import base64
import json
import re
from types import SimpleNamespace
from decimal import Decimal, InvalidOperation
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select

from .models import Bot, BotPaymentProvider, Identity, Order, PaymentVerification
from .acquirers import effective_credentials
from .bot_settings import setting_value


class VerificationError(Exception):
    pass


def _amount(value):
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number <= 0 or number.as_tuple().exponent < -2:
            raise VerificationError('Некорректная сумма провайдера')
        return number
    except (InvalidOperation, ValueError, TypeError):
        raise VerificationError('Некорректная сумма провайдера') from None


def _portmone(config, reference, client):
    if not all((config.portmone_login, config.portmone_password, config.portmone_payee_id)):
        raise VerificationError('Укажите учетные данные Portmone в .env')
    # The gateway defaults to the last month; explicitly search the order's recent history.
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=30)
    payload = {'method': 'result', 'params': {'data': {
        'login': config.portmone_login, 'password': config.portmone_password,
        'payeeId': config.portmone_payee_id, 'shopOrderNumber': reference,
        'status': '', 'startDate': start.strftime('%d.%m.%Y'),
        'endDate': end.strftime('%d.%m.%Y')}}, 'id': '1'}
    response = client.post('https://www.portmone.com.ua/gateway/', json=payload, timeout=12)
    response.raise_for_status()
    rows = response.json()
    if not isinstance(rows, list):
        raise VerificationError('Portmone не вернул список операций')
    matches = [r for r in rows if isinstance(r, dict) and
               str(r.get('shopOrderNumber')) == reference and
               str(r.get('payee_id', r.get('payeeId'))) == config.portmone_payee_id and
               r.get('status') == 'PAYED' and str(r.get('chargeback', 'N')).upper() == 'N']
    if len(matches) != 1 or not matches[0].get('shopBillId'):
        raise VerificationError('Оплата Portmone не подтверждена однозначно')
    return _amount(matches[0].get('billAmount')), 'UAH', str(matches[0]['shopBillId'])


def _wayforpay(config, reference, client):
    account, secret = config.wayforpay_merchant_account, config.wayforpay_secret_key
    if not account or not secret:
        raise VerificationError('Укажите учетные данные WayForPay в .env')
    signature = hmac.new(secret.encode(), f'{account};{reference}'.encode(), hashlib.md5).hexdigest()
    response = client.post('https://api.wayforpay.com/api', json={
        'transactionType': 'CHECK_STATUS', 'merchantAccount': account,
        'orderReference': reference, 'merchantSignature': signature, 'apiVersion': 1}, timeout=12)
    response.raise_for_status()
    row = response.json()
    if not isinstance(row, dict) or row.get('merchantAccount') != account or row.get('orderReference') != reference:
        raise VerificationError('Ответ WayForPay относится к другому магазину или заказу')
    signed_fields = ('merchantAccount', 'orderReference', 'amount', 'currency', 'authCode', 'cardPan', 'transactionStatus', 'reasonCode')
    if any(row.get(f) is None for f in signed_fields):
        raise VerificationError('Неполный ответ WayForPay')
    expected = hmac.new(secret.encode(), ';'.join(str(row[f]) for f in signed_fields).encode(), hashlib.md5).hexdigest()
    if not hmac.compare_digest(str(row.get('merchantSignature', '')).lower(), expected):
        raise VerificationError('Неверная подпись WayForPay')
    if row.get('transactionStatus') != 'Approved' or row.get('refundAmount') not in (None, 0, '0', '0.00'):
        raise VerificationError('WayForPay не подтверждает оплаченную операцию без возврата')
    return _amount(row['amount']), str(row['currency']).upper(), str(row['authCode'])


def _liqpay(config, reference, client):
    public, private = config.public_key, config.private_key
    if not public or not private:
        raise VerificationError('Настройте ключи LiqPay')
    data = base64.b64encode(json.dumps({'version': 7, 'public_key': public,
        'action': 'status', 'order_id': reference}, separators=(',', ':')).encode()).decode()
    signature = base64.b64encode(hashlib.sha1((private + data + private).encode()).digest()).decode()
    response = client.post('https://www.liqpay.ua/api/request', data={
        'data': data, 'signature': signature}, timeout=12, follow_redirects=False)
    response.raise_for_status()
    row = response.json()
    if not isinstance(row, dict) or row.get('public_key') != public or row.get('order_id') != reference:
        raise VerificationError('Ответ LiqPay относится к другому магазину или заказу')
    if row.get('result') != 'ok' or row.get('status') != 'success' or row.get('action') != 'pay':
        raise VerificationError('LiqPay не подтверждает окончательную оплату')
    provider_id = str(row.get('payment_id') or '')
    if not provider_id:
        raise VerificationError('Нет ID операции LiqPay')
    return _amount(row.get('amount')), str(row.get('currency', '')).upper(), provider_id


def _paypal(config, reference, client):
    if config.environment != 'live':
        raise VerificationError('PayPal sandbox не подтверждает реальную выручку')
    if not all((config.client_id, config.client_secret, config.merchant_id)):
        raise VerificationError('Настройте Client ID, secret и Merchant ID PayPal')
    if not re.fullmatch(r'[A-Za-z0-9]{10,64}', reference):
        raise VerificationError('Для PayPal укажите настоящий Orders v2 ID в заказе CRM')
    base = 'https://api-m.paypal.com'
    auth = client.post(base + '/v1/oauth2/token', data={'grant_type': 'client_credentials'},
        auth=(config.client_id, config.client_secret), timeout=12, follow_redirects=False)
    auth.raise_for_status()
    token = auth.json().get('access_token', '')
    if not token:
        raise VerificationError('PayPal не вернул OAuth access token')
    response = client.get(base + '/v2/checkout/orders/' + reference,
        headers={'Authorization': 'Bearer ' + token}, timeout=12, follow_redirects=False)
    response.raise_for_status()
    row = response.json()
    if not isinstance(row, dict) or row.get('id') != reference or row.get('status') != 'COMPLETED':
        raise VerificationError('PayPal order не подтвержден как COMPLETED')
    units = row.get('purchase_units') or []
    if len(units) != 1 or units[0].get('payee', {}).get('merchant_id') != config.merchant_id:
        raise VerificationError('PayPal merchant или число purchase units не совпадает')
    captures = units[0].get('payments', {}).get('captures') or []
    if len(captures) != 1 or captures[0].get('status') != 'COMPLETED' or not captures[0].get('id'):
        raise VerificationError('PayPal capture не подтвержден однозначно')
    captured = captures[0]['amount']
    return _amount(captured.get('value')), str(captured.get('currency_code', '')).upper(), str(captures[0]['id'])


def _telegram_stars(token, charge_id, client, order_reference, user_id):
    """Match a real successful-payment charge against the bot's Star ledger.

    All pages must be inspected, including refunds; an incomplete ledger is not proof.
    """
    if not token or not re.fullmatch(r'[\w:-]{1,120}', charge_id):
        raise VerificationError('Нет токена или ID платежа Telegram Stars')
    matches = []
    for page in range(50):
        response = client.get(f'https://api.telegram.org/bot{token}/getStarTransactions',
            params={'offset': page * 100, 'limit': 100}, timeout=15, follow_redirects=False)
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict) or body.get('ok') is not True:
            raise VerificationError('Telegram не вернул журнал операций Stars')
        transactions = body.get('result', {}).get('transactions')
        if not isinstance(transactions, list):
            raise VerificationError('Некорректный журнал операций Stars')
        matches.extend(row for row in transactions if isinstance(row, dict) and row.get('id') == charge_id)
        if len(transactions) < 100:
            break
    else:
        raise VerificationError('Журнал Stars длиннее доступного окна проверки')
    if len(matches) != 1 or type(matches[0].get('amount')) is not int or matches[0]['amount'] <= 0:
        raise VerificationError('Операция Stars не подтверждена однозначно или возвращена')
    source = matches[0].get('source')
    if (not isinstance(source, dict) or source.get('type') != 'user' or
            source.get('transaction_type') != 'invoice_payment' or
            source.get('invoice_payload') != order_reference or
            not isinstance(source.get('user'), dict) or str(source['user'].get('id')) != user_id):
        raise VerificationError('Покупатель или счет Stars не совпадает с заказом')
    return Decimal(matches[0]['amount']), 'XTR', charge_id


def merchant_for_payment(db, payment, config):
    """Prefer the specific bot account; never fall back after disabling it."""
    row = db.scalar(select(BotPaymentProvider).where(BotPaymentProvider.bot_id == payment.bot_id,
                                                      BotPaymentProvider.provider == payment.provider))
    if row is not None:
        if not row.enabled:
            raise VerificationError('Эквайер выключен для этого бота')
        try:
            data = effective_credentials(db, payment.bot_id, payment.provider, config.encryption_key, row)
        except RuntimeError:
            raise VerificationError('Не удается расшифровать эквайер этого бота') from None
        if payment.provider == 'portmone':
            return SimpleNamespace(portmone_login=data.get('login', ''),
                portmone_password=data.get('password', ''), portmone_payee_id=data.get('payee_id', ''))
        if payment.provider == 'wayforpay':
            return SimpleNamespace(wayforpay_merchant_account=data.get('merchant_account', ''),
                wayforpay_secret_key=data.get('secret_key', ''))
        if payment.provider == 'liqpay':
            return SimpleNamespace(public_key=data.get('public_key', ''), private_key=data.get('private_key', ''))
        if payment.provider == 'paypal':
            return SimpleNamespace(client_id=data.get('client_id', ''), client_secret=data.get('client_secret', ''),
                merchant_id=data.get('merchant_id', ''), environment=data.get('environment', ''))
    bot = db.get(Bot, payment.bot_id)
    if bot and bot.key == 'food_porn' and payment.provider in ('portmone', 'wayforpay'):
        # Compatibility for deployments configured before per-bot accounts existed.
        return config
    raise VerificationError('Настройте отдельный эквайер в карточке этого бота')


def verify_payment(db, payment, config, client=None):
    """Record proof only after server-side provider lookup and exact amount match."""
    if payment.provider not in ('portmone', 'wayforpay', 'liqpay', 'paypal', 'telegram_stars'):
        raise VerificationError('Для этого провайдера пока нет проверки статуса')
    order = db.get(Order, payment.order_id) if payment.order_id else None
    if not order or order.bot_id != payment.bot_id or order.customer_id != payment.customer_id:
        raise VerificationError('Платеж должен быть связан с заказом и клиентом')
    if payment.module_key and order.module_key and payment.module_key != order.module_key:
        raise VerificationError('Платеж относится к другому продукту')
    if _amount(payment.amount) != _amount(order.amount) or payment.currency != order.currency:
        raise VerificationError('Сумма заказа и платежа должна совпадать')
    if payment.provider == 'telegram_stars':
        if payment.currency != 'XTR' or payment.amount != payment.amount.to_integral_value():
            raise VerificationError('Stars должны быть целым числом в XTR')
        bot = db.get(Bot, payment.bot_id)
        identity = db.scalar(select(Identity).where(Identity.bot_id == payment.bot_id,
            Identity.customer_id == payment.customer_id))
        if not identity:
            raise VerificationError('Свяжите покупателя Stars с Telegram ID клиента')
        try:
            token = setting_value(db, bot.id, 'telegram_token', config) or config.bot_tokens.get(bot.key)
        except RuntimeError:
            raise VerificationError('Токен Telegram недоступен') from None
        merchant = SimpleNamespace(token=token)
    else:
        merchant = merchant_for_payment(db, payment, config)
    transport = client or httpx.Client()
    try:
        if payment.provider == 'telegram_stars':
            amount, currency, provider_id = _telegram_stars(merchant.token, payment.external_id,
                transport, order.external_id, identity.external_user_id)
        elif payment.provider == 'portmone':
            amount, currency, provider_id = _portmone(merchant, order.external_id, transport)
        elif payment.provider == 'wayforpay':
            amount, currency, provider_id = _wayforpay(merchant, order.external_id, transport)
        elif payment.provider == 'liqpay':
            amount, currency, provider_id = _liqpay(merchant, order.external_id, transport)
        else:
            amount, currency, provider_id = _paypal(merchant, order.external_id, transport)
    except (httpx.HTTPError, ValueError) as exc:
        raise VerificationError('Не удалось получить корректный ответ платежного провайдера') from exc
    finally:
        if client is None:
            transport.close()
    if amount != payment.amount or currency != payment.currency:
        raise VerificationError('Сумма или валюта у провайдера не совпадает с платежом')
    existing = db.scalar(select(PaymentVerification).where(
        PaymentVerification.provider == payment.provider,
        PaymentVerification.provider_reference == provider_id,
        PaymentVerification.payment_id != payment.id))
    if existing:
        raise VerificationError('Операция провайдера уже привязана к другому платежу')
    proof = db.scalar(select(PaymentVerification).where(
        PaymentVerification.payment_id == payment.id,
        PaymentVerification.provider_reference == provider_id))
    if not proof:
        db.add(PaymentVerification(payment_id=payment.id, provider=payment.provider,
            provider_reference=provider_id, amount=amount, currency=currency, status='verified'))
    payment.status = 'paid'
    order.status = 'paid'
    db.flush()
    return provider_id
