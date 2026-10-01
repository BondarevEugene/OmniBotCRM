import csv
import io
import secrets
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from fastapi import APIRouter, Form, HTTPException, Request, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload
from .ingest import money
from .metrics import summary
from .models import Activity, Admin, Audit, Bot, Campaign, Customer, Deal, Delivery, Expense, Identity, Order, Payment, Project, ProjectExpense, Stage, Task, Ticket
from .security import admin_for, audit, csrf, new_csrf, require_admin, verify_password
from .excel import workbook_response

router = APIRouter()


def redirect(path):
    return RedirectResponse(path, status_code=303)


def csv_cell(value):
    value = str(value or '')
    return "'" + value if value.lstrip().startswith(('=', '+', '-', '@')) else value


def context(request, db, **data):
    return {'request': request, 'admin': admin_for(request, db), 'csrf': new_csrf(request),
            'theme': request.session.get('theme', 'light'), 'locale': request.session.get('locale', 'ru'), **data}


def page(request, name, db, **data):
    from .i18n import localize_response
    response = request.app.state.templates.TemplateResponse(request, name, context(request, db, **data))
    return localize_response(response, request.session.get('locale', 'ru'))


@router.get('/login')
def login_form(request: Request):
    with request.app.state.session_factory() as db:
        return page(request, 'login.html', db, error='')


@router.post('/login')
def login(request: Request, email: str = Form(), password: str = Form(), token: str = Form()):
    csrf(request, token)
    with request.app.state.session_factory() as db:
        admin = db.scalar(select(Admin).where(Admin.email == email.strip().lower(), Admin.active.is_(True)))
        locked = admin.locked_until if admin else None
        if locked and locked.tzinfo is None: locked = locked.replace(tzinfo=timezone.utc)
        if not admin or (locked and locked > datetime.now(timezone.utc)) or not verify_password(password, admin.password_hash):
            if admin and (not locked or locked <= datetime.now(timezone.utc)):
                admin.failed_logins += 1
                if admin.failed_logins >= 5:
                    admin.failed_logins = 0
                    admin.locked_until = datetime.now(timezone.utc) + timedelta(minutes=15)
                db.commit()
            return page(request, 'login.html', db, error='Неверные учетные данные')
        admin.failed_logins = 0
        admin.locked_until = None
        prefs = {k: request.session[k] for k in ('theme', 'locale') if k in request.session}
        request.session.clear()
        request.session.update(prefs)
        request.session['admin_id'] = admin.id
        request.session['csrf'] = secrets.token_urlsafe(32)
        audit(db, 'login', 'session', admin.id)
        db.commit()
        return redirect('/')


@router.post('/logout')
def logout(request: Request, token: str = Form()):
    csrf(request, token)
    request.session.clear()
    return redirect('/login')


@router.get('/')
def dashboard(request: Request):
    with request.app.state.session_factory() as db:
        require_admin(request, db)
        return page(request, 'dashboard.html', db, metrics=summary(db), recent=db.scalars(select(Activity).order_by(Activity.id.desc()).limit(10)).all())


def customer_query(q='', status='', source='', tag='', bot_id=0):
    stmt = select(Customer)
    if q.strip():
        pattern = f'%{q.strip()[:100]}%'
        stmt = stmt.where(or_(Customer.name.ilike(pattern), Customer.email.ilike(pattern),
                              Customer.phone.ilike(pattern)))
    if status:
        stmt = stmt.where(Customer.status == status[:30])
    if source.strip():
        stmt = stmt.where(Customer.source.ilike('%' + source.strip()[:80] + '%'))
    if tag.strip():
        stmt = stmt.where(Customer.tags.ilike('%' + tag.strip()[:80] + '%'))
    if bot_id:
        stmt = stmt.where(select(Identity.id).where(Identity.customer_id == Customer.id,
            Identity.bot_id == bot_id).exists())
    return stmt


