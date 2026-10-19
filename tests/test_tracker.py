import datetime as dt
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ykf import config
from ykf.core import alert_text, build_watch, live_rows, load_skycare, skycare_inbound, step, norm_aircraft
from ykf.geo import distance_nm, bearing_deg, angle_diff
from ykf.regs import canada_hex, us_hex, norm_tail, pretty_tail, Resolver
from ykf.shifts import parse_shifts, current_window, https_url
from ykf.timeutil import TZ

CFG = config.load()
AP = CFG["airport"]
ROOT = config.ROOT


def ac_at(dist_nm, bearing_from_field, **kw):
    """Fake feed record dist_nm from CYKF on the given bearing (flat-earth approx, fine < 30 NM)."""
    import math
    dlat = dist_nm / 60.0 * math.cos(math.radians(bearing_from_field))
    dlon = dist_nm / 60.0 * math.sin(math.radians(bearing_from_field)) / math.cos(math.radians(AP["lat"]))
    rec = {"lat": AP["lat"] + dlat, "lon": AP["lon"] + dlon, "alt_baro": 3000, "baro_rate": -500,
           "track": (bearing_from_field + 180) % 360, "gs": 160}
    rec.update(kw)
    return rec


class Geo(unittest.TestCase):
    def test_known_distance(self):
        # CYKF -> CYYZ (43.6772,-79.6306) is about 35.3 NM
        self.assertAlmostEqual(distance_nm(AP["lat"], AP["lon"], 43.6772, -79.6306), 35.3, delta=0.3)

    def test_zero_and_symmetry(self):
        self.assertAlmostEqual(distance_nm(1, 2, 1, 2), 0.0)
        a = distance_nm(43.4, -80.3, 44.1, -79.1)
        self.assertAlmostEqual(a, distance_nm(44.1, -79.1, 43.4, -80.3))

    def test_one_degree_lat_is_60nm(self):
        self.assertAlmostEqual(distance_nm(43, -80, 44, -80), 60.0, delta=0.1)

    def test_fake_points(self):
        for d in (2, 9.6, 10.4, 20):
            r = ac_at(d, 45)
            self.assertAlmostEqual(distance_nm(r["lat"], r["lon"], AP["lat"], AP["lon"]), d, delta=0.05)

    def test_bearing(self):
        self.assertAlmostEqual(bearing_deg(43, -80, 44, -80), 0, delta=0.01)
        self.assertEqual(angle_diff(350, 10), 20)
        self.assertEqual(angle_diff(90, 270), 180)


class Regs(unittest.TestCase):
    def test_canada(self):
        # pairs seen live on 2026-10-19
        self.assertEqual(canada_hex("C-GGBN"), "c054a8")
        self.assertEqual(canada_hex("CGUSB"), "c07b4e")
        self.assertEqual(canada_hex("C-FXDP"), "c03d1a")
        self.assertEqual(canada_hex("C-FDAX"), "c00804")
        self.assertIsNone(canada_hex("C-IABC"))

    def test_us(self):
        self.assertEqual(us_hex("N1"), "a00001")
        self.assertEqual(us_hex("N2"), "a18d50")  # 0xa00001 + 101711
        self.assertIsNone(us_hex("N0123"))

    def test_norm(self):
        self.assertEqual(norm_tail(" c-ggib "), "CGGIB")
        self.assertEqual(pretty_tail("CGGIB"), "C-GGIB")

    def test_resolver_offline(self):
        with tempfile.TemporaryDirectory() as d:
            r = Resolver(os.path.join(d, "c.json"), os.path.join(d, "none"))
            self.assertEqual(r.resolve("CGGIB", online=False), (canada_hex("CGGIB"), "algorithm"))
            self.assertEqual(r.resolve("D-ABCD", online=False), (None, "unresolved"))


def shift(start="2026-10-20T15:00"):
    s = dt.datetime.fromisoformat(start).replace(tzinfo=TZ)
    return {"id": start, "start": s, "end": s + dt.timedelta(hours=10), "win_start": s - dt.timedelta(minutes=30),
            "win_end": s + dt.timedelta(hours=10)}


