"""Durable per-device delivery state, independent of Tower's pending queue."""
import json
import os
import sqlite3
import time

SAFE_FIELDS = {"id", "alert_type", "created", "tail", "type", "text", "basis", "distance_nm", "altitude_ft", "eta"}

class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path, check_same_thread=False)
        if path != ":memory:":
            os.chmod(path, 0o600)
        self.db.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS devices(token TEXT PRIMARY KEY, active INTEGER NOT NULL DEFAULT 1, kinds TEXT NOT NULL DEFAULT '[]', muted TEXT NOT NULL DEFAULT '[]');
        CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY, payload TEXT NOT NULL, created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS delivery(event TEXT, token TEXT, attempts INTEGER DEFAULT 0, next_try REAL DEFAULT 0,
          status TEXT DEFAULT 'pending', error TEXT, receipt REAL, PRIMARY KEY(event,token));
        ''')
        self.db.commit()

    def device(self, token, kinds=(), muted=()):
        self.db.execute("INSERT INTO devices(token,kinds,muted) VALUES (?,?,?) ON CONFLICT(token) DO UPDATE SET active=1,kinds=excluded.kinds,muted=excluded.muted", (token, json.dumps(list(kinds)), json.dumps(list(muted))))
        self.db.commit()

    def revoke(self, token):
        self.db.execute("UPDATE devices SET active=0 WHERE token=?", (token,))
        self.db.execute("UPDATE delivery SET status='revoked' WHERE token=? AND status='pending'", (token,))
        self.db.commit()

    def ingest(self, event, now=None):
        now = time.time() if now is None else now
        if not isinstance(event.get('id'), str) or not event['id'] or not isinstance(event.get('text'), str):
            raise ValueError('alert requires stable id and text')
        for key in ('tail', 'alert_type', 'created', 'type', 'basis', 'eta'):
            if event.get(key) is not None and not isinstance(event[key], str):
                raise ValueError('invalid alert string field')
        safe = {k: v for k, v in event.items() if k in SAFE_FIELDS}
        # Alert text is existing public issue text. No raw track, hex, calendar or client record is copied.
        self.db.execute("INSERT OR IGNORE INTO events VALUES (?,?,?)", (event['id'], json.dumps(safe), now))
        for token, kinds, muted in self.db.execute("SELECT token,kinds,muted FROM devices WHERE active=1").fetchall():
            normalize = lambda value: "".join(c for c in value.upper() if c.isalnum())
            if normalize(event.get("tail") or "") in {normalize(tail) for tail in json.loads(muted)}:
                continue
            if json.loads(kinds) and event.get('alert_type') not in json.loads(kinds):
                continue
            self.db.execute("INSERT OR IGNORE INTO delivery(event,token) VALUES (?,?)", (event['id'], token))
        self.db.commit()

    def pending(self, now=None):
        now = time.time() if now is None else now
        return self.db.execute('''SELECT d.event,d.token,d.attempts,e.payload FROM delivery d
          JOIN events e ON e.id=d.event JOIN devices v ON v.token=d.token
          WHERE d.status='pending' AND d.next_try<=? AND v.active=1 AND e.created>=?''', (now, now-10800)).fetchall()

    def result(self, event, token, status_code, reason='', now=None):
        now = time.time() if now is None else now
        row = self.db.execute("SELECT attempts FROM delivery WHERE event=? AND token=?", (event, token)).fetchone()
        if row is None:
            return
        attempts = row[0] + 1
        permanent = status_code in (400, 403, 404, 405, 410, 413)
        status = 'accepted' if status_code == 200 else 'failed' if permanent or attempts >= 8 else 'pending'
        self.db.execute("UPDATE delivery SET attempts=?,next_try=?,status=?,error=? WHERE event=? AND token=?",
                        (attempts, now + min(1800, 30 * 2 ** (attempts-1)), status, reason[:200], event, token))
        if status_code == 410 or reason in ('BadDeviceToken', 'Unregistered', 'DeviceTokenNotForTopic'):
            self.revoke(token)
        self.db.commit()

    def receipt(self, event, token):
        cursor = self.db.execute("UPDATE delivery SET receipt=? WHERE event=? AND token=?", (time.time(), event, token))
        self.db.commit()
        return cursor.rowcount > 0

    def events(self, limit=100):
        return [json.loads(x[0]) for x in self.db.execute("SELECT payload FROM events ORDER BY created DESC LIMIT ?", (limit,))]

    def diagnostics(self):
        return dict(self.db.execute("SELECT status,COUNT(*) FROM delivery GROUP BY status").fetchall())

    def prune(self, now=None):
        now = time.time() if now is None else now
        self.db.execute("UPDATE delivery SET status='expired' WHERE status='pending' AND event IN (SELECT id FROM events WHERE created<?)", (now-10800,))
        self.db.execute("DELETE FROM delivery WHERE event IN (SELECT id FROM events WHERE created<?)", (now-172800,))
        self.db.execute("DELETE FROM events WHERE created<?", (now-172800,))
        self.db.commit()
