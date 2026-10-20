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
- **Feeds:** airplanes.live first, then adsb.lol, then adsb.fi (opendata), with a 250 NM point query every poll, plus a Skycare fleet hex lookup every 2 min (see the call budget below). adsb.lol is capped at 450 calls a day, because its header advertises `500;w=86400`. A 401 or 403 skips that provider for 6 h, a 429 for 10 min, and any other error for 1 min. At most 1 call per second per provider.

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

## Missed arrivals and blind spots (safety net)
The 10 NM alert needs a poll to run while the aircraft is airborne inside 10 NM. If the box is paused, every feed is down, or the aircraft is in a coverage hole low down, that moment can pass unseen. The 2026-10-20 miss was exactly this: the box paused for about 15 min while a scheduled arrival flew its last 25 NM and landed. These extra alerts use the same channel (pending file, then one issue, then Tower). Each fires at most once per arrival per shift, and only for a scheduled arrival that hasn't had a 10 NM alert. Every alert carries a plain `text`.
- **`landed-unalerted`:** the arrival is seen on the ground within 3 NM of CYKF with no alert sent, and it was either seen airborne earlier in the shift or this is just after a blind gap. An aircraft parked on the ramp at shift start doesn't fire.
- **`tracker-gap`:** no good poll for 5 min or more (`gap_alert_s`) while an arrival was heading in within 40 NM or was due from 30 min before the gap to 60 min after it. This sends one alert that names all of them.
- **`contact-lost`:** last seen airborne heading in, within 40 NM, with no position for 5 min.
- **`eta-no-contact`:** fires only when the arrival isn't visible on ADS-B right now. It fires 20 min before the ETA if the aircraft hasn't been seen this shift or is flagged as not broadcasting ADS-B, and 15 min before if it was seen earlier. The text says "ETA-based only" when there's no position behind it (`basis: eta-only`). It's skipped if `contact-lost` already fired.
- **Late adds:** if FlightBridge lists a tail, or moves its ETA, after that ETA has already passed by more than 10 min, no ETA alert is sent and only a `late add` line is logged. The 10 NM and landed alerts stay armed. Moving an ETA later re-arms the warning.
- **No-ADS-B aircraft:** set `"adsb": false` on the arrival in `arrivals.json`, or list the tail in `no_adsb_tails` in config.
- **`tracker-health`:** every feed has been failing for 5 min (`feed_stale_alert_s`). It sends one alert per outage, and `FEED RECOVERED` is logged afterwards.
- **Catch-up lookup:** after a gap, or while an arrival is lost, the tracker asks every working provider (not just the first) for the watched hexes once a minute (only for tracks lost less than 30 min, `catchup_max_s`) and merges the freshest result, because coverage differs between the networks, especially on the ground. Watched hexes also ride along on the 2-minute fleet lookup.
- **Climbs:** a scheduled arrival counts as inbound when it's airborne and pointed within 90° of the field, or getting closer, whatever its vertical rate. A climb out of a nearby field toward CYKF is still inbound.
- **Timeouts:** each feed request has 10 s per socket read and 30 s in total, so a slow-dripping response can't stall the loop. The log also shows `TRACKER GAP` and `feed provider now …` lines when the poll cadence breaks or the feed fails over.
- **Known limit:** nothing inside the box can alert *while* the box is paused. The alerts above fire on the first poll after it resumes.

## Live page privacy
The rows carry only tail, type, ETA, distance, altitude, last seen, status and an "unscheduled" note. There are no owner or operator names, no hex codes, no links and no client info. `page_show_operator` is false.

## GitHub (live)
- **Page:** GitHub Pages from `main:/docs`. It reads `live.json` from the `data` branch through the GitHub contents API every 60 s; ETag revalidations are free. If that fails, it falls back to raw.githubusercontent.com, which caches a branch for up to 5 min.
- **Data:** inside a shift window, `live.json` is force-pushed about every 2 min (and right away on an alert) as the only commit on the `data` branch. Outside windows it's pushed once, with `active:false` and `next_window`, and the page then says "Not on shift — next window …".
- **Alerts:** each poll that produces alerts writes the pending files first, then opens ONE issue for the batch. The title is the alert text, the label is `alert`, it's assigned to jacksonpjwilson-dev, and the body has public fields only. The issue number is added to the pending files. A failure is logged and doesn't stop the tracker. Tower closes the issue after delivering.
- **Test issue:** `python3 -m ykf.run test-issue --dry-run` previews it; drop `--dry-run` to create it.
- **Dry runs:** `--shared` or `--dry-run` never publish or open issues.