class Logic(unittest.TestCase):
    def setUp(self):
        self.sky = load_skycare(os.path.join(ROOT, "config", "skycare.json"))
        r = Resolver("/tmp/ykf-test-regs.json", None)
        self.watch = build_watch([{"tail": "CGGIB", "type": "Citation XLS", "eta": None},
                                  {"tail": "CFABC", "type": "PC-12", "eta": "16:10"}], r, online=False)
        self.t = dt.datetime(2026, 10, 20, 16, 0, tzinfo=TZ)

    def test_once_per_aircraft_per_shift(self):
        st = {}
        sh = shift()
        rec = ac_at(9.6, 90, hex=canada_hex("CGGIB"), r="C-GGIB", t="C56X", alt_baro=3200)
        a1 = step([rec], self.watch, self.sky, st, sh, self.t, CFG)
        self.assertEqual(len(a1), 1)
        self.assertEqual(a1[0]["text"], "C-GGIB Citation XLS 9.6 NM from YKF, 3,200 ft, no ETA")
        closer = ac_at(4, 90, hex=canada_hex("CGGIB"), r="C-GGIB", alt_baro=1800)
        for i in range(5):
            self.assertEqual(step([closer], self.watch, self.sky, st, sh, self.t + dt.timedelta(seconds=30 * (i + 1)), CFG), [])
        # new shift -> may alert again
        a3 = step([rec], self.watch, self.sky, st, shift("2026-10-21T08:00"), self.t + dt.timedelta(hours=16), CFG)
        self.assertEqual(len(a3), 1)

    def test_outside_radius_no_alert_but_tracked(self):
        st = {}
        a = step([ac_at(10.4, 0, hex=canada_hex("CFABC"))], self.watch, self.sky, st, shift(), self.t, CFG)
        self.assertEqual(a, [])
        rows = live_rows(st, shift(), self.watch, self.t, CFG)
        row = [r for r in rows if r["tail"] == "C-FABC"][0]
        self.assertEqual(row["status"], "tracking")
        self.assertEqual(row["eta"], "16:10")

    def test_eta_in_text_and_match_by_callsign_only(self):
        st = {}
        rec = ac_at(7, 200, flight="CFABC   ", alt_baro=2500)  # no hex, no r
        a = step([rec], self.watch, self.sky, st, shift(), self.t, CFG)
        self.assertEqual(len(a), 1)
        self.assertIn("ETA 16:10", a[0]["text"])

    def test_overflight_ignored(self):
        st = {}
        a = step([ac_at(1, 0, hex="c0ffee", r="C-GZZZ", alt_baro=35000, baro_rate=0)], self.watch, self.sky, st, shift(), self.t, CFG)
        self.assertEqual(a, [])
        self.assertEqual(st["shifts"][shift()["id"]]["tracks"], {})

    def test_scheduled_on_ground_or_departing_no_alert(self):
        st = {}
        g = ac_at(0.3, 0, hex=canada_hex("CGGIB"), alt_baro="ground")
        dep = ac_at(5, 90, hex=canada_hex("CGGIB"), alt_baro=3000, baro_rate=1500, track=90)
        self.assertEqual(step([g], self.watch, self.sky, st, shift(), self.t, CFG), [])
        self.assertEqual(step([dep], self.watch, self.sky, st, shift(), self.t, CFG), [])

    def test_skycare_inbound(self):
        st = {}
        rec = ac_at(8.9, 300, hex=canada_hex("CFIOH"), r="C-FIOH", t="SW4", alt_baro=3100, baro_rate=-700, flight="PHX432")
        a = step([rec], self.watch, self.sky, st, shift(), self.t, CFG)
        self.assertEqual(len(a), 1)
        self.assertTrue(a[0]["skycare"])
        self.assertFalse(a[0]["on_flightbridge"])
        self.assertEqual(a[0]["text"], "Skycare C-FIOH SW4 8.9 NM from YKF, 3,100 ft, not on FlightBridge")

    def test_skycare_by_callsign_prefix_unknown_tail(self):
        st = {}
        rec = ac_at(6, 0, hex="c0abcd", flight="PHX999", alt_baro=4000, baro_rate=-200)
        self.assertEqual(len(step([rec], self.watch, self.sky, st, shift(), self.t, CFG)), 1)

    def test_skycare_not_inbound(self):
        h = CFG["skycare_inbound"]
        R = CFG["alert_radius_nm"]
        n = lambda rec: norm_aircraft(rec, AP)
        self.assertFalse(skycare_inbound(n(ac_at(6, 0, alt_baro=28000, baro_rate=0)), R, h))  # high cruise overhead
        self.assertFalse(skycare_inbound(n(ac_at(6, 0, alt_baro=3000, baro_rate=-800, track=0)), R, h))  # heading away
        self.assertFalse(skycare_inbound(n(ac_at(5, 90, alt_baro=2500, baro_rate=1800, track=90)), R, h))  # departing climb
        self.assertFalse(skycare_inbound(n(ac_at(12, 0, alt_baro=3000, baro_rate=-800)), R, h))  # outside 10 NM
        self.assertTrue(skycare_inbound(n(ac_at(9, 0, alt_baro=12000, baro_rate=-1500)), R, h))  # descending in
        self.assertTrue(skycare_inbound(n(ac_at(2, 0, alt_baro=1800, baro_rate=0, track=0)), R, h))  # circuit near field
        self.assertFalse(skycare_inbound(n(ac_at(1, 0, alt_baro="ground")), R, h))

    def test_skycare_passing_not_shown(self):
        st = {}
        step([ac_at(6, 0, hex=canada_hex("CFIOH"), alt_baro=28000, baro_rate=0)], self.watch, self.sky, st, shift(), self.t, CFG)
        rows = live_rows(st, shift(), self.watch, self.t, CFG)
        self.assertEqual([r["tail"] for r in rows if r["tail"] == "C-FIOH"], [])

    def test_live_privacy_fields(self):
        st = {}
        step([ac_at(8, 300, hex=canada_hex("CFIOH"), alt_baro=3000, baro_rate=-700, ownOp="Some Owner")], self.watch, self.sky, st, shift(), self.t, CFG)
        rows = live_rows(st, shift(), self.watch, self.t, CFG)
        allowed = {"tail", "type", "eta", "dist_nm", "alt", "last_seen", "status", "scheduled", "alerted_at", "note"}
        for r in rows:
            self.assertTrue(set(r) <= allowed, set(r) - allowed)
        self.assertNotIn("Skycare", json.dumps(rows))
        self.assertNotIn("Owner", json.dumps(rows))


