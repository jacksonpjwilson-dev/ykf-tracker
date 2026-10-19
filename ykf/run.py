"""CLI for the CYKF tracker.

  python3 -m ykf.run windows [--n 5]       next Chartright windows from the calendar
  python3 -m ykf.run once [--force] [--polls N]   poll now (needs an active window unless --force)
  python3 -m ykf.run supervise             run forever: sleep outside windows, poll every 30 s inside
  python3 -m ykf.run resolve C-GGIB ...    tail -> ICAO hex
  python3 -m ykf.run refresh-ccar          download the Transport Canada CCAR (weekly)

Common flags: --shared DIR (redirect arrivals/alerts/state/live, for dry runs),
              --config FILE, --offline (no online reg lookups)
No AI anywhere in this loop. Alerts are files in alerts/pending/ for Felix to deliver.
"""
import argparse
import datetime as dt
import fcntl
import json
import os
import sys
import time
import urllib.request
import zipfile

from . import config as config_mod
from .core import build_watch, live_rows, load_arrivals, load_skycare, step
from .feeds import FeedError, Feeds
from . import github as gh
from .regs import Resolver
from .shifts import current_window, fetch_calendar, next_window, parse_shifts, shift_public
from .timeutil import iso, label, now as tnow

CCAR_URL = "https://wwwapps.tc.gc.ca/Saf-Sec-Sur/2/CCARCS-RIACC/download/ccarcsdb.zip"


