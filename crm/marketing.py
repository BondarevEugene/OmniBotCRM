"""SEO and social promotion metrics with a provider-neutral signed ingest."""
import csv
import hashlib
import hmac
import io
import json
import time
import re
import httpx
from urllib.parse import quote
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, StreamingResponse
from sqlalchemy import select

from .bot_settings import decrypt, encrypt
from .ingest import field, money
from .models import Bot, MarketingAdAccount, MarketingCampaign, MarketingChannel, MarketingMetric, MarketingTouch, Pipeline, Project, SeoKeyword, SeoRankSnapshot
from .ad_reporting import AdSyncError, sync_account
from .security import audit, csrf, require_admin
from .web import page
from .excel import workbook_response

router = APIRouter()
PLATFORMS = ('seo', 'google_ads', 'google_search_console', 'instagram', 'facebook', 'threads',
             'tiktok', 'youtube', 'linkedin', 'telegram', 'x', 'email', 'referral', 'other')


def day_value(raw):
    try:
        return datetime.fromisoformat(raw).date()
    except (ValueError, TypeError):
        raise HTTPException(422, 'Invalid metric date') from None


def integer(raw):
    if type(raw) is bool:
        raise HTTPException(422, 'Invalid metric count')
    try:
        number = int(raw)
        if number < 0 or number > 1000000000 or str(raw).strip() != str(number):
            raise ValueError()
        return number
    except (TypeError, ValueError):
        raise HTTPException(422, 'Invalid metric count') from None


def upsert_metric(db, campaign, day, impressions, clicks, leads, spend):
    date = datetime.combine(day, datetime.min.time(), timezone.utc)
    row = db.scalar(select(MarketingMetric).where(MarketingMetric.campaign_id == campaign.id, MarketingMetric.day == date))
    if row is None:
        row = MarketingMetric(campaign_id=campaign.id, day=date)
        db.add(row)
    row.impressions, row.clicks, row.leads, row.spend = (
        integer(impressions), integer(clicks), integer(leads), money(spend))
    return row


def overview(db, days):
    days = days if days in (7, 30, 90, 365) else 30
    since = datetime.now(timezone.utc) - timedelta(days=days)
    channels = db.scalars(select(MarketingChannel).order_by(MarketingChannel.id.desc())).all()
    campaigns = db.scalars(select(MarketingCampaign).order_by(MarketingCampaign.id.desc())).all()
    metrics = db.scalars(select(MarketingMetric).where(MarketingMetric.day >= since)).all()
    attributed = Counter(row.campaign_id for row in db.scalars(select(MarketingTouch)).all())
    keywords = db.scalars(select(SeoKeyword).order_by(SeoKeyword.id.desc()).limit(200)).all()
    ranks = db.scalars(select(SeoRankSnapshot).where(SeoRankSnapshot.keyword_id.in_([k.id for k in keywords]))
                       .order_by(SeoRankSnapshot.day.desc())).all() if keywords else []
    rank_history = defaultdict(list)
    for snapshot in ranks:
        if len(rank_history[snapshot.keyword_id]) < 2:
            rank_history[snapshot.keyword_id].append(snapshot)
    by_campaign = defaultdict(lambda: {'impressions': 0, 'clicks': 0, 'leads': 0, 'conversions': Decimal('0'), 'spend': Decimal('0')})
    by_day = defaultdict(lambda: {'clicks': 0, 'leads': 0})
    for metric in metrics:
        row = by_campaign[metric.campaign_id]
        row['impressions'] += metric.impressions
        row['clicks'] += metric.clicks
        row['leads'] += metric.leads
        row['conversions'] += metric.conversions
        row['spend'] += metric.spend
        by_day[metric.day.date().isoformat()]['clicks'] += metric.clicks
        by_day[metric.day.date().isoformat()]['leads'] += metric.leads
    spend_by_currency = defaultdict(Decimal)
    for campaign in campaigns:
        spend_by_currency[campaign.currency] += by_campaign[campaign.id]['spend']
    series = []
    for n in range(min(days, 30)-1, -1, -1):
        day = (datetime.now(timezone.utc) - timedelta(days=n)).date().isoformat()
        series.append({'day': day, **by_day[day]})
    return {'channels': channels, 'campaigns': campaigns, 'stats': by_campaign, 'attributed': attributed,
            'keywords': keywords, 'rank_history': rank_history,
            'spend': dict(spend_by_currency), 'clicks': sum(m.clicks for m in metrics),
            'impressions': sum(m.impressions for m in metrics), 'leads': sum(m.leads for m in metrics),
            'conversions': sum((m.conversions for m in metrics), Decimal('0')),
            'series': series, 'days': days}