class SkycareEarly(unittest.TestCase):
    """Early 'skycare-inbound' alert heuristic."""
    def setUp(self):
        self.sky = load_skycare(os.path.join(ROOT, "config", "skycare.json"))
        self.watch = {"by_hex": {}, "by_tail": {}}
        self.t0 = dt.datetime(2026, 10, 20, 16, 0, tzinfo=TZ)
        self.hex = canada_hex("CGKKC")

    def run_seq(self, recs, st=None, sh=None, start=0):
        st = {} if st is None else st
        sh = sh or shift()
        out = []
        for i, r in enumerate(recs):
            out.append(step([r], self.watch, self.sky, st, sh, self.t0 + dt.timedelta(seconds=30 * (i + start)), CFG))
        return st, out

    def inbound_seq(self, d0=84, brg=315, n=4, gs=230, **kw):
        step_nm = gs * 30 / 3600.0
        base = dict(hex=self.hex, r="C-GKKC", flight="PHX370", t="SW4", alt_baro=14000, baro_rate=-200, gs=gs)
        base.update(kw)
        return [ac_at(d0 - i * step_nm, brg, **base) for i in range(n)]

    def kinds(self, out):
        return [[a["alert_type"] for a in o] for o in out]

    def test_inbound_fires_on_third_obs_once(self):
        st, out = self.run_seq(self.inbound_seq(n=6))
        self.assertEqual(self.kinds(out), [[], [], ["skycare-inbound"], [], [], []])
        a = out[2][0]
        self.assertEqual(a["text"], "Skycare C-GKKC (PHX370) Metro inbound to YKF: 80 NM out, ETA ~21 min, 14,000 ft")
        self.assertEqual(a["eta"], "~21 min")
        rows = live_rows(st, shift(), self.watch, self.t0 + dt.timedelta(minutes=3), CFG)
        self.assertEqual(rows[0]["status"], "inbound, 74 NM")
        self.assertEqual(rows[0]["note"], "unscheduled")
        self.assertNotIn("Skycare", json.dumps(rows))

    def test_then_10nm_alert_once_per_flight(self):
        st, _ = self.run_seq(self.inbound_seq(n=3))
        near = [ac_at(9, 315, hex=self.hex, r="C-GKKC", flight="PHX370", alt_baro=3500, baro_rate=-700, gs=150),
                ac_at(8, 315, hex=self.hex, r="C-GKKC", flight="PHX370", alt_baro=3000, baro_rate=-700, gs=150)]
        _, out = self.run_seq(near, st, start=3)
        self.assertEqual(self.kinds(out), [["near-10nm"], []])

    def test_new_flight_after_gap_can_alert_again(self):
        st, _ = self.run_seq(self.inbound_seq(n=3))
        # 2 h later (new flight): fresh inbound alert, even in the same shift
        _, out = self.run_seq(self.inbound_seq(n=3, d0=120), st, start=240)
        self.assertEqual(self.kinds(out), [[], [], ["skycare-inbound"]])

    def test_overflight_offset_track_no_alert(self):
        # parallel track passing 30 NM abeam: off-track grows from ~20 deg, cross-track 30 NM
        recs = []
        for i in range(5):
            r = ac_at(80 - i * 2, 315, hex=self.hex, alt_baro=15000, baro_rate=0, gs=240)
            r["track"] = (315 + 180 + 22) % 360
            recs.append(r)
        _, out = self.run_seq(recs)
        self.assertEqual(sum(len(o) for o in out), 0)

    def test_high_overflight_across_no_alert(self):
        recs = [ac_at(40, 0, hex=self.hex, alt_baro=28000, baro_rate=0, gs=400, track=90)] * 1
        recs = [ac_at(40, 0 + i, hex=self.hex, alt_baro=28000, baro_rate=0, gs=400, track=90) for i in range(4)]
        _, out = self.run_seq(recs)
        self.assertEqual(sum(len(o) for o in out), 0)

    def test_departure_from_cykf_no_alert(self):
        # on the ground at YKF, then airborne climbing out and later turning back toward the field far out
        recs = [ac_at(0.3, 90, hex=self.hex, alt_baro="ground", gs=0)]
        recs += [ac_at(2 + i * 2, 270, hex=self.hex, alt_baro=2000 + i * 1500, baro_rate=1500, gs=180, track=270) for i in range(3)]
        recs += self.inbound_seq(n=3, d0=30, brg=270)  # same flight swings back toward the field
        _, out = self.run_seq(recs)
        self.assertEqual([k for o in self.kinds(out) for k in o], [])

    def test_first_seen_close_and_climbing_is_departure(self):
        recs = [ac_at(5 + i * 1.5, 45, hex=self.hex, alt_baro=3000 + i * 800, baro_rate=1200, gs=180, track=45) for i in range(4)]
        st, out = self.run_seq(recs)
        self.assertTrue(st["sky_flights"][self.hex]["departed_ykf"])
        self.assertEqual(sum(len(o) for o in out), 0)

    def test_turn_away_before_three_no_alert_and_no_cancel(self):
        recs = self.inbound_seq(n=2)
        away = ac_at(83, 315, hex=self.hex, alt_baro=14000, gs=230)
        away["track"] = 315  # pointing away from the field
        recs += [away, ac_at(84, 315, hex=self.hex, alt_baro=14000, gs=230, track=315), ac_at(85, 315, hex=self.hex, alt_baro=14000, gs=230, track=315)]
        st, out = self.run_seq(recs)
        self.assertEqual(sum(len(o) for o in out), 0)

    def test_turn_away_after_alert_drops_quietly(self):
        st, out = self.run_seq(self.inbound_seq(n=3))
        recs = [ac_at(78 + i, 315, hex=self.hex, alt_baro=14000, gs=230, track=315) for i in range(3)]
        st, out2 = self.run_seq(recs, st, start=3)
        self.assertEqual(sum(len(o) for o in out2), 0)  # no 'cancelled'
        rows = live_rows(st, shift(), self.watch, self.t0 + dt.timedelta(minutes=4), CFG)
        self.assertEqual(rows, [])

    def test_descent_widens_tolerance(self):
        recs = self.inbound_seq(n=3, d0=40, baro_rate=-1200)
        for r in recs:
            r["track"] = (r["track"] + 20) % 360  # 20 deg off: only OK because descending
        _, out = self.run_seq(recs)
        self.assertEqual(self.kinds(out)[-1], ["skycare-inbound"])
        recs = self.inbound_seq(n=3, d0=40, baro_rate=0)
        for r in recs:
            r["track"] = (r["track"] + 20) % 360
        _, out = self.run_seq(recs, start=500)
        self.assertEqual(sum(len(o) for o in out), 0)

    def test_dest_field_wins(self):
        recs = [ac_at(150, 270, hex=self.hex, flight="PHX370", alt_baro=20000, gs=250, dest="CYKF")]
        recs[0]["track"] = 0  # even off-track
        _, out = self.run_seq(recs)
        self.assertEqual(self.kinds(out), [["skycare-inbound"]])

    def test_unknown_phx_callsign_counts(self):
        recs = [ac_at(60 - i * 2, 200, hex="c0beef", flight="PHX999", t="SW4", alt_baro=12000, gs=240) for i in range(3)]
        _, out = self.run_seq(recs)
        self.assertEqual(self.kinds(out)[-1], ["skycare-inbound"])

    def test_scheduled_skycare_gets_no_early_alert(self):
        r = Resolver("/tmp/ykf-test-regs.json", None)
        self.watch = build_watch([{"tail": "CGKKC", "type": "Metro", "eta": "17:00"}], r, online=False)
        _, out = self.run_seq(self.inbound_seq(n=4))
        self.assertEqual(sum(len(o) for o in out), 0)


