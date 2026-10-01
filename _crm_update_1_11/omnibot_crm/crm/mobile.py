"""Authenticated, deliberately read-only smartphone views of CRM-owned tables."""
from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import func, select
from . import models as m
from .security import require_admin
from .excel import workbook_response

router=APIRouter(prefix='/m')

# Strict allowlist. Password hashes, secrets and arbitrary SQL tables are never exposed.
TABLES={
    'customers': ('Клиенты',m.Customer,('name','phone','status'),('id','name','email','phone','language','status','source','tags','notes','marketing_consent','consent_at','last_seen_at','created_at'),'name'),
    'companies': ('Компании',m.Company,('name','industry','status'),('id','name','industry','website','email','phone','status','notes','created_at'),'name'),
    'company_contacts': ('Контакты компаний',m.CompanyContact,('company_id','customer_id','role'),('id','company_id','customer_id','role'),None),
    'bots': ('Боты',m.Bot,('name','key','last_seen_at'),('id','key','name','description','active','last_seen_at','created_at'),'name'),
    'bot_settings': ('Параметры ботов',m.BotSetting,('key','label','kind'),('id','bot_id','key','label','group_name','kind','is_configured','description','display_order','updated_at'), 'key'),
    'bot_modules': ('Продукты ботов',m.BotModule,('key','name','status'),('id','bot_id','key','name','description','status','updated_at'),'name'),
    'bot_scenarios': ('Сценарии ботов',m.BotScenario,('name','trigger_kind','status'),('id','bot_id','key','name','trigger_kind','trigger_value','segment_tags','status','version','updated_at'),'name'),
    'bot_scenario_steps': ('Шаги сценариев',m.BotScenarioStep,('scenario_id','position','kind'),('id','scenario_id','position','kind','text'),'kind'),
    'bot_config_releases': ('Выпуски настроек ботов',m.BotConfigRelease,('bot_id','version','action'),('id','bot_id','version','revision','action','source_version','created_at'),None),
    'bot_payment_providers': ('Эквайеры ботов',m.BotPaymentProvider,('provider','label','enabled'),('id','bot_id','provider','label','enabled','updated_at'),'provider'),
    'marketing_ad_accounts': ('Рекламные кабинеты',m.MarketingAdAccount,('external_id','status','currency'),('id','channel_id','external_id','currency','api_version','enabled','status','last_error','last_synced_at'), 'external_id'),
    'food_porn_profiles': ('География Food Porn',m.FoodPornCustomerProfile,('customer_id','country','city'),('id','customer_id','country','city','created_at'),None),
    'food_porn_menus': ('Меню Food Porn',m.FoodPornMenuSnapshot,('order_id','has_error','payment_provider_reported'),('id','order_id','cover_uploaded','spread_uploaded','has_error','payment_provider_reported','created_at'),None),
    'food_porn_menu_items': ('Блюда Food Porn',m.FoodPornMenuItem,('title','category','generation_status'),('id','order_id','source_id','category','position','title','generation_status','created_at'),'title'),
    'food_porn_generated_files': ('Файлы Food Porn',m.FoodPornGeneratedFile,('kind','order_id','source_id'),('id','order_id','source_id','kind','source_path','created_at'),None),
    'identities': ('Идентификаторы ботов',m.Identity,('external_user_id','bot_id','customer_id'),('id','bot_id','customer_id','external_user_id','chat_id','blocked','created_at'),'external_user_id'),
    'pipelines': ('Воронки',m.Pipeline,('name','active'),('id','name','description','active','created_at'),'name'),
    'stages': ('Стадии сделок',m.Stage,('name','position','probability'),('id','pipeline_id','name','position','probability','terminal'),'name'),
    'deals': ('Сделки',m.Deal,('title','service','budget'),('id','title','customer_id','company_id','pipeline_id','stage_id','service','budget','currency','source','owner_id','expected_close_at','notes','created_at','updated_at'),'title'),
    'projects': ('Веб-проекты',m.Project,('title','service','status'),('id','title','deal_id','customer_id','company_id','service','status','domain','repository_url','budget','currency','starts_at','due_at','launched_at','notes','created_at'),'title'),
    'milestones': ('Этапы проектов',m.Milestone,('title','status','due_at'),('id','project_id','title','position','status','due_at','completed_at','created_at'),'title'),
    'tickets': ('Обращения',m.Ticket,('title','priority','status'),('id','title','customer_id','project_id','kind','priority','status','description','resolved_at','created_at'),'title'),
    'tasks': ('Задачи',m.Task,('title','status','due_at'),('id','title','customer_id','status','due_at','created_at'),'title'),
    'orders': ('Заказы ботов',m.Order,('title','status','amount'),('id','bot_id','module_key','customer_id','external_id','title','status','amount','currency','created_at'),'title'),
    'products': ('Каталог',m.Product,('sku','title','price'),('id','bot_id','module_key','sku','title','description','price','currency','active','created_at'),'title'),
    'order_items': ('Позиции заказов',m.OrderItem,('order_id','product_id','quantity'),('id','order_id','product_id','quantity','unit_price'),None),
    'payments': ('Платежи',m.Payment,('external_id','amount','status'),('id','bot_id','module_key','order_id','customer_id','external_id','provider','status','amount','currency','created_at'),'external_id'),
    'payment_verifications': ('Проверки оплат',m.PaymentVerification,('provider','provider_reference','amount'),('id','payment_id','provider','provider_reference','amount','currency','status','created_at'),'provider_reference'),
    'invoices': ('Счета',m.Invoice,('number','bill_to','total'),('id','number','seller_name','bill_to','currency','total','status','issue_at','due_at','notes','created_at'),'number'),
    'invoice_lines': ('Строки счетов',m.InvoiceLine,('title','quantity','unit_price'),('id','invoice_id','title','quantity','unit_price'),'title'),
    'marketing_channels': ('Маркетинговые каналы',m.MarketingChannel,('platform','account','mode'),('id','platform','account','url','mode','active','created_at'),'platform'),
    'marketing_campaigns': ('Маркетинговые кампании',m.MarketingCampaign,('title','utm_campaign','currency'),('id','channel_id','title','utm_campaign','objective','budget','currency','status','created_at'),'title'),
    'marketing_metrics': ('Метрики рекламы',m.MarketingMetric,('day','clicks','leads'),('id','campaign_id','day','impressions','clicks','leads','spend','created_at'),None),
    'marketing_touches': ('Источники клиентов',m.MarketingTouch,('customer_id','campaign_id','medium'),('id','customer_id','campaign_id','bot_id','medium','created_at'),None),
    'marketing_leads': ('Заявки Meta',m.MarketingLead,('external_id','page_id','campaign_id'),('id','channel_id','external_id','page_id','form_id','ad_id','campaign_id','customer_id','deal_id','created_at'),'external_id'),
    'seo_keywords': ('Поисковые запросы',m.SeoKeyword,('phrase','country','project_id'),('id','project_id','phrase','country','target_url','created_at'),'phrase'),
    'seo_rank_snapshots': ('Позиции SEO',m.SeoRankSnapshot,('keyword_id','day','position'),('id','keyword_id','day','position','created_at'),None),
    'expenses': ('Расходы',m.Expense,('category','vendor','amount'),('id','bot_id','category','vendor','amount','currency','spent_at','memo','created_at'),'category'),
    'project_expenses': ('Расходы проектов',m.ProjectExpense,('project_id','expense_id'),('id','project_id','expense_id'),None),
    'campaigns': ('Рассылки',m.Campaign,('title','status','bot_id'),('id','bot_id','title','message','tag_filter','status','created_by','created_at'),'title'),
    'deliveries': ('Доставки',m.Delivery,('campaign_id','status','attempts'),('id','campaign_id','identity_id','status','attempts','next_attempt_at','telegram_message_id','error','created_at'),None),
    'activities': ('История событий',m.Activity,('kind','summary','customer_id'),('id','customer_id','bot_id','kind','summary','created_at'),'summary'),
    'ingest_events': ('Входящие события',m.IngestEvent,('event_type','event_id','bot_id'),('id','bot_id','module_key','scenario_key','event_id','event_type','created_at'),'event_id'),
    'bot_incidents': ('Ошибки ботов',m.BotIncident,('component','severity','message'),('id','bot_id','severity','component','message','resolved_at','created_at'),'component'),
    'sync_runs': ('Загрузки источников',m.SyncRun,('source','status','customers'),('id','source','status','customers','orders','detail','created_at'),'source'),
    'audit': ('Аудит действий',m.Audit,('action','target','created_at'),('id','admin_id','action','target','detail','created_at'),'action'),
    'admins': ('Администраторы',m.Admin,('email','role','active'),('id','email','role','active','created_at'),'email'),
}

