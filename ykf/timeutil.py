import datetime as dt
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Toronto")


def now():
    return dt.datetime.now(TZ).replace(microsecond=0)


def iso(t):
    """Ontario ISO with offset, e.g. 2026-10-20T15:00:00-04:00."""
    return t.astimezone(TZ).replace(microsecond=0).isoformat()


def hhmm(t):
    return t.astimezone(TZ).strftime("%H:%M")


def label(t):
    """'Tue Oct 20 15:00'."""
    t = t.astimezone(TZ)
    return t.strftime("%a %b ") + str(t.day) + t.strftime(" %H:%M")


def parse_iso(s):
    t = dt.datetime.fromisoformat(s)
    if t.tzinfo is None:
        t = t.replace(tzinfo=TZ)
    return t
