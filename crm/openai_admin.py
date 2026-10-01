"""OpenAI provider control center: read-only provider telemetry and local bot mapping."""
import csv
import io
import json
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, StreamingResponse
from sqlalchemy import select

from .bot_settings import encrypt
from .models import (Bot, OpenAIBotBinding, OpenAICost, OpenAIOrganization,
                     OpenAIResource, OpenAIUsage)
from .openai_sync import ID, OpenAISyncError, sync_organization
from .security import audit, csrf, require_admin
from .web import page

router = APIRouter()


def _redirect(path='/openai'):
    return RedirectResponse(path, status_code=303)


def report(db, organization, bot_id=0, days=30):
    current = datetime.now(timezone.utc)
    start = current - timedelta(days=days)
    bindings = db.scalars(select(OpenAIBotBinding).where(
        OpenAIBotBinding.organization_id == organization.id)).all()
    costs = db.scalars(select(OpenAICost).where(OpenAICost.organization_id == organization.id,
        OpenAICost.bucket_start >= start).order_by(OpenAICost.bucket_start.desc())).all()
    resources = db.scalars(select(OpenAIResource).where(
        OpenAIResource.organization_id == organization.id).order_by(OpenAIResource.kind,
        OpenAIResource.external_id)).all()
    usage = db.scalars(select(OpenAIUsage).where(OpenAIUsage.organization_id == organization.id,
        OpenAIUsage.bucket_start >= start)).all()
    projects = {row.external_id: json.loads(row.payload_json).get('name') or row.external_id
                for row in resources if row.kind == 'project'}
    by_key = defaultdict(list)
    by_project = defaultdict(list)
    for binding in bindings:
        if binding.api_key_id: by_key[binding.api_key_id].append(binding)
        elif binding.project_id: by_project[binding.project_id].append(binding)

    def attributed(cost):
        matches = by_key.get(cost.api_key_id, []) if cost.api_key_id else []
        if not matches: matches = by_project.get(cost.project_id, []) if cost.project_id else []
        return matches[0].bot_id if len(matches) == 1 else None

    shown = [c for c in costs if not bot_id or attributed(c) == bot_id]
    totals, by_day, by_line, by_project_spend, unassigned = defaultdict(Decimal), defaultdict(Decimal), defaultdict(Decimal), defaultdict(Decimal), defaultdict(Decimal)
    for cost in costs:
        if attributed(cost) is None:
            unassigned[cost.currency] += cost.amount
    for cost in shown:
        totals[cost.currency] += cost.amount
        if cost.currency == 'USD':
            by_day[cost.bucket_start.date().isoformat()] += cost.amount
            by_line[cost.line_item or 'Прочее'] += cost.amount
            by_project_spend[projects.get(cost.project_id, cost.project_id or 'Без проекта')] += cost.amount
    usage_totals = defaultdict(lambda: defaultdict(Decimal))
    models = defaultdict(lambda: defaultdict(Decimal))
    usage_rows = []
    for item in usage:
        dims = json.loads(item.dimensions_json)
        if bot_id:
            class Slice: pass
            fake = Slice()
            fake.project_id, fake.api_key_id = dims.get('project_id') or '', dims.get('api_key_id') or ''
            if attributed(fake) != bot_id: continue
        metrics = json.loads(item.metrics_json)
        if len(usage_rows) < 150:
            usage_rows.append((item.bucket_start, item.family, dims, metrics))
        for key, value in metrics.items():
            if type(value) in (int, float) and key != 'object':
                usage_totals[item.family][key] += Decimal(str(value))
                if dims.get('model'):
                    models[str(dims['model'])][key] += Decimal(str(value))
    groups = defaultdict(list)
    for item in resources:
        groups[item.kind].append(dict(id=item.external_id, data=json.loads(item.payload_json),
            observed_at=item.observed_at))
    max_day = max(by_day.values(), default=Decimal('0'))
    month_start = datetime(current.year, current.month, 1, tzinfo=timezone.utc)
    monthly = db.scalars(select(OpenAICost).where(OpenAICost.organization_id == organization.id,
        OpenAICost.bucket_start >= month_start, OpenAICost.bucket_start < current)).all()
    month_usd = sum((c.amount for c in monthly if c.currency == 'USD'), Decimal('0'))
    org_limit = groups.get('org_spend_limit', [])
    limit_usd = (Decimal(str(org_limit[0]['data']['threshold_amount'])) / 100
                 if org_limit and 'threshold_amount' in org_limit[0]['data'] else None)
    bars = [(day, amount, int(amount * 100 / max_day) if max_day else 0)
            for day, amount in sorted(by_day.items())]
    return dict(costs=shown[:150], totals=dict(totals), unassigned=dict(unassigned),
        daily=sorted(by_day.items()), lines=sorted(by_line.items(), key=lambda x: -x[1])[:20],
        projects_spend=sorted(by_project_spend.items(), key=lambda x: -x[1])[:20],
        usage={k: dict(v) for k,v in usage_totals.items()}, bars=bars,
        models={k: dict(v) for k, v in models.items()}, usage_rows=usage_rows,
        resources=groups, projects=projects, bindings=bindings,
        month_usd=month_usd, limit_usd=limit_usd,
        headroom_usd=max(Decimal('0'), limit_usd-month_usd) if limit_usd is not None else None)