def log(cfg, msg):
    line = "%s %s" % (iso(tnow()), msg)
    print(line, flush=True)
    try:
        os.makedirs(os.path.dirname(cfg["paths"]["log"]), exist_ok=True)
        with open(cfg["paths"]["log"], "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def atomic_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=1, default=str)
    os.replace(tmp, path)


def load_state(cfg):
    p = cfg["paths"]["state"]
    if os.path.exists(p):
        try:
            return json.load(open(p))
        except ValueError:
            pass
    return {"shifts": {}, "providers": {}}


def prune_state(state, keep_ids):
    for k in list(state.get("shifts", {})):
        if k not in keep_ids:
            del state["shifts"][k]


def http_json(cfg):
    def get(url):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": cfg["user_agent"]})
            return json.load(urllib.request.urlopen(req, timeout=15))
        except Exception:
            return None
    return get


class Tracker:
    def __init__(self, cfg, online=True):
        self.cfg = cfg
        self.online = online
        self.resolver = Resolver(cfg["paths"]["regs_cache"], cfg["paths"]["ccar_dir"], http_json(cfg))
        self.sky = load_skycare(cfg["paths"]["skycare"])
        self.state = load_state(cfg)
        self.feeds = Feeds(cfg, self.state)
        self._arr_mtime = None
        self.arrivals_doc = {"arrivals": []}
        self.watch = {"by_hex": {}, "by_tail": {}}
        self.problems = []
        self.last_publish = 0
        self.last_fleet = 0
        self.issue_dry_run = False

    def reload_arrivals(self):
        p = self.cfg["paths"]["arrivals"]
        m = os.path.getmtime(p) if os.path.exists(p) else None
        if m != self._arr_mtime:
            self._arr_mtime = m
            self.arrivals_doc, self.problems = load_arrivals(p)
            self.watch = build_watch(self.arrivals_doc["arrivals"], self.resolver, online=self.online)
            log(self.cfg, "watch list: %d arrivals (%s)%s" % (
                len(self.watch["by_tail"]),
                ", ".join("%s=%s" % (t, e["hex"] or "?") for t, e in self.watch["by_tail"].items()),
                (" problems: " + "; ".join(self.problems)) if self.problems else ""))

    def poll(self, shift):
        self.reload_arrivals()
        t = tnow()
        try:
            provider, _, ac = self.feeds.fetch()
            feed_status = {"provider": provider, "ok": True, "count": len(ac)}
            if time.time() - self.last_fleet >= self.cfg["fleet_lookup_every_s"]:
                self.last_fleet = time.time()
                try:
                    _, _, fac = self.feeds.fetch_hexes(self.sky["hexes"].keys())
                    have = {a.get("hex") for a in ac}
                    ac = ac + [a for a in fac if a.get("hex") not in have]
                    feed_status["fleet"] = len(fac)
                except FeedError as e:
                    log(self.cfg, "fleet lookup error: %s" % e)
        except FeedError as e:
            provider, ac = None, []
            feed_status = {"provider": None, "ok": False, "error": str(e)}
            log(self.cfg, "feed error: %s" % e)
        alerts = step(ac, self.watch, self.sky, self.state, shift, t, self.cfg) if provider else []
        for a in alerts:  # pending files first: always written, even if GitHub is down
            atomic_json(os.path.join(self.cfg["paths"]["alerts_pending"], a["id"] + ".json"), a)
            log(self.cfg, "ALERT %s" % a["text"])
        if alerts:
            self.open_issue(alerts)
        doc = self.write_live(shift, t, feed_status)
        if alerts or time.time() - self.last_publish >= self.cfg["publish"]["every_s"]:
            self.publish(doc)
        atomic_json(self.cfg["paths"]["state"], self.state)
        return alerts, feed_status

    def open_issue(self, alerts):
        if not self.cfg["issues"]["enabled"] and not self.issue_dry_run:
            return None
        try:
            num, url = gh.create_issue(self.cfg, gh.issue_title(alerts), gh.issue_body(alerts),
                                       dry_run=self.issue_dry_run)
            if num:
                log(self.cfg, "issue #%s opened for %d alert(s)" % (num, len(alerts)))
                for a in alerts:
                    a["issue"] = num
                    atomic_json(os.path.join(self.cfg["paths"]["alerts_pending"], a["id"] + ".json"), a)
            return num
        except Exception as e:
            log(self.cfg, "issue creation failed (pending files kept): %s" % e)
            return None

    def publish(self, doc):
        if not self.cfg["publish"]["enabled"]:
            return False
        self.last_publish = time.time()
        try:
            gh.publish_live(self.cfg, doc)
            return True
        except Exception as e:
            log(self.cfg, "publish failed: %s" % e)
            return False

    def write_live(self, shift, t, feed_status, active=True, next_shift=None):
        upd = self.arrivals_doc.get("updated")
        stale = True
        try:
            from .timeutil import parse_iso
            stale = (t - parse_iso(upd)).total_seconds() > 3 * 3600  # hourly rewrites expected
        except Exception:
            pass
        doc = {
            "generated": iso(t), "airport": self.cfg["airport"]["icao"],
            "radius_nm": self.cfg["alert_radius_nm"], "active": active,
            "shift": shift_public(shift) if shift else None,
            "arrivals_updated": upd, "arrivals_stale": bool(active and stale),
            "feed": {k: v for k, v in feed_status.items() if k in ("provider", "ok")},
            "next_window": shift_public(next_shift) if next_shift else None,
            "rows": live_rows(self.state, shift, self.watch, t, self.cfg) if shift else [],
        }
        if not active:
            doc.update(shift=None, arrivals_updated=None, arrivals_stale=False, feed=None)
        for key in ("live_json", "web_live_json"):
            if self.cfg["paths"].get(key):
                atomic_json(self.cfg["paths"][key], doc)
        return doc


def cmd_windows(cfg, n):
    shifts = parse_shifts(fetch_calendar(cfg, force=True), cfg)
    t = tnow()
    cur = current_window(shifts, t)
    if cur:
        print("NOW in window: shift %s–%s (window from %s)" % (label(cur["start"]), cur["end"].strftime("%H:%M"), cur["win_start"].strftime("%H:%M")))
    upcoming = [s for s in shifts if s["win_start"] > t][:n]
    for s in upcoming:
        print("shift %s – %s | tracker window %s → %s" % (
            label(s["start"]), label(s["end"]), iso(s["win_start"]), iso(s["win_end"])))
    return upcoming


def cmd_once(cfg, force, online, polls=1, issue_dry_run=False):
    shifts = parse_shifts(fetch_calendar(cfg), cfg)
    t = tnow()
    shift = current_window(shifts, t)
    if not shift:
        if not force:
            nxt = next_window(shifts, t)
            print("Not in a Chartright window. Next: %s" % (iso(nxt["win_start"]) if nxt else "none found"))
            return []
        shift = {"id": "dryrun-" + t.strftime("%Y-%m-%dT%H:%M"), "start": t, "end": t + dt.timedelta(hours=1),
                 "win_start": t, "win_end": t + dt.timedelta(hours=1), "summary": "dry run"}
    tr = Tracker(cfg, online=online)
    tr.issue_dry_run = issue_dry_run
    out = []
    for i in range(polls):
        if i:
            time.sleep(cfg["poll_seconds"])
        alerts, fs = tr.poll(shift)
        out += alerts
        print(json.dumps({"poll": i + 1, "time": iso(tnow()), "feed": fs, "new_alerts": [a["text"] for a in alerts]}))
    return out


def cmd_supervise(cfg, online):
    os.makedirs(cfg["paths"]["shared"], exist_ok=True)
    lockf = open(cfg["paths"]["lock"], "w")
    try:
        fcntl.flock(lockf, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("another tracker is already running", file=sys.stderr)
        return 1
    tr = Tracker(cfg, online=online)
    active_id = None
    idle_published = None  # next-window id last published while idle
    log(cfg, "supervisor started")
    while True:
        try:
            shifts = parse_shifts(fetch_calendar(cfg), cfg)
        except Exception as e:
            log(cfg, "calendar error: %s" % e)
            time.sleep(600)
            continue
        t = tnow()
        shift = current_window(shifts, t)
        if not shift:
            nxt = next_window(shifts, t)
            if active_id:
                log(cfg, "window for shift %s ended" % active_id)
                active_id = None
                idle_published = None
            nid = nxt["id"] if nxt else "none"
            if idle_published != nid:
                doc = tr.write_live(None, t, {"provider": None, "ok": True}, active=False, next_shift=nxt)
                if tr.publish(doc) or not cfg["publish"]["enabled"]:
                    idle_published = nid
                log(cfg, "idle until %s" % (iso(nxt["win_start"]) if nxt else "no window found"))
            prune_state(tr.state, {s["id"] for s in shifts if s["end"] > t - dt.timedelta(days=2)})
            from .skyflight import prune as prune_flights
            prune_flights(tr.state, t)
            atomic_json(cfg["paths"]["state"], tr.state)
            secs = 600 if not nxt else max(5, min(600, (nxt["win_start"] - t).total_seconds()))
            time.sleep(secs)  # cheap idle; recheck calendar at most every 10 min
            continue
        if shift["id"] != active_id:
            active_id = shift["id"]
            tr.last_publish = 0  # publish on the first poll of the window
            log(cfg, "window open for shift %s (%s–%s)" % (shift["id"], shift["start"].strftime("%H:%M"), shift["end"].strftime("%H:%M")))
        started = time.time()
        try:
            tr.poll(shift)
        except Exception as e:  # never die mid-shift
            log(cfg, "poll error: %r" % e)
        time.sleep(max(1, cfg["poll_seconds"] - (time.time() - started)))


def cmd_refresh_ccar(cfg, force=False):
    d = cfg["paths"]["ccar_dir"]
    os.makedirs(d, exist_ok=True)
    stamp = os.path.join(d, ".downloaded")
    if not force and os.path.exists(stamp) and time.time() - os.path.getmtime(stamp) < 7 * 86400:
        print("CCAR cache is less than a week old")
        return 0
    zpath = os.path.join(d, "ccarcsdb.zip")
    try:
        req = urllib.request.Request(CCAR_URL, headers={"User-Agent": cfg["user_agent"]})
        with urllib.request.urlopen(req, timeout=300) as r, open(zpath + ".tmp", "wb") as f:
            f.write(r.read())
        os.replace(zpath + ".tmp", zpath)
        zipfile.ZipFile(zpath).extractall(d)
        open(stamp, "w").write(iso(tnow()))
        print("CCAR downloaded to", d)
        return 0
    except Exception as e:
        print("CCAR download failed (%s); algorithmic C-F/C-G mapping and online lookups still work" % e)
        return 2


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["windows", "once", "supervise", "resolve", "refresh-ccar", "test-issue", "publish-idle"])
    ap.add_argument("--dry-run", action="store_true", help="test-issue/once: print the gh command, create nothing")
    ap.add_argument("args", nargs="*")
    ap.add_argument("--shared")
    ap.add_argument("--config")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--n", type=int, default=5)
    ap.add_argument("--polls", type=int, default=1, help="once: poll N times, poll_seconds apart, same shift")
    a = ap.parse_args(argv)
    cfg = config_mod.load(a.config, a.shared)
    if a.shared or a.dry_run:  # dry runs never touch GitHub
        cfg = dict(cfg, publish=dict(cfg["publish"], enabled=False), issues=dict(cfg["issues"], enabled=False))
    if a.cmd == "windows":
        cmd_windows(cfg, a.n)
    elif a.cmd == "once":
        cmd_once(cfg, a.force, not a.offline, a.polls, a.dry_run)
    elif a.cmd == "supervise":
        return cmd_supervise(cfg, not a.offline)
    elif a.cmd == "resolve":
        r = Resolver(cfg["paths"]["regs_cache"], cfg["paths"]["ccar_dir"], http_json(cfg))
        for t in a.args:
            print(t, *r.resolve(t, online=not a.offline))
    elif a.cmd == "test-issue":
        sample = {"tail": "C-GTST", "type": "TEST", "distance_nm": 9.9, "altitude_ft": 3000,
                  "eta": "no ETA", "time": tnow().strftime("%H:%M"),
                  "text": "TEST alert, ignore"}
        body = ("TEST alert, ignore. Sent by hand to check the Tower listener; not a real aircraft.\n\n"
                + gh.issue_body([sample]))
        num, url = gh.create_issue(cfg, "TEST alert, ignore", body, dry_run=a.dry_run)
        print("created #%s %s" % (num, url) if num else "dry run only, nothing created")
    elif a.cmd == "publish-idle":
        shifts = parse_shifts(fetch_calendar(cfg), cfg)
        t = tnow()
        tr = Tracker(cfg, online=False)
        doc = tr.write_live(None, t, {"provider": None, "ok": True}, active=False, next_shift=next_window(shifts, t))
        print("published" if (a.dry_run or tr.publish(doc)) else "publish failed", doc["next_window"])
    elif a.cmd == "refresh-ccar":
        return cmd_refresh_ccar(cfg, a.force)
    return 0


if __name__ == "__main__":
    sys.exit(main())
