"""Sales pipeline and IT project delivery screens."""
from datetime import datetime, timezone
from collections import defaultdict
from decimal import Decimal
from fastapi import APIRouter, Form, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload
from .ingest import money
from .models import Company, CompanyContact, Customer, Deal, Expense, Invoice, Milestone, Pipeline, Project, ProjectExpense, Stage, Ticket
from .security import audit, csrf, require_admin
from .web import page, redirect

router=APIRouter()
SERVICES={'website':'Сайт','web_app':'Веб-приложение','portal':'Портал','bot':'Telegram-бот','support':'Поддержка','integration':'Интеграция','other':'Другое'}
PROJECT_STATUSES={'discovery':'Исследование','design':'Дизайн','development':'Разработка','qa':'Тестирование','launch':'Запуск','support':'Поддержка','completed':'Завершен','paused':'Пауза'}
TICKET_STATUSES={'open':'Открыто','in_progress':'В работе','waiting':'Ожидает клиента','resolved':'Решено'}


def required(value,limit=200):
    if not value.strip() or len(value)>limit: raise HTTPException(422,'Required field is invalid')
    return value.strip()


def optional_id(db,model,value):
    if not value: return None
    if not value.isdigit() or not db.get(model,int(value)): raise HTTPException(422,'Related record not found')
    return int(value)


def date(value):
    if not value: return None
    try: return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
    except ValueError: raise HTTPException(422,'Invalid date')


@router.get('/companies')
def companies(request:Request,q:str=''):
    with request.app.state.session_factory() as db:
        require_admin(request,db)
        stmt=select(Company)
        if q: stmt=stmt.where(Company.name.ilike('%'+q[:100]+'%'))
        return page(request,'companies.html',db,rows=db.scalars(stmt.order_by(Company.id.desc()).limit(200)).all(),q=q)


@router.post('/companies')
def company_create(request:Request,token:str=Form(),name:str=Form(),website:str=Form(''),industry:str=Form(''),email:str=Form(''),phone:str=Form(''),notes:str=Form('')):
    with request.app.state.session_factory() as db:
        admin=require_admin(request,db);csrf(request,token)
        row=Company(name=required(name),website=website[:255],industry=industry[:100],email=email[:255],phone=phone[:40],notes=notes[:5000])
        db.add(row);db.flush();audit(db,'company.create',str(row.id),admin.id);db.commit()
        return redirect(f'/companies/{row.id}')


@router.get('/companies/{company_id}')
def company_detail(request:Request,company_id:int):
    with request.app.state.session_factory() as db:
        require_admin(request,db)
        company=db.get(Company,company_id)
        if not company: raise HTTPException(404)
        contacts=db.scalars(select(CompanyContact).options(selectinload(CompanyContact.customer)).where(CompanyContact.company_id==company_id)).all()
        deals=db.scalars(select(Deal).where(Deal.company_id==company_id).order_by(Deal.id.desc())).all()
        projects=db.scalars(select(Project).where(Project.company_id==company_id).order_by(Project.id.desc())).all()
        return page(request,'company.html',db,company=company,contacts=contacts,deals=deals,projects=projects)


@router.post('/companies/{company_id}/contacts')
def company_contact(request:Request,company_id:int,token:str=Form(),customer_id:str=Form(),role:str=Form('contact')):
    with request.app.state.session_factory() as db:
        admin=require_admin(request,db);csrf(request,token)
        if not db.get(Company,company_id): raise HTTPException(404)
        cid=optional_id(db,Customer,customer_id)
        if not cid: raise HTTPException(422)
        existing=db.scalar(select(CompanyContact).where(CompanyContact.company_id==company_id,CompanyContact.customer_id==cid))
        if existing: existing.role=role[:100]
        else: db.add(CompanyContact(company_id=company_id,customer_id=cid,role=role[:100]))
        audit(db,'company.contact',str(company_id),admin.id,str(cid));db.commit()
        return redirect(f'/companies/{company_id}')


@router.get('/pipeline')
def pipeline(request:Request,pipeline_id:int|None=None):
    with request.app.state.session_factory() as db:
        require_admin(request,db)
        pipelines=db.scalars(select(Pipeline).order_by(Pipeline.id)).all()
        current=db.get(Pipeline,pipeline_id) if pipeline_id else (pipelines[0] if pipelines else None)
        if pipeline_id and not current: raise HTTPException(404)
        stages=db.scalars(select(Stage).where(Stage.pipeline_id==current.id).order_by(Stage.position)).all() if current else []
        deals=db.scalars(select(Deal).options(selectinload(Deal.customer),selectinload(Deal.company)).where(Deal.pipeline_id==current.id).order_by(Deal.id.desc())).all() if current else []
        return page(request,'pipeline.html',db,pipelines=pipelines,current=current,stages=stages,deals=deals,services=SERVICES)