@router.get('/marketing')
def marketing(request: Request, days: int = 30, result: str = ''):
    with request.app.state.session_factory() as db:
        require_admin(request, db)
        accounts = db.scalars(select(MarketingAdAccount).order_by(MarketingAdAccount.id)).all()
        return page(request, 'marketing.html', db, data=overview(db, days), platforms=PLATFORMS,
                    ad_accounts=accounts, ad_accounts_by_channel={a.channel_id: a for a in accounts},
                    pipelines=db.scalars(select(Pipeline).where(Pipeline.active.is_(True)).order_by(Pipeline.id)).all(),
                    bots=db.scalars(select(Bot).order_by(Bot.name)).all(),
                    projects=db.scalars(select(Project).order_by(Project.id.desc()).limit(100)).all(),
                    result=result[:140], encryption_ready=bool(request.app.state.settings.encryption_key))


@router.post('/marketing/channels')
def add_channel(request: Request, token: str = Form(), platform: str = Form(),
                account: str = Form(''), url: str = Form(''), project_id: str = Form(''),
                bot_id: str = Form('')):
    with request.app.state.session_factory() as db:
        admin = require_admin(request, db); csrf(request, token)
        if platform not in PLATFORMS:
            raise HTTPException(422, 'Unknown platform')
        if url and (not url.startswith('https://') or len(url) > 500):
            raise HTTPException(422, 'Use an HTTPS account URL')
        if project_id and (not project_id.isdigit() or not db.get(Project, int(project_id))):
            raise HTTPException(422, 'Unknown project')
        if bot_id and (not bot_id.isdigit() or not db.get(Bot, int(bot_id))):
            raise HTTPException(422, 'Unknown bot')
        channel = MarketingChannel(platform=platform, account=field(account, 150), url=url,
                                   project_id=int(project_id) if project_id else None,
                                   bot_id=int(bot_id) if bot_id else None)
        db.add(channel); db.flush()
        audit(db, 'marketing.channel.create', str(channel.id), admin.id)
        db.commit()
        return RedirectResponse('/marketing', status_code=303)


@router.post('/marketing/channels/{channel_id}/bot')
def assign_channel_bot(request: Request, channel_id: int, token: str = Form(),
                       bot_id: str = Form('')):
    with request.app.state.session_factory() as db:
        admin = require_admin(request, db, owner=True); csrf(request, token)
        channel = db.get(MarketingChannel, channel_id)
        if not channel:
            raise HTTPException(404)
        if bot_id and (not bot_id.isdigit() or not db.get(Bot, int(bot_id))):
            raise HTTPException(422, 'Unknown bot')
        channel.bot_id = int(bot_id) if bot_id else None
        audit(db, 'marketing.channel.bot', str(channel_id), admin.id, f'bot_id={bot_id or "shared"}')
        db.commit()
        return RedirectResponse('/marketing', status_code=303)


