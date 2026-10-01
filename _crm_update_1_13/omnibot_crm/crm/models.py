from datetime import date, datetime, timezone
from decimal import Decimal
from sqlalchemy import BigInteger, Boolean, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .db import Base


def now():
    return datetime.now(timezone.utc)


class Stamp:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)


class Admin(Stamp, Base):
    __tablename__ = 'crm_admins'
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default='owner')
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Bot(Stamp, Base):
    __tablename__ = 'crm_bots'
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default='')
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BotSetting(Stamp, Base):
    __tablename__ = 'crm_bot_settings'
    __table_args__ = (UniqueConstraint('bot_id', 'key'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    bot_id: Mapped[int] = mapped_column(ForeignKey('crm_bots.id'), index=True)
    key: Mapped[str] = mapped_column(String(80))
    label: Mapped[str] = mapped_column(String(120))
    group_name: Mapped[str] = mapped_column(String(80), default='general')
    kind: Mapped[str] = mapped_column(String(20), default='text')
    value: Mapped[str] = mapped_column(Text, default='')
    description: Mapped[str] = mapped_column(String(500), default='')
    schema_json: Mapped[str] = mapped_column(Text, default='{}', server_default='{}', nullable=False)
    display_order: Mapped[int] = mapped_column(Integer, default=0)
    is_configured: Mapped[bool] = mapped_column(Boolean, default=True, server_default='1', nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey('crm_admins.id'))


class BotModule(Stamp, Base):
    """Product/workflow within a Telegram bot; never implies a second token."""
    __tablename__ = 'crm_bot_modules'
    __table_args__ = (UniqueConstraint('bot_id', 'key'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    bot_id: Mapped[int] = mapped_column(ForeignKey('crm_bots.id'), index=True)
    key: Mapped[str] = mapped_column(String(80))
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default='')
    status: Mapped[str] = mapped_column(String(20), default='draft')
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey('crm_admins.id'))


class BotScenario(Stamp, Base):
    __tablename__ = 'crm_bot_scenarios'
    __table_args__ = (UniqueConstraint('bot_id', 'key'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    bot_id: Mapped[int] = mapped_column(ForeignKey('crm_bots.id'), index=True)
    key: Mapped[str] = mapped_column(String(80))
    name: Mapped[str] = mapped_column(String(120))
    trigger_kind: Mapped[str] = mapped_column(String(20), default='command')
    trigger_value: Mapped[str] = mapped_column(String(120), default='')
    segment_tags: Mapped[str] = mapped_column(String(500), default='')
    status: Mapped[str] = mapped_column(String(20), default='draft')
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    steps: Mapped[list['BotScenarioStep']] = relationship(back_populates='scenario',
        cascade='all, delete-orphan', order_by='BotScenarioStep.position')


class BotScenarioStep(Base):
    __tablename__ = 'crm_bot_scenario_steps'
    __table_args__ = (UniqueConstraint('scenario_id', 'position'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    scenario_id: Mapped[int] = mapped_column(ForeignKey('crm_bot_scenarios.id'), index=True)
    position: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(20))
    text: Mapped[str] = mapped_column(Text, default='')
    options_json: Mapped[str] = mapped_column(Text, default='{}')
    scenario: Mapped['BotScenario'] = relationship(back_populates='steps')


class BotScenarioGraph(Base):
    __tablename__ = 'crm_bot_scenario_graphs'
    id: Mapped[int] = mapped_column(primary_key=True)
    scenario_id: Mapped[int] = mapped_column(ForeignKey('crm_bot_scenarios.id'), unique=True, index=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    graph_json: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey('crm_admins.id'))


class BotConfigRelease(Stamp, Base):
    """Immutable encrypted runtime configuration published for one bot."""
    __tablename__ = 'crm_bot_config_releases'
    __table_args__ = (UniqueConstraint('bot_id', 'version'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    bot_id: Mapped[int] = mapped_column(ForeignKey('crm_bots.id'), index=True)
    version: Mapped[int] = mapped_column(Integer)
    revision: Mapped[str] = mapped_column(String(64))
    encrypted_payload: Mapped[str] = mapped_column(Text)
    action: Mapped[str] = mapped_column(String(20), default='publish')
    source_version: Mapped[int | None] = mapped_column(Integer)
    created_by: Mapped[int] = mapped_column(ForeignKey('crm_admins.id'))


class BotConfigApplied(Stamp, Base):
    """Last revision a bot process explicitly reported applying."""
    __tablename__ = 'crm_bot_config_applied'
    id: Mapped[int] = mapped_column(primary_key=True)
    bot_id: Mapped[int] = mapped_column(ForeignKey('crm_bots.id'), unique=True, index=True)
    revision: Mapped[str] = mapped_column(String(64), default='')
    release_version: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default='unknown')
    error: Mapped[str] = mapped_column(String(200), default='')
    reported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class BotGraphRelease(Stamp, Base):
    """Validated, immutable executable snapshot of a visual scenario."""
    __tablename__ = 'crm_bot_graph_releases'
    __table_args__ = (UniqueConstraint('scenario_id', 'version'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    bot_id: Mapped[int] = mapped_column(ForeignKey('crm_bots.id'), index=True)
    scenario_id: Mapped[int] = mapped_column(ForeignKey('crm_bot_scenarios.id'), index=True)
    version: Mapped[int] = mapped_column(Integer)
    graph_json: Mapped[str] = mapped_column(Text)
    created_by: Mapped[int] = mapped_column(ForeignKey('crm_admins.id'))


class BotDialogueSession(Stamp, Base):
    __tablename__ = 'crm_bot_dialogue_sessions'
    __table_args__ = (UniqueConstraint('scenario_id', 'external_user_id'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    bot_id: Mapped[int] = mapped_column(ForeignKey('crm_bots.id'), index=True)
    scenario_id: Mapped[int] = mapped_column(ForeignKey('crm_bot_scenarios.id'), index=True)
    release_id: Mapped[int] = mapped_column(ForeignKey('crm_bot_graph_releases.id'))
    external_user_id: Mapped[str] = mapped_column(String(100))
    current_node_id: Mapped[str] = mapped_column(String(50), default='start')
    values_json: Mapped[str] = mapped_column(Text, default='{}')
    status: Mapped[str] = mapped_column(String(20), default='active')
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class BotDialogueEvent(Stamp, Base):
    __tablename__ = 'crm_bot_dialogue_events'
    __table_args__ = (UniqueConstraint('bot_id', 'event_id'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    bot_id: Mapped[int] = mapped_column(ForeignKey('crm_bots.id'), index=True)
    session_id: Mapped[int] = mapped_column(ForeignKey('crm_bot_dialogue_sessions.id'), index=True)
    event_id: Mapped[str] = mapped_column(String(120))
    result_json: Mapped[str] = mapped_column(Text)


class OpenAIOrganization(Stamp, Base):
    """Admin credential is encrypted and never returned to browser or bot APIs."""
    __tablename__ = 'crm_openai_organizations'
    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(160))
    encrypted_admin_key: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(30), default='not_synced')
    last_error: Mapped[str] = mapped_column(String(200), default='')
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OpenAIResource(Stamp, Base):
    __tablename__ = 'crm_openai_resources'
    __table_args__ = (UniqueConstraint('organization_id', 'kind', 'external_id'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey('crm_openai_organizations.id'), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    external_id: Mapped[str] = mapped_column(String(180))
    payload_json: Mapped[str] = mapped_column(Text)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class OpenAICost(Stamp, Base):
    __tablename__ = 'crm_openai_costs'
    __table_args__ = (UniqueConstraint('organization_id', 'bucket_start', 'fingerprint'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey('crm_openai_organizations.id'), index=True)
    bucket_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    bucket_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    fingerprint: Mapped[str] = mapped_column(String(64))
    project_id: Mapped[str] = mapped_column(String(120), default='')
    api_key_id: Mapped[str] = mapped_column(String(120), default='')
    line_item: Mapped[str] = mapped_column(String(200), default='')
    currency: Mapped[str] = mapped_column(String(3), default='USD')
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    quantity_unit: Mapped[str] = mapped_column(String(60), default='')


class OpenAIUsage(Stamp, Base):
    __tablename__ = 'crm_openai_usage'
    __table_args__ = (UniqueConstraint('organization_id', 'family', 'bucket_start', 'fingerprint'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey('crm_openai_organizations.id'), index=True)
    family: Mapped[str] = mapped_column(String(40))
    bucket_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    bucket_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    fingerprint: Mapped[str] = mapped_column(String(64))
    dimensions_json: Mapped[str] = mapped_column(Text)
    metrics_json: Mapped[str] = mapped_column(Text)


class OpenAIBotBinding(Stamp, Base):
    __tablename__ = 'crm_openai_bot_bindings'
    __table_args__ = (UniqueConstraint('bot_id', 'label'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey('crm_openai_organizations.id'), index=True)
    bot_id: Mapped[int] = mapped_column(ForeignKey('crm_bots.id'), index=True)
    label: Mapped[str] = mapped_column(String(120))
    project_id: Mapped[str] = mapped_column(String(120), default='')
    api_key_id: Mapped[str] = mapped_column(String(120), default='')
    notes: Mapped[str] = mapped_column(String(300), default='')


class BotPaymentProvider(Stamp, Base):
    """One isolated merchant account per bot/provider; all values are encrypted."""
    __tablename__ = 'crm_bot_payment_providers'
    __table_args__ = (UniqueConstraint('bot_id', 'provider'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    bot_id: Mapped[int] = mapped_column(ForeignKey('crm_bots.id'), index=True)
    provider: Mapped[str] = mapped_column(String(80))
    label: Mapped[str] = mapped_column(String(120))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    encrypted_credentials: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey('crm_admins.id'))


class SharedPaymentProvider(Stamp, Base):
    """Optional encrypted defaults; a bot must still explicitly enable its account."""
    __tablename__ = 'crm_shared_payment_providers'
    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    encrypted_credentials: Mapped[str] = mapped_column(Text)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey('crm_admins.id'))


class ExchangeRate(Stamp, Base):
    """Owner entered UAH per one unit, dated for reproducible cashbooks."""
    __tablename__ = 'crm_exchange_rates'
    __table_args__ = (UniqueConstraint('currency', 'rate_date'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    currency: Mapped[str] = mapped_column(String(3), index=True)
    rate_date: Mapped[date] = mapped_column(Date, index=True)
    uah_per_unit: Mapped[Decimal] = mapped_column(Numeric(16, 6))
    source: Mapped[str] = mapped_column(String(120), default='Внесен вручную')
    updated_by: Mapped[int] = mapped_column(ForeignKey('crm_admins.id'))


class BotAccountingProfile(Stamp, Base):
    """Owner-entered invoice and bookkeeping identity, scoped to one bot."""
    __tablename__ = 'crm_bot_accounting_profiles'
    id: Mapped[int] = mapped_column(primary_key=True)
    bot_id: Mapped[int] = mapped_column(ForeignKey('crm_bots.id'), unique=True, index=True)
    owner_name: Mapped[str] = mapped_column(String(200), default='')
    tax_id: Mapped[str] = mapped_column(String(30), default='')
    tax_group: Mapped[str] = mapped_column(String(30), default='')
    iban: Mapped[str] = mapped_column(String(50), default='')
    vat_status: Mapped[str] = mapped_column(String(60), default='')
    note: Mapped[str] = mapped_column(Text, default='')
    updated_by: Mapped[int | None] = mapped_column(ForeignKey('crm_admins.id'))


class FoodPornCustomerProfile(Stamp, Base):
    """Source-specific customer fields that do not belong to the common CRM card."""
    __tablename__ = 'crm_food_porn_profiles'
    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey('crm_customers.id'), unique=True, index=True)
    country: Mapped[str] = mapped_column(String(100), default='')
    city: Mapped[str] = mapped_column(String(100), default='')


class FoodPornMenuSnapshot(Stamp, Base):
    """Source menu details; reported payment fields are never trusted as revenue."""
    __tablename__ = 'crm_food_porn_menus'
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey('crm_orders.id'), unique=True, index=True)
    cover_uploaded: Mapped[bool] = mapped_column(Boolean, default=False)
    spread_uploaded: Mapped[bool] = mapped_column(Boolean, default=False)
    has_error: Mapped[bool] = mapped_column(Boolean, default=False)
    payment_provider_reported: Mapped[str] = mapped_column(String(50), default='')


class FoodPornMenuItem(Stamp, Base):
    __tablename__ = 'crm_food_porn_menu_items'
    __table_args__ = (UniqueConstraint('order_id', 'source_id'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey('crm_orders.id'), index=True)
    source_id: Mapped[int] = mapped_column(Integer)
    category: Mapped[str] = mapped_column(String(30))
    position: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(200))
    generation_status: Mapped[str] = mapped_column(String(30), default='pending')


class FoodPornGeneratedFile(Stamp, Base):
    __tablename__ = 'crm_food_porn_generated_files'
    __table_args__ = (UniqueConstraint('order_id', 'source_id'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey('crm_orders.id'), index=True)
    source_id: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(30))
    # Paths live on the bot host. Import metadata only; no public file URLs.
    source_path: Mapped[str] = mapped_column(Text, default='')


class Customer(Stamp, Base):
    __tablename__ = 'crm_customers'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), default='')
    email: Mapped[str | None] = mapped_column(String(255), index=True)
    phone: Mapped[str | None] = mapped_column(String(40), index=True)
    language: Mapped[str] = mapped_column(String(10), default='ru')
    status: Mapped[str] = mapped_column(String(30), default='lead')
    source: Mapped[str] = mapped_column(String(120), default='manual')
    tags: Mapped[str] = mapped_column(String(500), default='')
    notes: Mapped[str] = mapped_column(Text, default='')
    marketing_consent: Mapped[bool] = mapped_column(Boolean, default=False)
    consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    identities: Mapped[list['Identity']] = relationship(back_populates='customer')


class Identity(Stamp, Base):
    __tablename__ = 'crm_identities'
    __table_args__ = (UniqueConstraint('bot_id', 'external_user_id'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    bot_id: Mapped[int] = mapped_column(ForeignKey('crm_bots.id'), index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey('crm_customers.id'), index=True)
    external_user_id: Mapped[str] = mapped_column(String(100))
    chat_id: Mapped[str | None] = mapped_column(String(100))
    blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    customer: Mapped[Customer] = relationship(back_populates='identities')
    bot: Mapped[Bot] = relationship()


class Activity(Stamp, Base):
    __tablename__ = 'crm_activities'
    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey('crm_customers.id'), index=True)
    bot_id: Mapped[int | None] = mapped_column(ForeignKey('crm_bots.id'))
    kind: Mapped[str] = mapped_column(String(60))
    summary: Mapped[str] = mapped_column(Text, default='')


class Order(Stamp, Base):
    __tablename__ = 'crm_orders'
    __table_args__ = (UniqueConstraint('bot_id', 'external_id'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    bot_id: Mapped[int] = mapped_column(ForeignKey('crm_bots.id'), index=True)
    module_key: Mapped[str | None] = mapped_column(String(80), index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey('crm_customers.id'), index=True)
    external_id: Mapped[str] = mapped_column(String(120))
    title: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(30), default='new')
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    currency: Mapped[str] = mapped_column(String(3), default='UAH')
    customer: Mapped[Customer] = relationship()
    bot: Mapped[Bot] = relationship()


class Product(Stamp, Base):
    __tablename__ = 'crm_products'
    __table_args__ = (UniqueConstraint('bot_id', 'sku'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    bot_id: Mapped[int] = mapped_column(ForeignKey('crm_bots.id'), index=True)
    module_key: Mapped[str | None] = mapped_column(String(80), index=True)
    sku: Mapped[str] = mapped_column(String(120))
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default='')
    price: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    currency: Mapped[str] = mapped_column(String(3), default='UAH')
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    bot: Mapped[Bot] = relationship()


class OrderItem(Base):
    __tablename__ = 'crm_order_items'
    __table_args__ = (UniqueConstraint('order_id', 'product_id'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey('crm_orders.id'), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey('crm_products.id'))
    quantity: Mapped[int] = mapped_column(Integer)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    product: Mapped[Product] = relationship()


class Payment(Stamp, Base):
    __tablename__ = 'crm_payments'
    __table_args__ = (UniqueConstraint('bot_id', 'external_id'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    bot_id: Mapped[int] = mapped_column(ForeignKey('crm_bots.id'), index=True)
    module_key: Mapped[str | None] = mapped_column(String(80), index=True)
    order_id: Mapped[int | None] = mapped_column(ForeignKey('crm_orders.id'), index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey('crm_customers.id'), index=True)
    external_id: Mapped[str] = mapped_column(String(120))
    provider: Mapped[str] = mapped_column(String(80), default='manual')
    status: Mapped[str] = mapped_column(String(30), default='pending')
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    currency: Mapped[str] = mapped_column(String(3), default='UAH')
    customer: Mapped[Customer] = relationship()
    bot: Mapped[Bot] = relationship()


class Expense(Stamp, Base):
    __tablename__ = 'crm_expenses'
    id: Mapped[int] = mapped_column(primary_key=True)
    bot_id: Mapped[int | None] = mapped_column(ForeignKey('crm_bots.id'))
    category: Mapped[str] = mapped_column(String(80))
    vendor: Mapped[str] = mapped_column(String(150), default='')
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    currency: Mapped[str] = mapped_column(String(3), default='UAH')
    spent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    memo: Mapped[str] = mapped_column(Text, default='')


class ProjectExpense(Base):
    __tablename__ = 'crm_project_expenses'
    id: Mapped[int] = mapped_column(primary_key=True)
    expense_id: Mapped[int] = mapped_column(ForeignKey('crm_expenses.id'), unique=True)
    project_id: Mapped[int] = mapped_column(ForeignKey('crm_projects.id'), index=True)


class Task(Stamp, Base):
    __tablename__ = 'crm_tasks'
    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int | None] = mapped_column(ForeignKey('crm_customers.id'))
    title: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default='open')
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    customer: Mapped[Customer | None] = relationship()


class Campaign(Stamp, Base):
    __tablename__ = 'crm_campaigns'
    id: Mapped[int] = mapped_column(primary_key=True)
    bot_id: Mapped[int] = mapped_column(ForeignKey('crm_bots.id'), index=True)
    title: Mapped[str] = mapped_column(String(200))
    message: Mapped[str] = mapped_column(Text)
    tag_filter: Mapped[str] = mapped_column(String(80), default='')
    status: Mapped[str] = mapped_column(String(20), default='draft')
    created_by: Mapped[int] = mapped_column(ForeignKey('crm_admins.id'))
    bot: Mapped[Bot] = relationship()


class Delivery(Stamp, Base):
    __tablename__ = 'crm_deliveries'
    __table_args__ = (UniqueConstraint('campaign_id', 'identity_id'), Index('ix_delivery_work', 'status', 'next_attempt_at'))
    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey('crm_campaigns.id'), index=True)
    identity_id: Mapped[int] = mapped_column(ForeignKey('crm_identities.id'))
    status: Mapped[str] = mapped_column(String(20), default='queued')
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    telegram_message_id: Mapped[str | None] = mapped_column(String(100))
    error: Mapped[str | None] = mapped_column(Text)
    campaign: Mapped[Campaign] = relationship()
    identity: Mapped[Identity] = relationship()


class IngestEvent(Stamp, Base):
    __tablename__ = 'crm_ingest_events'
    __table_args__ = (UniqueConstraint('bot_id', 'event_id'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    bot_id: Mapped[int] = mapped_column(ForeignKey('crm_bots.id'))
    module_key: Mapped[str | None] = mapped_column(String(80), index=True)
    scenario_key: Mapped[str | None] = mapped_column(String(80), index=True)
    event_id: Mapped[str] = mapped_column(String(120))
    event_type: Mapped[str] = mapped_column(String(50))


class Audit(Stamp, Base):
    __tablename__ = 'crm_audit'
    id: Mapped[int] = mapped_column(primary_key=True)
    admin_id: Mapped[int | None] = mapped_column(ForeignKey('crm_admins.id'))
    action: Mapped[str] = mapped_column(String(120))
    target: Mapped[str] = mapped_column(String(120), default='')
    detail: Mapped[str] = mapped_column(Text, default='')


class SyncRun(Stamp, Base):
    __tablename__ = 'crm_sync_runs'
    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(80), index=True)
    status: Mapped[str] = mapped_column(String(20))
    customers: Mapped[int] = mapped_column(Integer, default=0)
    orders: Mapped[int] = mapped_column(Integer, default=0)
    detail: Mapped[str] = mapped_column(Text, default='')


class BotIncident(Stamp, Base):
    __tablename__ = 'crm_bot_incidents'
    id: Mapped[int] = mapped_column(primary_key=True)
    bot_id: Mapped[int] = mapped_column(ForeignKey('crm_bots.id'), index=True)
    severity: Mapped[str] = mapped_column(String(20), default='error')
    component: Mapped[str] = mapped_column(String(120))
    message: Mapped[str] = mapped_column(String(500))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PaymentVerification(Stamp, Base):
    __tablename__ = 'crm_payment_verifications'
    __table_args__ = (UniqueConstraint('provider', 'provider_reference'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    payment_id: Mapped[int] = mapped_column(ForeignKey('crm_payments.id'), index=True)
    provider_reference: Mapped[str] = mapped_column(String(120))
    provider: Mapped[str] = mapped_column(String(80))
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(20), default='verified')


class Invoice(Stamp, Base):
    __tablename__ = 'crm_invoices'
    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    customer_id: Mapped[int | None] = mapped_column(ForeignKey('crm_customers.id'), index=True)
    company_id: Mapped[int | None] = mapped_column(ForeignKey('crm_companies.id'), index=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey('crm_projects.id'))
    payment_id: Mapped[int | None] = mapped_column(ForeignKey('crm_payments.id'))
    seller_name: Mapped[str] = mapped_column(String(200))
    seller_details: Mapped[str] = mapped_column(Text, default='')
    bill_to: Mapped[str] = mapped_column(String(200))
    bill_to_details: Mapped[str] = mapped_column(Text, default='')
    currency: Mapped[str] = mapped_column(String(3), default='UAH')
    total: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    status: Mapped[str] = mapped_column(String(20), default='draft')
    issue_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str] = mapped_column(Text, default='')
    lines: Mapped[list['InvoiceLine']] = relationship(back_populates='invoice', cascade='all, delete-orphan')


class InvoiceLine(Base):
    __tablename__ = 'crm_invoice_lines'
    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey('crm_invoices.id'), index=True)
    title: Mapped[str] = mapped_column(String(200))
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    unit_price: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    invoice: Mapped[Invoice] = relationship(back_populates='lines')


class MarketingChannel(Stamp, Base):
    __tablename__ = 'crm_marketing_channels'
    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(80))
    account: Mapped[str] = mapped_column(String(150), default='')
    project_id: Mapped[int | None] = mapped_column(ForeignKey('crm_projects.id'))
    bot_id: Mapped[int | None] = mapped_column(ForeignKey('crm_bots.id'), index=True)
    url: Mapped[str] = mapped_column(String(500), default='')
    mode: Mapped[str] = mapped_column(String(20), default='manual')
    inbound_secret: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class BotDatabaseConnection(Stamp, Base):
    """Encrypted source connection; credentials are never sent to bot configuration."""
    __tablename__ = 'crm_bot_database_connections'
    __table_args__ = (UniqueConstraint('bot_id', 'label'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    bot_id: Mapped[int] = mapped_column(ForeignKey('crm_bots.id'), index=True)
    label: Mapped[str] = mapped_column(String(120))
    dialect: Mapped[str] = mapped_column(String(20))
    encrypted_url: Mapped[str] = mapped_column(Text)
    schema_name: Mapped[str] = mapped_column(String(120), default='')
    last_status: Mapped[str] = mapped_column(String(20), default='not_checked')
    last_error: Mapped[str] = mapped_column(String(300), default='')
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_by: Mapped[int | None] = mapped_column(ForeignKey('crm_admins.id'))


class BotDatabaseSnapshot(Stamp, Base):
    __tablename__ = 'crm_bot_database_snapshots'
    id: Mapped[int] = mapped_column(primary_key=True)
    connection_id: Mapped[int] = mapped_column(ForeignKey('crm_bot_database_connections.id'), index=True)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    table_count: Mapped[int] = mapped_column(Integer, default=0)
    schema_json: Mapped[str] = mapped_column(Text)
    schema_hash: Mapped[str] = mapped_column(String(64), index=True)


class BotDataMapping(Stamp, Base):
    """Explicit mapping of a business entity to a source table and columns."""
    __tablename__ = 'crm_bot_data_mappings'
    __table_args__ = (UniqueConstraint('connection_id', 'entity'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    connection_id: Mapped[int] = mapped_column(ForeignKey('crm_bot_database_connections.id'), index=True)
    entity: Mapped[str] = mapped_column(String(30))
    table_name: Mapped[str] = mapped_column(String(120))
    columns_json: Mapped[str] = mapped_column(Text)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey('crm_admins.id'))


class MarketingAdAccount(Stamp, Base):
    """One read-only reporting connection to Meta Ads or Google Ads."""
    __tablename__ = 'crm_marketing_ad_accounts'
    __table_args__ = (UniqueConstraint('channel_id'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey('crm_marketing_channels.id'), index=True)
    external_id: Mapped[str] = mapped_column(String(80))
    currency: Mapped[str] = mapped_column(String(3))
    api_version: Mapped[str] = mapped_column(String(16), default='')
    encrypted_credentials: Mapped[str] = mapped_column(Text)
    meta_page_id: Mapped[str] = mapped_column(String(40), default='', server_default='', nullable=False)
    meta_pipeline_id: Mapped[int | None] = mapped_column(ForeignKey('crm_pipelines.id'))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default='not_synced')
    last_error: Mapped[str] = mapped_column(String(200), default='')
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    channel: Mapped['MarketingChannel'] = relationship()


class MarketingCampaign(Stamp, Base):
    __tablename__ = 'crm_marketing_campaigns'
    __table_args__ = (UniqueConstraint('channel_id', 'external_id'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey('crm_marketing_channels.id'), index=True)
    title: Mapped[str] = mapped_column(String(200))
    utm_campaign: Mapped[str] = mapped_column(String(120), unique=True)
    objective: Mapped[str] = mapped_column(String(300), default='')
    budget: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    currency: Mapped[str] = mapped_column(String(3), default='UAH')
    status: Mapped[str] = mapped_column(String(20), default='planned')
    external_id: Mapped[str | None] = mapped_column(String(100))
    channel: Mapped[MarketingChannel] = relationship()


class MarketingMetric(Stamp, Base):
    __tablename__ = 'crm_marketing_metrics'
    __table_args__ = (UniqueConstraint('campaign_id', 'day'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey('crm_marketing_campaigns.id'), index=True)
    day: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    impressions: Mapped[int] = mapped_column(Integer, default=0)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    leads: Mapped[int] = mapped_column(Integer, default=0)
    conversions: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0, server_default='0', nullable=False)
    origin: Mapped[str] = mapped_column(String(20), default='manual', server_default='manual', nullable=False)
    spend: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    campaign: Mapped[MarketingCampaign] = relationship()


class MarketingTouch(Stamp, Base):
    __tablename__ = 'crm_marketing_touches'
    __table_args__ = (UniqueConstraint('customer_id', 'campaign_id'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey('crm_customers.id'), index=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey('crm_marketing_campaigns.id'), index=True)
    bot_id: Mapped[int | None] = mapped_column(ForeignKey('crm_bots.id'))
    medium: Mapped[str] = mapped_column(String(80), default='')


class MarketingLead(Stamp, Base):
    """One deduplicated external lead; provider payload is never stored."""
    __tablename__ = 'crm_marketing_leads'
    __table_args__ = (UniqueConstraint('channel_id', 'external_id'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey('crm_marketing_channels.id'), index=True)
    external_id: Mapped[str] = mapped_column(String(100))
    page_id: Mapped[str] = mapped_column(String(40))
    form_id: Mapped[str] = mapped_column(String(80), default='')
    ad_id: Mapped[str] = mapped_column(String(80), default='')
    campaign_id: Mapped[int | None] = mapped_column(ForeignKey('crm_marketing_campaigns.id'))
    customer_id: Mapped[int] = mapped_column(ForeignKey('crm_customers.id'))
    deal_id: Mapped[int] = mapped_column(ForeignKey('crm_deals.id'))
    channel: Mapped['MarketingChannel'] = relationship()
    campaign: Mapped['MarketingCampaign | None'] = relationship()
    customer: Mapped['Customer'] = relationship()
    deal: Mapped['Deal'] = relationship()


class SeoKeyword(Stamp, Base):
    __tablename__ = 'crm_seo_keywords'
    __table_args__ = (UniqueConstraint('project_id','phrase','country'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey('crm_projects.id'), index=True)
    phrase: Mapped[str] = mapped_column(String(200))
    country: Mapped[str] = mapped_column(String(2), default='UA')
    target_url: Mapped[str] = mapped_column(String(500), default='')


class SeoRankSnapshot(Stamp, Base):
    __tablename__ = 'crm_seo_rank_snapshots'
    __table_args__ = (UniqueConstraint('keyword_id','day'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    keyword_id: Mapped[int] = mapped_column(ForeignKey('crm_seo_keywords.id'), index=True)
    day: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    position: Mapped[int] = mapped_column(Integer)
    keyword: Mapped[SeoKeyword] = relationship()


class Company(Stamp, Base):
    __tablename__ = 'crm_companies'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    website: Mapped[str] = mapped_column(String(255), default='')
    industry: Mapped[str] = mapped_column(String(100), default='')
    email: Mapped[str] = mapped_column(String(255), default='')
    phone: Mapped[str] = mapped_column(String(40), default='')
    notes: Mapped[str] = mapped_column(Text, default='')
    status: Mapped[str] = mapped_column(String(30), default='prospect')


class CompanyContact(Base):
    __tablename__ = 'crm_company_contacts'
    __table_args__ = (UniqueConstraint('company_id','customer_id'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey('crm_companies.id'), index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey('crm_customers.id'), index=True)
    role: Mapped[str] = mapped_column(String(100), default='contact')
    company: Mapped[Company] = relationship()
    customer: Mapped[Customer] = relationship()


class Pipeline(Stamp, Base):
    __tablename__ = 'crm_pipelines'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    description: Mapped[str] = mapped_column(Text, default='')
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Stage(Base):
    __tablename__ = 'crm_stages'
    __table_args__ = (UniqueConstraint('pipeline_id','position'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    pipeline_id: Mapped[int] = mapped_column(ForeignKey('crm_pipelines.id'), index=True)
    name: Mapped[str] = mapped_column(String(100))
    position: Mapped[int] = mapped_column(Integer)
    probability: Mapped[int] = mapped_column(Integer, default=0)
    terminal: Mapped[str] = mapped_column(String(10), default='')  # '', 'won', 'lost'
    pipeline: Mapped[Pipeline] = relationship()


class Deal(Stamp, Base):
    __tablename__ = 'crm_deals'
    __table_args__ = (UniqueConstraint('bot_id','external_id'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    bot_id: Mapped[int | None] = mapped_column(ForeignKey('crm_bots.id'), index=True)
    external_id: Mapped[str | None] = mapped_column(String(120))
    title: Mapped[str] = mapped_column(String(200))
    customer_id: Mapped[int | None] = mapped_column(ForeignKey('crm_customers.id'), index=True)
    company_id: Mapped[int | None] = mapped_column(ForeignKey('crm_companies.id'), index=True)
    pipeline_id: Mapped[int] = mapped_column(ForeignKey('crm_pipelines.id'), index=True)
    stage_id: Mapped[int] = mapped_column(ForeignKey('crm_stages.id'), index=True)
    service: Mapped[str] = mapped_column(String(80), default='website')
    budget: Mapped[Decimal] = mapped_column(Numeric(14,2), default=0)
    currency: Mapped[str] = mapped_column(String(3), default='UAH')
    source: Mapped[str] = mapped_column(String(120), default='manual')
    owner_id: Mapped[int | None] = mapped_column(ForeignKey('crm_admins.id'))
    expected_close_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str] = mapped_column(Text, default='')
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    customer: Mapped[Customer | None] = relationship()
    company: Mapped[Company | None] = relationship()
    pipeline: Mapped[Pipeline] = relationship()
    stage: Mapped[Stage] = relationship()


class Project(Stamp, Base):
    __tablename__ = 'crm_projects'
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    deal_id: Mapped[int | None] = mapped_column(ForeignKey('crm_deals.id'), index=True)
    customer_id: Mapped[int | None] = mapped_column(ForeignKey('crm_customers.id'))
    company_id: Mapped[int | None] = mapped_column(ForeignKey('crm_companies.id'))
    service: Mapped[str] = mapped_column(String(80), default='website')
    status: Mapped[str] = mapped_column(String(30), default='discovery')
    domain: Mapped[str] = mapped_column(String(255), default='')
    repository_url: Mapped[str] = mapped_column(String(500), default='')
    budget: Mapped[Decimal] = mapped_column(Numeric(14,2), default=0)
    currency: Mapped[str] = mapped_column(String(3), default='UAH')
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    launched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str] = mapped_column(Text, default='')
    customer: Mapped[Customer | None] = relationship()
    company: Mapped[Company | None] = relationship()
    deal: Mapped[Deal | None] = relationship()


class Milestone(Stamp, Base):
    __tablename__ = 'crm_milestones'
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey('crm_projects.id'), index=True)
    title: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default='planned')
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    position: Mapped[int] = mapped_column(Integer, default=0)
    project: Mapped[Project] = relationship()


class Ticket(Stamp, Base):
    __tablename__ = 'crm_tickets'
    __table_args__ = (UniqueConstraint('bot_id','external_id'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    bot_id: Mapped[int | None] = mapped_column(ForeignKey('crm_bots.id'), index=True)
    external_id: Mapped[str | None] = mapped_column(String(120))
    title: Mapped[str] = mapped_column(String(200))
    customer_id: Mapped[int | None] = mapped_column(ForeignKey('crm_customers.id'))
    project_id: Mapped[int | None] = mapped_column(ForeignKey('crm_projects.id'))
    kind: Mapped[str] = mapped_column(String(30), default='support')
    priority: Mapped[str] = mapped_column(String(20), default='normal')
    status: Mapped[str] = mapped_column(String(20), default='open')
    description: Mapped[str] = mapped_column(Text, default='')
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    customer: Mapped[Customer | None] = relationship()
    project: Mapped[Project | None] = relationship()
