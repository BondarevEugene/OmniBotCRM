"""Read-only source import of Food_Porn people and menu production metadata."""
import argparse

from sqlalchemy import create_engine, inspect, select, text

from crm.config import settings
from crm.db import make_session_factory
from crm.models import (Bot, Customer, FoodPornCustomerProfile, FoodPornGeneratedFile,
                        FoodPornMenuItem, FoodPornMenuSnapshot, Identity, Order)


def source_engine(url):
    url = url.replace('postgresql+asyncpg://', 'postgresql+psycopg://', 1)
    return create_engine(url, pool_pre_ping=True,
                         connect_args={'connect_timeout': 5} if url.startswith('postgresql+') else {})


def source_inventory(url):
    """Counts and capabilities only: never return people, secrets or connection URLs."""
    source = source_engine(url)
    try:
        available = set(inspect(source).get_table_names())
        if not {'customers', 'menus'} <= available:
            raise RuntimeError('The source requires customers and menus tables')
        names = (name for name in ('customers', 'menus', 'menu_items', 'generated_files') if name in available)
        with source.connect() as conn:
            return {name: conn.scalar(text(f'SELECT COUNT(*) FROM {name}')) for name in names}
    finally:
        source.dispose()


def upsert_source(db, model, order_id, source_id, values):
    row = db.scalar(select(model).where(model.order_id == order_id, model.source_id == source_id))
    if row is None:
        row = model(order_id=order_id, source_id=source_id)
        db.add(row)
    for name, value in values.items():
        setattr(row, name, value)


def import_data(source_url: str, target_url: str, *, dry_run=False):
    """Import idempotently. Paid flags and stored payment IDs never become revenue."""
    source = source_engine(source_url)
    target, factory = make_session_factory(target_url)
    customers = orders = 0
    try:
        inspector = inspect(source)
        available = set(inspector.get_table_names())
        if not {'customers', 'menus'} <= available:
            raise RuntimeError('The source requires customers and menus tables')
        person_columns = {col['name'] for col in inspector.get_columns('customers')}
        menu_columns = {col['name'] for col in inspector.get_columns('menus')}
        with source.connect() as old, factory() as db:
            bot = db.scalar(select(Bot).where(Bot.key == 'food_porn'))
            if bot is None:
                raise RuntimeError('Register food_porn first: python -m crm.cli seed-bots')
            for person in old.execute(text('SELECT id, telegram_user_id, name, phone, language, last_seen_at '
                                           'FROM customers ORDER BY id')).mappings():
                telegram_id = str(person['telegram_user_id'])
                identity = db.scalar(select(Identity).where(Identity.bot_id == bot.id,
                                                            Identity.external_user_id == telegram_id))
                if identity is None:
                    customer = Customer(name=str(person['name'] or '')[:200], phone=person['phone'] or None,
                                        language=str(person['language'] or 'uk').lower()[:10], source='food_porn',
                                        last_seen_at=person['last_seen_at'])
                    db.add(customer); db.flush()
                    identity = Identity(bot_id=bot.id, customer_id=customer.id, external_user_id=telegram_id,
                                        chat_id=telegram_id)
                    db.add(identity)
                else:
                    customer = db.get(Customer, identity.customer_id)
                    customer.name = str(person['name'] or customer.name)[:200]
                    customer.phone = person['phone'] or customer.phone
                    customer.last_seen_at = person['last_seen_at'] or customer.last_seen_at
                customers += 1
                if {'country', 'city'} <= person_columns:
                    location = old.execute(text('SELECT country, city FROM customers WHERE id=:id'),
                                           {'id': person['id']}).mappings().one()
                    profile = db.scalar(select(FoodPornCustomerProfile).where(
                        FoodPornCustomerProfile.customer_id == customer.id))
                    if profile is None:
                        profile = FoodPornCustomerProfile(customer_id=customer.id)
                        db.add(profile)
                    profile.country = str(location['country'] or '')[:100]
                    profile.city = str(location['city'] or '')[:100]
                optional = ('is_paid', 'cover_photo_path', 'spread_photo_path', 'error_message', 'payment_system')
                fields = ('id', 'status') + tuple(name for name in optional if name in menu_columns)
                for menu in old.execute(text('SELECT ' + ', '.join(fields) +
                                             ' FROM menus WHERE customer_id=:id ORDER BY id'),
                                        {'id': person['id']}).mappings():
                    external_id = str(menu['id'])
                    order = db.scalar(select(Order).where(Order.bot_id == bot.id, Order.external_id == external_id))
                    status = str(menu['status']).lower()[:30]
                    if status == 'paid' or menu.get('is_paid'):
                        status = 'reported_paid'
                    if order is None:
                        order = Order(bot_id=bot.id, module_key='menu', customer_id=customer.id, external_id=external_id,
                                      title=f'Меню #{external_id}', status=status, amount=0, currency='UAH')
                        db.add(order); db.flush()
                    elif order.status != 'paid':
                        order.status = status
                    if not order.module_key:
                        order.module_key = 'menu'
                    snapshot = db.scalar(select(FoodPornMenuSnapshot).where(FoodPornMenuSnapshot.order_id == order.id))
                    if snapshot is None:
                        snapshot = FoodPornMenuSnapshot(order_id=order.id)
                        db.add(snapshot)
                    snapshot.cover_uploaded = bool(menu.get('cover_photo_path'))
                    snapshot.spread_uploaded = bool(menu.get('spread_photo_path'))
                    snapshot.has_error = bool(menu.get('error_message'))
                    snapshot.payment_provider_reported = str(menu.get('payment_system') or '')[:50]
                    if 'menu_items' in available:
                        items = old.execute(text('SELECT id, category, position, title, generation_status '
                                                 'FROM menu_items WHERE menu_id=:id'), {'id': menu['id']}).mappings()
                        for item in items:
                            upsert_source(db, FoodPornMenuItem, order.id, item['id'], {
                                'category': str(item['category']).lower()[:30], 'position': item['position'],
                                'title': str(item['title'] or '')[:200],
                                'generation_status': str(item['generation_status'] or 'pending').lower()[:30]})
                    if 'generated_files' in available:
                        files = old.execute(text('SELECT id, kind, path FROM generated_files WHERE menu_id=:id'),
                                            {'id': menu['id']}).mappings()
                        for item in files:
                            upsert_source(db, FoodPornGeneratedFile, order.id, item['id'], {
                                'kind': str(item['kind']).lower()[:30],
                                'source_path': str(item['path'] or '')[:2000]})
                    orders += 1
            if dry_run:
                db.rollback()
            else:
                db.commit()
        return customers, orders
    finally:
        source.dispose(); target.dispose()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Read-only Food_Porn source import')
    parser.add_argument('--source-url', help='Default: FOOD_PORN_SOURCE_DATABASE_URL from CRM .env')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    config = settings()
    url = args.source_url or config.food_porn_source_database_url
    if not url:
        parser.error('Set FOOD_PORN_SOURCE_DATABASE_URL in the CRM .env')
    customers, menus = import_data(url, config.database_url, dry_run=args.dry_run)
    print(f'Customers scanned: {customers}, menus scanned: {menus}; dry_run={args.dry_run}')
