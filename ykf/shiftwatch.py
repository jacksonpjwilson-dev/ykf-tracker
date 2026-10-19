"""Shift watcher: detect Chartright shift changes and wake Cal via a GitHub issue.

Runs from cron every 15 min (separate from the tracker loop):
  python3 -m ykf.run shift-watch [--dry-run]

* Fetches the published Chartright calendar (URL from config/config.json, never committed).
  A successful fetch also refreshes cache/chartright.ics, so the tracker supervisor
  picks up new shift windows within its next idle check (<= 10 min).
* Normalises shifts from today 00:00 onward: {uid, start, end} (Ontario ISO).
* Diffs against /home/box/shared/shift-watch/last.json by event UID:
  added, removed, changed (same UID, new start/end). Removed shifts that already ended
  (end < now) are ignored, so normal ageing never fires.
* On a change: writes change.json (box-only, full details) and opens ONE issue in
  jacksonpjwilson-dev/schedule (label schedule-change, assigned to jacksonpjwilson-dev,
  title 'Shifts changed HH:MM', no shift details). If an open schedule-change issue
  already exists, it only appends to change.json (comments don't wake Cal).
* First run seeds last.json, no issue. Fetch/parse failures: logged, nothing else.
* Safety: a calendar that suddenly has no upcoming shifts while last.json had 3+ is
  treated as a bad fetch (logged, not saved, no issue).
"""
import datetime as dt
import fcntl
import json
import os
import subprocess
import urllib.request

from .shifts import https_url, parse_shifts
from .timeutil import TZ, hhmm, iso, now as tnow, parse_iso

DIR = "/home/box/shared/shift-watch"
REPO = "jacksonpjwilson-dev/schedule"
LABEL = "schedule-change"
ASSIGNEE = "jacksonpjwilson-dev"


def normalize(shifts):
    return sorted(({"uid": s["uid"], "start": iso(s["start"]), "end": iso(s["end"])} for s in shifts),
                  key=lambda x: (x["start"], x["uid"]))


def diff(old, new, now):
    """old/new: normalized lists. Returns {"added","removed","changed"} (empty lists if none)."""
    o = {x["uid"]: x for x in old}
    n = {x["uid"]: x for x in new}
    added = [n[u] for u in n if u not in o and parse_iso(n[u]["end"]) >= now]
    removed = [o[u] for u in o if u not in n and parse_iso(o[u]["end"]) >= now]
    changed = [{"before": o[u], "after": n[u]} for u in n if u in o
               and (o[u]["start"], o[u]["end"]) != (n[u]["start"], n[u]["end"])
               and (parse_iso(n[u]["end"]) >= now or parse_iso(o[u]["end"]) >= now)]
    key = lambda x: x["start"]
    return {"added": sorted(added, key=key), "removed": sorted(removed, key=key),
            "changed": sorted(changed, key=lambda c: c["after"]["start"])}


def has_changes(d):
    return bool(d["added"] or d["removed"] or d["changed"])


def _log(msg, d=DIR):
    os.makedirs(d, exist_ok=True)
    line = "%s %s" % (iso(tnow()), msg)
    print(line, flush=True)
    with open(os.path.join(d, "watch.log"), "a") as f:
        f.write(line + "\n")


def _atomic(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=1)
    os.replace(tmp, path)


def fetch_ics(cfg):
    """Raises on failure. Refreshes the tracker's calendar cache on success."""
    url = cfg["calendar_url"]
    if not url:
        raise RuntimeError("calendar_url not set in config/config.json")
    req = urllib.request.Request(https_url(url), headers={"User-Agent": cfg["user_agent"]})
    data = urllib.request.urlopen(req, timeout=30).read()
    if b"BEGIN:VCALENDAR" not in data:
        raise RuntimeError("response is not a calendar (%d bytes)" % len(data))
    cache = cfg["paths"]["calendar_cache"]
    os.makedirs(os.path.dirname(cache), exist_ok=True)
    with open(cache + ".tmp", "wb") as f:
        f.write(data)
    os.replace(cache + ".tmp", cache)
    return data


def _gh(args, inp=None):
    r = subprocess.run(["gh"] + args, input=inp, capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip()[:300])
    return r.stdout