def _ics(events):
    body = "".join("BEGIN:VEVENT\nUID:%s\nDTSTART;TZID=America/Toronto:%s\nDTEND;TZID=America/Toronto:%s\nSUMMARY:%s\nEND:VEVENT\n"
                   % e for e in events)
    return ("BEGIN:VCALENDAR\nVERSION:2.0\n" + body + "END:VCALENDAR\n").encode()


class ShiftWatch(unittest.TestCase):
    def setUp(self):
        from ykf import shiftwatch
        self.sw = shiftwatch
        self.tmp = tempfile.mkdtemp()
        self.now = dt.datetime(2026, 10, 19, 16, 0, tzinfo=TZ)
        self.base = [("a", "20261019T080000", "20261019T120000", "Chartright"),   # already ended today
                     ("b", "20261020T150000", "20261021T013000", "Chartright"),
                     ("c", "20261021T080000", "20261021T183000", "Chartright"),
                     ("d", "20261022T033000", "20261022T140000", "Chartright"),
                     ("e", "20261021T180000", "20261021T210000", "EA shift")]
        self.created = []
        self.open_num = None

    def go(self, events, now=None, dry=False):
        return self.sw.run(CFG, dry_run=dry, d=self.tmp, ics=_ics(events), now=now or self.now,
                           gh_open=lambda: self.open_num, gh_create=lambda t: (self.created.append(t) or 41))

    def test_seed_then_no_change(self):
        r = self.go(self.base)
        self.assertEqual(r["result"], "seeded")
        self.assertEqual(r["upcoming"], 3)  # EA ignored, ended shift not upcoming
        self.assertEqual(self.go(self.base)["result"], "no-change")
        self.assertEqual(self.created, [])

    def test_added_removed_moved(self):
        self.go(self.base)
        ev = [x for x in self.base if x[0] != "d"]                         # removed d
        ev = [("c", "20261021T090000", "20261021T183000", "Chartright") if x[0] == "c" else x for x in ev]  # moved c
        ev.append(("f", "20261024T050000", "20261024T153000", "Chartright"))  # added f
        r = self.go(ev)
        self.assertEqual(r["result"], "changed")
        self.assertEqual([x["uid"] for x in r["diff"]["added"]], ["f"])
        self.assertEqual([x["uid"] for x in r["diff"]["removed"]], ["d"])
        self.assertEqual(r["diff"]["changed"][0]["after"]["start"], "2026-10-21T09:00:00-04:00")
        self.assertEqual(len(self.created), 1)
        doc = json.load(open(os.path.join(self.tmp, "change.json")))
        self.assertEqual(doc["issue"], 41)
        self.assertEqual(len(doc["events"]), 1)
        # nothing new on the next run
        self.assertEqual(self.go(ev)["result"], "no-change")

    def test_past_drop_ignored(self):
        self.go(self.base)
        later = dt.datetime(2026, 10, 22, 15, 0, tzinfo=TZ)  # b, c, d all ended
        ev = [x for x in self.base if x[0] not in ("a", "b")]  # calendar drops old events
        ev.append(("g", "20261030T033000", "20261030T140000", "Chartright"))
        self.assertEqual(self.go(ev, now=later)["result"], "changed")  # g is new
        r = self.go([x for x in ev if x[0] != "c"], now=later)  # c ended: dropping it is not a change
        self.assertEqual(r["result"], "no-change")

    def test_open_issue_dedupes_and_appends(self):
        self.go(self.base)
        self.go(self.base + [("f", "20261024T050000", "20261024T153000", "Chartright")])
        self.open_num = 41  # Cal hasn't closed it yet
        r = self.go(self.base + [("f", "20261024T060000", "20261024T153000", "Chartright")])
        self.assertEqual(r["result"], "changed")
        self.assertFalse(r["created"])
        self.assertEqual(len(self.created), 1)
        doc = json.load(open(os.path.join(self.tmp, "change.json")))
        self.assertEqual(len(doc["events"]), 2)

    def test_fetch_failure_and_suspicious_empty(self):
        self.go(self.base)
        r = self.sw.run(CFG, d=self.tmp, ics=b"garbage", now=self.now, gh_open=lambda: None, gh_create=lambda t: 1 / 0)
        self.assertEqual(r["result"], "fetch-failed")
        self.assertEqual(self.go([])["result"], "suspicious-empty")
        self.assertEqual(self.created, [])

    def test_dry_run_writes_nothing(self):
        self.go(self.base)
        before = open(os.path.join(self.tmp, "last.json")).read()
        r = self.go(self.base[:3], dry=True)
        self.assertEqual(r["result"], "dry-run-change")
        self.assertEqual(open(os.path.join(self.tmp, "last.json")).read(), before)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "change.json")))
        self.assertEqual(self.created, [])


