"""Defaults; override any key in config.json next to this package."""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHARED = "/home/box/shared/ykf-tracker"

DEFAULTS = {
    # CYKF aerodrome reference point (Canada Flight Supplement via Wikipedia): 43°27'39"N 080°22'43"W
    "airport": {"icao": "CYKF", "short": "YKF", "lat": 43.46083, "lon": -80.37861, "elev_ft": 1054},
    "alert_radius_nm": 10.0,
    "query_radius_nm": 25,
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
    "lost_after_s": 300,
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
