"""Defaults; override any key in config.json next to this package."""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHARED = "/home/box/shared/ykf-tracker"

DEFAULTS = {
    # CYKF aerodrome reference point (Canada Flight Supplement via Wikipedia): 43°27'39"N 080°22'43"W
    "airport": {"icao": "CYKF", "short": "YKF", "lat": 43.46083, "lon": -80.37861, "elev_ft": 1054},
    "alert_radius_nm": 10.0,
    # One 250 NM point query per poll covers the 10 NM alerts AND distant Skycare (PHX*) traffic.
    "query_radius_nm": 250,
    # Plus a Skycare fleet hex lookup (all fleet hexes in one call, worldwide) every N s,
    # to catch fleet aircraft still beyond 250 NM.
    "fleet_lookup_every_s": 120,
    "poll_seconds": 30,
    "pre_shift_minutes": 30,
    # Private: set in config/config.json (gitignored) or env YKF_CALENDAR_URL. Never commit it.
    "calendar_url": "",
    "calendar_refresh_hours": 6,
    "shift_summary_regex": "chartright",
    "shift_exclude_regex": "\\bEA\\b|day off|vacation|\\bOFF\\b",
    # Feeds, tried in order. airplanes.live returned 403 from this box on 2026-10-19
    # (they ask API users to email them first); it is skipped automatically while it 403s.
    "providers": ["airplanes.live", "adsb.lol", "adsb.fi"],
    "provider_daily_budget": {"adsb.lol": 450},
    "user_agent": "ykf-tracker/0.1 (personal, non-commercial; github.com/jacksonpjwilson-dev)",
    "paths": {
        "shared": SHARED,
        "arrivals": SHARED + "/arrivals.json",
        "alerts_pending": SHARED + "/alerts/pending",
        "state": SHARED + "/state.json",
        "live_json": SHARED + "/live.json",
        "web_live_json": ROOT + "/docs/data/live.json",
        "publish_dir": ROOT + "/cache/publish-git",
        "calendar_cache": ROOT + "/cache/chartright.ics",
        "ccar_dir": ROOT + "/cache/ccar",
        "regs_cache": ROOT + "/cache/regs_cache.json",
        "skycare": ROOT + "/config/skycare.json",
        "lock": SHARED + "/tracker.lock",
        "log": SHARED + "/tracker.log",
    },
    # Inbound heuristic for unscheduled Skycare aircraft (see README)
    "skycare_inbound": {"max_alt_ft": 5000, "descent_fpm": -300, "max_climb_fpm": 300,
                        "toward_deg": 60, "close_nm": 3},
    # Early 'Skycare inbound' alert (see ykf/skyflight.py)
    "skycare_early": {"track_tol_deg": 15, "track_tol_desc_deg": 25, "desc_bonus_fpm": -500,
                      "max_cross_track_nm": 20, "min_gs_kt": 60, "depart_nm": 8, "flight_gap_min": 20},
    "lost_after_s": 300,
    # Blind-spot safety net (see core.safety_net / README "Missed arrivals").
    "gap_alert_s": 300,          # no good poll for this long = tracker was blind (box paused, feed down)
    "gap_recent_s": 600,         # how long after a gap a ground sighting at the field still counts as missed
    "landed_nm": 3,              # 'on the ground at the field'
    "lost_inbound_nm": 40,       # contact lost while heading in within this range -> alert
    "eta_warn_min": 15,          # seen earlier but lost: alert this many minutes before ETA
    "eta_warn_noadsb_min": 20,   # never seen / flagged no-ADS-B: ETA-based heads-up this early
    "eta_late_grace_min": 10,    # ETA already this far past when first listed -> late add, no ETA alert
    "no_adsb_tails": [],         # tails known not to broadcast ADS-B (or "adsb": false in arrivals.json)
    "catchup_max_s": 1800,       # stop all-provider catch-up for a track not seen for this long
    "feed_stale_alert_s": 300,   # every feed failing this long -> tracker-health alert
    "catchup_every_s": 60,       # extra all-provider hex lookup for watched tails when one is lost/after a gap
    "feed_timeout_s": 10,        # per socket operation
    "feed_total_timeout_s": 30,  # whole request, so a slow-drip response cannot stall the loop
    "page_show_operator": False,
    # Public live.json -> single-commit, force-pushed 'data' branch (raw.githubusercontent.com)
    "publish": {"enabled": False, "repo": "jacksonpjwilson-dev/ykf-tracker", "branch": "data", "every_s": 120,
                "git_name": "jacksonpjwilson-dev", "git_email": "jacksonpjwilson-dev@users.noreply.github.com"},
    # One GitHub issue per poll that produced alerts (wakes the Tower bot via issue-assigned)
    "issues": {"enabled": False, "repo": "jacksonpjwilson-dev/ykf-tracker", "label": "alert",
               "assignee": "jacksonpjwilson-dev"},
}


def _merge(a, b):
    out = dict(a)
    for k, v in b.items():
        out[k] = _merge(a[k], v) if isinstance(v, dict) and isinstance(a.get(k), dict) else v
    return out


def load(path=None, shared=None):
    cfg = DEFAULTS
    path = path or os.path.join(ROOT, "config", "config.json")
    if os.path.exists(path):
        cfg = _merge(cfg, json.load(open(path)))
    if os.environ.get("YKF_CALENDAR_URL"):
        cfg = dict(cfg, calendar_url=os.environ["YKF_CALENDAR_URL"])
    if shared:  # redirect all shared paths (dry runs / tests)
        p = dict(cfg["paths"])
        for k in ("arrivals", "alerts_pending", "state", "live_json", "lock", "log"):
            p[k] = p[k].replace(cfg["paths"]["shared"], shared)
        p["shared"] = shared
        cfg = dict(cfg, paths=p)
    return cfg
