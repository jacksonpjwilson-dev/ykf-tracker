# Existing system audit — 2026-11-11

Audit baseline: `main` at `7eea9fa`. Both remote branches (`main`, `data`) were fetched. Development branch: `feature/native-ykf-arrivals`. No applicable AGENTS.md was present. No production process was launched, no private calendar or ADS-B provider queried, no issue created, and no production data pushed.

## Verified repository architecture

- `geo.py`: nautical-mile Haversine and bearing helpers; retained exclusively in the tracking backend.
- `regs.py`: cached registration resolution, CCAR cache, Canadian/US algorithms, then online lookup. Registration/callsign matching remains in `core.py`.
- `shifts.py`: iCalendar parsing, optional recurring event expansion, Toronto timezone, Chartright inclusion/exclusion, 30-minute pre-shift window, six-hour calendar cache.
- `feeds.py`: provider fallback, one-second provider spacing, daily adsb.lol budget, HTTP backoff, bounded reads, fleet and catch-up queries. No iPhone provider requests were added.
- `core.py`: scheduled proximity and inbound Skycare rules, per-shift deduplication, contact-lost/ETA/landed-unalerted safeguards, cross-shift schedule rejection, gap reconstruction, public rows. Existing `landed?` is an uncertain inference; native UI explicitly says landing unconfirmed.
- `skyflight.py`: separate early unscheduled Skycare inbound observation logic, flight-scoped deduplication and departure exclusion. Left unchanged.
- `run.py`: supervisor lock, persistent state, shift gating, gap detection on resume, provider watchdog, publication, pending-file retries and Tower wakeups.
- `shiftwatch.py`: independent calendar change watcher and private local diff with public issue wakeup. Left unchanged.
- `github.py`: single-commit force publication to `data`, optional private Firestore copy through an external writer, batched public GitHub issues, and issue reassignment. Credentials come from the existing host's GitHub setup.
- `config.py`, `config/skycare.json`: operational settings and 27-aircraft fleet. No values changed.
- `timeutil.py`: America/Toronto offset-aware times. Native schedule displays use the same timezone.
- Supervisor script and cron example depend on external `/home/box/shared` and `/workspace/ops/watchdog.sh` infrastructure. Their presence/running status on the operational host could not be established from this isolated checkout.
- Existing GitHub Actions CI installs pytest, icalendar, recurring-ical-events and runs the tracker tests. Python standard library otherwise supplies tracker network/filesystem functionality; `gh` and `git` provide publication and issue operations.

## Arrival format and live publication

`ARRIVALS-FORMAT.md` is referenced in README but absent from both fetched branches and this environment's shared directories. Actual authoritative reader `core.load_arrivals` accepts `{updated, shift, arrivals:[{tail,type,eta,adsb?}]}`. ETA is optional HH:mm; private shift metadata identifies the correct work window. The native app reads published rows rather than privately reading arrivals.json or interpreting its matching rules.

The `data` branch contains only README and live.json and has deliberately no operational history. Observed publication: `2026-11-11T09:04:38-05:00`; active shift, adsb.lol healthy at publication, old schedule flag, one expected row. This is evidence of a published snapshot, not proof of the current process health. The app always retains publication age.

Live envelope: `generated`, `airport`, `radius_nm`, `active`, `shift`, `arrivals_updated`, `arrivals_stale`, `feed:{provider,ok}`, `next_window`, `rows`. Rows: `tail`, `type`, `eta`, `dist_nm`, `alt` (formatted string or null), `last_seen`, `status`, `scheduled`; optional `note` and `alerted_at`. Operator designations are normally removed. Native optional models additionally accept authorized position/speed fields, but no current endpoint supplies these and no public schema expansion was made.

GitHub Pages HTML was fetched successfully. It matches the repository's native feed strategy: GitHub contents API, raw fallback, roughly one-minute foreground refresh, two-minute backend publication, raw caching potentially five minutes. Private Firestore gate exists but is disabled in docs/config.js. Its writer, access rules, credentials and runtime availability are not present here. Native app does not bypass it or reuse browser credentials.

## Alerts and uncertainty

Existing classes: `near-10nm`, `skycare-inbound`, `landed-unalerted`, `contact-lost`, `eta-no-contact`, `tracker-gap`, `tracker-health`. Feed recovery is logged; it is not currently a stable recovery event. No fabricated recovery event is emitted by the native implementation.

Tracker writes stable-ID files into pending, opens one batched GitHub issue, and Tower delivers and closes it. Pending delivery retries issue creation, reassigns open issues to wake Tower, then archives stale alerts. The native service reads copies of files in pending/delivered/stale and never moves or marks those files. APNs acceptance and iPhone receipts remain separate from Tower delivery and user acknowledgement.

## Privacy boundaries

Public rows already disclose registration, type, schedule ETA, shift windows, distance, altitude and timestamps. They intentionally omit hex, coordinates, owner/client records and operator details. Existing alert text has already been used in public issues. The optional native adapter copies an explicit field allowlist from those alert events, never raw state.json. Credentials, device tokens and APNs keys remain server-private or in iOS Keychain. The adapter's local SQLite file is mode 0600.

The app does not infer Skycare membership from fleet data suppressed in the public feed, infer positions from distances, or independently detect arrivals. A coordinate-bearing interface needs an authenticated service, explicit privacy review and provider/license review before operational deployment. All tests use synthetic aircraft.
