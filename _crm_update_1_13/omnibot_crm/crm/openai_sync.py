"""Read-only OpenAI Admin API observability. No model or key-management writes."""
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

import httpx
from sqlalchemy import delete, select

from .bot_settings import decrypt
from .models import OpenAICost, OpenAIOrganization, OpenAIResource, OpenAIUsage

BASE = 'https://api.openai.com/v1'
# The supported Admin Usage API families, grouped by fields exposed by each endpoint.
FAMILIES = {
    'completions': ['project_id', 'api_key_id', 'model', 'batch', 'service_tier'],
    'images': ['project_id', 'api_key_id', 'model', 'size', 'source'],
    'audio_speeches': ['project_id', 'api_key_id', 'model'],
    'audio_transcriptions': ['project_id', 'api_key_id', 'model'],
    'embeddings': ['project_id', 'api_key_id', 'model'],
    'moderations': ['project_id', 'api_key_id', 'model'],
    'vector_stores': ['project_id'],
    'code_interpreter_sessions': ['project_id'],
    'file_search_calls': ['project_id', 'api_key_id'],
    'web_search_calls': ['project_id', 'api_key_id', 'model'],
}
DIMENSIONS = {'project_id', 'api_key_id', 'user_id', 'model', 'batch', 'service_tier',
              'size', 'source', 'context_level', 'vector_store_id'}
ID = re.compile(r'^[a-zA-Z0-9_-]{1,120}$')


class OpenAISyncError(Exception):
    def __init__(self, code='provider_unavailable'):
        self.code = code
        super().__init__(code)


