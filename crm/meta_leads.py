"""Verified Meta Lead Ads webhook and first-party CRM lead intake."""
import hashlib
import hmac
import json
import re

import httpx
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import PlainTextResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from .bot_settings import decrypt, encrypt
from .models import (Activity, Customer, Deal, MarketingAdAccount, MarketingCampaign,
                     MarketingChannel, MarketingLead, MarketingTouch, Pipeline, Stage)
from .security import audit, csrf, require_admin

router = APIRouter()


def account_credentials(account, encryption_key):
    try:
        return json.loads(decrypt(account.encrypted_credentials, encryption_key))
    except (RuntimeError, ValueError):
        raise HTTPException(503, 'Meta connection unavailable') from None


@router.post('/marketing/channels/{channel_id}/meta-leads')
def configure_leads(request: Request, channel_id: int, token: str = Form(),
                    page_id: str = Form(), app_secret: str = Form(''),
                    verify_token: str = Form(''), page_access_token: str = Form(''),
                    pipeline_id: int = Form()):
    with request.app.state.session_factory() as db:
        admin = require_admin(request, db, owner=True); csrf(request, token)
        account = db.scalar(select(MarketingAdAccount).where(MarketingAdAccount.channel_id == channel_id))
        if not account or account.channel.platform not in ('facebook', 'instagram'):
            raise HTTPException(404)
        pipeline = db.get(Pipeline, pipeline_id)
        if not pipeline or not pipeline.active or not re.fullmatch(r'\d{2,40}', page_id):
            raise HTTPException(422, 'Укажите Page ID и действующую воронку')
        if db.scalar(select(MarketingAdAccount.id).where(MarketingAdAccount.meta_page_id == page_id,
                                                         MarketingAdAccount.id != account.id)):
            raise HTTPException(409, 'Эта страница Meta уже подключена к другому каналу')
        values = account_credentials(account, request.app.state.settings.encryption_key)
        values['app_secret'] = app_secret or values.get('app_secret', '')
        values['verify_token'] = verify_token or values.get('verify_token', '')
        values['page_access_token'] = page_access_token or values.get('page_access_token', '')
        if (not 16 <= len(values['app_secret']) <= 256 or
            not 24 <= len(values['verify_token']) <= 256 or
            not values['page_access_token'] or len(values['page_access_token']) > 4096):
            raise HTTPException(422, 'Укажите App Secret, Verify Token и Page Access Token')
        account.encrypted_credentials = encrypt(json.dumps(values), request.app.state.settings.encryption_key)
        account.meta_page_id, account.meta_pipeline_id = page_id, pipeline_id
        audit(db, 'marketing.meta_leads.configure', str(channel_id), admin.id, 'credentials hidden')
        db.commit()
        return RedirectResponse('/marketing#ads-connectors', status_code=303)


def webhook_account(db, channel_id):
    account = db.scalar(select(MarketingAdAccount).where(MarketingAdAccount.channel_id == channel_id))
    if not account or not account.enabled or not account.channel.active or account.channel.platform not in ('facebook', 'instagram') or not account.meta_page_id or not account.meta_pipeline_id:
        raise HTTPException(404)
    return account


@router.get('/api/v1/meta/lead-ads/{channel_id}')
def verify_webhook(request: Request, channel_id: int):
    with request.app.state.session_factory() as db:
        account = webhook_account(db, channel_id)
        credentials = account_credentials(account, request.app.state.settings.encryption_key)
        params = request.query_params
        expected = credentials.get('verify_token', '')
        if params.get('hub.mode') != 'subscribe' or not expected or not hmac.compare_digest(params.get('hub.verify_token', ''), expected):
            raise HTTPException(403, 'Invalid verification token')
        return PlainTextResponse(params.get('hub.challenge', '')[:1024])


def find_customer(db, email, phone):
    by_email = db.scalars(select(Customer).where(func.lower(Customer.email) == email.lower()).limit(2)).all() if email else []
    by_phone = db.scalars(select(Customer).where(Customer.phone == phone).limit(2)).all() if phone else []
    candidates = {row.id: row for row in by_email + by_phone}
    if len(candidates) == 1 and len(by_email) <= 1 and len(by_phone) <= 1:
        return next(iter(candidates.values()))
    return None


def first_stage(db, pipeline_id):
    stage = db.scalar(select(Stage).where(Stage.pipeline_id == pipeline_id).order_by(Stage.position))
    if stage is None:
        raise HTTPException(409, 'Создайте этап воронки для лидов Meta')
    return stage


