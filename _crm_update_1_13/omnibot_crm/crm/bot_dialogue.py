"""Execute immutable visual scenarios for one authenticated Telegram bot."""
import json
import re

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .bot_graph import Graph
from .ingest import authenticated_bot
from .models import (Bot, BotDialogueEvent, BotDialogueSession, BotGraphRelease,
                     BotScenario, Customer, Identity, Ticket)

router = APIRouter(prefix='/api/v1')
PLACEHOLDER = re.compile(r'\{\{([a-zA-Z_][a-zA-Z0-9_]{0,39})\}\}')


class Incoming(BaseModel):
    model_config = ConfigDict(extra='forbid')
    event_id: str = Field(min_length=1, max_length=120)
    scenario_key: str = Field(min_length=1, max_length=80)
    external_user_id: str = Field(min_length=1, max_length=100)
    text: str = Field(default='', max_length=500)


def advance(graph: Graph, current: str, values: dict[str, str], text: str, waiting: bool):
    """Return actions, new state and values; the caller commits all of them atomically."""
    nodes = {node.id: node for node in graph.nodes}
    links = {(edge.source, edge.handle): edge.target for edge in graph.edges}
    actions = []

    def expand(value):
        return PLACEHOLDER.sub(lambda match: values.get(match.group(1), ''), value)[:4000]

    if waiting:
        node = nodes[current]
        if node.type == 'question':
            if not text.strip():
                return [{'type': 'text', 'text': expand(node.text)}], current, 'waiting', values
            values[node.variable] = text.strip()[:500]
            current = links[(current, 'next')]
        elif node.type == 'buttons':
            if text not in node.buttons:
                return [{'type': 'text', 'text': expand(node.label or 'Выберите вариант'),
                         'buttons': node.buttons}], current, 'waiting', values
            current = links[(current, 'button_' + str(node.buttons.index(text)))]
        else:
            raise HTTPException(409, 'Invalid dialogue state')
    for _ in range(40):
        node = nodes[current]
        if node.type == 'end':
            return actions, current, 'finished', values
        if node.type == 'handoff':
            actions.append({'type': 'handoff', 'queue': node.queue,
                            'text': expand(node.text or 'Передаю запрос специалисту.')})
            return actions, current, 'handoff', values
        if node.type == 'question':
            actions.append({'type': 'text', 'text': expand(node.text)})
            return actions, current, 'waiting', values
        if node.type == 'buttons':
            actions.append({'type': 'text', 'text': expand(node.label or 'Выберите вариант'),
                            'buttons': node.buttons})
            return actions, current, 'waiting', values
        if node.type == 'message':
            actions.append({'type': 'text', 'text': expand(node.text)})
        elif node.type == 'media':
            actions.append({'type': 'media', 'url': node.media_url})
        elif node.type == 'wait':
            raise HTTPException(409, 'Wait is not enabled in a live scenario')
        handle = ('yes' if values.get(node.variable, '') == node.equals else 'no') if node.type == 'condition' else 'next'
        current = links[(current, handle)]
    raise HTTPException(422, 'Scenario exceeded 40 transitions')


@router.post('/bot-dialogue')
async def bot_dialogue(request: Request):
    raw = await request.body()
    if len(raw) > 4096:
        raise HTTPException(413, 'Message too large')
    bot_id = authenticated_bot(request, raw)
    try:
        data = Incoming.model_validate_json(raw)
    except ValidationError:
        raise HTTPException(422, 'Invalid dialogue input') from None
    with request.app.state.session_factory() as db:
        existing = db.scalar(select(BotDialogueEvent).where(
            BotDialogueEvent.bot_id == bot_id, BotDialogueEvent.event_id == data.event_id))
        if existing:
            return json.loads(existing.result_json)
        scenario = db.scalar(select(BotScenario).where(BotScenario.bot_id == bot_id,
            BotScenario.key == data.scenario_key, BotScenario.status == 'published'))
        if not scenario:
            raise HTTPException(404, 'Published scenario not found')
        release = db.scalar(select(BotGraphRelease).where(BotGraphRelease.scenario_id == scenario.id)
            .order_by(BotGraphRelease.version.desc()).limit(1))
        if not release:
            raise HTTPException(409, 'Publish the visual scenario first')
        session = db.scalar(select(BotDialogueSession).where(
            BotDialogueSession.scenario_id == scenario.id,
            BotDialogueSession.external_user_id == data.external_user_id).with_for_update())
        start = not session or data.text.strip() == '/start'
        if session and session.status in ('finished', 'handoff') and not start:
            result = {'actions': [], 'status': session.status, 'restart_command': '/start',
                      'release_version': db.get(BotGraphRelease, session.release_id).version}
            db.add(BotDialogueEvent(bot_id=bot_id, session_id=session.id,
                                    event_id=data.event_id, result_json=json.dumps(result)))
            db.commit()
            return result
        if not session:
            graph = Graph.model_validate_json(release.graph_json)
            start_node = next(node.id for node in graph.nodes if node.type == 'start')
            session = BotDialogueSession(bot_id=bot_id, scenario_id=scenario.id,
                external_user_id=data.external_user_id, release_id=release.id,
                current_node_id=start_node, values_json='{}', status='active')
            db.add(session); db.flush()
        elif start:
            session.release_id = release.id
            graph = Graph.model_validate_json(release.graph_json)
            session.current_node_id = next(node.id for node in graph.nodes if node.type == 'start')
            session.values_json = '{}'
            session.status = 'active'
        else:
            release = db.get(BotGraphRelease, session.release_id)
        graph = Graph.model_validate_json(release.graph_json)
        values = json.loads(session.values_json)
        actions, current, status, values = advance(graph, session.current_node_id,
            values, '' if start else data.text, session.status == 'waiting' and not start)
        session.current_node_id, session.status = current, status
        session.values_json = json.dumps(values, ensure_ascii=False)
        identity = db.scalar(select(Identity).where(Identity.bot_id == bot_id,
            Identity.external_user_id == data.external_user_id))
        if not identity:
            customer = Customer(source=db.get(Bot, bot_id).key)
            db.add(customer); db.flush()
            identity = Identity(bot_id=bot_id, customer_id=customer.id,
                external_user_id=data.external_user_id)
            db.add(identity)
        else:
            customer = db.get(Customer, identity.customer_id)
        for name in ('name', 'email', 'phone'):
            if name in values:
                setattr(customer, name, values[name][:255 if name == 'email' else 40 if name == 'phone' else 200])
        if status == 'handoff':
            reference = 'consult-' + str(session.id)
            if not db.scalar(select(Ticket.id).where(Ticket.bot_id == bot_id,
                                                  Ticket.external_id == reference)):
                db.add(Ticket(bot_id=bot_id, customer_id=customer.id, external_id=reference,
                    title='Консультация: ' + scenario.name[:140], status='open',
                    description='Передача: ' + actions[-1]['queue'][:100]))
        result = {'actions': actions, 'status': status, 'release_version': release.version,
                  'scenario_key': scenario.key}
        db.add(BotDialogueEvent(bot_id=bot_id, session_id=session.id,
                                event_id=data.event_id, result_json=json.dumps(result, ensure_ascii=False)))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, 'Concurrent or duplicate update; retry with the same event_id') from None
        return result