@router.get('/customers')
def customers(request: Request, q: str = '', status: str = '', source: str = '', tag: str = '',
              bot_id: int = 0, sort: str = 'recent', page_no: int = 1):
    with request.app.state.session_factory() as db:
        require_admin(request, db)
        stmt = customer_query(q, status, source, tag, bot_id)
        page_no = max(1, min(100000, page_no))
        total = db.scalar(select(func.count()).select_from(stmt.subquery()))
        order = {'recent': Customer.id.desc(), 'name': Customer.name.asc(),
                 'oldest': Customer.id.asc()}.get(sort, Customer.id.desc())
        rows = db.scalars(stmt.order_by(order, Customer.id.desc()).offset((page_no-1)*50).limit(50)).all()
        return page(request, 'customers.html', db, rows=rows, q=q, status=status, source=source,
                    tag=tag, bot_id=bot_id, sort=sort, page_no=page_no, total=total,
                    bots=db.scalars(select(Bot).order_by(Bot.name)).all())


@router.get('/customers/export.csv')
def export_customers(request: Request, q: str = '', status: str = '', source: str = '', tag: str = '', bot_id: int = 0):
    with request.app.state.session_factory() as db:
        require_admin(request, db)
        stream = io.StringIO()
        writer = csv.writer(stream)
        writer.writerow(['id', 'name', 'email', 'phone', 'status', 'source', 'tags', 'consent', 'created_at'])
        for c in db.scalars(customer_query(q, status, source, tag, bot_id).order_by(Customer.id)):
            writer.writerow([c.id, *(csv_cell(x) for x in (c.name, c.email, c.phone, c.status, c.source, c.tags)), c.marketing_consent, c.created_at])
        stream.seek(0)
        return StreamingResponse(iter([stream.getvalue().encode('utf-8-sig')]), media_type='text/csv', headers={'Content-Disposition': 'attachment; filename="customers.csv"'})


@router.get('/customers/export.xlsx')
def export_customers_excel(request: Request, q: str = '', status: str = '', source: str = '',
                           tag: str = '', bot_id: int = 0):
    with request.app.state.session_factory() as db:
        require_admin(request, db)
        stmt = customer_query(q, status, source, tag, bot_id)
        rows = db.scalars(stmt.order_by(Customer.id).limit(10001)).all()
        if len(rows) > 10000:
            raise HTTPException(413, 'До 10 000 клиентов за выгрузку; уточните фильтр')
        ids = db.scalars(select(Identity).where(Identity.customer_id.in_([r.id for r in rows]))).all() if rows else []
        return workbook_response('customers.xlsx', [
            ('Клиенты', ['ID', 'Имя', 'Email', 'Телефон', 'Язык', 'Статус', 'Источник', 'Теги',
                         'Согласие', 'Дата согласия', 'Заметки', 'Создан'],
                ([c.id, c.name, c.email, c.phone, c.language, c.status, c.source, c.tags,
                  c.marketing_consent, c.consent_at, c.notes, c.created_at] for c in rows)),
            ('Боты', ['ID клиента', 'ID бота', 'Внешний ID', 'Chat ID', 'Блокирован'],
                ([i.customer_id, i.bot_id, i.external_user_id, i.chat_id, i.blocked] for i in ids))])


@router.get('/customers/import')
def import_customers_form(request: Request):
    with request.app.state.session_factory() as db:
        require_admin(request, db)
        return page(request, 'customer_import.html', db)


@router.post('/customers/import')
async def import_customers_csv(request: Request, token: str = Form(), upload: UploadFile = File()):
    with request.app.state.session_factory() as db:
        admin = require_admin(request, db); csrf(request, token)
        raw = await upload.read(2_000_001)
        if len(raw) > 2_000_000:
            raise HTTPException(413, 'Максимум 2 MB')
        try:
            text_data = raw.decode('utf-8-sig')
            reader = csv.DictReader(io.StringIO(text_data))
            if not reader.fieldnames or not ({'name','email','phone'} & set(reader.fieldnames)):
                raise ValueError()
            records = list(reader)
        except (UnicodeDecodeError, csv.Error, ValueError):
            raise HTTPException(422, 'Нужен UTF-8 CSV с колонками name, email или phone') from None
        if len(records) > 5000:
            raise HTTPException(413, 'Максимум 5000 строк за импорт')
        created = updated = skipped = 0
        for row in records:
            name, email, phone = tuple((row.get(k) or '').strip() for k in ('name', 'email', 'phone'))
            if not (name or email or phone) or len(name) > 200 or len(email) > 255 or len(phone) > 40:
                skipped += 1; continue
            by_email = db.scalar(select(Customer).where(Customer.email == email)) if email else None
            by_phone = db.scalar(select(Customer).where(Customer.phone == phone)) if phone else None
            if by_email and by_phone and by_email.id != by_phone.id:
                skipped += 1; continue
            customer = by_email or by_phone
            if not customer:
                customer = Customer(name=name, email=email or None, phone=phone or None,
                                    source='csv', marketing_consent=False)
                db.add(customer); created += 1
            else:
                if name and not customer.name: customer.name = name
                if email and not customer.email: customer.email = email
                if phone and not customer.phone: customer.phone = phone
                updated += 1
            db.flush()
        audit(db, 'customer.import', 'csv', admin.id,
              f'created={created}; matched={updated}; skipped={skipped}; no consent granted')
        db.commit()
        return redirect(f'/customers/import?created={created}&matched={updated}&skipped={skipped}')