@router.get('/openai')
def openai_dashboard(request: Request, organization_id: int = 0, bot_id: int = 0,
                     days: int = 30):
    with request.app.state.session_factory() as db:
        require_admin(request, db)
        organizations = db.scalars(select(OpenAIOrganization).order_by(OpenAIOrganization.id)).all()
        chosen = next((o for o in organizations if o.id == organization_id), None)
        if not chosen and organizations: chosen = organizations[0]
        bots = db.scalars(select(Bot).order_by(Bot.name)).all()
        if bot_id and not any(b.id == bot_id for b in bots): bot_id = 0
        days = days if days in (7, 30, 90) else 30
        return page(request, 'openai.html', db, organizations=organizations,
            organization=chosen, bots=bots, bot_id=bot_id, days=days,
            report=report(db, chosen, bot_id, days) if chosen else None)


def key_inventory(db, organization):
    """Provider inventory: IDs and metadata only, never usable key values."""
    resources = db.scalars(select(OpenAIResource).where(
        OpenAIResource.organization_id == organization.id,
        OpenAIResource.kind.in_(('project', 'project_key', 'admin_key')))
        .order_by(OpenAIResource.kind, OpenAIResource.external_id)).all()
    projects = {r.external_id: json.loads(r.payload_json).get('name') or r.external_id
                for r in resources if r.kind == 'project'}
    bots = {b.id: b.name for b in db.scalars(select(Bot)).all()}
    bindings = db.scalars(select(OpenAIBotBinding).where(
        OpenAIBotBinding.organization_id == organization.id)).all()
    by_key = defaultdict(set)
    by_project = defaultdict(set)
    for binding in bindings:
        if binding.api_key_id:
            by_key[(binding.project_id, binding.api_key_id)].add(bots.get(binding.bot_id, str(binding.bot_id)))
        else:
            by_project[binding.project_id].add(bots.get(binding.bot_id, str(binding.bot_id)))
    since = datetime.now(timezone.utc) - timedelta(days=30)
    costs = db.scalars(select(OpenAICost).where(OpenAICost.organization_id == organization.id,
        OpenAICost.bucket_start >= since, OpenAICost.currency == 'USD')).all()
    spend = defaultdict(Decimal)
    for cost in costs:
        if cost.api_key_id:
            spend[(cost.project_id, cost.api_key_id)] += cost.amount

    def timestamp(value):
        if isinstance(value, (float, int)) and 0 < value < 32503680000:
            return datetime.fromtimestamp(value, timezone.utc).strftime('%d.%m.%Y %H:%M UTC')
        return '—'

    now_ts = datetime.now(timezone.utc).timestamp()
    rows = []
    for resource in resources:
        if resource.kind == 'project':
            continue
        data = json.loads(resource.payload_json)
        project_id, key_id = (resource.external_id.split(':', 1)
            if resource.kind == 'project_key' else ('', resource.external_id))
        owner = data.get('owner') or {}
        expires = data.get('expires_at')
        access = data.get('owner_project_access')
        status = ('expired' if isinstance(expires, (float, int)) and 0 < expires <= now_ts
                  else 'inactive' if access == 'inactive' else 'active' if access == 'active'
                  else 'listed')
        if organization.last_synced_at and resource.observed_at:
            observed = resource.observed_at.replace(tzinfo=timezone.utc) if resource.observed_at.tzinfo is None else resource.observed_at
            synced = organization.last_synced_at.replace(tzinfo=timezone.utc) if organization.last_synced_at.tzinfo is None else organization.last_synced_at
            if observed < synced - timedelta(seconds=1):
                status = 'stale'
        rows.append(dict(kind=resource.kind, id=key_id,
            name=data.get('name') or key_id, project_id=project_id,
            project_name=projects.get(project_id, project_id or 'Организация'),
            masked=data.get('redacted_value') if isinstance(data.get('redacted_value'), str)
                and '...' in data['redacted_value'] else '',
            owner=owner.get('name') or owner.get('email') or owner.get('id') or '—',
            owner_type=owner.get('type') or '—', access=access or '—', status=status,
            created=timestamp(data.get('created_at')),
            last_used=timestamp(data.get('last_used_at')),
            expires=timestamp(expires), unused=not data.get('last_used_at'),
            observed=resource.observed_at.strftime('%d.%m.%Y %H:%M UTC'),
            bots=', '.join(sorted(by_key.get((project_id, key_id), set()) or
                by_project.get(project_id, set()))) if project_id else '—',
            spend=spend[(project_id, key_id)] if project_id else None))
    return projects, rows