@router.post('/pipeline')
def pipeline_create(request:Request,token:str=Form(),name:str=Form()):
    with request.app.state.session_factory() as db:
        admin=require_admin(request,db,owner=True);csrf(request,token)
        if db.scalar(select(Pipeline).where(Pipeline.name==name.strip())): raise HTTPException(409)
        row=Pipeline(name=required(name,120));db.add(row);db.flush()
        for position,title,prob,terminal in [(1,'Новая заявка',10,''),(2,'Квалификация',25,''),(3,'Оценка',45,''),(4,'Предложение',65,''),(5,'Согласование',85,''),(6,'Выиграна',100,'won'),(7,'Проиграна',0,'lost')]:
            db.add(Stage(pipeline_id=row.id,position=position,name=title,probability=prob,terminal=terminal))
        audit(db,'pipeline.create',str(row.id),admin.id);db.commit()
        return redirect(f'/pipeline?pipeline_id={row.id}')


@router.post('/pipeline/{pipeline_id}/stages')
def stage_create(request:Request,pipeline_id:int,token:str=Form(),name:str=Form(),probability:int=Form(0)):
    with request.app.state.session_factory() as db:
        admin=require_admin(request,db,owner=True);csrf(request,token)
        if not db.get(Pipeline,pipeline_id):raise HTTPException(404)
        if not 0<=probability<=100:raise HTTPException(422)
        pos=db.scalar(select(func.max(Stage.position)).where(Stage.pipeline_id==pipeline_id)) or 0
        row=Stage(pipeline_id=pipeline_id,position=pos+1,name=required(name,100),probability=probability)
        db.add(row);db.flush();audit(db,'stage.create',str(row.id),admin.id);db.commit()
        return redirect(f'/pipeline?pipeline_id={pipeline_id}')


@router.get('/deals/new')
def deal_new(request:Request):
    with request.app.state.session_factory() as db:
        require_admin(request,db)
        pipelines=db.scalars(select(Pipeline).order_by(Pipeline.id)).all()
        stages=db.scalars(select(Stage).order_by(Stage.pipeline_id,Stage.position)).all()
        return page(request,'deal_new.html',db,pipelines=pipelines,stages=stages,services=SERVICES)


@router.post('/deals/new')
def deal_create(request:Request,token:str=Form(),title:str=Form(),pipeline_id:int=Form(),stage_id:int=Form(),customer_id:str=Form(''),company_id:str=Form(''),service:str=Form('website'),budget:str=Form('0'),currency:str=Form('UAH'),expected_close_at:str=Form(''),notes:str=Form('')):
    with request.app.state.session_factory() as db:
        admin=require_admin(request,db);csrf(request,token)
        stage=db.get(Stage,stage_id)
        if not stage or stage.pipeline_id!=pipeline_id or service not in SERVICES or len(currency)!=3:raise HTTPException(422)
        row=Deal(title=required(title),pipeline_id=pipeline_id,stage_id=stage_id,
            customer_id=optional_id(db,Customer,customer_id),company_id=optional_id(db,Company,company_id),
            service=service,budget=money(budget),currency=currency.upper(),owner_id=admin.id,
            expected_close_at=date(expected_close_at),notes=notes[:5000])
        db.add(row);db.flush();audit(db,'deal.create',str(row.id),admin.id);db.commit()
        return redirect(f'/deals/{row.id}')


@router.get('/deals/{deal_id}')
def deal_detail(request:Request,deal_id:int):
    with request.app.state.session_factory() as db:
        require_admin(request,db)
        row=db.scalar(select(Deal).options(selectinload(Deal.stage),selectinload(Deal.customer),selectinload(Deal.company),selectinload(Deal.pipeline)).where(Deal.id==deal_id))
        if not row: raise HTTPException(404)
        stages=db.scalars(select(Stage).where(Stage.pipeline_id==row.pipeline_id).order_by(Stage.position)).all()
        projects=db.scalars(select(Project).where(Project.deal_id==row.id)).all()
        return page(request,'deal.html',db,row=row,stages=stages,projects=projects,services=SERVICES)