@router.get('/customers/new')
def customer_new(request: Request):
    with request.app.state.session_factory() as db:
        require_admin(request, db)
        return page(request, 'customer_new.html', db)


@router.post('/customers/new')
def customer_create(request: Request, token: str = Form(), name: str = Form(), email: str = Form(''), phone: str = Form(''), language: str = Form('ru'), notes: str = Form('')):
    with request.app.state.session_factory() as db:
        admin=require_admin(request,db); csrf(request,token)
        if not name.strip() or len(name)>200 or len(email)>255 or len(phone)>40 or len(notes)>10000: raise HTTPException(422)
        row=Customer(name=name.strip(),email=email.strip() or None,phone=phone.strip() or None,
            language=language[:10],notes=notes,source='manual')
        db.add(row); db.flush(); audit(db,'customer.create',str(row.id),admin.id); db.commit()
        return redirect(f'/customers/{row.id}')


@router.get('/customers/{customer_id}')
def customer_detail(request: Request, customer_id: int):
    with request.app.state.session_factory() as db:
        require_admin(request, db)
        customer = db.scalar(select(Customer).options(selectinload(Customer.identities).selectinload(Identity.bot)).where(Customer.id == customer_id))
        if not customer: raise HTTPException(404)
        return page(request, 'customer.html', db, customer=customer,
            orders=db.scalars(select(Order).where(Order.customer_id == customer_id).order_by(Order.id.desc())).all(),
            payments=db.scalars(select(Payment).where(Payment.customer_id == customer_id).order_by(Payment.id.desc())).all(),
            activities=db.scalars(select(Activity).where(Activity.customer_id == customer_id).order_by(Activity.id.desc()).limit(50)).all())


@router.post('/customers/{customer_id}')
def update_customer(request: Request, customer_id: int, token: str = Form(), name: str = Form(''), email: str = Form(''), phone: str = Form(''), status: str = Form('lead'), language: str = Form('ru'), tags: str = Form(''), notes: str = Form(''), marketing_consent: str = Form('')):
    with request.app.state.session_factory() as db:
        admin = require_admin(request, db)
        csrf(request, token)
        customer = db.get(Customer, customer_id)
        if not customer: raise HTTPException(404)
        if status not in {'lead','active','vip','inactive','blocked'} or len(tags)>500 or len(notes)>10000: raise HTTPException(422)
        customer.name, customer.email, customer.phone = name[:200].strip(), email[:255].strip() or None, phone[:40].strip() or None
        customer.status, customer.language, customer.tags, customer.notes = status, language[:10], tags, notes
        if marketing_consent != 'on' and customer.marketing_consent:
            customer.marketing_consent = False
            customer.consent_at = None
        # Consent is granted by a bot event with evidence; admin UI only revokes.
        audit(db, 'customer.update', str(customer_id), admin.id)
        db.commit()
        return redirect(f'/customers/{customer_id}')


@router.post('/customers/{customer_id}/activity')
def add_activity(request: Request, customer_id:int, token:str=Form(), summary:str=Form()):
    with request.app.state.session_factory() as db:
        admin=require_admin(request,db); csrf(request,token)
        if not db.get(Customer,customer_id): raise HTTPException(404)
        if not summary.strip() or len(summary)>2000: raise HTTPException(422)
        db.add(Activity(customer_id=customer_id,kind='manager.note',summary=summary.strip()))
        audit(db,'customer.note',str(customer_id),admin.id); db.commit()
        return redirect(f'/customers/{customer_id}')


