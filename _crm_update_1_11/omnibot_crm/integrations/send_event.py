"""Small reusable client for aiogram bot applications."""
import hashlib
import hmac
import json
import time
import uuid
import httpx


async def send_event(crm_url: str, bot_key: str, secret: str, event_type: str, data: dict, event_id: str | None = None):
    # Stable event_id is required when retrying the same business event.
    payload={'event_id':event_id or str(uuid.uuid4()),'type':event_type,'data':data}
    body=json.dumps(payload,ensure_ascii=False,separators=(',',':')).encode()
    stamp=str(int(time.time()))
    signature=hmac.new(secret.encode(),stamp.encode()+b'.'+body,hashlib.sha256).hexdigest()
    async with httpx.AsyncClient(timeout=10) as client:
        response=await client.post(crm_url.rstrip('/')+'/api/v1/events',content=body,headers={
            'Content-Type':'application/json','X-Bot-Key':bot_key,'X-Timestamp':stamp,'X-Signature':signature})
        response.raise_for_status()
        return response.json()


async def fetch_bot_config(crm_url: str, bot_key: str, secret: str):
    """Fetch bot-scoped parameters; keep the response out of logs."""
    stamp = str(int(time.time()))
    signature = hmac.new(secret.encode(), (stamp + '.GET./api/v1/bot-config').encode(), hashlib.sha256).hexdigest()
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(crm_url.rstrip('/') + '/api/v1/bot-config', headers={
            'X-Bot-Key': bot_key, 'X-Timestamp': stamp, 'X-Signature': signature})
        response.raise_for_status()
        return response.json()['settings']