@router.post('/deals/{deal_id}/stage')
def deal_stage(request:Request,deal_id:int,token:str=Form(),stage_id:int=Form()):
    with request.app.state.session_factory() as db:
        admin=require_admin(request,db);csrf(request,token)
        row=db.get(Deal,deal_id);stage=db.get(Stage,stage_id)
        if not row:raise HTTPException(404)
        if not stage or stage.pipeline_id!=row.pipeline_id:raise HTTPException(422)
        row.stage_id=stage_id;audit(db,'deal.stage',str(deal_id),admin.id,stage.name);db.commit()
        return redirect(f'/deals/{deal_id}')


@router.post('/deals/{deal_id}/project')
def deal_to_project(request:Request,deal_id:int,token:str=Form()):
    with request.app.state.session_factory() as db:
        admin=require_admin(request,db);csrf(request,token)
        row=db.get(Deal,deal_id)
        if not row:raise HTTPException(404)
        if row.stage.terminal!='won':raise HTTPException(409,'Win the deal before creating a project')
        existing=db.scalar(select(Project).where(Project.deal_id==deal_id))
        if existing:return redirect(f'/projects/{existing.id}')
        project=Project(title=row.title,deal_id=row.id,customer_id=row.customer_id,company_id=row.company_id,
            service=row.service,budget=row.budget,currency=row.currency,status='discovery')
        db.add(project);db.flush();audit(db,'project.from_deal',str(project.id),admin.id,str(deal_id));db.commit()
        return redirect(f'/projects/{project.id}')


@router.get('/projects')
def projects(request:Request,status:str=''):
    with request.app.state.session_factory() as db:
        require_admin(request,db)
        stmt=select(Project).options(selectinload(Project.customer),selectinload(Project.company))
        if status in PROJECT_STATUSES:stmt=stmt.where(Project.status==status)
        return page(request,'projects.html',db,rows=db.scalars(stmt.order_by(Project.id.desc()).limit(300)).all(),statuses=PROJECT_STATUSES,status=status)


@router.post('/projects')
def project_create(request:Request,token:str=Form(),title:str=Form(),customer_id:str=Form(''),company_id:str=Form(''),service:str=Form('website'),budget:str=Form('0'),currency:str=Form('UAH'),due_at:str=Form('')):
    with request.app.state.session_factory() as db:
        admin=require_admin(request,db);csrf(request,token)
        if service not in SERVICES or len(currency)!=3:raise HTTPException(422)
        row=Project(title=required(title),customer_id=optional_id(db,Customer,customer_id),company_id=optional_id(db,Company,company_id),
            service=service,budget=money(budget),currency=currency.upper(),due_at=date(due_at))
        db.add(row);db.flush();audit(db,'project.create',str(row.id),admin.id);db.commit()
        return redirect(f'/projects/{row.id}')


@router.get('/projects/{project_id}')
def project_detail(request:Request,project_id:int):
    with request.app.state.session_factory() as db:
        require_admin(request,db)
        row=db.scalar(select(Project).options(selectinload(Project.customer),selectinload(Project.company)).where(Project.id==project_id))
        if not row:raise HTTPException(404)
        milestones=db.scalars(select(Milestone).where(Milestone.project_id==project_id).order_by(Milestone.position,Milestone.id)).all()
        tickets=db.scalars(select(Ticket).where(Ticket.project_id==project_id).order_by(Ticket.id.desc())).all()
        expenses=db.scalars(select(Expense).join(ProjectExpense, ProjectExpense.expense_id==Expense.id)
                            .where(ProjectExpense.project_id==project_id)).all()
        invoices=db.scalars(select(Invoice).where(Invoice.project_id==project_id)).all()
        costs, received = defaultdict(Decimal), defaultdict(Decimal)
        for expense in expenses: costs[expense.currency] += expense.amount
        for invoice in invoices:
            if invoice.status=='paid' and invoice.payment_id: received[invoice.currency] += invoice.total
        economics=[{'currency':c,'cost':costs[c],'received':received[c],
                    'margin':received[c]-costs[c]} for c in sorted(set(costs)|set(received)|{row.currency})]
        return page(request,'project.html',db,row=row,milestones=milestones,tickets=tickets,
                    expenses=expenses,invoices=invoices,economics=economics,statuses=PROJECT_STATUSES)


