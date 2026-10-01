"""Metadata-only Neon API catalog; data rows always use scoped SQL credentials."""
import re

import httpx
from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from .models import BotDatabaseConnection
from .security import require_admin
from .web import page

router = APIRouter()
API_ROOT = 'https://console.neon.tech/api/v2'
NEON_ID = re.compile(r'^[a-z0-9-]{1,60}$')


def neon_get(api_key, path, params=None):
    """Never return tokens, authorization headers, or raw provider errors to UI."""
    if not api_key or not path.startswith('/projects'):
        raise ValueError('Neon API is not configured')
    with httpx.Client(base_url=API_ROOT, timeout=12, follow_redirects=False,
                      headers={'Authorization': f'Bearer {api_key}', 'Accept': 'application/json'}) as client:
        response = client.get(path, params=params or {})
        response.raise_for_status()
        return response.json()


def project_catalog(api_key, org_id=''):
    params = {'limit': 100}
    if org_id:
        if not NEON_ID.fullmatch(org_id):
            raise ValueError('Invalid organization ID')
        params['org_id'] = org_id
    projects, cursor = [], None
    for _ in range(10):
        if cursor:
            params['cursor'] = cursor
        result = neon_get(api_key, '/projects', params)
        projects.extend(result.get('projects') or [])
        cursor = (result.get('pagination') or {}).get('next') or result.get('next')
        if not cursor:
            break
    return projects[:1000]


@router.get('/neon')
def neon_workspace(request: Request, project_id: str = ''):
    with request.app.state.session_factory() as db:
        require_admin(request, db)
        config = request.app.state.settings
        projects, branches, databases, issue = [], [], {}, ''
        if config.neon_api_key:
            try:
                projects = project_catalog(config.neon_api_key, config.neon_org_id)
                if project_id:
                    if not NEON_ID.fullmatch(project_id) or project_id not in {p.get('id') for p in projects}:
                        raise HTTPException(404)
                    branches = neon_get(config.neon_api_key, f'/projects/{project_id}/branches').get('branches') or []
                    for branch in branches[:20]:
                        branch_id = branch.get('id', '')
                        if NEON_ID.fullmatch(branch_id):
                            databases[branch_id] = neon_get(config.neon_api_key,
                                f'/projects/{project_id}/branches/{branch_id}/databases').get('databases') or []
            except HTTPException:
                raise
            except (httpx.HTTPError, ValueError, KeyError):
                issue = 'Neon API недоступен: проверьте ключ, организацию и сетевой доступ.'
        connections = db.scalars(select(BotDatabaseConnection).where(
            BotDatabaseConnection.dialect == 'neon').order_by(BotDatabaseConnection.bot_id,
            BotDatabaseConnection.id)).all()
        return page(request, 'neon.html', db, projects=projects, selected=project_id,
                    branches=branches, databases=databases, connections=connections,
                    configured=bool(config.neon_api_key), issue=issue)