@router.post('/marketing/channels/{channel_id}/ads-account')
def save_ads_account(request: Request, channel_id: int, token: str = Form(),
                     external_id: str = Form(), currency: str = Form('UAH'), api_version: str = Form(''),
                     access_token: str = Form(''), client_id: str = Form(''), client_secret: str = Form(''),
                     refresh_token: str = Form(''), login_customer_id: str = Form(''),
                     enabled: bool = Form(False)):
    with request.app.state.session_factory() as db:
        admin = require_admin(request, db, owner=True); csrf(request, token)
        channel = db.get(MarketingChannel, channel_id)
        if not channel or channel.platform not in ('facebook', 'instagram', 'google_ads'):
            raise HTTPException(422, 'Выберите Meta или Google Ads канал')
        provider = channel.platform
        if not re.fullmatch(r'\d{8,12}' if provider == 'google_ads' else r'\d{2,30}', external_id):
            raise HTTPException(422, 'ID рекламного аккаунта: цифры без префикса и дефисов')
        currency = currency.upper()
        if not re.fullmatch(r'[A-Z]{3}', currency):
            raise HTTPException(422, 'Введите код валюты ISO 4217')
        version = api_version.strip() or ('v25' if provider == 'google_ads' else 'v25.0')
        if not re.fullmatch(r'v\d{1,2}' if provider == 'google_ads' else r'v\d{1,2}\.0', version):
            raise HTTPException(422, 'Неверный формат версии API')
        account = db.scalar(select(MarketingAdAccount).where(MarketingAdAccount.channel_id == channel_id))
        if account is None:
            account = MarketingAdAccount(channel_id=channel_id, encrypted_credentials='')
            db.add(account)
        values = ({'access_token': access_token} if provider != 'google_ads' else {
            'client_id': client_id, 'client_secret': client_secret,
            'refresh_token': refresh_token, 'login_customer_id': login_customer_id})
        if account.encrypted_credentials:
            try:
                previous = json.loads(decrypt(account.encrypted_credentials, request.app.state.settings.encryption_key))
            except RuntimeError:
                raise HTTPException(409, 'Не удаётся открыть ранее сохранённые реквизиты') from None
            values = {k: v or previous.get(k, '') for k, v in values.items()}
            if provider != 'google_ads':
                values.update({k: previous[k] for k in ('app_secret', 'verify_token', 'page_access_token') if k in previous})
        required = ('client_id', 'client_secret', 'refresh_token') if provider == 'google_ads' else ('access_token',)
        if any(len(v) > 4096 for v in values.values()) or any(not values.get(k) for k in required):
            raise HTTPException(422, 'Введите необходимые реквизиты API')
        account.encrypted_credentials = encrypt(json.dumps(values), request.app.state.settings.encryption_key)
        account.external_id, account.currency, account.api_version = external_id, currency, version
        account.enabled = enabled
        channel.mode = 'ads_api' if enabled else 'manual'
        audit(db, 'marketing.ads_account.save', str(channel_id), admin.id, 'credentials hidden')
        db.commit()
        return RedirectResponse('/marketing#ads-connectors', status_code=303)


@router.post('/marketing/channels/{channel_id}/sync')
def sync_ads_now(request: Request, channel_id: int, token: str = Form()):
    with request.app.state.session_factory() as db:
        admin = require_admin(request, db, owner=True); csrf(request, token)
        account = db.scalar(select(MarketingAdAccount).where(MarketingAdAccount.channel_id == channel_id))
        if not account:
            raise HTTPException(404)
        try:
            with httpx.Client(timeout=20, follow_redirects=False) as client:
                count = sync_account(db, account, request.app.state.settings.encryption_key, client)
            audit(db, 'marketing.ads.sync', str(channel_id), admin.id, f'{count} daily rows')
            db.commit()
            result = f'Загружено {count} дневных строк'
        except AdSyncError as exc:
            db.rollback()
            account = db.scalar(select(MarketingAdAccount).where(MarketingAdAccount.channel_id == channel_id))
            account.status, account.last_error = 'failed', str(exc)[:200]
            db.commit()
            result = 'Синхронизация: ' + str(exc)
        return RedirectResponse('/marketing?result=' + quote(result) + '#ads-connectors', status_code=303)


@router.post('/marketing/channels/{channel_id}/secret')
def set_channel_secret(request: Request, channel_id: int, token: str = Form(), secret: str = Form()):
    with request.app.state.session_factory() as db:
        admin = require_admin(request, db, owner=True); csrf(request, token)
        channel = db.get(MarketingChannel, channel_id)
        if not channel:
            raise HTTPException(404)
        if len(secret) < 24 or len(secret) > 256:
            raise HTTPException(422, 'Use at least 24 random characters')
        channel.inbound_secret = encrypt(secret, request.app.state.settings.encryption_key)
        channel.mode = 'signed_import'
        audit(db, 'marketing.channel.secret', str(channel.id), admin.id, 'secret replaced')
        db.commit()
        return RedirectResponse('/marketing', status_code=303)