def filter_keys(rows, project_id='', state='', q=''):
    if project_id:
        rows = [r for r in rows if r['project_id'] == project_id]
    if state == 'unused':
        rows = [r for r in rows if r['unused']]
    elif state in ('active', 'inactive', 'expired', 'stale'):
        rows = [r for r in rows if r['status'] == state]
    if q:
        needle = q.strip().casefold()[:100]
        rows = [r for r in rows if needle in ' '.join(str(r[field]) for field in
            ('id', 'name', 'project_name', 'owner', 'bots')).casefold()]
    return rows


@router.get('/openai/keys')
def openai_keys(request: Request, organization_id: int = 0, project_id: str = '',
                state: str = '', q: str = '', page_num: int = 1):
    with request.app.state.session_factory() as db:
        require_admin(request, db)
        organizations = db.scalars(select(OpenAIOrganization).order_by(OpenAIOrganization.id)).all()
        chosen = next((o for o in organizations if o.id == organization_id), None)
        if not chosen and organizations: chosen = organizations[0]
        if not chosen:
            return _redirect('/openai')
        projects, all_rows = key_inventory(db, chosen)
        rows = filter_keys(all_rows, project_id, state, q)
        page_num = max(1, min(page_num, 10000))
        return page(request, 'openai_keys.html', db, organizations=organizations,
            organization=chosen, projects=projects, rows=rows[(page_num-1)*50:page_num*50],
            total=len(rows), project_count=len(projects),
            project_id=project_id, state=state, q=q[:100], page_num=page_num,
            project_keys=sum(r['kind']=='project_key' for r in all_rows),
            admin_keys=sum(r['kind']=='admin_key' for r in all_rows),
            inactive=sum(r['status'] in ('expired', 'inactive') for r in all_rows),
            stale=sum(r['status'] == 'stale' for r in all_rows))


@router.get('/openai/keys.csv')
def export_openai_keys(request: Request, organization_id: int, project_id: str = '',
                       state: str = '', q: str = ''):
    with request.app.state.session_factory() as db:
        require_admin(request, db)
        org = db.get(OpenAIOrganization, organization_id)
        if not org: raise HTTPException(404)
        _, rows = key_inventory(db, org)
        stream = io.StringIO()
        writer = csv.writer(stream)
        writer.writerow(['type', 'project_id', 'project_name', 'key_id', 'name',
            'owner', 'owner_type', 'access', 'status', 'created_utc', 'last_used_utc',
            'expires_utc', 'bots', 'cost_30d_usd', 'observed_utc'])
        from .web import csv_cell
        for r in filter_keys(rows, project_id, state, q):
            writer.writerow([csv_cell(r['kind']), csv_cell(r['project_id']),
                csv_cell(r['project_name']), csv_cell(r['id']), csv_cell(r['name']),
                csv_cell(r['owner']), csv_cell(r['owner_type']), csv_cell(r['access']),
                csv_cell(r['status']), r['created'], r['last_used'], r['expires'],
                csv_cell(r['bots']), str(r['spend']) if r['spend'] is not None else '',
                r['observed']])
        return StreamingResponse(iter(['\ufeff' + stream.getvalue()]),
            media_type='text/csv; charset=utf-8',
            headers={'Content-Disposition': 'attachment; filename="openai_keys.csv"'})


