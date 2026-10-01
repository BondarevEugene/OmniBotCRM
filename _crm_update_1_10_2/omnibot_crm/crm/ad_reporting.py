"""Read-only daily campaign reporting from first-party advertising APIs.

No audience uploads, campaign mutations or conversion imports. No remote response
or credential is stored in audit records; accounts are isolated by channel ID.
"""
import json
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

import httpx
from sqlalchemy import select

from .bot_settings import decrypt
from .models import MarketingAdAccount, MarketingCampaign, MarketingMetric


class AdSyncError(Exception):
    pass


def _amount(value):
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount < 0 or amount > Decimal('999999999999.99'):
            raise ValueError()
        return amount.quantize(Decimal('0.01'))
    except (InvalidOperation, ValueError):
        raise AdSyncError('Некорректное значение расходов/конверсий в API') from None


def _count(value):
    try:
        number = int(value)
        if number < 0 or number > 1000000000:
            raise ValueError()
        return number
    except (TypeError, ValueError):
        raise AdSyncError('Некорректное значение показов/кликов в API') from None


def _rows_meta(client, account, credentials, start, end):
    token = credentials.get('access_token', '')
    if not token:
        raise AdSyncError('Добавьте access token Meta')
    external = account.external_id
    if not re.fullmatch(r'\d{2,30}', external):
        raise AdSyncError('ID рекламного аккаунта Meta должен содержать только цифры')
    version = account.api_version or 'v25.0'
    if not re.fullmatch(r'v\d{1,2}\.0', version):
        raise AdSyncError('Некорректная версия Meta Graph API')
    url = f'https://graph.facebook.com/{version}/act_{external}/insights'
    params = {'level': 'campaign', 'time_increment': '1', 'limit': '500',
              'fields': 'campaign_id,campaign_name,date_start,impressions,clicks,spend,actions',
              'time_range': json.dumps({'since': start.isoformat(), 'until': end.isoformat()})}
    headers = {'Authorization': 'Bearer ' + token}
    rows = []
    for _ in range(20):
        response = client.get(url, params=params, headers=headers, follow_redirects=False)
        if response.status_code != 200:
            raise AdSyncError(f'Meta Ads: HTTP {response.status_code}; проверьте разрешения и ID аккаунта')
        payload = response.json()
        rows.extend(payload.get('data', []))
        if len(rows) > 10000:
            raise AdSyncError('Слишком много строк Meta; уменьшите период')
        after = payload.get('paging', {}).get('cursors', {}).get('after')
        if not after or not payload.get('paging', {}).get('next'):
            break
        params['after'] = after
    else:
        raise AdSyncError('Достигнут предел страниц Meta')
    for row in rows:
        actions = row.get('actions') or []
        lead = next((x.get('value', '0') for x in actions if x.get('action_type') == 'lead'), '0')
        yield {'id': str(row['campaign_id']), 'name': str(row['campaign_name']),
               'day': date.fromisoformat(row['date_start']), 'impressions': _count(row.get('impressions', '0')),
               'clicks': _count(row.get('clicks', '0')), 'leads': _count(lead),
               'conversions': _amount(lead), 'spend': _amount(row.get('spend', '0'))}