def import_lead(db, account, value, credentials, client):
    lead_id = str(value.get('leadgen_id', ''))
    page_id = str(value.get('page_id', ''))
    if not re.fullmatch(r'\d{2,100}', lead_id) or page_id != account.meta_page_id:
        raise HTTPException(422, 'Webhook относится к другой странице или лиду')
    existing = db.scalar(select(MarketingLead.id).where(MarketingLead.channel_id == account.channel_id,
                                                        MarketingLead.external_id == lead_id))
    if existing:
        return False
    version = account.api_version or 'v25.0'
    if not re.fullmatch(r'v\d{1,2}\.0', version):
        raise HTTPException(503, 'Meta Graph API version unavailable')
    try:
        response = client.get(f'https://graph.facebook.com/{version}/{lead_id}',
            params={'fields': 'id,created_time,field_data,ad_id,campaign_id,form_id'},
            headers={'Authorization': 'Bearer ' + credentials['page_access_token']},
            follow_redirects=False)
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError, KeyError):
        raise HTTPException(503, 'Не удалось получить лид Meta; повторите webhook') from None
    if str(payload.get('id', '')) != lead_id or not isinstance(payload.get('field_data'), list):
        raise HTTPException(503, 'Meta вернула неполные данные лида')
    fields = {}
    for item in payload['field_data']:
        if isinstance(item, dict) and isinstance(item.get('name'), str) and isinstance(item.get('values'), list):
            fields[item['name']] = str(item['values'][0])[:255] if item['values'] else ''
    email = fields.get('email', '').strip()[:255]
    phone = fields.get('phone_number', '').strip()[:40]
    name = (fields.get('full_name') or ' '.join((fields.get('first_name', ''), fields.get('last_name', '')))).strip()[:200]
    customer = find_customer(db, email, phone)
    if customer is None:
        customer = Customer(name=name, email=email or None, phone=phone or None, source='meta_lead_ads')
        db.add(customer); db.flush()
    else:
        customer.name = customer.name or name
        customer.email = customer.email or email or None
        customer.phone = customer.phone or phone or None
    campaign = None
    external_campaign = str(payload.get('campaign_id') or '')
    if re.fullmatch(r'\d{2,100}', external_campaign):
        campaign = db.scalar(select(MarketingCampaign).where(MarketingCampaign.channel_id == account.channel_id,
                                                            MarketingCampaign.external_id == external_campaign))
        if campaign is None:
            campaign = MarketingCampaign(channel_id=account.channel_id, external_id=external_campaign,
                title='Meta campaign ' + external_campaign, utm_campaign=f'meta_{account.channel_id}_{external_campaign}'[:120],
                currency=account.currency, budget=0)
            db.add(campaign); db.flush()
        if not db.scalar(select(MarketingTouch.id).where(MarketingTouch.customer_id == customer.id,
                                                     MarketingTouch.campaign_id == campaign.id)):
            db.add(MarketingTouch(customer_id=customer.id, campaign_id=campaign.id, medium='meta_lead_ads'))
    stage = first_stage(db, account.meta_pipeline_id)
    deal = Deal(title='Заявка Meta · ' + (name or lead_id), customer_id=customer.id,
                pipeline_id=account.meta_pipeline_id, stage_id=stage.id, service='other',
                source='meta_lead_ads', currency=account.currency, budget=0)
    db.add(deal); db.flush()
    db.add(MarketingLead(channel_id=account.channel_id, external_id=lead_id, page_id=page_id,
        form_id=str(payload.get('form_id') or value.get('form_id') or '')[:80],
        ad_id=str(payload.get('ad_id') or '')[:80], campaign_id=campaign.id if campaign else None,
        customer_id=customer.id, deal_id=deal.id))
    db.add(Activity(customer_id=customer.id, kind='meta.lead', summary='Новая заявка из формы Meta'))
    db.flush()
    return True


@router.post('/api/v1/meta/lead-ads/{channel_id}')
async def receive_webhook(request: Request, channel_id: int):
    raw = await request.body()
    if len(raw) > 65536:
        raise HTTPException(413)
    with request.app.state.session_factory() as db:
        account = webhook_account(db, channel_id)
        credentials = account_credentials(account, request.app.state.settings.encryption_key)
        signature = request.headers.get('X-Hub-Signature-256', '')
        expected = 'sha256=' + hmac.new(credentials.get('app_secret', '').encode(), raw, hashlib.sha256).hexdigest()
        if not credentials.get('app_secret') or not hmac.compare_digest(signature, expected):
            raise HTTPException(401, 'Invalid Meta webhook signature')
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            raise HTTPException(422, 'Invalid Meta webhook') from None
        if not isinstance(payload, dict) or payload.get('object') != 'page':
            raise HTTPException(422, 'Unsupported Meta webhook')
        entries = payload.get('entry', [])
        if not isinstance(entries, list) or len(entries) > 50:
            raise HTTPException(422, 'Invalid Meta webhook entries')
        count = 0
        lead_changes = sum(1 for entry in entries if isinstance(entry, dict)
                           for change in (entry.get('changes') if isinstance(entry.get('changes'), list) else [])
                           if isinstance(change, dict) and change.get('field') == 'leadgen')
        if lead_changes > 25:
            raise HTTPException(413, 'Слишком много лидов в одном webhook')
        with httpx.Client(timeout=15, follow_redirects=False) as client:
            for entry in entries:
                if not isinstance(entry, dict) or str(entry.get('id', '')) != account.meta_page_id:
                    raise HTTPException(422, 'Unrecognized Page ID')
                changes = entry.get('changes', [])
                if not isinstance(changes, list) or len(changes) > 50:
                    raise HTTPException(422, 'Invalid Meta webhook changes')
                for change in changes:
                    if not isinstance(change, dict) or change.get('field') != 'leadgen':
                        continue
                    value = change.get('value')
                    if not isinstance(value, dict):
                        raise HTTPException(422, 'Invalid Meta lead event')
                    count += bool(import_lead(db, account, value, credentials, client))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(503, 'Попробуйте повторить webhook') from None
        return {'ok': True, 'created': count}