def _digest(*items):
    return hashlib.sha256(json.dumps(items, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _number(value):
    try:
        result = Decimal(str(value))
        if not result.is_finite() or abs(result) >= Decimal('1000000000000'):
            raise ValueError()
        return result
    except (ValueError, TypeError, InvalidOperation):
        raise OpenAISyncError('invalid_provider_amount') from None


def _request(client, path, headers, params=None):
    if not path.startswith('/organization/') or '://' in path:
        raise OpenAISyncError('invalid_endpoint')
    try:
        response = client.get(BASE + path, headers=headers, params=params, timeout=15,
                              follow_redirects=False)
        if response.status_code in (401, 403):
            raise OpenAISyncError('admin_permission_denied')
        if response.status_code == 404:
            raise OpenAISyncError('feature_unavailable')
        if response.status_code == 429:
            raise OpenAISyncError('rate_limited')
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError, TypeError):
        # Do not include URL, headers or provider response: they may include a key.
        raise OpenAISyncError('provider_unavailable') from None
    if not isinstance(data, dict) or len(json.dumps(data)) > 2_000_000:
        raise OpenAISyncError('invalid_provider_response')
    return data


def _pages(client, path, headers, params, *, cursor='page', limit=50):
    next_cursor = None
    seen = set()
    for _ in range(limit):
        query = dict(params)
        if next_cursor:
            query[cursor] = next_cursor
        page = _request(client, path, headers, query)
        rows = page.get('data')
        if not isinstance(rows, list):
            raise OpenAISyncError('invalid_provider_response')
        yield rows
        if cursor == 'page':
            next_cursor = page.get('next_page') if page.get('has_more') else None
        else:
            next_cursor = page.get('last_id') if page.get('has_more') else None
        if not next_cursor:
            return
        if not isinstance(next_cursor, str) or len(next_cursor) > 250 or next_cursor in seen:
            raise OpenAISyncError('invalid_pagination')
        seen.add(next_cursor)
    raise OpenAISyncError('pagination_limit')


def _resource(kind, data, identifier=None):
    if not isinstance(data, dict):
        raise OpenAISyncError('invalid_provider_response')
    identifier = identifier or data.get('id')
    if not isinstance(identifier, str) or len(identifier) > 180 or not identifier:
        raise OpenAISyncError('invalid_provider_response')
    if kind == 'project':
        keep = ('id', 'name', 'status', 'created_at', 'archived_at', 'residency', 'external_key_id')
    elif kind == 'project_key':
        keep = ('id', 'name', 'created_at', 'last_used_at', 'redacted_value', 'permission')
    elif kind in ('org_spend_limit', 'project_spend_limit'):
        keep = ('threshold_amount', 'currency', 'interval', 'enforcement')
    elif kind in ('org_spend_alert', 'project_spend_alert'):
        keep = ('id', 'threshold_amount', 'currency', 'interval', 'notification_channel')
    elif kind == 'project_rate_limit':
        keep = ('id', 'model', 'max_requests_per_1_minute', 'max_tokens_per_1_minute',
                'max_images_per_1_minute', 'max_audio_megabytes_per_1_minute',
                'max_requests_per_1_day', 'batch_1_day_max_input_tokens')
    else:
        keep = ('id', 'name', 'created_at', 'last_used_at', 'expires_at')
    meta = {key: data[key] for key in keep if key in data}
    if kind == 'project_key':
        owner = data.get('owner') or {}
        if isinstance(owner, dict):
            meta['owner'] = {'type': owner.get('type'),
                'id': (owner.get('user') or owner.get('service_account') or {}).get('id')}
    return kind, identifier, meta


def _collect(client, headers, start, end):
    resources, costs, usage, errors = [], [], [], {}
    projects = []
    for rows in _pages(client, '/organization/projects', headers,
                       {'limit': 100, 'include_archived': 'true'}, cursor='after'):
        for row in rows:
            projects.append(row)
            resources.append(_resource('project', row))
    for project in projects:
        project_id = project['id']
        if not ID.fullmatch(project_id):
            raise OpenAISyncError('invalid_provider_response')
        base = '/organization/projects/' + project_id
        for kind, path, params in (
            ('project_key', base + '/api_keys', {'limit': 100, 'owner_project_access': 'any'}),
            ('project_rate_limit', base + '/rate_limits', {'limit': 100}),
            ('project_spend_alert', base + '/spend_alerts', {'limit': 100})):
            try:
                for rows in _pages(client, path, headers, params, cursor='after'):
                    resources.extend(_resource(kind, row, project_id + ':' + str(row.get('id')))
                                     for row in rows)
            except OpenAISyncError as exc:
                errors[kind + ':' + project_id] = exc.code
        try:
            resources.append(_resource('project_spend_limit',
                _request(client, base + '/spend_limit', headers), project_id))
        except OpenAISyncError as exc:
            if exc.code != 'feature_unavailable':
                errors['project_spend_limit:' + project_id] = exc.code
    for kind, path in (('org_spend_limit', '/organization/spend_limit'),):
        try:
            resources.append(_resource(kind, _request(client, path, headers), 'organization'))
        except OpenAISyncError as exc:
            if exc.code != 'feature_unavailable':
                errors[kind] = exc.code
    try:
        for rows in _pages(client, '/organization/admin_api_keys', headers,
                           {'limit': 100}, cursor='after'):
            resources.extend(_resource('admin_key', row) for row in rows)
    except OpenAISyncError as exc:
        errors['admin_key'] = exc.code
    try:
        for rows in _pages(client, '/organization/spend_alerts', headers,
                           {'limit': 100}, cursor='after'):
            resources.extend(_resource('org_spend_alert', row) for row in rows)
    except OpenAISyncError as exc:
        errors['org_spend_alert'] = exc.code
    base_query = {'start_time': int(start.timestamp()), 'end_time': int(end.timestamp())}
    for buckets in _pages(client, '/organization/costs', headers,
        {**base_query, 'bucket_width': '1d', 'limit': 31,
         'group_by': ['project_id', 'api_key_id', 'line_item']}):
        for bucket in buckets:
            if not isinstance(bucket, dict) or not isinstance(bucket.get('results'), list):
                raise OpenAISyncError('invalid_provider_response')
            for row in bucket['results']:
                if not isinstance(row, dict) or row.get('object') != 'organization.costs.result':
                    raise OpenAISyncError('invalid_provider_response')
                amount = row.get('amount') or {}
                currency = str(amount.get('currency') or '').upper()
                if not re.fullmatch(r'[A-Z]{3}', currency):
                    raise OpenAISyncError('invalid_provider_response')
                quantity = row.get('quantity')
                costs.append((bucket['start_time'], bucket['end_time'],
                    str(row.get('project_id') or ''), str(row.get('api_key_id') or ''),
                    str(row.get('line_item') or ''), currency, _number(amount.get('value')),
                    _number(quantity) if quantity is not None else None,
                    str(row.get('quantity_unit') or '')))
    for family, group_by in FAMILIES.items():
        try:
            for buckets in _pages(client, '/organization/usage/' + family, headers,
                {**base_query, 'bucket_width': '1d', 'limit': 31, 'group_by': group_by}):
                for bucket in buckets:
                    if not isinstance(bucket, dict) or not isinstance(bucket.get('results'), list):
                        raise OpenAISyncError('invalid_provider_response')
                    for row in bucket['results']:
                        if not isinstance(row, dict):
                            raise OpenAISyncError('invalid_provider_response')
                        dimensions = {k: v for k, v in row.items() if k in DIMENSIONS}
                        metrics = {k: v for k, v in row.items() if k not in DIMENSIONS}
                        usage.append((family, bucket['start_time'], bucket['end_time'],
                                      dimensions, metrics))
        except OpenAISyncError as exc:
            errors['usage:' + family] = exc.code
    return resources, costs, usage, errors


def sync_organization(db, organization: OpenAIOrganization, encryption_key: str,
                      client=None, now=None):
    """Fetch first; persist exact source buckets atomically after essential data succeeds."""
    if not organization.enabled:
        raise OpenAISyncError('disabled')
    try:
        admin_key = decrypt(organization.encrypted_admin_key, encryption_key)
    except (RuntimeError, ValueError):
        raise OpenAISyncError('cannot_decrypt_key') from None
    if not admin_key:
        raise OpenAISyncError('missing_admin_key')
    now = now or datetime.now(timezone.utc)
    lookback = 90 if organization.last_synced_at is None else 30
    start = datetime.combine((now - timedelta(days=lookback)).date(), datetime.min.time(), timezone.utc)
    headers = {'Authorization': 'Bearer ' + admin_key,
               'OpenAI-Organization': organization.organization_id}
    own_client = client is None
    if own_client:
        client = httpx.Client(timeout=15, follow_redirects=False)
    try:
        resources, costs, usage, errors = _collect(client, headers, start, now)
    finally:
        if own_client: client.close()
    resource_kinds = ('project', 'project_key', 'project_rate_limit',
        'project_spend_alert', 'project_spend_limit', 'org_spend_limit',
        'org_spend_alert', 'admin_key')
    for kind in resource_kinds:
        if not any(k == kind or k.startswith(kind + ':') for k in errors):
            db.execute(delete(OpenAIResource).where(OpenAIResource.organization_id == organization.id,
                OpenAIResource.kind == kind))
    db.flush()
    for kind, external_id, payload in resources:
        row = db.scalar(select(OpenAIResource).where(OpenAIResource.organization_id == organization.id,
            OpenAIResource.kind == kind, OpenAIResource.external_id == external_id))
        if row is None:
            row = OpenAIResource(organization_id=organization.id, kind=kind,
                external_id=external_id)
            db.add(row)
        row.payload_json = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
        row.observed_at = now
    db.execute(delete(OpenAICost).where(OpenAICost.organization_id == organization.id,
        OpenAICost.bucket_start >= start))
    successful_families = [family for family in FAMILIES if 'usage:' + family not in errors]
    if successful_families:
        db.execute(delete(OpenAIUsage).where(OpenAIUsage.organization_id == organization.id,
            OpenAIUsage.bucket_start >= start, OpenAIUsage.family.in_(successful_families)))
    for begin, finish, project_id, key_id, line, currency, amount, qty, unit in costs:
        begin_dt = datetime.fromtimestamp(begin, timezone.utc)
        end_dt = datetime.fromtimestamp(finish, timezone.utc)
        fingerprint = _digest(project_id, key_id, line, currency)
        row = OpenAICost(organization_id=organization.id, bucket_start=begin_dt,
                         fingerprint=fingerprint)
        db.add(row)
        row.bucket_end, row.project_id, row.api_key_id = end_dt, project_id[:120], key_id[:120]
        row.line_item, row.currency, row.amount = line[:200], currency, amount
        row.quantity, row.quantity_unit = qty, unit[:60]
    for family, begin, finish, dimensions, metrics in usage:
        if family not in successful_families:
            continue
        begin_dt = datetime.fromtimestamp(begin, timezone.utc)
        fingerprint = _digest(dimensions)
        row = OpenAIUsage(organization_id=organization.id, family=family,
            bucket_start=begin_dt, fingerprint=fingerprint)
        db.add(row)
        row.bucket_end = datetime.fromtimestamp(finish, timezone.utc)
        row.dimensions_json = json.dumps(dimensions, ensure_ascii=False)
        row.metrics_json = json.dumps(metrics, ensure_ascii=False)
    organization.status = 'partial' if errors else 'ready'
    organization.last_error = ', '.join(f'{k}: {v}' for k, v in errors.items())[:200]
    organization.last_synced_at = now
    db.commit()
    return {'cost_rows': len(costs), 'usage_rows': len(usage),
            'resources': len(resources), 'errors': errors}
