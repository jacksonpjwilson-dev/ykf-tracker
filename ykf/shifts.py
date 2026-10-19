"""Chartright shift windows from the published iCloud calendar."""
import datetime as dt
import os
import re
import time
import urllib.request

import icalendar

from .timeutil import TZ, iso

try:
    import recurring_ical_events
except ImportError:  # plain events only
    recurring_ical_events = None


def https_url(url):
    return re.sub(r"^webcals?://", "https://", url.strip())


def fetch_calendar(cfg, force=False):
    """Refresh the cache if older than calendar_refresh_hours. Returns ics bytes.
    On network failure the cached copy is used."""
    path = cfg["paths"]["calendar_cache"]
    max_age = cfg["calendar_refresh_hours"] * 3600
    fresh = os.path.exists(path) and time.time() - os.path.getmtime(path) < max_age
    if force or not fresh:
        try:
            req = urllib.request.Request(https_url(cfg["calendar_url"]),
                                         headers={"User-Agent": cfg["user_agent"]})
            data = urllib.request.urlopen(req, timeout=30).read()
            if b"BEGIN:VCALENDAR" in data:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                tmp = path + ".tmp"
                open(tmp, "wb").write(data)
                os.replace(tmp, path)
        except Exception:
            if not os.path.exists(path):
                raise
    return open(path, "rb").read()


def _to_dt(v):
    if isinstance(v, dt.datetime):
        return (v.replace(tzinfo=TZ) if v.tzinfo is None else v).astimezone(TZ)
    return None  # all-day events are never shifts


def parse_shifts(ics_bytes, cfg, start=None, end=None):
    """Return sorted list of shifts: {id, start, end, win_start, win_end, summary}."""
    start = start or dt.datetime.now(TZ) - dt.timedelta(days=2)
    end = end or start + dt.timedelta(days=60)
    cal = icalendar.Calendar.from_ical(ics_bytes)
    if recurring_ical_events:
        events = recurring_ical_events.of(cal).between(start, end)
    else:
        events = cal.walk("VEVENT")
    inc = re.compile(cfg["shift_summary_regex"], re.I)
    exc = re.compile(cfg["shift_exclude_regex"], re.I)
    pre = dt.timedelta(minutes=cfg["pre_shift_minutes"])
    out, seen = [], set()
    for e in events:
        summary = str(e.get("SUMMARY", ""))
        if not inc.search(summary) or exc.search(summary):
            continue
        if str(e.get("STATUS", "")).upper() == "CANCELLED":
            continue
        s = _to_dt(e.get("DTSTART").dt)
        en = _to_dt(e.get("DTEND").dt) if e.get("DTEND") else None
        if s is None or en is None or en <= s:
            continue
        if en < start or s > end:
            continue
        sid = s.strftime("%Y-%m-%dT%H:%M")
        if sid in seen:
            continue
        seen.add(sid)
        out.append({"id": sid, "start": s, "end": en, "win_start": s - pre, "win_end": en,
                    "summary": summary})
    out.sort(key=lambda x: x["start"])
    return out


def current_window(shifts, t):
    for s in shifts:
        if s["win_start"] <= t < s["win_end"]:
            return s
    return None


def next_window(shifts, t):
    for s in shifts:
        if s["win_start"] > t:
            return s
    return None


def shift_public(s):
    return {"id": s["id"], "start": iso(s["start"]), "end": iso(s["end"]),
            "window_start": iso(s["win_start"]), "window_end": iso(s["win_end"])}
