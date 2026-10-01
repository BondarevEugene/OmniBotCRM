"""Opt-in bridge for an existing Telegram bot's message handler.

The bot owns Telegram updates and sends resulting text/buttons/media itself.
Keep `event_id` stable across retries (for example, Telegram update_id).
"""
import hashlib
import hmac
import json
import time

import httpx


class BotDialogueGateway:
    def __init__(self, crm_url: str, bot_key: str, ingest_secret: str, timeout: float = 8):
        if not crm_url.startswith(('https://', 'http://127.0.0.1:', 'http://localhost:')):
            raise ValueError('Use HTTPS outside localhost')
        if len(ingest_secret) < 24:
            raise ValueError('Use a bot-specific secret of at least 24 characters')
        self.url = crm_url.rstrip('/') + '/api/v1/bot-dialogue'
        self.bot_key, self.secret, self.timeout = bot_key, ingest_secret, timeout

    def message(self, *, event_id: str, scenario_key: str, external_user_id: str,
                text: str = '', client: httpx.Client | None = None) -> dict:
        raw = json.dumps(dict(event_id=str(event_id), scenario_key=scenario_key,
                              external_user_id=str(external_user_id), text=text),
                         ensure_ascii=False, separators=(',', ':')).encode()
        stamp = str(int(time.time()))
        signature = hmac.new(self.secret.encode(), stamp.encode() + b'.' + raw,
                             hashlib.sha256).hexdigest()
        headers = {'X-Bot-Key': self.bot_key, 'X-Timestamp': stamp,
                   'X-Signature': signature, 'Content-Type': 'application/json'}
        if client is None:
            with httpx.Client(timeout=self.timeout, follow_redirects=False) as transport:
                return self.message(event_id=event_id, scenario_key=scenario_key,
                    external_user_id=external_user_id, text=text, client=transport)
        response = client.post(self.url, content=raw, headers=headers,
                               timeout=self.timeout, follow_redirects=False)
        response.raise_for_status()
        return response.json()