@router.post('/marketing/campaigns')
def add_campaign(request: Request, token: str = Form(), channel_id: int = Form(), title: str = Form(),
                 utm_campaign: str = Form(), objective: str = Form(''), budget: str = Form('0'),
                 currency: str = Form('UAH')):
    with request.app.state.session_factory() as db:
        admin = require_admin(request, db); csrf(request, token)
        if not db.get(MarketingChannel, channel_id):
            raise HTTPException(422, 'Unknown channel')
        utm = field(utm_campaign, 120, True)
        if db.scalar(select(MarketingCampaign).where(MarketingCampaign.utm_campaign == utm)):
            raise HTTPException(409, 'UTM campaign already exists')
        curr = field(currency, 3, True).upper()
        if len(curr) != 3 or not curr.isalpha():
            raise HTTPException(422, 'Invalid currency')
        row = MarketingCampaign(channel_id=channel_id, title=field(title, 200, True),
                                utm_campaign=utm, objective=field(objective, 300),
                                budget=money(budget), currency=curr)
        db.add(row); db.flush()
        audit(db, 'marketing.campaign.create', str(row.id), admin.id)
        db.commit()
        return RedirectResponse('/marketing', status_code=303)


@router.post('/marketing/campaigns/{campaign_id}/metrics')
def add_metric(request: Request, campaign_id: int, token: str = Form(), day: str = Form(),
               impressions: str = Form('0'), clicks: str = Form('0'), leads: str = Form('0'), spend: str = Form('0')):
    with request.app.state.session_factory() as db:
        admin = require_admin(request, db); csrf(request, token)
        campaign = db.get(MarketingCampaign, campaign_id)
        if not campaign:
            raise HTTPException(404)
        upsert_metric(db, campaign, day_value(day), impressions, clicks, leads, spend)
        audit(db, 'marketing.metric.upsert', str(campaign.id), admin.id, day)
        db.commit()
        return RedirectResponse('/marketing', status_code=303)


@router.post('/marketing/seo/keywords')
def add_keyword(request: Request, token: str = Form(), project_id: int = Form(), phrase: str = Form(),
                country: str = Form('UA'), target_url: str = Form('')):
    with request.app.state.session_factory() as db:
        admin = require_admin(request, db); csrf(request, token)
        if not db.get(Project, project_id):
            raise HTTPException(422, 'Unknown project')
        clean_phrase = field(phrase, 200, True)
        clean_country = field(country, 2, True).upper()
        if len(clean_country) != 2 or not clean_country.isascii() or not clean_country.isalpha():
            raise HTTPException(422, 'Use a two-letter country code')
        if target_url and (not target_url.startswith('https://') or len(target_url) > 500):
            raise HTTPException(422, 'Use an HTTPS target URL')
        if db.scalar(select(SeoKeyword).where(SeoKeyword.project_id == project_id,
                                              SeoKeyword.phrase == clean_phrase, SeoKeyword.country == clean_country)):
            raise HTTPException(409, 'Keyword already exists')
        row = SeoKeyword(project_id=project_id, phrase=clean_phrase, country=clean_country,
                         target_url=target_url)
        db.add(row); db.flush()
        audit(db, 'seo.keyword.create', str(row.id), admin.id)
        db.commit()
        return RedirectResponse('/marketing#seo', status_code=303)


@router.post('/marketing/seo/keywords/{keyword_id}/rank')
def add_rank(request: Request, keyword_id: int, token: str = Form(), day: str = Form(), position: int = Form()):
    with request.app.state.session_factory() as db:
        admin = require_admin(request, db); csrf(request, token)
        keyword = db.get(SeoKeyword, keyword_id)
        if not keyword:
            raise HTTPException(404)
        if not 1 <= position <= 200:
            raise HTTPException(422, 'Rank must be from 1 to 200')
        date = day_value(day)
        if date > datetime.now(timezone.utc).date():
            raise HTTPException(422, 'Future rankings are not available')
        stamp = datetime.combine(date, datetime.min.time(), timezone.utc)
        snapshot = db.scalar(select(SeoRankSnapshot).where(SeoRankSnapshot.keyword_id == keyword_id,
                                                            SeoRankSnapshot.day == stamp))
        if snapshot is None:
            snapshot = SeoRankSnapshot(keyword_id=keyword_id, day=stamp)
            db.add(snapshot)
        snapshot.position = position
        audit(db, 'seo.rank.upsert', str(keyword_id), admin.id, f'{date}: {position}')
        db.commit()
        return RedirectResponse('/marketing#seo', status_code=303)


