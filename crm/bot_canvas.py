"""Visual scenario editor integrated with OmniFactory CRM sessions and bots."""
import json

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from .bot_graph import Graph, Node, Edge, simulate, template, validate
from .models import Bot, BotGraphRelease, BotScenario, BotScenarioGraph
from .security import audit, csrf, require_admin
from .web import page

router = APIRouter()


class SaveGraph(BaseModel):
    version: int = Field(ge=0)
    graph: Graph


class PreviewGraph(BaseModel):
    graph: Graph
    inputs: dict[str, str] = Field(default_factory=dict)


def locate(db, bot_id: int, scenario_id: int):
    bot = db.get(Bot, bot_id)
    scenario = db.get(BotScenario, scenario_id)
    if not bot or not scenario or scenario.bot_id != bot_id:
        raise HTTPException(404, 'Бот или сценарий не найден')
    return bot, scenario


def initial_graph(scenario):
    if not scenario.steps:
        graph = template()
        graph['nodes'][0]['label'] = f'Запуск: {scenario.trigger_value}'
        return graph
    nodes = [Node(id='start', type='start', label=f'Запуск: {scenario.trigger_value}', x=80, y=200)]
    edges = []
    previous = 'start'
    for i, step in enumerate(scenario.steps):
        options = json.loads(step.options_json or '{}')
        kind = step.kind if step.kind in {'message', 'question', 'media', 'wait', 'handoff'} else 'message'
        node_id = f'step_{step.id}'
        nodes.append(Node(id=node_id, type=kind, label=f'Шаг {i + 1}', text=step.text or '',
                          variable=options.get('field', ''), media_url=options.get('url', ''),
                          queue=options.get('queue', ''), seconds=options.get('seconds', 5),
                          x=380 + (i % 5) * 300, y=200 + (i // 5) * 180))
        edges.append(Edge(id=f'edge_{i}', source=previous, target=node_id))
        previous = node_id
        if kind == 'handoff':
            break
    if nodes[-1].type != 'handoff':
        nodes.append(Node(id='finish', type='end', label='Конец',
                          x=380 + (len(scenario.steps) % 5) * 300,
                          y=200 + (len(scenario.steps) // 5) * 180))
        edges.append(Edge(id='edge_end', source=previous, target='finish'))
    return Graph(nodes=nodes, edges=edges).model_dump()


def current(db, scenario):
    row = db.scalar(select(BotScenarioGraph).where(BotScenarioGraph.scenario_id == scenario.id))
    return row, json.loads(row.graph_json) if row else initial_graph(scenario)


@router.get('/bots/{bot_id}/scenarios/{scenario_id}/canvas')
def canvas_page(request: Request, bot_id: int, scenario_id: int):
    with request.app.state.session_factory() as db:
        require_admin(request, db)
        bot, scenario = locate(db, bot_id, scenario_id)
        return page(request, 'bot_canvas.html', db, bot=bot, scenario=scenario)


@router.get('/api/bots/{bot_id}/scenarios/{scenario_id}/canvas')
def read_canvas(request: Request, bot_id: int, scenario_id: int):
    with request.app.state.session_factory() as db:
        require_admin(request, db)
        _bot, scenario = locate(db, bot_id, scenario_id)
        row, graph = current(db, scenario)
        return {'version': row.version if row else 0, 'graph': graph,
                'scenario': {'id': scenario.id, 'name': scenario.name, 'key': scenario.key,
                             'trigger_kind': scenario.trigger_kind, 'trigger_value': scenario.trigger_value}}


@router.put('/api/bots/{bot_id}/scenarios/{scenario_id}/canvas')
def save_canvas(request: Request, bot_id: int, scenario_id: int, payload: SaveGraph,
                x_csrf_token: str = Header(default='')):
    with request.app.state.session_factory() as db:
        admin = require_admin(request, db, owner=True)
        csrf(request, x_csrf_token)
        _bot, scenario = locate(db, bot_id, scenario_id)
        errors = validate(payload.graph)
        if errors:
            raise HTTPException(422, errors)
        graph_json = json.dumps(payload.graph.model_dump(), ensure_ascii=False, separators=(',', ':'))
        if len(graph_json.encode('utf-8')) > 256_000:
            raise HTTPException(413, 'Схема слишком большая')
        if payload.version == 0:
            if db.scalar(select(BotScenarioGraph.id).where(BotScenarioGraph.scenario_id == scenario_id)):
                raise HTTPException(409, 'Схема уже создана; обновите страницу')
            db.add(BotScenarioGraph(scenario_id=scenario_id, graph_json=graph_json,
                                    version=1, updated_by=admin.id))
            new_version = 1
        else:
            result = db.execute(update(BotScenarioGraph).where(
                BotScenarioGraph.scenario_id == scenario_id,
                BotScenarioGraph.version == payload.version).values(
                    graph_json=graph_json, version=payload.version + 1, updated_by=admin.id))
            if result.rowcount != 1:
                raise HTTPException(409, 'Схема изменилась в другой вкладке; обновите страницу')
            new_version = payload.version + 1
        scenario.status = 'draft'
        scenario.version += 1
        audit(db, 'bot.canvas.save', f'{bot_id}:{scenario_id}', admin.id, f'version={new_version}')
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, 'Схема изменилась; обновите страницу')
        return {'version': new_version, 'status': 'draft'}


@router.post('/api/bots/{bot_id}/scenarios/{scenario_id}/canvas/check')
def check_canvas(request: Request, bot_id: int, scenario_id: int, graph: Graph):
    with request.app.state.session_factory() as db:
        require_admin(request, db)
        locate(db, bot_id, scenario_id)
        errors = validate(graph, complete=True)
        return {'valid': not errors, 'errors': errors}


@router.post('/api/bots/{bot_id}/scenarios/{scenario_id}/canvas/preview')
def preview_canvas(request: Request, bot_id: int, scenario_id: int, payload: PreviewGraph):
    with request.app.state.session_factory() as db:
        require_admin(request, db)
        locate(db, bot_id, scenario_id)
        return simulate(payload.graph, payload.inputs)


@router.get('/api/bots/{bot_id}/scenarios/{scenario_id}/canvas/export')
def export_canvas(request: Request, bot_id: int, scenario_id: int):
    with request.app.state.session_factory() as db:
        require_admin(request, db)
        bot, scenario = locate(db, bot_id, scenario_id)
        row, graph = current(db, scenario)
        return {'schema_version': 1, 'bot_key': bot.key, 'scenario_key': scenario.key,
                'trigger': {'kind': scenario.trigger_kind, 'value': scenario.trigger_value},
                'version': row.version if row else 0, 'graph': graph}


@router.post('/api/bots/{bot_id}/scenarios/{scenario_id}/canvas/publish')
def publish_canvas(request: Request, bot_id: int, scenario_id: int,
                   x_csrf_token: str = Header(default='')):
    with request.app.state.session_factory() as db:
        admin = require_admin(request, db, owner=True)
        csrf(request, x_csrf_token)
        _bot, scenario = locate(db, bot_id, scenario_id)
        row, graph_data = current(db, scenario)
        if not row:
            raise HTTPException(422, 'Сохраните схему перед публикацией')
        graph = Graph.model_validate(graph_data)
        errors = validate(graph, complete=True)
        if any(node.type == 'wait' for node in graph.nodes):
            errors.append('Пауза пока недоступна в работающем консультанте')
        if errors:
            raise HTTPException(422, errors)
        version = (db.scalar(select(func.max(BotGraphRelease.version)).where(
            BotGraphRelease.scenario_id == scenario_id)) or 0) + 1
        db.add(BotGraphRelease(bot_id=bot_id, scenario_id=scenario_id, version=version,
            graph_json=json.dumps(graph.model_dump(), ensure_ascii=False), created_by=admin.id))
        scenario.status = 'published'
        scenario.version += 1
        audit(db, 'bot.graph.publish', f'{bot_id}:{scenario_id}', admin.id, f'version={version}')
        db.commit()
        return {'published': True, 'version': version}
