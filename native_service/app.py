"""Owner-only APNs service. Run separately; TLS must be terminated by a trusted gateway."""
import asyncio
import hmac
import json
import os
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import jwt
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field
from .store import Store

ALLOWED_KINDS = {'near-10nm', 'skycare-inbound', 'contact-lost', 'eta-no-contact', 'landed-unalerted', 'tracker-gap', 'tracker-health'}

def require_owner(authorization: str = Header(default='')):
    credential = os.environ.get('YKF_DEVICE_CREDENTIAL', '')
    if len(credential) < 32:
        raise HTTPException(503, 'Enrollment is not configured')
    if not hmac.compare_digest(authorization, 'Bearer ' + credential):
        raise HTTPException(401, 'Unauthorized')

class Device(BaseModel):
    token: str = Field(pattern=r'^[0-9a-f]{64,200}$')
    kinds: list[str] = Field(default_factory=list, max_length=20)
    muted_tails: list[str] = Field(default_factory=list, max_length=500)

class Receipt(Device):
    alert_id: str = Field(min_length=1, max_length=250)

class APNs:
    def __init__(self):
        self.token = None
        self.issued = 0

    def authorization(self):
        if not self.token or time.time() - self.issued > 3000:
            key = Path(os.environ['APNS_KEY_FILE']).read_text()
            self.issued = int(time.time())
            self.token = jwt.encode({'iss': os.environ['APNS_TEAM_ID'], 'iat': self.issued}, key, algorithm='ES256', headers={'kid': os.environ['APNS_KEY_ID']})
        return self.token

    async def send(self, client, token, event):
        topic = os.environ['APNS_TOPIC']
        production = os.environ.get('APNS_ENVIRONMENT', 'sandbox') == 'production'
        host = 'api.push.apple.com' if production else 'api.sandbox.push.apple.com'
        body = {'aps': {'alert': {'title': 'YKF arrival alert', 'body': event['text'][:500]}, 'sound': 'default'}, 'tail': event.get('tail', ''), 'event': event}
        # APNs has a 4096 byte limit, including UTF-8 expansion.
        if len(json.dumps(body).encode()) > 4096:
            return 413, 'PayloadTooLarge'
        response = await client.post(f'https://{host}/3/device/{token}', json=body, headers={
            'authorization': 'bearer ' + self.authorization(), 'apns-topic': topic,
            'apns-push-type': 'alert', 'apns-priority': '10',
            'apns-expiration': str(int(time.time()) + 3600),
            'apns-collapse-id': __import__('hashlib').sha256(event['id'].encode()).hexdigest(),
        })
        try:
            reason = response.json().get('reason', '') if response.status_code != 200 else ''
        except ValueError:
            reason = 'InvalidAPNsResponse'
        return response.status_code, reason

async def worker(store):
    sender = APNs()
    async with httpx.AsyncClient(http2=True, timeout=20) as client:
        while True:
            # Read copies from all alert lifecycle directories: Tower may move pending files.
            # Never move, delete, acknowledge, or mutate original tracker alert files.
            root = os.environ.get('YKF_ALERT_ROOT')
            if root:
                for folder in ('pending', 'delivered', 'stale'):
                    for path in Path(root, folder).glob('*.json'):
                        try:
                            event = json.loads(path.read_text())
                            from datetime import datetime
                            created = datetime.fromisoformat(event['created']).timestamp()
                            if time.time() - 10800 <= created <= time.time() + 60:
                                store.ingest(event, now=created)
                        except (ValueError, TypeError, KeyError, OSError):
                            # Avoid logging payloads, paths, or credentials.
                            print('native adapter: invalid alert file', flush=True)
            store.prune()
            if all(os.environ.get(x) for x in ('APNS_KEY_FILE', 'APNS_TEAM_ID', 'APNS_KEY_ID', 'APNS_TOPIC')):
                for event_id, token, _, payload in store.pending():
                    try:
                        status, reason = await sender.send(client, token, json.loads(payload))
                        store.result(event_id, token, status, reason)
                    except Exception:
                        store.result(event_id, token, 503, 'APNsTransportOrConfigurationError')
            await asyncio.sleep(15)

@asynccontextmanager
async def lifespan(app):
    path = os.environ.get('YKF_NATIVE_DB', 'native-delivery.sqlite3')
    app.state.store = Store(path)
    task = asyncio.create_task(worker(app.state.store))
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    app.state.store.db.close()

app = FastAPI(title='YKF native delivery', lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

@app.post('/devices', dependencies=[Depends(require_owner)])
async def register(device: Device):
    if any(kind not in ALLOWED_KINDS for kind in device.kinds):
        raise HTTPException(422, 'Unknown alert preference')
    if any(not re.fullmatch(r"[A-Za-z0-9-]{1,16}", tail) for tail in device.muted_tails):
        raise HTTPException(422, "Invalid aircraft preference")
    app.state.store.device(device.token, device.kinds, device.muted_tails)
    return {'registered': True}

@app.delete('/devices', dependencies=[Depends(require_owner)])
async def revoke(device: Device):
    app.state.store.revoke(device.token)
    return {'revoked': True}

@app.post('/receipts', dependencies=[Depends(require_owner)])
async def receipt(receipt: Receipt):
    if not app.state.store.receipt(receipt.alert_id, receipt.token):
        raise HTTPException(404, 'Unknown delivery')
    return {'received': True}

@app.get('/alerts', dependencies=[Depends(require_owner)])
async def alerts():
    return app.state.store.events()

@app.get('/health', dependencies=[Depends(require_owner)])
async def health():
    return {'adapter_version': '1.0', 'delivery': app.state.store.diagnostics(),
            'apns_configured': all(os.environ.get(x) for x in ('APNS_KEY_FILE', 'APNS_TEAM_ID', 'APNS_KEY_ID', 'APNS_TOPIC')),
            'tracker_availability': 'unknown', 'feed_health': 'not-observed',
            'publication_failures': 'not-observed'}