@router.post('/openai/organizations')
def save_organization(request: Request, token: str = Form(), organization_id: str = Form(),
                      name: str = Form(), admin_key: str = Form(default='')):
    with request.app.state.session_factory() as db:
        admin = require_admin(request, db, owner=True); csrf(request, token)
        org_key = organization_id.strip()
        if not re.fullmatch(r'org[-_][a-zA-Z0-9_-]{1,116}', org_key):
            raise HTTPException(422, 'Укажите OpenAI Organization ID вида org-... из настроек OpenAI Platform. Число 1 — внутренний ID CRM.')
        if not 1 <= len(name.strip()) <= 160:
            raise HTTPException(422, 'Укажите название организации (до 160 символов)')
        row = db.scalar(select(OpenAIOrganization).where(OpenAIOrganization.organization_id == org_key))
        if admin_key and (len(admin_key) > 512 or not admin_key.strip()):
            raise HTTPException(422, 'Некорректный Admin API key')
        if not row:
            if not admin_key.strip():
                raise HTTPException(422, 'При первом подключении нужен Admin API key из OpenAI Platform')
            if not request.app.state.settings.encryption_key:
                raise HTTPException(422, 'На сервере не задан CRM_ENCRYPTION_KEY')
            row = OpenAIOrganization(organization_id=org_key, name=name.strip(),
                encrypted_admin_key=encrypt(admin_key.strip(), request.app.state.settings.encryption_key))
            db.add(row)
        else:
            row.name = name.strip()
            if admin_key.strip():
                row.encrypted_admin_key = encrypt(admin_key.strip(), request.app.state.settings.encryption_key)
                row.status, row.last_error = 'not_synced', ''
        audit(db, 'openai.organization.save', org_key, admin.id)
        db.commit()
        return _redirect('/openai?organization_id=' + str(row.id))


@router.post('/openai/organizations/{organization_id}/sync')
def sync_now(request: Request, organization_id: int, token: str = Form()):
    with request.app.state.session_factory() as db:
        admin = require_admin(request, db, owner=True); csrf(request, token)
        row = db.get(OpenAIOrganization, organization_id)
        if not row: raise HTTPException(404)
        try:
            result = sync_organization(db, row, request.app.state.settings.encryption_key)
            audit(db, 'openai.organization.sync', str(row.id), admin.id,
                  f"cost={result['cost_rows']}, usage={result['usage_rows']}")
            db.commit()
        except OpenAISyncError as exc:
            db.rollback()
            row = db.get(OpenAIOrganization, organization_id)
            row.status, row.last_error = 'failed', exc.code
            db.commit()
        return _redirect('/openai?organization_id=' + str(row.id))


