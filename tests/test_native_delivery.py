"""Synthetic tests. No production queue access or network delivery."""
import json
import tempfile
import unittest
from pathlib import Path
from native_service.store import Store

class NativeDelivery(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / 'delivery.sqlite3')
        self.store = Store(self.path)
        self.token = 'a' * 64
        self.store.device(self.token)
        self.event = {'id': 'synthetic-1', 'text': 'Synthetic arrival', 'alert_type': 'near-10nm',
                      'created': '2026-11-11T14:00:00Z', 'hex': 'private', 'client': 'private'}
    def tearDown(self):
        self.store.db.close()
        self.tmp.cleanup()
    def test_stable_id_dedup_and_durable_acceptance(self):
        self.store.ingest(self.event)
        self.store.ingest(self.event)
        self.assertEqual(len(self.store.pending()), 1)
        self.store.result(self.event['id'], self.token, 200)
        self.store.db.close()
        self.store = Store(self.path)
        self.store.ingest(self.event)
        self.assertEqual(self.store.pending(), [])
        self.assertEqual(self.store.diagnostics(), {'accepted': 1})
    def test_retry_backoff_and_limit(self):
        self.store.ingest(self.event, now=1000)
        self.store.result(self.event['id'], self.token, 503, 'ServiceUnavailable', now=1000)
        self.assertEqual(self.store.pending(now=1029), [])
        self.assertEqual(len(self.store.pending(now=1030)), 1)
        for _ in range(7):
            self.store.result(self.event['id'], self.token, 503, now=2000)
        self.assertEqual(self.store.diagnostics(), {'failed': 1})
    def test_unregistered_revokes_token(self):
        self.store.ingest(self.event)
        self.store.result(self.event['id'], self.token, 410, 'Unregistered')
        self.store.ingest(dict(self.event, id='synthetic-2'))
        self.assertEqual(self.store.pending(), [])
    def test_preferences_and_receipts(self):
        self.store.device(self.token, ['tracker-gap'])
        self.store.ingest(self.event)
        self.assertEqual(self.store.pending(), [])
        self.store.ingest(dict(self.event, id='gap', alert_type='tracker-gap'))
        self.assertTrue(self.store.receipt('gap', self.token))
        self.assertFalse(self.store.receipt('unknown', self.token))
    def test_private_fields_not_copied(self):
        self.store.ingest(self.event)
        event = self.store.events()[0]
        self.assertNotIn('hex', event)
        self.assertNotIn('client', event)
    def test_expiry_and_retention(self):
        self.store.ingest(self.event, now=0)
        self.store.prune(now=10801)
        self.assertEqual(self.store.diagnostics(), {'expired': 1})
        self.store.prune(now=172801)
        self.assertEqual(self.store.events(), [])
    def test_missing_stable_id_rejected(self):
        with self.assertRaises(ValueError): self.store.ingest({'text': 'No ID'})
    def test_muted_aircraft_and_nonaircraft_health_alert(self):
        self.store.device(self.token, muted=['C-GTST'])
        self.store.ingest(dict(self.event, tail='CGTST'))
        self.assertEqual(self.store.pending(), [])
        self.store.ingest(dict(self.event, id='health', tail='', alert_type='tracker-health'))
        self.assertEqual(len(self.store.pending()), 1)