def open_issue_number():
    out = _gh(["issue", "list", "-R", REPO, "--label", LABEL, "--state", "open", "--json", "number", "--limit", "5"])
    nums = [i["number"] for i in json.loads(out or "[]")]
    return min(nums) if nums else None


def create_issue(t):
    try:
        _gh(["label", "create", LABEL, "-R", REPO, "--color", "1D76DB",
             "--description", "Chartright shifts changed; Cal rebuilds and closes"])
    except RuntimeError as e:
        if "already exists" not in str(e):
            raise
    body = ("Jackson's Chartright shifts changed on his published calendar.\n\n"
            "Details are box-only in `/home/box/shared/shift-watch/change.json`.\n"
            "Cal: rebuild the schedule from the calendar, then close this issue.\n\n"
            "Opened automatically by the shift watcher.")
    out = _gh(["issue", "create", "-R", REPO, "--title", "Shifts changed %s" % hhmm(t), "--body-file", "-",
               "--label", LABEL, "--assignee", ASSIGNEE], inp=body)
    return int(out.strip().splitlines()[-1].rstrip("/").split("/")[-1])


def run(cfg, dry_run=False, d=DIR, **kw):
    """Returns a dict describing what happened. gh_* are injectable for tests."""
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, ".lock"), "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"result": "locked"}
        return _run(cfg, dry_run, d, **kw)


def _run(cfg, dry_run, d, ics=None, now=None, gh_open=None, gh_create=None):
    t = now or tnow()
    gh_open = gh_open or open_issue_number
    gh_create = gh_create or create_issue
    try:
        data = ics if ics is not None else fetch_ics(cfg)
        today = t.replace(hour=0, minute=0, second=0, microsecond=0)
        shifts = parse_shifts(data, cfg, today, today + dt.timedelta(days=3650))
    except Exception as e:
        _log("fetch/parse failed, nothing changed: %s" % e, d)
        return {"result": "fetch-failed", "error": str(e)}
    new = normalize(shifts)
    upcoming = [x for x in new if parse_iso(x["end"]) >= t]
    last_p, change_p = os.path.join(d, "last.json"), os.path.join(d, "change.json")
    if not os.path.exists(last_p):
        if not dry_run:
            _atomic(last_p, {"saved": iso(t), "shifts": new})
        _log("seeded last.json with %d shifts (%d upcoming), no issue" % (len(new), len(upcoming)), d)
        return {"result": "seeded", "shifts": len(new), "upcoming": len(upcoming)}
    old = json.load(open(last_p))["shifts"]
    old_up = [x for x in old if parse_iso(x["end"]) >= t]
    if not upcoming and len(old_up) >= 3:
        _log("calendar shows no upcoming shifts but last.json had %d; treating as a bad fetch" % len(old_up), d)
        return {"result": "suspicious-empty"}
    ch = diff(old, new, t)
    if not has_changes(ch):
        if not dry_run:
            _atomic(last_p, {"saved": iso(t), "shifts": new})  # past shifts age out silently
        return {"result": "no-change", "upcoming": len(upcoming)}
    summary = "%d added, %d removed, %d moved" % (len(ch["added"]), len(ch["removed"]), len(ch["changed"]))
    if dry_run:
        _log("DRY RUN change detected (%s); would write change.json and open/keep issue" % summary, d)
        return {"result": "dry-run-change", "diff": ch}
    try:
        num = gh_open()
    except Exception as e:
        num = None
        _log("could not list open issues (%s); will try to create" % e, d)
    event = dict(ch, detected=iso(t))
    doc = None
    if num and os.path.exists(change_p):
        try:
            doc = json.load(open(change_p))
            if doc.get("issue") != num:
                doc = None
        except ValueError:
            doc = None
    if doc is None:
        doc = {"issue": num, "events": []}
    doc["events"].append(event)
    doc.update(updated=iso(t), current_shifts=upcoming)
    created = None
    if not num:
        try:
            created = gh_create(t)
            doc["issue"] = created
        except Exception as e:
            _log("issue creation failed (change.json still written): %s" % e, d)
    _atomic(change_p, doc)
    _atomic(last_p, {"saved": iso(t), "shifts": new})
    _log("change: %s; issue %s" % (summary, ("#%s opened" % created) if created else ("#%s already open" % num if num else "not opened")), d)
    return {"result": "changed", "issue": doc["issue"], "created": bool(created), "diff": ch}
