"""Small opt-in configuration gateway client for Python Telegram projects.

Use one instance per bot. Keep the last known good configuration in memory;
never log tokens or persist them unencrypted. Existing handlers must explicitly
read and validate settings before changes become effective.
"""
import hashlib
import hmac
import json
import time

import httpx


class BotConfigurationGateway:
    def __init__(self, crm_url: str, bot_key: str, ingest_secret: str, *, timeout: float = 8):
        if not crm_url.startswith(('https://', 'http://127.0.0.1:', 'http://localhost:')):
            raise ValueError('Use HTTPS outside localhost')
        if len(ingest_secret) < 24:
            raise ValueError('A unique bot secret of at least 24 characters is required')
        self.url = crm_url.rstrip('/') + '/api/v1/bot-config'
        self.bot_key = bot_key
        self.secret = ingest_secret
        self.timeout = timeout
        self.current: dict | None = None
        self.etag: str | None = None

    def refresh(self, client: httpx.Client | None = None) -> dict | None:
        stamp = str(int(time.time()))
        signature = hmac.new(self.secret.encode(),
                             (stamp + '.GET./api/v1/bot-config').encode(),
                             hashlib.sha256).hexdigest()
        headers = {'X-Bot-Key': self.bot_key, 'X-Timestamp': stamp, 'X-Signature': signature}
        if self.etag:
            headers['If-None-Match'] = self.etag
        if client is None:
            with httpx.Client(timeout=self.timeout, follow_redirects=False) as new_client:
                return self.refresh(new_client)
        response = client.get(self.url, headers=headers, timeout=self.timeout,
                              follow_redirects=False)
        if response.status_code == 304:
            return self.current
        response.raise_for_status()
        result = response.json()
        if (result.get('schema') != 1 or not isinstance(result.get('settings'), dict)
                or not isinstance(result.get('modules'), list)
                or not isinstance(result.get('scenarios'), list)):
            raise ValueError('Unexpected CRM configuration schema')
        self.current = result
        self.etag = response.headers.get('ETag')
        return self.current

    def acknowledge(self, status: str = 'applied', reason: str = '', client: httpx.Client | None = None) -> dict:
        """Call only after the bot has validated and switched its active settings."""
        if not self.current or not self.current.get('revision'):
            raise ValueError('Fetch a published configuration first')
        if status not in ('applied', 'failed') or reason not in (
                '', 'validation_failed', 'restart_required', 'dependency_unavailable', 'other'):
            raise ValueError('Unknown application result')
        raw = json.dumps({'revision': self.current['revision'], 'status': status,
                          'reason': reason}, separators=(',', ':')).encode()
        stamp = str(int(time.time()))
        signature = hmac.new(self.secret.encode(), stamp.encode() + b'.' + raw,
                             hashlib.sha256).hexdigest()
        headers = {'X-Bot-Key': self.bot_key, 'X-Timestamp': stamp,
                   'X-Signature': signature, 'Content-Type': 'application/json'}
        if client is None:
            with httpx.Client(timeout=self.timeout, follow_redirects=False) as new_client:
                return self.acknowledge(status, reason, new_client)
        response = client.post(self.url.rsplit('/bot-config', 1)[0] + '/config-applied',
                               content=raw, headers=headers, timeout=self.timeout, follow_redirects=False)
        response.raise_for_status()
        return response.json()