@router.post('/api/v1/marketing/metrics')
async def receive_metric(request: Request):
    raw = await request.body()
    if len(raw) > 8192:
        raise HTTPException(413, 'Metric event too large')
    stamp = request.headers.get('X-Timestamp', '')
    channel_id = request.headers.get('X-Channel-ID', '')
    try:
        valid_time = abs(time.time() - int(stamp)) <= 300
        channel_number = int(channel_id)
    except ValueError:
        raise HTTPException(401, 'Invalid signature') from None
    with request.app.state.session_factory() as db:
        channel = db.get(MarketingChannel, channel_number)
        if not channel or not channel.active or not channel.inbound_secret:
            raise HTTPException(401, 'Invalid signature')
        try:
            secret = decrypt(channel.inbound_secret, request.app.state.settings.encryption_key)
        except RuntimeError:
            raise HTTPException(503, 'Marketing key unavailable') from None
        signature = hmac.new(secret.encode(), stamp.encode() + b'.' + raw, hashlib.sha256).hexdigest()
        if not valid_time or not hmac.compare_digest(signature, request.headers.get('X-Signature', '')):
            raise HTTPException(401, 'Invalid signature')
        try:
            payload = json.loads(raw)
        except ValueError:
            raise HTTPException(422, 'Invalid JSON') from None
        if not isinstance(payload, dict):
            raise HTTPException(422, 'Invalid metric')
        campaign = db.scalar(select(MarketingCampaign).where(MarketingCampaign.utm_campaign == field(payload.get('utm_campaign'), 120, True),
                                                             MarketingCampaign.channel_id == channel.id))
        if not campaign:
            raise HTTPException(422, 'Unknown campaign for channel')
        upsert_metric(db, campaign, day_value(payload.get('day')), payload.get('impressions', 0),
                      payload.get('clicks', 0), payload.get('leads', 0), payload.get('spend', 0))
        db.commit()
        return {'ok': True, 'campaign_id': campaign.id}


@router.get('/marketing/export.csv')
def marketing_export(request: Request, days: int = 30):
    with request.app.state.session_factory() as db:
        require_admin(request, db)
        since = datetime.now(timezone.utc) - timedelta(days=days if days in (7,30,90,365) else 30)
        rows = db.scalars(select(MarketingMetric).where(MarketingMetric.day >= since).order_by(MarketingMetric.day)).all()
        stream = io.StringIO(); writer = csv.writer(stream)
        writer.writerow(['day','utm_campaign','platform','impressions','clicks','leads','spend','currency'])
        for row in rows:
            writer.writerow([row.day.date(), "'" + row.campaign.utm_campaign, row.campaign.channel.platform,
                             row.impressions, row.clicks, row.leads, row.spend, row.campaign.currency])
        stream.seek(0)
        return StreamingResponse(iter([stream.getvalue().encode('utf-8-sig')]), media_type='text/csv',
                                 headers={'Content-Disposition':'attachment; filename="marketing.csv"'})


@router.get('/marketing/export.xlsx')
def marketing_export_excel(request: Request, days: int = 30):
    with request.app.state.session_factory() as db:
        require_admin(request, db)
        since = datetime.now(timezone.utc) - timedelta(days=days if days in (7, 30, 90, 365) else 30)
        rows = db.scalars(select(MarketingMetric).where(MarketingMetric.day >= since)
            .order_by(MarketingMetric.day).limit(10001)).all()
        if len(rows) > 10000:
            raise HTTPException(413, 'До 10 000 строк; уменьшите период')
        return workbook_response('marketing.xlsx', [('Реклама',
            ['Дата', 'Кампания', 'Площадка', 'Канал', 'Показы', 'Клики', 'Лиды',
             'Конверсии', 'Расходы', 'Валюта', 'Источник данных'],
            ([r.day, r.campaign.title, r.campaign.channel.platform, r.campaign.channel.account,
              r.impressions, r.clicks, r.leads, r.conversions, r.spend,
              r.campaign.currency, r.origin] for r in rows))])