LABELS={'id':'№ записи','name':'Имя / название','title':'Название','status':'Статус','created_at':'Создано',
    'updated_at':'Изменено','customer_id':'Клиент','company_id':'Компания','bot_id':'Бот','deal_id':'Сделка',
    'project_id':'Проект','pipeline_id':'Воронка','stage_id':'Стадия','campaign_id':'Рассылка','identity_id':'Аккаунт бота',
    'admin_id':'Администратор','owner_id':'Ответственный','order_id':'Заказ','email':'Email','phone':'Телефон',
    'language':'Язык','source':'Источник','tags':'Теги','notes':'Заметки','marketing_consent':'Согласие на рассылку',
    'consent_at':'Дата согласия','last_seen_at':'Последний сигнал','website':'Сайт','industry':'Отрасль',
    'role':'Роль','key':'Ключ','description':'Описание','active':'Активен','external_user_id':'Telegram ID',
    'chat_id':'Чат ID','blocked':'Блокирован','position':'Порядок','probability':'Вероятность, %',
    'terminal':'Финальная стадия','service':'Услуга','budget':'Бюджет','currency':'Валюта',
    'expected_close_at':'Ожидаемая дата','domain':'Домен','repository_url':'Репозиторий','starts_at':'Начало',
    'due_at':'Срок','launched_at':'Дата запуска','completed_at':'Завершено','kind':'Тип','priority':'Приоритет',
    'resolved_at':'Решено','external_id':'Внешний ID','amount':'Сумма','provider':'Платежный сервис',
    'category':'Категория','vendor':'Поставщик','spent_at':'Дата расхода','memo':'Комментарий',
    'message':'Сообщение','tag_filter':'Фильтр по тегу','created_by':'Автор','attempts':'Попытки',
    'next_attempt_at':'Следующая попытка','telegram_message_id':'Telegram сообщение','error':'Ошибка',
    'summary':'Содержание','event_id':'ID события','event_type':'Тип события','action':'Действие','target':'Объект','detail':'Детали',
    'payment_id':'Платеж','provider_reference':'ID провайдера','component':'Компонент','severity':'Важность','customers':'Клиенты','orders':'Заказы',
    'sku':'Артикул','product_id':'Товар','quantity':'Количество','unit_price':'Цена в заказе','price':'Цена',
    'invoice_id':'Счет','channel_id':'Канал','group_name':'Группа','updated_at':'Изменено',
    'expense_id':'Расход',
    'number':'Номер','seller_name':'Продавец','bill_to':'Покупатель','issue_at':'Выставлен',
    'utm_campaign':'UTM кампания','objective':'Цель','mode':'Режим','day':'День',
    'impressions':'Показы','clicks':'Клики','leads':'Лиды','spend':'Расход',
    'phrase':'Поисковый запрос','country':'Регион','target_url':'Целевая страница','keyword_id':'Поисковый запрос',
    'city':'Город','cover_uploaded':'Фото обложки','spread_uploaded':'Фото разворота',
    'has_error':'Ошибка генерации','payment_provider_reported':'Провайдер по данным бота',
    'generation_status':'Этап генерации','source_id':'ID в базе бота','source_path':'Путь файла в боте',
    'enabled':'Включен','display_order':'Порядок','version':'Версия','revision':'Хэш версии','source_version':'Версия-источник',
    'page_id':'Страница Meta','form_id':'Форма Meta','ad_id':'Объявление'}