class Issues(unittest.TestCase):
    def test_batch_title_and_public_body(self):
        from ykf import github as gh
        a = [{"text": "C-GGIB Citation XLS 9.6 NM from YKF, 3,200 ft, no ETA", "tail": "C-GGIB", "type": "Citation XLS",
              "distance_nm": 9.6, "altitude_ft": 3200, "eta": "no ETA", "time": "17:45", "hex": "c05552", "id": "x",
              "shift": {"id": "s"}, "skycare": False},
             {"text": "Skycare C-FIOH SW4 8.9 NM from YKF, 3,100 ft, not on FlightBridge", "tail": "C-FIOH", "type": "SW4",
              "distance_nm": 8.9, "altitude_ft": 3100, "eta": "no ETA", "time": "17:45", "hex": "c01694", "id": "y"}]
        self.assertEqual(gh.issue_title(a[:1]), a[0]["text"])
        self.assertTrue(gh.issue_title(a).startswith("2 YKF arrivals: C-GGIB"))
        body = gh.issue_body(a)
        for bad in ("c05552", "shift", '"id"', "hex"):
            self.assertNotIn(bad, body)
        self.assertIn('"distance_nm": 8.9', body)


ICS = b"""BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:1
DTSTART;TZID=America/Toronto:20261020T150000
DTEND;TZID=America/Toronto:20261021T013000
SUMMARY:Chartright
END:VEVENT
BEGIN:VEVENT
UID:2
DTSTART;TZID=America/Toronto:20261021T180000
DTEND;TZID=America/Toronto:20261021T210000
SUMMARY:EA shift
END:VEVENT
BEGIN:VEVENT
UID:3
DTSTART;VALUE=DATE:20261023
DTEND;VALUE=DATE:20261024
SUMMARY:Chartright day off
END:VEVENT
BEGIN:VEVENT
UID:4
DTSTART;TZID=America/Toronto:20261101T050000
DTEND;TZID=America/Toronto:20261101T153000
SUMMARY:Chartright
END:VEVENT
END:VCALENDAR
"""