@router.get('/bots')
def bots(request: Request):
    with request.app.state.session_factory() as db:
        require_admin(request, db)
        rows = db.scalars(select(Bot).order_by(Bot.id)).all()
        counts = dict(db.execute(select(Identity.bot_id, func.count()).group_by(Identity.bot_id)).all())
        return page(request, 'bots.html', db, rows=rows, counts=counts)


@router.post('/bots')
def add_bot(request: Request, token: str = Form(), key: str = Form(), name: str = Form(), description: str = Form('')):
    with request.app.state.session_factory() as db:
        admin = require_admin(request, db, owner=True)
        csrf(request, token)
        from .bot_settings import KEY_PATTERN
        if not KEY_PATTERN.fullmatch(key) or not name.strip() or len(name)>120 or len(description)>2000:
            raise HTTPException(422)
        if db.scalar(select(Bot).where(Bot.key == key)): raise HTTPException(409, 'Bot key exists')
        bot = Bot(key=key, name=name.strip(), description=description.strip())
        db.add(bot); db.flush()
        audit(db, 'bot.create', key, admin.id)
        db.commit()
        return redirect(f'/bots/{bot.id}/configure#modules')


@router.get('/orders')
def orders(request: Request):
    with request.app.state.session_factory() as db:
        require_admin(request, db)
        return page(request, 'ledger.html', db, kind='orders', rows=db.scalars(select(Order).options(selectinload(Order.customer), selectinload(Order.bot)).order_by(Order.id.desc()).limit(300)).all())


@router.get('/payments')
def payments(request: Request):
    with request.app.state.session_factory() as db:
        require_admin(request, db)
        return page(request, 'ledger.html', db, kind='payments', rows=db.scalars(select(Payment).options(selectinload(Payment.customer), selectinload(Payment.bot)).order_by(Payment.id.desc()).limit(300)).all())


@router.get('/expenses')
def expenses(request: Request):
    with request.app.state.session_factory() as db:
        require_admin(request, db)
        return page(request, 'ledger.html', db, kind='expenses', rows=db.scalars(select(Expense).order_by(Expense.id.desc()).limit(300)).all(),
                    bots=db.scalars(select(Bot)).all(), projects=db.scalars(select(Project).order_by(Project.id.desc()).limit(200)).all())


@router.post('/expenses')
def add_expense(request: Request, token: str = Form(), category: str = Form(), vendor: str = Form(''), amount: str = Form(), currency: str = Form('UAH'), bot_id: str = Form(''), project_id: str = Form(''), memo: str = Form(''), spent_on: str = Form('')):
    with request.app.state.session_factory() as db:
        admin = require_admin(request, db)
        csrf(request, token)
        bot = db.get(Bot, int(bot_id)) if bot_id.isdigit() else None
        if bot_id and not bot: raise HTTPException(422)
        project = db.get(Project, int(project_id)) if project_id.isdigit() else None
        if project_id and not project: raise HTTPException(422)
        if not category.strip() or len(currency)!=3 or not currency.isalpha(): raise HTTPException(422)
        value = money(amount)
        if value <= 0: raise HTTPException(422, 'Expense must be positive')
        try:
            spent_at = datetime.fromisoformat(spent_on).replace(tzinfo=timezone.utc) if spent_on else datetime.now(timezone.utc)
        except ValueError:
            raise HTTPException(422, 'Некорректная дата расхода') from None
        entry = Expense(category=category[:80], vendor=vendor[:150], amount=value, currency=currency.upper(), bot_id=bot.id if bot else None, memo=memo[:2000], spent_at=spent_at)
        db.add(entry); db.flush()
        if project:
            db.add(ProjectExpense(expense_id=entry.id, project_id=project.id))
        audit(db, 'expense.create', str(entry.id), admin.id)
        db.commit()
        return redirect('/expenses')