def _rows_google(client, account, credentials, start, end):
    customer_id = account.external_id
    if not re.fullmatch(r'\d{8,12}', customer_id):
        raise AdSyncError('ID клиента Google Ads — только цифры, без дефисов')
    required = ('client_id', 'client_secret', 'refresh_token')
    if any(not credentials.get(k) for k in required):
        raise AdSyncError('Добавьте OAuth client ID, secret и refresh token')
    response = client.post('https://oauth2.googleapis.com/token', data={
        'client_id': credentials['client_id'], 'client_secret': credentials['client_secret'],
        'refresh_token': credentials['refresh_token'], 'grant_type': 'refresh_token'},
        follow_redirects=False)
    if response.status_code != 200 or not response.json().get('access_token'):
        raise AdSyncError(f'Google OAuth: HTTP {response.status_code}; проверьте доступ')
    version = account.api_version or 'v25'
    if not re.fullmatch(r'v\d{1,2}', version):
        raise AdSyncError('Некорректная версия Google Ads API')
    url = f'https://googleads.googleapis.com/{version}/customers/{customer_id}/googleAds:search'
    headers = {'Authorization': 'Bearer ' + response.json()['access_token']}
    if credentials.get('login_customer_id'):
        login = credentials['login_customer_id']
        if not re.fullmatch(r'\d{8,12}', login):
            raise AdSyncError('Некорректный ID управляющего аккаунта')
        headers['login-customer-id'] = login
    query = ('SELECT campaign.id, campaign.name, segments.date, metrics.impressions, '
             'metrics.clicks, metrics.cost_micros, metrics.conversions FROM campaign '
             f"WHERE segments.date BETWEEN '{start.isoformat()}' AND '{end.isoformat()}'")
    page_token = None
    for _ in range(20):
        body = {'query': query}
        if page_token:
            body['pageToken'] = page_token
        response = client.post(url, headers=headers, json=body, follow_redirects=False)
        if response.status_code != 200:
            raise AdSyncError(f'Google Ads: HTTP {response.status_code}; проверьте OAuth и разрешения')
        payload = response.json()
        for row in payload.get('results', []):
            metrics = row.get('metrics', {})
            yield {'id': str(row['campaign']['id']), 'name': str(row['campaign']['name']),
                   'day': date.fromisoformat(row['segments']['date']),
                   'impressions': _count(metrics.get('impressions', '0')),
                   'clicks': _count(metrics.get('clicks', '0')), 'leads': 0,
                   'conversions': _amount(metrics.get('conversions', '0')),
                   'spend': _amount(Decimal(str(metrics.get('costMicros', '0'))) / 1000000)}
        page_token = payload.get('nextPageToken')
        if not page_token:
            break
    else:
        raise AdSyncError('Достигнут предел страниц Google Ads')


def sync_account(db, account: MarketingAdAccount, encryption_key: str, client: httpx.Client,
                 days: int = 30) -> int:
    """Fetch and validate fully before upserting. Caller commits or rolls back."""
    if not account.enabled or not account.channel.active:
        raise AdSyncError('Синхронизация аккаунта выключена')
    if account.channel.platform not in ('facebook', 'instagram', 'google_ads'):
        raise AdSyncError('Для этого канала нет рекламного адаптера')
    try:
        credentials = json.loads(decrypt(account.encrypted_credentials, encryption_key))
    except (RuntimeError, ValueError):
        raise AdSyncError('Не удалось открыть реквизиты; проверьте CRM_ENCRYPTION_KEY') from None
    end = datetime.now(timezone.utc).date() - timedelta(days=1)
    start = end - timedelta(days=min(max(days, 1), 90) - 1)
    adapter = _rows_google if account.channel.platform == 'google_ads' else _rows_meta
    try:
        rows = list(adapter(client, account, credentials, start, end))
    except (httpx.HTTPError, ValueError, KeyError, TypeError, ArithmeticError):
        raise AdSyncError('Рекламный API недоступен или вернул неожиданные данные') from None
    if len(rows) > 10000:
        raise AdSyncError('Слишком много строк; уменьшите период')
    for row in rows:
        if not start <= row['day'] <= end or len(row['name']) > 200 or len(row['id']) > 100:
            raise AdSyncError('Некорректная дата или название рекламной кампании')
    for item in rows:
        campaign = db.scalar(select(MarketingCampaign).where(
            MarketingCampaign.channel_id == account.channel_id,
            MarketingCampaign.external_id == item['id']))
        if campaign is None:
            campaign = MarketingCampaign(channel_id=account.channel_id, external_id=item['id'],
                                         title=item['name'],
                                         utm_campaign=f'ads_{account.channel_id}_{item["id"]}'[:120],
                                         budget=0, currency=account.currency)
            db.add(campaign); db.flush()
        campaign.title = item['name']
        stamp = datetime.combine(item['day'], datetime.min.time(), timezone.utc)
        metric = db.scalar(select(MarketingMetric).where(MarketingMetric.campaign_id == campaign.id,
                                                        MarketingMetric.day == stamp))
        if metric is None:
            metric = MarketingMetric(campaign_id=campaign.id, day=stamp)
            db.add(metric)
        metric.impressions, metric.clicks = item['impressions'], item['clicks']
        metric.leads, metric.conversions, metric.spend = item['leads'], item['conversions'], item['spend']
        metric.origin = 'ads_api'
    account.last_synced_at = datetime.now(timezone.utc)
    account.status, account.last_error = 'ok', ''
    return len(rows)