LINKS={'customer_id':'customers','company_id':'companies','bot_id':'bots','deal_id':'deals','project_id':'projects',
       'pipeline_id':'pipelines','stage_id':'stages','campaign_id':'campaigns','identity_id':'identities',
       'admin_id':'admins','owner_id':'admins','order_id':'orders','payment_id':'payments','product_id':'products',
       'invoice_id':'invoices','channel_id':'marketing_channels','expense_id':'expenses','keyword_id':'seo_keywords'}
OWNER_ONLY={'admins','audit','ingest_events','bot_settings','bot_config_releases','bot_payment_providers','marketing_ad_accounts','food_porn_generated_files'}
GROUPS=[('Продажи',['customers','companies','company_contacts','pipelines','stages','deals']),
        ('Разработка',['projects','milestones','tickets','tasks']),
        ('Коммерция и финансы',['products','orders','order_items','payments','payment_verifications','expenses','project_expenses','invoices','invoice_lines']),
        ('Маркетинг',['marketing_channels','marketing_ad_accounts','marketing_campaigns','marketing_metrics','marketing_touches','marketing_leads','seo_keywords','seo_rank_snapshots']),
        ('Боты и сообщения',['bots','bot_modules','bot_scenarios','bot_scenario_steps','bot_settings','bot_config_releases','bot_payment_providers','food_porn_profiles','food_porn_menus','food_porn_menu_items','food_porn_generated_files','identities','campaigns','deliveries','activities','bot_incidents']),
        ('Система',['sync_runs','ingest_events','audit','admins'])]


