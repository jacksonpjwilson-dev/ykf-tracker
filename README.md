# YKF arrivals proximity tracker (CYKF, Waterloo)

A plain Python script with no AI in the loop. During Chartright shifts it polls free ADS-B feeds every 30 s. It writes one alert file the first time a FlightBridge arrival, or an inbound Skycare aircraft, comes within 10 NM of CYKF, and it keeps a small live page up to date.

**Status:** live. Page: https://jacksonpjwilson-dev.github.io/ykf-tracker/ . Supervisor kept alive by cron and /workspace/ops/watchdog.sh.

## Layout
| Path | What |
|---|---|
| `ykf/geo.py` | Haversine distance (NM) and bearing |
| `ykf/regs.py` | Tail to ICAO hex: cache, then CCAR, then algorithm (C-F/C-G and N-numbers), then airplanes.live/adsb.lol `/v2/reg/` |
| `ykf/shifts.py` | Chartright windows from the published iCloud calendar (webcal is converted to https, cached, refreshed every 6 h) |
| `ykf/feeds.py` | Point query around CYKF with provider fallback, a daily budget and backoff |
| `ykf/core.py` | Matching, alert rules, once-per-shift dedup, live rows (pure, unit-tested) |
| `ykf/run.py` | CLI and supervisor |
| `config/skycare.json` | Skycare fleet (27 tails with hex), PHX callsign prefix, sources |
| `config/config.json` | Private, gitignored overrides (calendar URL, publish/issues on). Template: `config.example.json` for any key in `ykf/config.py` DEFAULTS |
| `docs/index.html` (GitHub Pages; reads live.json from the `data` branch) | Static, mobile-friendly live page |
| `tests/test_tracker.py` | 22 unit tests |
| `run-supervisor.sh`, `cron.example` | How it would be kept running (not installed) |

Shared files in `/home/box/shared/ykf-tracker/`:
- `arrivals.json` is written by Sunny. The spec is in `ARRIVALS-FORMAT.md`.
- `alerts/pending/<id>.json` holds one file per alert, for Felix to deliver. After delivery, move it to `alerts/delivered/`.
- `live.json`, `state.json`, `tracker.log` and `tracker.lock` are runtime files.

## Run
```bash
cd /workspace/ykf-tracker
python3 -m unittest discover -s tests -v          # tests
python3 -m ykf.run windows --n 3                  # next Chartright windows
python3 -m ykf.run resolve C-GGIB N123AB          # tail -> hex
python3 -m ykf.run once --force --polls 4 --shared /tmp/ykf-dry   # dry run outside a shift
./run-supervisor.sh                               # the real thing (NOT started)
cd docs && python3 -m http.server 18791            # view the page locally
```

## Rules
- **Run window:** from 30 min before a calendar event whose summary matches `chartright` until that event ends. Summaries matching `EA`, `day off`, `vacation` or `OFF`, all-day events and cancelled events are ignored. Outside a window the supervisor sleeps, rechecking at least every 10 min, and makes no feed calls.
- **Watch list:** the tails in `arrivals.json`. A live aircraft matches on hex, the feed's `r` (registration) or `flight` (callsign).
- **Scheduled alert:** an aircraft on the list that is airborne inside 10 NM, unless it is plainly departing (climbing at 500 fpm or more and pointed more than 90° away from the field).
- **Skycare alert,** even when the aircraft isn't on FlightBridge. The aircraft counts as Skycare when its hex or registration is in the fleet list, or its callsign starts with `PHX`. It must be airborne inside 10 NM, heading toward the field (track within 60° of the bearing to CYKF, or within 3 NM), and either descending at 300 fpm or more, or below 5,000 ft and not climbing faster than 300 fpm. A Skycare aircraft that is passing over is ignored and doesn't appear on the page.
- **Everything else** (overflights, training traffic, the other FBO's aircraft) is ignored and never stored.
- **Once per aircraft per shift:** the dedup keys are the shift id plus hex and tail, saved in `state.json`, so a restart doesn't re-alert.
- **Feeds:** airplanes.live first, then adsb.lol, then adsb.fi (opendata), with a 25 NM point query. adsb.lol is capped at 450 calls a day, because its header advertises `500;w=86400`. A 401 or 403 skips that provider for 6 h, a 429 for 10 min, and any other error for 1 min. At most 1 call per second per provider.

## Alert file
```json
{"id":"2026-10-20T1500-CGGIB-174512","created":"2026-10-20T17:45:12-04:00","time":"17:45",
 "shift":{"id":"2026-10-20T15:00","start":"...","end":"..."},
 "tail":"C-GGIB","hex":"c05552","type":"Citation XLS","distance_nm":9.6,"altitude_ft":3200,
 "vertical_rate_fpm":-900,"groundspeed_kt":180,"eta":"no ETA","has_eta":false,
 "on_flightbridge":true,"skycare":false,
 "text":"C-GGIB Citation XLS 9.6 NM from YKF, 3,200 ft, no ETA","sms_text":"<= 160 chars",
 "channel_hint":"in-app","delivered":false}
```

## Live page privacy
The rows carry only tail, type, ETA, distance, altitude, last seen, status and an "unscheduled" note. There are no owner or operator names, no hex codes, no links and no client info. `page_show_operator` is false.

## GitHub (live)
- **Page:** GitHub Pages from `main:/docs`. It fetches `https://raw.githubusercontent.com/jacksonpjwilson-dev/ykf-tracker/data/live.json?t=<now>` every 15 s.
- **Data:** inside a shift window, `live.json` is force-pushed about every 2 min (and right away on an alert) as the only commit on the `data` branch. Outside windows it's pushed once, with `active:false` and `next_window`, and the page then says "Not on shift — next window …".
- **Alerts:** each poll that produces alerts writes the pending files first, then opens ONE issue for the batch. The title is the alert text, the label is `alert`, it's assigned to jacksonpjwilson-dev, and the body has public fields only. The issue number is added to the pending files. A failure is logged and doesn't stop the tracker. Tower closes the issue after delivering.
- **Test issue:** `python3 -m ykf.run test-issue --dry-run` previews it; drop `--dry-run` to create it.
- **Dry runs:** `--shared` or `--dry-run` never publish or open issues.
