"""Portable bot scenario format, validation and deterministic local preview."""
import re
from collections import defaultdict, deque
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


ALLOWED = {"start", "message", "question", "buttons", "condition", "media", "wait", "handoff", "end"}
VARIABLE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]{0,39}$")


class Node(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,50}$")
    type: str
    label: str = Field(default="", max_length=120)
    text: str = Field(default="", max_length=4000)
    variable: str = Field(default="", max_length=40)
    equals: str = Field(default="", max_length=500)
    buttons: list[str] = Field(default_factory=list, max_length=8)
    media_url: str = Field(default="", max_length=1000)
    queue: str = Field(default="", max_length=120)
    seconds: int = Field(default=5, ge=1, le=86400)
    x: int = Field(default=100, ge=0, le=5000)
    y: int = Field(default=100, ge=0, le=5000)


class Edge(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,50}$")
    source: str
    target: str
    handle: str = "next"


class Graph(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nodes: list[Node] = Field(default_factory=list, max_length=150)
    edges: list[Edge] = Field(default_factory=list, max_length=300)


def template() -> dict:
    return Graph(nodes=[
        Node(id="start", type="start", label="Запуск /start", x=100, y=220),
        Node(id="welcome", type="message", label="Приветствие", text="Привет! Чем могу помочь?", x=400, y=220),
        Node(id="finish", type="end", label="Завершение", x=700, y=220),
    ], edges=[Edge(id="e1", source="start", target="welcome"),
              Edge(id="e2", source="welcome", target="finish")]).model_dump()


def validate(graph: Graph, *, complete: bool = False) -> list[str]:
    errors: list[str] = []
    nodes = {n.id: n for n in graph.nodes}
    if len(nodes) != len(graph.nodes):
        errors.append("ID блоков должны быть уникальными")
    edge_ids = {e.id for e in graph.edges}
    if len(edge_ids) != len(graph.edges):
        errors.append("ID связей должны быть уникальными")
    starts = [n for n in graph.nodes if n.type == "start"]
    if complete and len(starts) != 1:
        errors.append("Нужен ровно один блок запуска")
    outgoing: dict[tuple[str, str], str] = {}
    adjacency: dict[str, list[str]] = defaultdict(list)
    for n in graph.nodes:
        if n.type not in ALLOWED:
            errors.append(f"{n.id}: неизвестный тип блока")
        if n.type in {"message", "question"} and complete and not n.text.strip():
            errors.append(f"{n.id}: заполните текст сообщения")
        if n.type in {"question", "condition"} and complete and not VARIABLE.fullmatch(n.variable):
            errors.append(f"{n.id}: задайте имя переменной латиницей")
        if n.type == "buttons" and complete and (not n.buttons or any(not b.strip() for b in n.buttons)):
            errors.append(f"{n.id}: заполните подписи кнопок")
        if n.type == 'media' and complete and not n.media_url.startswith('https://'):
            errors.append(f"{n.id}: укажите HTTPS ссылку на медиа")
        if n.type == 'handoff' and complete and not n.queue.strip():
            errors.append(f"{n.id}: укажите команду операторов")
    for e in graph.edges:
        src = nodes.get(e.source)
        if src is None or e.target not in nodes:
            errors.append(f"{e.id}: связь ведёт к отсутствующему блоку")
            continue
        if e.source == e.target:
            errors.append(f"{e.id}: блок не может вести на себя")
        handles = (['yes', 'no'] if src.type == 'condition' else
                   [f'button_{i}' for i in range(len(src.buttons))] if src.type == 'buttons' else
                   [] if src.type in ('end', 'handoff') else ['next'])
        if e.handle not in handles:
            errors.append(f"{e.id}: выход «{e.handle}» недоступен для блока {src.id}")
        if (e.source, e.handle) in outgoing:
            errors.append(f"{src.id}: у выхода «{e.handle}» две связи")
        outgoing[(e.source, e.handle)] = e.target
        adjacency[e.source].append(e.target)
    if complete and len(starts) == 1:
        reachable = {starts[0].id}
        queue = deque(reachable)
        while queue:
            for target in adjacency[queue.popleft()]:
                if target not in reachable:
                    reachable.add(target)
                    queue.append(target)
        for n in graph.nodes:
            if n.id not in reachable:
                errors.append(f"{n.id}: блок недостижим из запуска")
            expected = (['yes', 'no'] if n.type == 'condition' else
                        [f'button_{i}' for i in range(len(n.buttons))] if n.type == 'buttons' else
                        [] if n.type in ('end', 'handoff') else ['next'])
            if n.id in reachable and n.type not in ('end', 'handoff'):
                for handle in expected:
                    if (n.id, handle) not in outgoing:
                        errors.append(f"{n.id}: выход «{handle}» не подключён")
        if not any(n.type in ('end', 'handoff') and n.id in reachable for n in graph.nodes):
            errors.append("Добавьте достижимый блок завершения")
    return errors


def simulate(graph: Graph, inputs: dict[str, str]) -> dict[str, Any]:
    errors = validate(graph, complete=True)
    if errors:
        return {"errors": errors, "messages": [], "path": []}
    nodes = {n.id: n for n in graph.nodes}
    links = {(e.source, e.handle): e.target for e in graph.edges}
    current = next(n.id for n in graph.nodes if n.type == 'start')
    values: dict[str, str] = {}
    messages: list[dict[str, str]] = []
    path: list[str] = []
    for _ in range(40):
        node = nodes[current]
        path.append(current)
        if node.type == 'end':
            return {"messages": messages, "path": path, "variables": values, "finished": True}
        if node.type == 'handoff':
            messages.append({"from": "system", "text": f"Передача оператору: {node.queue}"})
            return {"messages": messages, "path": path, "variables": values, "finished": True}
        handle = 'next'
        if node.type in {'message', 'question'}:
            messages.append({"from": "bot", "text": node.text})
        if node.type == 'question':
            answer = inputs.get(node.id, '')
            if not answer:
                return {"messages": messages, "path": path, "variables": values,
                        "awaiting": {"node": node.id, "kind": "text", "label": node.text}}
            values[node.variable] = answer[:500]
            messages.append({"from": "user", "text": answer[:500]})
        elif node.type == 'buttons':
            choice = inputs.get(node.id, '')
            if choice not in node.buttons:
                return {"messages": messages, "path": path, "variables": values,
                        "awaiting": {"node": node.id, "kind": "choice", "options": node.buttons}}
            handle = f'button_{node.buttons.index(choice)}'
            messages.append({"from": "user", "text": choice})
        elif node.type == 'condition':
            handle = 'yes' if values.get(node.variable, '') == node.equals else 'no'
        elif node.type == 'media':
            messages.append({"from": "bot", "text": f"Медиа: {node.media_url}"})
        elif node.type == 'wait':
            messages.append({"from": "system", "text": f"Пауза {node.seconds} сек. (в превью без ожидания)"})
        current = links[(node.id, handle)]
    return {"errors": ["Сценарий превысил 40 шагов: проверьте цикл"], "messages": messages, "path": path}
