"""Food_Porn integration dashboard and idempotent project passport."""
from collections import Counter

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select

from .ingest import money
from .bot_sources import food_porn_source
from .models import (Bot, BotSetting, Customer, FoodPornCustomerProfile,
                     FoodPornGeneratedFile, FoodPornMenuItem, FoodPornMenuSnapshot, Identity,
                     Order, Payment, PaymentVerification, Product, SyncRun)
from .security import audit, csrf, require_admin
from .web import page

router = APIRouter()

PRODUCTS = {
    'menu_digital': 'Гастрономическое меню · электронное',
    'menu_print': 'Гастрономическое меню · печать',
    'collage_digital': 'AI-коллаж · электронный',
    'poster_print': 'AI-постер · печать',
}


def food_bot(db):
    row = db.scalar(select(Bot).where(Bot.key == 'food_porn'))
    if row is None:
        raise HTTPException(404, 'Register food_porn first')
    return row


@router.post('/food-porn/setup')
def setup_food_porn(request: Request, token: str = Form()):
    with request.app.state.session_factory() as db:
        admin = require_admin(request, db, owner=True); csrf(request, token)
        bot = db.scalar(select(Bot).where(Bot.key == 'food_porn'))
        if bot is None:
            bot = Bot(key='food_porn', name='Food Porn')
            db.add(bot); db.flush()
        if bot.name in ('Food Porn', 'Food_Porn') and not bot.description:
            bot.description = 'Telegram · гастрономические меню и AI-коллажи / постеры'
        audit(db, 'food_porn.register', str(bot.id), admin.id, 'No credentials, prices or custom settings copied')
        db.commit()
        return RedirectResponse('/food-porn', status_code=303)


@router.post('/food-porn/catalog')
def save_catalog(request: Request, token: str = Form(), sku: str = Form(),
                 price: str = Form(), currency: str = Form('UAH'), active: bool = Form(False)):
    with request.app.state.session_factory() as db:
        admin = require_admin(request, db, owner=True); csrf(request, token)
        bot = food_bot(db)
        if sku not in PRODUCTS or len(currency) != 3 or not currency.isascii() or not currency.isalpha():
            raise HTTPException(422, 'Unknown product or currency')
        amount = money(price)
        if amount <= 0:
            raise HTTPException(422, 'A real product price must be greater than zero')
        row = db.scalar(select(Product).where(Product.bot_id == bot.id, Product.sku == sku))
        if row is None:
            row = Product(bot_id=bot.id, module_key='menu', sku=sku)
            db.add(row)
        elif not row.module_key:
            row.module_key = 'menu'
        row.title, row.price, row.currency, row.active = PRODUCTS[sku], amount, currency.upper(), active
        row.description = 'Управленческий тариф CRM; настройки оплаты в исходном боте обновляются отдельно'
        audit(db, 'food_porn.catalog.save', sku, admin.id, f'{amount} {currency.upper()}; active={active}')
        db.commit()
        return RedirectResponse('/food-porn', status_code=303)


@router.get('/food-porn')
def food_porn(request: Request):
    with request.app.state.session_factory() as db:
        admin = require_admin(request, db)
        bot = db.scalar(select(Bot).where(Bot.key == 'food_porn'))
        source_counts = None
        source_error = ''
        config = request.app.state.settings
        try:
            source_url = food_porn_source(db, config)
            if source_url:
                from integrations.food_porn_import import source_inventory
                source_counts = source_inventory(source_url)
        except RuntimeError:
            source_url = ''
            source_error = 'Не удаётся расшифровать адрес источника; проверьте CRM_ENCRYPTION_KEY'
        except Exception:
            source_error = 'Источник недоступен или схема отличается'
        if bot is None:
            return page(request, 'food_porn.html', db, admin=admin, bot=None,
                        source_counts=source_counts, source_error=source_error,
                        source_ready=bool(source_url), settings_count=0)
        orders = db.scalars(select(Order).where(Order.bot_id == bot.id).order_by(Order.id.desc()).limit(100)).all()
        status = Counter(db.scalars(select(Order.status).where(Order.bot_id == bot.id)).all())
        generation = Counter(db.scalars(select(FoodPornMenuItem.generation_status)
            .join(Order, FoodPornMenuItem.order_id == Order.id).where(Order.bot_id == bot.id)).all())
        files = Counter(db.scalars(select(FoodPornGeneratedFile.kind)
            .join(Order, FoodPornGeneratedFile.order_id == Order.id).where(Order.bot_id == bot.id)).all())
        verification_count = db.scalar(select(func.count()).select_from(PaymentVerification)
            .join(Payment, PaymentVerification.payment_id == Payment.id)
            .where(Payment.bot_id == bot.id, PaymentVerification.status == 'verified')) or 0
        last_sync = db.scalar(select(SyncRun).where(SyncRun.source == 'food_porn').order_by(SyncRun.id.desc()))
        return page(request, 'food_porn.html', db, bot=bot, source_counts=source_counts,
            source_error=source_error, status=status, generation=generation, files=files,
            orders=orders, verification_count=verification_count, last_sync=last_sync,
            customers=db.scalar(select(func.count()).select_from(Identity).where(Identity.bot_id == bot.id)) or 0,
            menu_count=db.scalar(select(func.count()).select_from(Order).where(Order.bot_id == bot.id)) or 0,
            settings_count=db.scalar(select(func.count()).select_from(BotSetting).where(BotSetting.bot_id == bot.id)) or 0,
            products=db.scalars(select(Product).where(Product.bot_id == bot.id).order_by(Product.id)).all(),
            product_types=PRODUCTS,
            signed=bool(config.ingest_secrets.get('food_porn')) or bool(db.scalar(select(BotSetting.id).where(
                BotSetting.bot_id == bot.id, BotSetting.key == 'ingest_secret', BotSetting.is_configured.is_(True)))),
            token_ready=bool(config.bot_tokens.get('food_porn')) or bool(db.scalar(select(BotSetting.id).where(
                BotSetting.bot_id == bot.id, BotSetting.key == 'telegram_token', BotSetting.is_configured.is_(True)))),
            encryption_ready=bool(config.encryption_key), source_ready=bool(source_url))


@router.get('/food-porn/menus/{order_id}')
def menu_detail(request: Request, order_id: int):
    with request.app.state.session_factory() as db:
        require_admin(request, db)
        bot = food_bot(db)
        order = db.get(Order, order_id)
        if order is None or order.bot_id != bot.id:
            raise HTTPException(404)
        profile = db.scalar(select(FoodPornCustomerProfile).where(FoodPornCustomerProfile.customer_id == order.customer_id))
        snapshot = db.scalar(select(FoodPornMenuSnapshot).where(FoodPornMenuSnapshot.order_id == order.id))
        return page(request, 'food_porn_menu.html', db, order=order, profile=profile,
                    customer=db.get(Customer, order.customer_id), snapshot=snapshot,
                    items=db.scalars(select(FoodPornMenuItem).where(FoodPornMenuItem.order_id == order.id)
                        .order_by(FoodPornMenuItem.category, FoodPornMenuItem.position)).all(),
                    files=db.scalars(select(FoodPornGeneratedFile).where(FoodPornGeneratedFile.order_id == order.id)
                        .order_by(FoodPornGeneratedFile.id)).all())