## Skycare early "inbound" alert (`alert_type: skycare-inbound`)
This applies to unscheduled Skycare only (hex or registration in the fleet list, or a `PHX*` callsign, and not on FlightBridge). It runs in the same shift windows, on top of the 10 NM alert (`alert_type: near-10nm`). **Each alert fires once per aircraft per flight.** A new flight starts after a gap of more than 20 min, or when the aircraft goes from on the ground to airborne, so a later flight can alert again. The details are in `ykf/skyflight.py`:
- The aircraft must be airborne, doing at least 60 kt, more than 10 NM out (inside that, the 10 NM alert covers it), and not on a flight that departed CYKF.
- If the feed has a destination field and it says CYKF/YKF, that is enough. (adsb.lol and adsb.fi don't send this field today.)
- Otherwise, the last 3 consecutive observations (about 1.5 min) must each have:
  - track within **15°** of the bearing to CYKF (25° when descending at 500 fpm or more, because descent counts as a plus, not a requirement), and
  - cross-track (closest approach on the current track) of **20 NM** or less,
  - with distance to CYKF strictly decreasing across the 3.
- **Departure from CYKF:** the first airborne observation is within 8 NM and the aircraft is climbing (more than +300 fpm) or moving away, or it was on the ground within 8 NM just before. That flight gets no early alert.
- **Turning away:** if it turns away before qualifying, nothing is sent. If it turns away after the alert, it quietly drops off the page. There are no "cancelled" messages.
- **Text:** `Skycare C-GKKC (PHX370) Metro inbound to YKF: 84 NM out, ETA ~22 min, 14,000 ft`. ETA is distance ÷ ground speed, rounded to the minute, and altitude is rounded to 100 ft.
- **Page:** the aircraft shows as "inbound, N NM" with the note "unscheduled". No operator name.
- **Known limit:** a Skycare flight to Toronto coming from the east lines up with YKF far out, and can look inbound until it descends.

## Call budget (free feeds)
- **Per poll (30 s):** one 250 NM point query, about 55–90 KB gzipped and about 600 aircraft.
- **Every 120 s:** one fleet hex lookup, with all 27 hexes in one call and no range limit.
- **Per shift window** (about 11 h, from 30 min before start to the end): 1,320 point calls + 330 fleet calls = **about 1,650 calls**. The busiest calendar day (for example Oct 21: 00:00–01:30 plus 07:30–18:30) is about 1,875.
- **How it's split:** adsb.lol is capped at 450 calls a day (its header says `500;w=86400`). The rest go to adsb.fi, about 1,200–1,400 a shift, at most 1 call per second. That's within adsb.fi's published 1 req/s limit; it publishes no daily cap. airplanes.live is skipped while it returns 403.
- **Outside windows:** zero calls.

## Shift watcher (wakes Cal)
`python3 -m ykf.run shift-watch` runs from cron every 15 min, separately from the tracker loop. The code is in `ykf/shiftwatch.py`.
- **Fetch:** it reads the Chartright calendar (the URL comes from the gitignored `config/config.json`). A good fetch also refreshes `cache/chartright.ics`, so the tracker supervisor picks up new windows within about 10 more minutes.
- **Compare:** it takes the shifts from today onward, `{uid, start, end}`, and compares them by event UID with `/home/box/shared/shift-watch/last.json`, sorting them into added, removed and moved.
- **Ignored:** shifts that have already ended dropping off.
- **On a change:**
  - It writes `/home/box/shared/shift-watch/change.json`. This is box-only and holds the full details, `events[]`, plus `current_shifts`.
  - It opens ONE issue in `jacksonpjwilson-dev/schedule` with the label `schedule-change`, assigned to jacksonpjwilson-dev, titled `Shifts changed HH:MM`. The body has no shift details.
  - If a `schedule-change` issue is already open, it only appends to change.json.
- **First run:** it seeds last.json and opens no issue.
- **Fetch or parse failure:** it's logged to `watch.log`, and nothing else happens.
- **Suddenly empty calendar:** if the calendar has no upcoming shifts but there were 3 or more before, that's treated as a bad fetch.
- **Dry runs:** `--dry-run` writes nothing. `--shared DIR` uses a test directory and never touches GitHub.
