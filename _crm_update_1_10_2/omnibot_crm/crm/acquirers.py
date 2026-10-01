"""Per-bot merchant configuration. Store every provider field encrypted."""
import json
import re

from fastapi import HTTPException
from sqlalchemy import select

from .bot_settings import decrypt, encrypt
from .models import BotPaymentProvider, SharedPaymentProvider

PROVIDERS = {
    'portmone': ('Portmone', ('login', 'password', 'payee_id')),
    'wayforpay': ('WayForPay', ('merchant_account', 'secret_key')),
    'liqpay': ('LiqPay', ('public_key', 'private_key')),
    'paypal': ('PayPal Orders v2', ('client_id', 'client_secret', 'merchant_id', 'environment')),
    'redsys': ('Redsys · только реестр', ('merchant_code', 'terminal', 'secret_key', 'currency')),
}
PROVIDER_KEY = re.compile(r'^[a-z][a-z0-9_]{0,39}$')
FIELD_KEY = re.compile(r'^[a-z][a-z0-9_]{0,79}$')
SHARED_FIELDS = {
    'portmone': ('login', 'password'),
    'wayforpay': ('secret_key',),
    'liqpay': ('private_key',),
    'paypal': ('client_id', 'client_secret', 'environment'),
}


def credentials(row, encryption_key):
    try:
        result = json.loads(decrypt(row.encrypted_credentials, encryption_key))
        if not isinstance(result, dict):
            raise ValueError()
        return result
    except (RuntimeError, ValueError):
        raise RuntimeError('The bot merchant credentials cannot be decrypted') from None


def effective_credentials(db, bot_id, provider, encryption_key, row=None):
    """Explicit bot account with optional inherited defaults; no other bot's keys."""
    shared = db.scalar(select(SharedPaymentProvider).where(SharedPaymentProvider.provider == provider))
    data = credentials(shared, encryption_key) if shared else {}
    if row is None:
        row = db.scalar(select(BotPaymentProvider).where(BotPaymentProvider.bot_id == bot_id,
            BotPaymentProvider.provider == provider))
    if row:
        data.update(credentials(row, encryption_key))
    return data


def save_acquirer(db, bot_id, provider, label, submitted, custom_json, enabled, encryption_key, admin_id):
    if not PROVIDER_KEY.fullmatch(provider):
        raise HTTPException(422, 'Invalid payment provider name')
    if provider in PROVIDERS:
        label = PROVIDERS[provider][0]
        names = PROVIDERS[provider][1]
        extra = {}
        if custom_json.strip():
            raise HTTPException(422, 'Use the named fields for this provider')
    else:
        label = label.strip()
        if not label or len(label) > 120:
            raise HTTPException(422, 'A provider label is required')
        names = ()
        try:
            extra = json.loads(custom_json or '{}')
        except ValueError:
            raise HTTPException(422, 'Invalid provider JSON') from None
        if not isinstance(extra, dict) or len(extra) > 40 or any(
            not isinstance(k, str) or not FIELD_KEY.fullmatch(k) or
            not isinstance(v, str) or len(v) > 4000 for k, v in extra.items()
        ):
            raise HTTPException(422, 'Provider JSON must contain up to 40 string fields')
    row = db.scalar(select(BotPaymentProvider).where(BotPaymentProvider.bot_id == bot_id,
                                                      BotPaymentProvider.provider == provider))
    try:
        data = credentials(row, encryption_key) if row else {}
    except RuntimeError:
        raise HTTPException(409, 'Restore CRM_ENCRYPTION_KEY before changing this provider') from None
    for name in names:
        value = submitted.get(name, '').strip()
        if len(value) > 4000:
            raise HTTPException(422, 'Provider field too long')
        if value:
            data[name] = value
    # Custom provider JSON replaces specified keys, preserving unspecified secrets.
    data.update(extra)
    combined = effective_credentials(db, bot_id, provider, encryption_key, row) | data
    if enabled and (not combined or any(not combined.get(name) for name in names)):
        raise HTTPException(422, 'Complete the provider credentials before enabling')
    if row is None:
        row = BotPaymentProvider(bot_id=bot_id, provider=provider)
        db.add(row)
    row.label, row.enabled, row.updated_by = label, enabled, admin_id
    row.encrypted_credentials = encrypt(json.dumps(data, ensure_ascii=False), encryption_key)
    return row
