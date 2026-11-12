import os
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from native_service.app import app

class NativeAPI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'YKF_NATIVE_DB': self.tmp.name+'/state.db', 'YKF_DEVICE_CREDENTIAL': 'x'*40, 'YKF_ALERT_ROOT': ''})
        self.env.start()
        self.client = TestClient(app)
        self.client.__enter__()
        self.headers = {'Authorization': 'Bearer '+'x'*40}
    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.env.stop(); self.tmp.cleanup()
    def test_auth_required_on_all_routes(self):
        for method, route in [('get','/health'), ('get','/alerts'), ('post','/devices'), ('delete','/devices'), ('post','/receipts')]:
            response = getattr(self.client, method)(route)
            self.assertEqual(response.status_code, 401)
    def test_device_validation_and_preferences(self):
        self.assertEqual(self.client.post('/devices', headers=self.headers, json={'token':'bad'}).status_code, 422)
        self.assertEqual(self.client.post('/devices', headers=self.headers, json={'token':'a'*64,'kinds':['invented']}).status_code, 422)
        self.assertEqual(self.client.post('/devices', headers=self.headers, json={'token':'a'*64,'kinds':['near-10nm']}).status_code, 200)
        response = self.client.request('DELETE','/devices', headers=self.headers, json={'token':'a'*64})
        self.assertEqual(response.status_code, 200)
    def test_health_does_not_claim_tracker_availability(self):
        response = self.client.get('/health', headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['tracker_availability'], 'unknown')
    def test_empty_alerts_and_unknown_receipt(self):
        self.assertEqual(self.client.get('/alerts', headers=self.headers).json(), [])
        self.assertEqual(self.client.post('/receipts', headers=self.headers, json={'token':'a'*64,'alert_id':'missing'}).status_code, 404)
    def test_unconfigured_auth_fails_closed(self):
        with patch.dict(os.environ, {'YKF_DEVICE_CREDENTIAL': ''}):
            self.assertEqual(self.client.get('/health', headers=self.headers).status_code, 503)

class APNsTransport(unittest.IsolatedAsyncioTestCase):
    async def test_synthetic_payload_and_headers(self):
        import httpx
        from native_service.app import APNs
        captured = []
        def handle(request):
            captured.append(request)
            return httpx.Response(200)
        sender = APNs()
        with patch.dict(os.environ, {'APNS_TOPIC':'dev.test.synthetic','APNS_ENVIRONMENT':'sandbox'}), patch.object(sender, 'authorization', return_value='synthetic-not-a-credential'):
            async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
                status, reason = await sender.send(client, 'a'*64, {'id':'synthetic','text':'Synthetic only','tail':'C-GTST'})
        self.assertEqual(status, 200)
        self.assertEqual(captured[0].url.host, 'api.sandbox.push.apple.com')
        self.assertEqual(captured[0].headers['apns-push-type'], 'alert')
        self.assertEqual(len(captured[0].headers['apns-collapse-id']), 64)
        import json
        self.assertEqual(json.loads(captured[0].content)['event']['id'], 'synthetic')
    async def test_payload_limit(self):
        import httpx
        from native_service.app import APNs
        with patch.dict(os.environ, {'APNS_TOPIC':'dev.test.synthetic'}):
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: self.fail('Must not send oversize payload'))) as client:
                status, _ = await APNs().send(client, 'a'*64, {'id':'synthetic','text':'x'*5000})
        self.assertEqual(status, 413)
