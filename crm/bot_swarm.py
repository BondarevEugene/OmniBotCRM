"""Authenticated Bot Swarm registry and schema-driven Mini CRM contracts.

This registry never provisions infrastructure or accepts bot tokens.
"""
from __future__ import annotations

import json
import re

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select

from .models import Bot, BotFleetInstance, BotModule, BotSetting, BotSwarm, BotSwarmMember
from .security import audit, csrf, require_admin

router = APIRouter(prefix='/api/fleet', tags=['Bot Swarm'])
KEY = re.compile(r'^[a-z][a-z0-9_-]{2,79}$')


class SwarmCreate(BaseModel):
    key: str = Field(pattern=r'^[a-z][a-z0-9_-]{2,79}$')
    name: str = Field(min_length=2, max_length=120)
    description: str = Field(default='', max_length=1000)
    parent_id: int | None = Field(default=None, gt=0)


class MemberAssign(BaseModel):
    bot_id: int = Field(gt=0)


class InstanceUpsert(BaseModel):
    telegram_bot_id: int | None = Field(default=None, gt=0)
    deployment_provider: str = Field(default='external', pattern=r'^(external|railway)$')
    deployment_ref: str = Field(default='', max_length=120)
    runtime_version: str = Field(default='', max_length=80)


class ManifestSetting(BaseModel):
    key: str = Field(pattern=r'^[a-z][a-z0-9_]{1,79}$')
    label: str = Field(min_length=1, max_length=120)
    kind: str = Field(pattern=r'^(text|number|boolean|select|secret)$')
    group: str = Field(default='general', max_length=80)
    editable: bool = True
    description: str = Field(default='', max_length=500)


class MiniCRMManifest(BaseModel):
    schema_version: int = Field(default=1, ge=1, le=1)
    bot_key: str
    settings: list[ManifestSetting] = Field(default_factory=list, max_length=100)
    modules: list[str] = Field(default_factory=list, max_length=50)

    @model_validator(mode='after')
    def unique_settings(self):
        keys = [item.key for item in self.settings]
        if len(keys) != len(set(keys)):
            raise ValueError('Duplicate setting keys')
        return self


def authenticated(request: Request, db, token: str, *, mutate: bool = False):
    admin = require_admin(request, db, owner=mutate)
    if mutate:
        csrf(request, token)
    return admin


@router.get('/overview')
def overview(request: Request):
    with request.app.state.session_factory() as db:
        authenticated(request, db, '')
        swarms = db.scalars(select(BotSwarm).order_by(BotSwarm.id)).all()
        instances = db.scalars(select(BotFleetInstance).order_by(BotFleetInstance.id)).all()
        members = db.scalars(select(BotSwarmMember)).all()
        return {
            'swarms': [
                {'id': s.id, 'key': s.key, 'name': s.name, 'parent_id': s.parent_id,
                 'bot_ids': [m.bot_id for m in members if m.swarm_id == s.id]}
                for s in swarms
            ],
            'instances': [
                {'id': i.id, 'bot_id': i.bot_id, 'telegram_bot_id': i.telegram_bot_id,
                 'status': i.status, 'provider': i.deployment_provider,
                 'runtime_version': i.runtime_version,
                 'last_heartbeat_at': i.last_heartbeat_at}
                for i in instances
            ],
            'live_control_enabled': False,
        }


@router.post('/swarms', status_code=201)
def create_swarm(request: Request, body: SwarmCreate, x_omni_csrf: str = Header(default='')):
    with request.app.state.session_factory() as db:
        admin = authenticated(request, db, x_omni_csrf, mutate=True)
        if db.scalar(select(BotSwarm).where(BotSwarm.key == body.key)):
            raise HTTPException(409, 'Swarm key already exists')
        if body.parent_id and db.get(BotSwarm, body.parent_id) is None:
            raise HTTPException(404, 'Parent swarm not found')
        swarm = BotSwarm(**body.model_dump())
        db.add(swarm)
        db.flush()
        audit(db, 'swarm.create', f'swarm:{swarm.id}', admin.id)
        db.commit()
        return {'id': swarm.id, 'key': swarm.key, 'name': swarm.name}


@router.post('/swarms/{swarm_id}/members', status_code=201)
def assign_member(
    swarm_id: int, request: Request, body: MemberAssign,
    x_omni_csrf: str = Header(default=''),
):
    with request.app.state.session_factory() as db:
        admin = authenticated(request, db, x_omni_csrf, mutate=True)
        if db.get(BotSwarm, swarm_id) is None or db.get(Bot, body.bot_id) is None:
            raise HTTPException(404, 'Swarm or bot not found')
        existing = db.get(BotSwarmMember, body.bot_id)
        if existing is not None:
            if existing.swarm_id != swarm_id:
                raise HTTPException(409, 'Bot belongs to another swarm')
            return {'swarm_id': swarm_id, 'bot_id': body.bot_id, 'already_exists': True}
        db.add(BotSwarmMember(bot_id=body.bot_id, swarm_id=swarm_id))
        audit(db, 'swarm.member.add', f'bot:{body.bot_id}', admin.id)
        db.commit()
        return {'swarm_id': swarm_id, 'bot_id': body.bot_id, 'already_exists': False}


@router.put('/bots/{bot_id}/instance')
def register_instance(
    bot_id: int, request: Request, body: InstanceUpsert,
    x_omni_csrf: str = Header(default=''),
):
    with request.app.state.session_factory() as db:
        admin = authenticated(request, db, x_omni_csrf, mutate=True)
        if db.get(Bot, bot_id) is None:
            raise HTTPException(404, 'Bot not found')
        if body.telegram_bot_id is not None:
            other = db.scalar(select(BotFleetInstance).where(
                BotFleetInstance.telegram_bot_id == body.telegram_bot_id,
                BotFleetInstance.bot_id != bot_id,
            ))
            if other is not None:
                raise HTTPException(409, 'Telegram Bot ID is already registered')
        instance = db.scalar(select(BotFleetInstance).where(BotFleetInstance.bot_id == bot_id))
        if instance is None:
            instance = BotFleetInstance(bot_id=bot_id)
            db.add(instance)
        for key, value in body.model_dump().items():
            setattr(instance, key, value)
        audit(db, 'fleet.instance.register', f'bot:{bot_id}', admin.id)
        db.commit()
        return {'bot_id': bot_id, 'telegram_bot_id': instance.telegram_bot_id,
                'status': instance.status, 'live_control_enabled': False}


@router.get('/bots/{bot_id}/mini-crm-manifest')
def mini_crm_manifest(bot_id: int, request: Request):
    with request.app.state.session_factory() as db:
        authenticated(request, db, '')
        bot = db.get(Bot, bot_id)
        if bot is None:
            raise HTTPException(404, 'Bot not found')
        settings = db.scalars(select(BotSetting).where(BotSetting.bot_id == bot_id)
                              .order_by(BotSetting.display_order, BotSetting.id)).all()
        modules = db.scalars(select(BotModule).where(BotModule.bot_id == bot_id)
                             .order_by(BotModule.id)).all()
        manifest = MiniCRMManifest(
            bot_key=bot.key,
            settings=[
                ManifestSetting(key=item.key, label=item.label,
                                kind=item.kind if item.kind in {
                                    'text', 'number', 'boolean', 'select', 'secret'
                                } else 'text',
                                group=item.group_name, description=item.description)
                for item in settings if KEY.fullmatch(item.key)
            ],
            modules=[item.key for item in modules],
        )
        return manifest.model_dump()
