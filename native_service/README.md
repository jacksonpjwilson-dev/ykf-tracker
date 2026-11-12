# Optional native notification service

A separate, owner-only adapter for the existing pending-alert contract. The production Python package, cron, public payload, alert rules, Tower queue and consumers are unchanged. Nothing starts this service automatically.

## Deploy privately

Use a dedicated account, Python virtual environment and a private persistent directory (mode 0700). Install `native_service/requirements.txt`. Mount the existing tracker `alerts` directory read-only and an APNs `.p8` key read-only. The adapter needs access to pending/delivered/stale files; it never modifies or consumes them. This isolated build environment has no operational queue or APNs credentials.

Configure these server environment variables through your deployment secret manager, never git or chat:

| Variable | Purpose |
| --- | --- |
| `YKF_NATIVE_DB` | Absolute private persistent SQLite path; file is chmod 0600 |
| `YKF_ALERT_ROOT` | Read-only tracker alerts directory containing pending/delivered/stale |
| `YKF_DEVICE_CREDENTIAL` | Random owner enrollment credential, at least 32 characters; rotate by replacing server value and re-enrolling devices |
| `APNS_KEY_FILE` | Private Apple signing-key path |
| `APNS_KEY_ID` | Apple APNs key identifier |
| `APNS_TEAM_ID` | Developer team identifier |
| `APNS_TOPIC` | Provisioned iPhone bundle ID, default project uses dev.jacksonwilson.ykfarrivals |
| `APNS_ENVIRONMENT` | `sandbox` for development or `production` for distribution |

Run behind an authenticated, TLS-terminating gateway with request size/rate limits and a persistent supervised process:

```sh
python -m uvicorn native_service.app:app --host 127.0.0.1 --port 8787
```

Permit outbound HTTPS/HTTP2 to Apple's appropriate push endpoint. Never expose the loopback HTTP listener publicly. Protect SQLite and its WAL sidecar/backups as device-token data. App and gateway must use valid trusted HTTPS. This implementation is intended for the single authorized owner; a shared static enrollment credential is not a multi-user identity/authorization system. It requires gateway hardening and an operational security review before deployment.

## API

All endpoints require `Authorization: Bearer <owner enrollment credential>` and fail closed if credential configuration is absent. Tokens are hex-validated. Registration replaces preferences for that token.

- `POST /devices`: `{token, kinds?: [backend alert types], muted_tails?: [registration]}`. Empty kinds means all existing event classes. Registration is idempotent and updates token preferences.
- `DELETE /devices`: `{token}`. Revokes token and cancels pending sends. APNs bad/unregistered tokens also revoke automatically.
- `GET /alerts`: last 100 retained allowlisted backend events, for owner-only in-app catch-up.
- `POST /receipts`: `{token, alert_id}`. Records app receipt for an existing per-device delivery. This is separate from local user acknowledgement and Tower delivery.
- `GET /health`: version, aggregate delivery states and whether APNs variables are present. Modular Head Quarter integration surface. Unknown tracker/feed/publication state stays explicitly unknown; this adapter cannot establish host health independently.

No public ingestion route exists. Every 15 seconds the adapter scans existing lifecycle directories and imports stable-ID events from the last three hours. It retains event/delivery history for 48 hours, filters per-device event classes and muted tails, and delivers using ES256 APNs provider authentication over HTTP/2. APNs acceptance is stored durably; restart/reimport does not resend accepted events. Token rotation registers the new token; revoke the old token explicitly when retiring it or wait for Apple's invalid-token response.

Transient failures back off from 30 seconds exponentially to 30 minutes, at most eight attempts and never after the three-hour delivery window. Permanent HTTP/configuration errors remain recorded as failed; repair configuration before manually reviewing/redriving affected records. The worker logs sanitized file-read failure summaries and stores sanitized APNs failure reasons. APNs signing keys are server-only. APNs collapse IDs are stable SHA256 event IDs. Network ambiguity can cause retry duplicates despite collapse IDs; iOS stores stable IDs and deduplicates alert history. Apple may display notifications before the app can deduplicate, so exactly-once banner presentation is not promised.

Alert payloads use the existing public issue-text templates and an explicit field allowlist. They never copy raw state, hex, calendar URL, client, owner or operator records. Review any future changes to tracker text templates before forwarding new fields. There is no aircraft detection, position inference, APNs Live Activity update service, synthetic recovery event or safety-critical guarantee here.

## Verify without sending

```sh
python -m pytest -q tests/test_native_delivery.py native_service/test_api.py
```

Tests use synthetic events, temporary queues/databases and mocked APNs HTTP transport. They do not issue real pushes or touch production alerts. End-to-end verification still requires a provisioned device, Apple key, a deployed TLS gateway and a separately authorized test notification.
