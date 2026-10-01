"""Push one daily snapshot from an authorized social or analytics adapter.

The adapter fetches provider statistics with its own approved credentials and
uses this small client to send them to CRM. Replays replace the same day.
"""
import hashlib
import hmac
import json
import time

import httpx


def send_metric(crm_url, channel_id, secret, *, utm_campaign, day,
                impressions=0, clicks=0, leads=0, spend='0'):
    payload = {'utm_campaign': utm_campaign, 'day': day, 'impressions': impressions,
               'clicks': clicks, 'leads': leads, 'spend': str(spend)}
    body = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode()
    stamp = str(int(time.time()))
    signature = hmac.new(secret.encode(), stamp.encode() + b'.' + body, hashlib.sha256).hexdigest()
    with httpx.Client(timeout=12) as client:
        response = client.post(crm_url.rstrip('/') + '/api/v1/marketing/metrics', content=body,
            headers={'Content-Type': 'application/json', 'X-Channel-ID': str(channel_id),
                     'X-Timestamp': stamp, 'X-Signature': signature})
        response.raise_for_status()
        return response.json()