@router.post('/openai/bindings')
def save_binding(request: Request, token: str = Form(), organization_id: int = Form(),
                 bot_id: int = Form(), label: str = Form(), project_id: str = Form(),
                 api_key_id: str = Form(default=''), notes: str = Form(default='')):
    with request.app.state.session_factory() as db:
        admin = require_admin(request, db, owner=True); csrf(request, token)
        if (not db.get(Bot, bot_id) or not db.get(OpenAIOrganization, organization_id) or
            not ID.fullmatch(project_id) or (api_key_id and not ID.fullmatch(api_key_id)) or
            not 1 <= len(label.strip()) <= 120 or len(notes) > 300):
            raise HTTPException(422, 'Проверьте параметры привязки')
        resource = db.scalar(select(OpenAIResource).where(
            OpenAIResource.organization_id == organization_id,
            OpenAIResource.kind == 'project', OpenAIResource.external_id == project_id))
        if not resource:
            raise HTTPException(422, 'Синхронизируйте реестр проектов OpenAI')
        if api_key_id and not db.scalar(select(OpenAIResource.id).where(
            OpenAIResource.organization_id == organization_id,
            OpenAIResource.kind == 'project_key',
            OpenAIResource.external_id == project_id + ':' + api_key_id)):
            raise HTTPException(422, 'Ключ отсутствует в этом OpenAI проекте')
        overlap = db.scalars(select(OpenAIBotBinding).where(
            OpenAIBotBinding.organization_id == organization_id,
            OpenAIBotBinding.project_id == project_id)).all()
        if any(existing.bot_id != bot_id and
               (not existing.api_key_id or not api_key_id or existing.api_key_id == api_key_id)
               for existing in overlap):
            raise HTTPException(409, 'Проект или ключ уже закреплен за другим ботом')
        row = db.scalar(select(OpenAIBotBinding).where(OpenAIBotBinding.bot_id == bot_id,
            OpenAIBotBinding.label == label.strip()))
        if row and row.organization_id != organization_id:
            raise HTTPException(409, 'Метка уже используется этим ботом для другой организации')
        if row is None:
            row = OpenAIBotBinding(bot_id=bot_id, organization_id=organization_id,
                                   label=label.strip())
            db.add(row)
        row.project_id, row.api_key_id, row.notes = project_id, api_key_id, notes.strip()
        audit(db, 'openai.binding.save', f'{bot_id}:{label.strip()}', admin.id)
        db.commit()
        return _redirect('/openai?organization_id=' + str(organization_id) + '&bot_id=' + str(bot_id))


@router.post('/openai/bindings/{binding_id}/delete')
def delete_binding(request: Request, binding_id: int, token: str = Form()):
    with request.app.state.session_factory() as db:
        admin = require_admin(request, db, owner=True); csrf(request, token)
        row = db.get(OpenAIBotBinding, binding_id)
        if not row: raise HTTPException(404)
        organization_id = row.organization_id
        audit(db, 'openai.binding.delete', str(binding_id), admin.id)
        db.delete(row); db.commit()
        return _redirect('/openai?organization_id=' + str(organization_id))


@router.get('/openai/costs.csv')
def export_costs(request: Request, organization_id: int, bot_id: int = 0, days: int = 30):
    with request.app.state.session_factory() as db:
        require_admin(request, db)
        organization = db.get(OpenAIOrganization, organization_id)
        if not organization: raise HTTPException(404)
        data = report(db, organization, bot_id, days if days in (7,30,90) else 30)
        stream = io.StringIO()
        writer = csv.writer(stream)
        writer.writerow(['day_utc', 'organization_id', 'project_id', 'api_key_id',
                         'line_item', 'amount', 'currency', 'quantity', 'quantity_unit'])
        # Export full filtered window, not just the 150 newest UI rows.
        rows = db.scalars(select(OpenAICost).where(OpenAICost.organization_id == organization.id,
            OpenAICost.bucket_start >= datetime.now(timezone.utc) - timedelta(days=days if days in (7,30,90) else 30))
            .order_by(OpenAICost.bucket_start)).all()
        if bot_id:
            # Reuse attribution over the full window (report exposes 150 rows only).
            bindings = data['bindings']
            def belongs(r):
                matches = [b for b in bindings if r.api_key_id and b.api_key_id == r.api_key_id]
                if not matches:
                    matches = [b for b in bindings if not b.api_key_id and b.project_id == r.project_id]
                return len(matches) == 1 and matches[0].bot_id == bot_id
            rows = [r for r in rows if belongs(r)]
        for r in rows:
            from .web import csv_cell
            writer.writerow([r.bucket_start.date().isoformat(), organization.organization_id,
                csv_cell(r.project_id), csv_cell(r.api_key_id), csv_cell(r.line_item),
                str(r.amount), r.currency, str(r.quantity or ''), csv_cell(r.quantity_unit)])
        return StreamingResponse(iter([stream.getvalue().encode('utf-8-sig')]),
            media_type='text/csv', headers={'Content-Disposition': 'attachment; filename="openai_costs.csv"'})