@router.post('/expenses/{expense_id}')
def edit_expense(request: Request, expense_id: int, token: str = Form(),
                 category: str = Form(), vendor: str = Form(''), amount: str = Form(),
                 currency: str = Form('UAH'), bot_id: str = Form(''),
                 memo: str = Form(''), spent_on: str = Form()):
    with request.app.state.session_factory() as db:
        admin = require_admin(request, db, owner=True); csrf(request, token)
        expense = db.get(Expense, expense_id)
        if not expense:
            raise HTTPException(404)
        bot = db.get(Bot, int(bot_id)) if bot_id.isdigit() else None
        if bot_id and not bot:
            raise HTTPException(422, 'Неизвестный бот')
        try:
            spent_at = datetime.fromisoformat(spent_on).replace(tzinfo=timezone.utc)
        except ValueError:
            raise HTTPException(422, 'Некорректная дата') from None
        currency = currency.upper()
        if not category.strip() or len(category) > 80 or currency not in ('UAH', 'USD', 'EUR'):
            raise HTTPException(422, 'Некорректная категория или валюта')
        value = money(amount)
        if value <= 0:
            raise HTTPException(422, 'Сумма должна быть положительной')
        before = f'{expense.amount} {expense.currency} on {expense.spent_at.date()}'
        expense.category, expense.vendor, expense.amount = category.strip(), vendor[:150], value
        expense.currency, expense.bot_id, expense.memo, expense.spent_at = currency, bot.id if bot else None, memo[:2000], spent_at
        audit(db, 'expense.update', str(expense.id), admin.id,
              f'{before} -> {expense.amount} {expense.currency} on {expense.spent_at.date()}')
        db.commit()
        return redirect('/expenses')


@router.get('/tasks')
def tasks(request: Request):
    with request.app.state.session_factory() as db:
        require_admin(request, db)
        return page(request, 'tasks.html', db, rows=db.scalars(select(Task).options(selectinload(Task.customer)).order_by(Task.id.desc()).limit(300)).all())


@router.post('/tasks')
def add_task(request: Request, token: str = Form(), title: str = Form(), customer_id: str = Form(''), due_at: str = Form('')):
    with request.app.state.session_factory() as db:
        admin = require_admin(request, db); csrf(request, token)
        if not title.strip(): raise HTTPException(422)
        customer = db.get(Customer, int(customer_id)) if customer_id.isdigit() else None
        if customer_id and not customer: raise HTTPException(422)
        try: due = datetime.fromisoformat(due_at) if due_at else None
        except ValueError: raise HTTPException(422)
        task = Task(title=title[:200], customer_id=customer.id if customer else None, due_at=due)
        db.add(task); db.flush(); audit(db, 'task.create', str(task.id), admin.id); db.commit()
        return redirect('/tasks')


@router.post('/tasks/{task_id}/complete')
def complete_task(request: Request, task_id: int, token: str = Form()):
    with request.app.state.session_factory() as db:
        admin=require_admin(request,db); csrf(request,token)
        task=db.get(Task,task_id)
        if not task: raise HTTPException(404)
        task.status='done'; audit(db,'task.complete',str(task_id),admin.id); db.commit()
        return redirect('/tasks')


@router.get('/analytics')
def analytics(request: Request, days: int = 30):
    with request.app.state.session_factory() as db:
        require_admin(request,db)
        days = max(1,min(days,365))
        statuses = db.execute(select(Customer.status,func.count()).group_by(Customer.status)).all()
        order_statuses = db.execute(select(Order.status,func.count()).group_by(Order.status)).all()
        deal_stages=db.execute(select(Stage.name,func.count(Deal.id)).join(Deal,Deal.stage_id==Stage.id,isouter=True).group_by(Stage.id,Stage.name,Stage.pipeline_id,Stage.position).order_by(Stage.pipeline_id,Stage.position)).all()
        project_statuses=db.execute(select(Project.status,func.count()).group_by(Project.status)).all()
        ticket_statuses=db.execute(select(Ticket.status,func.count()).group_by(Ticket.status)).all()
        return page(request,'analytics.html',db,metrics=summary(db,days),statuses=statuses,order_statuses=order_statuses,
                    deal_stages=deal_stages,project_statuses=project_statuses,ticket_statuses=ticket_statuses)