@router.post('/projects/{project_id}/status')
def project_status(request:Request,project_id:int,token:str=Form(),status:str=Form(),domain:str=Form(''),repository_url:str=Form(''),notes:str=Form('')):
    with request.app.state.session_factory() as db:
        admin=require_admin(request,db);csrf(request,token)
        row=db.get(Project,project_id)
        if not row:raise HTTPException(404)
        if status not in PROJECT_STATUSES or len(domain)>255 or len(repository_url)>500 or len(notes)>5000:raise HTTPException(422)
        row.status=status;row.domain=domain;row.repository_url=repository_url;row.notes=notes
        if status=='completed' and not row.launched_at:row.launched_at=datetime.now(timezone.utc)
        audit(db,'project.update',str(project_id),admin.id,status);db.commit()
        return redirect(f'/projects/{project_id}')


@router.post('/projects/{project_id}/milestones')
def milestone_create(request:Request,project_id:int,token:str=Form(),title:str=Form(),due_at:str=Form('')):
    with request.app.state.session_factory() as db:
        admin=require_admin(request,db);csrf(request,token)
        if not db.get(Project,project_id):raise HTTPException(404)
        pos=db.scalar(select(func.max(Milestone.position)).where(Milestone.project_id==project_id)) or 0
        row=Milestone(project_id=project_id,title=required(title),position=pos+1,due_at=date(due_at))
        db.add(row);db.flush();audit(db,'milestone.create',str(row.id),admin.id);db.commit()
        return redirect(f'/projects/{project_id}')


@router.post('/milestones/{milestone_id}/complete')
def milestone_complete(request:Request,milestone_id:int,token:str=Form()):
    with request.app.state.session_factory() as db:
        admin=require_admin(request,db);csrf(request,token)
        row=db.get(Milestone,milestone_id)
        if not row:raise HTTPException(404)
        row.status='done';row.completed_at=datetime.now(timezone.utc)
        audit(db,'milestone.complete',str(row.id),admin.id);db.commit()
        return redirect(f'/projects/{row.project_id}')


@router.get('/tickets')
def tickets(request:Request,status:str=''):
    with request.app.state.session_factory() as db:
        require_admin(request,db)
        stmt=select(Ticket).options(selectinload(Ticket.customer),selectinload(Ticket.project))
        if status in TICKET_STATUSES:stmt=stmt.where(Ticket.status==status)
        return page(request,'tickets.html',db,rows=db.scalars(stmt.order_by(Ticket.id.desc()).limit(300)).all(),statuses=TICKET_STATUSES,status=status)


@router.post('/tickets')
def ticket_create(request:Request,token:str=Form(),title:str=Form(),customer_id:str=Form(''),project_id:str=Form(''),priority:str=Form('normal'),kind:str=Form('support'),description:str=Form('')):
    with request.app.state.session_factory() as db:
        admin=require_admin(request,db);csrf(request,token)
        if priority not in {'low','normal','high','urgent'} or kind not in {'support','bug','change_request','question'} or len(description)>5000:raise HTTPException(422)
        row=Ticket(title=required(title),customer_id=optional_id(db,Customer,customer_id),project_id=optional_id(db,Project,project_id),priority=priority,kind=kind,description=description)
        db.add(row);db.flush();audit(db,'ticket.create',str(row.id),admin.id);db.commit()
        return redirect(f'/tickets/{row.id}')


@router.get('/tickets/{ticket_id}')
def ticket_detail(request:Request,ticket_id:int):
    with request.app.state.session_factory() as db:
        require_admin(request,db)
        row=db.scalar(select(Ticket).options(selectinload(Ticket.customer),selectinload(Ticket.project)).where(Ticket.id==ticket_id))
        if not row:raise HTTPException(404)
        return page(request,'ticket.html',db,row=row,statuses=TICKET_STATUSES)


@router.post('/tickets/{ticket_id}/status')
def ticket_status(request:Request,ticket_id:int,token:str=Form(),status:str=Form()):
    with request.app.state.session_factory() as db:
        admin=require_admin(request,db);csrf(request,token)
        row=db.get(Ticket,ticket_id)
        if not row:raise HTTPException(404)
        if status not in TICKET_STATUSES:raise HTTPException(422)
        row.status=status;row.resolved_at=datetime.now(timezone.utc) if status=='resolved' else None
        audit(db,'ticket.status',str(ticket_id),admin.id,status);db.commit()
        return redirect(f'/tickets/{ticket_id}')