class Shifts(unittest.TestCase):
    def test_parse(self):
        start = dt.datetime(2026, 10, 19, tzinfo=TZ)
        s = parse_shifts(ICS, CFG, start, start + dt.timedelta(days=30))
        self.assertEqual([x["id"] for x in s], ["2026-10-20T15:00", "2026-11-01T05:00"])
        self.assertEqual(s[0]["win_start"].strftime("%H:%M"), "14:30")
        # DST end Nov 1: 05:00 is EST (-05:00)
        self.assertEqual(s[1]["start"].utcoffset(), dt.timedelta(hours=-5))
        self.assertEqual(s[1]["win_start"].isoformat(), "2026-11-01T04:30:00-05:00")

    def test_window(self):
        start = dt.datetime(2026, 10, 19, tzinfo=TZ)
        s = parse_shifts(ICS, CFG, start, start + dt.timedelta(days=30))
        at = lambda *a: current_window(s, dt.datetime(*a, tzinfo=TZ))
        self.assertIsNone(at(2026, 10, 20, 14, 29))
        self.assertEqual(at(2026, 10, 20, 14, 30)["id"], "2026-10-20T15:00")
        self.assertEqual(at(2026, 10, 21, 1, 29)["id"], "2026-10-20T15:00")
        self.assertIsNone(at(2026, 10, 21, 1, 30))
        self.assertIsNone(at(2026, 10, 21, 19, 0))  # EA shift

    def test_webcal(self):
        self.assertEqual(https_url("webcal://x.icloud.com/a"), "https://x.icloud.com/a")


if __name__ == "__main__":
    unittest.main()
