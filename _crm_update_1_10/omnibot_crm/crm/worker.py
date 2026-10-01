"""Run exactly one worker replica for predictable Telegram pacing."""
import argparse
import logging
import time
from datetime import datetime, timedelta, timezone
import httpx
from sqlalchemy import func, select, update
from sqlalchemy.orm import selectinload
from .config import settings
from .db import make_session_factory
from .models import Audit, Campaign, Customer, Delivery, Identity, Payment, PaymentVerification, SyncRun
from .payment_providers import VerificationError, verify_payment
from .bot_settings import setting_value

log=logging.getLogger(__name__)


def utc(): return datetime.now(timezone.utc)


def process_one(factory, tokens, client, config=None):
    with factory() as db:
        # Stale processing rows are marked unknown: Telegram may have delivered a request
        # before our process died, so automatic re-sending might duplicate it.
        db.execute(update(Delivery).where(Delivery.status=='processing',Delivery.next_attempt_at<utc()).values(status='unknown',error='Delivery outcome unknown; review manually'))
        row=db.scalar(select(Delivery).join(Campaign).where(Delivery.status=='queued',Delivery.next_attempt_at<=utc(),Campaign.status.in_(['queued','running'])).order_by(Delivery.id).with_for_update(skip_locked=True).limit(1))
        if not row:
            db.commit(); return False
        campaign=db.get(Campaign,row.campaign_id)
        identity=db.get(Identity,row.identity_id)
        customer=db.get(Customer,identity.customer_id)
        if not customer.marketing_consent or customer.status=='blocked' or identity.blocked or not identity.chat_id:
            row.status='skipped'; row.error='Consent revoked or chat unavailable'; db.commit(); return True
        token = tokens.get(campaign.bot.key)
        if config is not None:
            try:
                token = setting_value(db, campaign.bot_id, 'telegram_token', config) or token
            except RuntimeError:
                row.status='failed'; row.error='Bot token cannot be decrypted'; db.commit(); return True
        if not token:
            row.status='failed'; row.error='Missing bot token'; db.commit(); return True
        row.status='processing'; row.attempts+=1; row.next_attempt_at=utc()+timedelta(minutes=5)
        campaign.status='running'
        db.commit()
        delivery_id=row.id
        chat_id=identity.chat_id
        message=campaign.message
    try:
        response=client.post(f'https://api.telegram.org/bot{token}/sendMessage',json={'chat_id':chat_id,'text':message,'disable_web_page_preview':True},timeout=15)
        result=response.json()
    except (httpx.RequestError,ValueError) as exc:
        # Unknown outcome, do not retry automatically.
        with factory() as db:
            row=db.get(Delivery,delivery_id); row.status='unknown'; row.error='Telegram request failed; inspect worker logs'; db.commit()
        return True
    with factory() as db:
        row=db.get(Delivery,delivery_id)
        if result.get('ok'):
            row.status='sent'; row.telegram_message_id=str(result['result']['message_id']); row.error=None
        elif response.status_code==429 and row.attempts<5:
            retry=max(1,min(3600,int(result.get('parameters',{}).get('retry_after',30))))
            row.status='queued'; row.next_attempt_at=utc()+timedelta(seconds=retry); row.error='Rate limited'
        else:
            row.status='failed'; row.error=str(result.get('description','Telegram error'))[:400]
            if response.status_code==403:
                identity=db.get(Identity,row.identity_id); identity.blocked=True
        db.commit()
    return True


def close_finished(factory):
    with factory() as db:
        active=db.scalars(select(Campaign).where(Campaign.status.in_(['queued','running']))).all()
        for campaign in active:
            pending=db.scalar(select(func.count()).select_from(Delivery).where(Delivery.campaign_id==campaign.id,Delivery.status.in_(['queued','processing'])))
            if pending==0: campaign.status='completed'
        db.commit()


def reconcile_pending(factory, config, client):
    """Periodically ask configured providers about payments reported by bots."""
    with factory() as db:
        rows = db.scalars(select(Payment).where(Payment.order_id.is_not(None), Payment.amount > 0,
            Payment.provider.in_(['portmone', 'wayforpay', 'liqpay', 'paypal']),
            ~Payment.id.in_(select(PaymentVerification.payment_id)))
            .order_by(Payment.id).limit(50)).all()
        for payment in rows:
            try:
                reference = verify_payment(db, payment, config, client)
                db.add(Audit(action='payment.verified.auto', target=str(payment.id),
                    detail=f'{payment.provider}:{reference}'))
                db.commit()
            except VerificationError:
                db.rollback()
            except Exception:
                db.rollback()
                log.warning('Payment provider lookup failed for payment %s', payment.id)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--once',action='store_true')
    args=parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    config=settings(); engine,factory=make_session_factory(config.database_url)
    next_source_sync = 0
    next_payment_sync = 0
    next_ads_sync = 0
    next_database_check = 0
    try:
        with httpx.Client() as client:
            while True:
                if time.monotonic() >= next_database_check:
                    next_database_check = time.monotonic() + 600
                    from .bot_databases import monitor_connections
                    try:
                        monitor_connections(factory, config)
                    except Exception:
                        log.warning('Source database monitoring cycle failed')
                if time.monotonic() >= next_source_sync:
                    next_source_sync = time.monotonic() + 900
                    from .bot_sources import food_porn_source
                    try:
                        with factory() as db:
                            source_url = food_porn_source(db, config)
                    except RuntimeError:
                        source_url = ''
                        log.warning('Cannot decrypt Food Porn source URL; check CRM_ENCRYPTION_KEY')
                    if source_url:
                        from integrations.food_porn_import import import_data
                        try:
                            customers, orders = import_data(source_url, config.database_url)
                            with factory() as db:
                                db.add(SyncRun(source='food_porn', status='success', customers=customers, orders=orders))
                                db.commit()
                            log.info('Food_Porn synchronized: %s customers, %s orders', customers, orders)
                        except Exception:
                            with factory() as db:
                                db.add(SyncRun(source='food_porn', status='failed', detail='Источник недоступен или схема не совпадает'))
                                db.commit()
                            log.warning('Food_Porn sync failed; check source URL and tables')
                if time.monotonic() >= next_payment_sync:
                    next_payment_sync = time.monotonic() + 300
                    reconcile_pending(factory, config, client)
                if time.monotonic() >= next_ads_sync:
                    next_ads_sync = time.monotonic() + 3600
                    from .ad_reporting import AdSyncError, sync_account
                    from .models import MarketingAdAccount
                    with factory() as db:
                        account_ids = db.scalars(select(MarketingAdAccount.id).where(
                            MarketingAdAccount.enabled.is_(True))).all()
                    for account_id in account_ids:
                        with factory() as db:
                            account = db.get(MarketingAdAccount, account_id)
                            try:
                                count = sync_account(db, account, config.encryption_key, client)
                                db.commit()
                                log.info('Ads reporting synchronized: channel %s, %s rows', account.channel_id, count)
                            except AdSyncError as exc:
                                db.rollback()
                                account = db.get(MarketingAdAccount, account_id)
                                account.status, account.last_error = 'failed', str(exc)[:200]
                                db.commit()
                                log.warning('Ads reporting failed for channel %s: %s', account.channel_id, exc)
                            except Exception:
                                db.rollback()
                                log.warning('Ads reporting failed for account %s', account_id)
                worked=process_one(factory,config.bot_tokens,client,config)
                close_finished(factory)
                if args.once: break
                time.sleep(.15 if worked else 2)
    finally: engine.dispose()


if __name__=='__main__': main()