def view(request, name, **data):
    from .i18n import localize_response
    response = request.app.state.templates.TemplateResponse(request,name,{
        'request':request,'theme':request.session.get('theme','light'),
        'locale':request.session.get('locale','ru'),**data})
    return localize_response(response,request.session.get('locale','ru'))


def access(request,db,key=None):
    admin=require_admin(request,db)
    if key in OWNER_ONLY and admin.role!='owner': raise HTTPException(403)
    return admin


def fields(row, names):
    result=[]
    for field in names:
        value=getattr(row,field)
        result.append({'key':field,'label':LABELS.get(field,field.replace('_',' ')),
            'value':value,'href':f"/m/t/{LINKS[field]}/{value}" if field in LINKS and value is not None else None})
    return result


@router.get('')
@router.get('/')
def mobile_home(request:Request):
    with request.app.state.session_factory() as db:
        admin=access(request,db)
        summaries={key:db.scalar(select(func.count()).select_from(model)) for key,(_,model,_,_,_) in TABLES.items() if key not in OWNER_ONLY or admin.role=='owner'}
        return view(request,'mobile/home.html',admin=admin,groups=GROUPS,tables=TABLES,counts=summaries,
                    recent=db.scalars(select(m.Project).order_by(m.Project.id.desc()).limit(3)).all())


@router.get('/export/{key}.xlsx')
def mobile_export(request: Request, key: str, q: str = ''):
    table = TABLES.get(key)
    if table is None:
        raise HTTPException(404)
    label, model, _, detail, search = table
    with request.app.state.session_factory() as db:
        access(request, db, key)
        stmt = select(model)
        if search and q.strip():
            stmt = stmt.where(getattr(model, search).ilike('%'+q.strip()[:100]+'%'))
        rows = db.scalars(stmt.order_by(model.id.desc()).limit(10001)).all()
        if len(rows) > 10000:
            raise HTTPException(413, 'Максимум 10 000 строк за выгрузку')
        return workbook_response(key + '.xlsx', [(label, [LABELS.get(f, f) for f in detail],
            ([getattr(r, f) for f in detail] for r in rows))])


@router.get('/t/{key}')
def mobile_table(request:Request,key:str,q:str='',page_no:int=1):
    table=TABLES.get(key)
    if table is None: raise HTTPException(404)
    label,model,summary,detail,search=table
    with request.app.state.session_factory() as db:
        admin=access(request,db,key)
        stmt=select(model)
        if search and q.strip(): stmt=stmt.where(getattr(model,search).ilike('%'+q.strip()[:100]+'%'))
        page_no=max(1,min(page_no,100000))
        total=db.scalar(select(func.count()).select_from(stmt.subquery()))
        rows=db.scalars(stmt.order_by(model.id.desc()).offset((page_no-1)*20).limit(20)).all()
        cards=[{'id':r.id,'fields':fields(r,summary)} for r in rows]
        return view(request,'mobile/table.html',admin=admin,key=key,label=label,total=total,cards=cards,
                    q=q,searchable=bool(search),page_no=page_no)


@router.get('/t/{key}/{row_id}')
def mobile_detail(request:Request,key:str,row_id:int):
    table=TABLES.get(key)
    if table is None: raise HTTPException(404)
    label,model,summary,detail,search=table
    with request.app.state.session_factory() as db:
        admin=access(request,db,key)
        row=db.get(model,row_id)
        if row is None: raise HTTPException(404)
        links = {'customers': f'/customers/{row_id}', 'bots': f'/bots/{row_id}/configure',
                 'projects': f'/projects/{row_id}', 'tickets': f'/tickets/{row_id}',
                 'deals': f'/deals/{row_id}', 'invoices': f'/invoices/{row_id}',
                 'bot_scenarios': f'/bots/{row.bot_id}/configure#scenario-{row_id}' if key=='bot_scenarios' else None}
        return view(request,'mobile/detail.html',admin=admin,key=key,label=label,row_id=row_id,
                    items=fields(row,detail),edit_link=links.get(key))