@router.get('/campaigns')
def campaigns(request: Request):
    with request.app.state.session_factory() as db:
        require_admin(request,db)
        rows=db.scalars(select(Campaign).options(selectinload(Campaign.bot)).order_by(Campaign.id.desc())).all()
        counts=dict(db.execute(select(Delivery.campaign_id,func.count()).group_by(Delivery.campaign_id)).all())
        sent=dict(db.execute(select(Delivery.campaign_id,func.count()).where(Delivery.status=='sent').group_by(Delivery.campaign_id)).all())
        return page(request,'campaigns.html',db,rows=rows,bots=db.scalars(select(Bot).where(Bot.active.is_(True))).all(),counts=counts,sent=sent)


@router.post('/campaigns')
def create_campaign(request: Request, token: str = Form(), bot_id: int = Form(), title: str = Form(), message: str = Form(), tag_filter: str = Form('')):
    with request.app.state.session_factory() as db:
        admin=require_admin(request,db); csrf(request,token)
        if not db.get(Bot,bot_id) or not title.strip() or not message.strip() or len(message)>4096 or len(tag_filter)>80: raise HTTPException(422)
        row=Campaign(bot_id=bot_id,title=title[:200],message=message,tag_filter=tag_filter.strip(),created_by=admin.id)
        db.add(row); db.flush(); audit(db,'campaign.create',str(row.id),admin.id); db.commit()
        return redirect(f'/campaigns/{row.id}')


def recipients(db, campaign):
    stmt=select(Identity).join(Customer,Identity.customer_id==Customer.id).where(
        Identity.bot_id==campaign.bot_id, Identity.chat_id.is_not(None), Identity.blocked.is_(False),
        Customer.marketing_consent.is_(True), Customer.status!='blocked')
    if campaign.tag_filter:
        stmt=stmt.where(Customer.tags.ilike('%'+campaign.tag_filter.replace('%','\\%').replace('_','\\_')+'%',escape='\\'))
    return db.scalars(stmt.order_by(Identity.id)).all()


@router.get('/campaigns/{campaign_id}')
def campaign_detail(request: Request,campaign_id:int):
    with request.app.state.session_factory() as db:
        require_admin(request,db)
        row=db.get(Campaign,campaign_id)
        if not row: raise HTTPException(404)
        counts=dict(db.execute(select(Delivery.status,func.count()).where(Delivery.campaign_id==row.id).group_by(Delivery.status)).all())
        return page(request,'campaign.html',db,row=row,preview=len(recipients(db,row)) if row.status=='draft' else None,counts=counts,
            deliveries=db.scalars(select(Delivery).where(Delivery.campaign_id==row.id).order_by(Delivery.id.desc()).limit(100)).all())


@router.post('/campaigns/{campaign_id}/launch')
def launch_campaign(request: Request,campaign_id:int, token:str=Form()):
    with request.app.state.session_factory() as db:
        admin=require_admin(request,db,owner=True); csrf(request,token)
        row=db.scalar(select(Campaign).where(Campaign.id==campaign_id).with_for_update())
        if not row: raise HTTPException(404)
        if row.status!='draft': raise HTTPException(409,'Campaign already launched')
        if not request.app.state.settings.bot_tokens.get(db.get(Bot,row.bot_id).key): raise HTTPException(409,'Bot token is not configured')
        ids=recipients(db,row)
        if not ids: raise HTTPException(409,'No eligible recipients')
        db.add_all(Delivery(campaign_id=row.id,identity_id=identity.id) for identity in ids)
        row.status='queued'
        audit(db,'campaign.launch',str(row.id),admin.id,f'recipients={len(ids)}'); db.commit()
        return redirect(f'/campaigns/{row.id}')


@router.post('/campaigns/{campaign_id}/pause')
def pause_campaign(request:Request,campaign_id:int,token:str=Form()):
    with request.app.state.session_factory() as db:
        admin=require_admin(request,db,owner=True); csrf(request,token)
        row=db.get(Campaign,campaign_id)
        if not row: raise HTTPException(404)
        if row.status not in {'queued','running','paused'}: raise HTTPException(409)
        row.status='paused' if row.status!='paused' else 'queued'
        audit(db,'campaign.status',str(row.id),admin.id,row.status); db.commit()
        return redirect(f'/campaigns/{row.id}')


@router.get('/audit')
def audit_page(request:Request):
    with request.app.state.session_factory() as db:
        require_admin(request,db,owner=True)
        return page(request,'audit.html',db,rows=db.scalars(select(Audit).order_by(Audit.id.desc()).limit(200)).all())
