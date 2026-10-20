import datetime as dt
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ykf import config
from ykf.core import alert_text, build_watch, live_rows, load_skycare, safety_net, skycare_inbound, step, norm_aircraft
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
        self.assertEqual(row["status"], "inbound, 10 NM")  # pointed at the field
        self.assertEqual(row["eta"], "16:10")
        st = {}
        away = ac_at(10.4, 0, hex=canada_hex("CFABC"), track=0, baro_rate=1500)
        self.assertEqual(step([away], self.watch, self.sky, st, shift(), self.t, CFG), [])
        row = [r for r in live_rows(st, shift(), self.watch, self.t, CFG) if r["tail"] == "C-FABC"][0]
        self.assertEqual(row["status"], "tracking")

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


def _gflz(lat, lon, alt, gs, trk, rate):
    """Real C-GFLZ positions from the public adsb.fi / airplanes.live traces, 2026-10-20."""
    return {"hex": "c05314", "r": "C-GFLZ", "t": "B350", "lat": lat, "lon": lon, "alt_baro": alt,
            "gs": gs, "track": trk, "baro_rate": rate}


class MissedArrival(unittest.TestCase):
    """Incident 2026-10-20: C-GFLZ (B300, ETA 19:30) landed CYKF ~18:20 with no alert.
    Box paused 18:08-18:22, so no poll ran while it crossed 10 NM; first poll after
    resume saw it (if at all) on the ground."""

    def setUp(self):
        self.sky = load_skycare(os.path.join(ROOT, "config", "skycare.json"))
        r = Resolver("/tmp/ykf-test-regs.json", None)
        self.watch = build_watch([{"tail": "CGFLZ", "type": "B300", "eta": "19:30"}], r, online=False)
        self.sh = shift()

    def at(self, hhmmss):
        h, m, s_ = map(int, hhmmss.split(":"))
        return dt.datetime(2026, 10, 20, h, m, s_, tzinfo=TZ)

    def poll(self, st, t, feed):
        return step(feed, self.watch, self.sky, st, self.sh, t, CFG) + safety_net(st, self.sh, self.watch, t, CFG)

    def climb_leg(self, st):
        a = self.poll(st, self.at("18:06:15"), [_gflz(43.338913, -79.707855, 3350, 231.8, 241.1, 1184)])
        a += self.poll(st, self.at("18:07:37"), [_gflz(43.296654, -79.811881, 5300, 225.1, 241.0, 1472)])
        return a

    def test_climb_toward_field_counts_as_inbound(self):
        st = {}
        self.assertEqual(self.climb_leg(st), [])  # 26.6 NM, too far for an alert
        tr = st["shifts"][self.sh["id"]]["tracks"]["CGFLZ"]
        self.assertTrue(tr["inbound"])
        self.assertEqual(tr["rate_fpm"], 1472)
        row = live_rows(st, self.sh, self.watch, self.at("18:07:40"), CFG)[0]
        self.assertTrue(row["status"].startswith("inbound"))

    def test_climbing_inside_radius_toward_field_still_alerts(self):
        st = {}
        a = self.poll(st, self.at("18:12:30"), [_gflz(43.335571, -80.25353, 5550, 263.5, 266.3, 1472)])
        self.assertEqual([x["alert_type"] for x in a], ["near-10nm"])

    def test_no_gap_normal_alert(self):
        st = {}
        self.climb_leg(st)
        a = self.poll(st, self.at("18:08:07"), [_gflz(43.29, -79.83, 5575, 246, 243, 0)])
        a += self.poll(st, self.at("18:12:30"), [_gflz(43.335571, -80.25353, 5550, 263.5, 266.3, -160)])
        self.assertEqual([x["alert_type"] for x in a], ["near-10nm"])

    def test_gap_then_on_ground_fires_landed(self):
        st = {}
        self.climb_leg(st)
        a = self.poll(st, self.at("18:22:42"), [_gflz(43.458389, -80.38444, "ground", 12.8, 295.3, None)])
        kinds = sorted(x["alert_type"] for x in a)
        self.assertEqual(kinds, ["landed-unalerted"])  # landed marks it alerted; no extra gap noise
        self.assertIn("on the ground at YKF", a[0]["text"])
        self.assertIn("blind 15 min, 18:07 to 18:22", a[0]["text"])
        # never again this shift
        self.assertEqual(self.poll(st, self.at("18:23:12"), [_gflz(43.4586, -80.3848, "ground", 14, 309, None)]), [])

    def test_gap_and_aircraft_not_on_feed_fires_gap_alert_once(self):
        st = {}
        self.climb_leg(st)
        a = self.poll(st, self.at("18:22:42"), [])
        self.assertEqual([x["alert_type"] for x in a], ["tracker-gap"])
        self.assertIn("blind 15 min (18:07 to 18:22)", a[0]["text"])
        self.assertIn("C-GFLZ ETA 19:30, last seen 18:07 27 NM out", a[0]["text"])
        # no contact-lost or eta duplicate for the same arrival afterwards
        for i in range(1, 200):
            self.assertEqual(self.poll(st, self.at("18:22:42") + dt.timedelta(seconds=30 * i), []), [])

    def test_contact_lost_without_gap(self):
        st = {}
        self.climb_leg(st)
        a = []
        t = self.at("18:07:37")
        for i in range(1, 12):  # polls keep running, aircraft just vanishes from the feed
            a += self.poll(st, t + dt.timedelta(seconds=30 * i), [])
        self.assertEqual([x["alert_type"] for x in a], ["contact-lost"])
        self.assertIn("lost signal 27 NM from YKF at 5,300 ft, heading in (last seen 18:07), ETA 19:30", a[0]["text"])

    def test_departing_outbound_loss_does_not_alert(self):
        st = {}
        away = _gflz(43.30, -79.81, 5300, 225, 61, 1472)  # same spot, flying away from CYKF
        self.poll(st, self.at("18:07:37"), [away])
        self.poll(st, self.at("18:08:07"), [_gflz(43.32, -79.75, 5800, 225, 61, 1400)])
        a = []
        for i in range(1, 12):
            a += self.poll(st, self.at("18:08:07") + dt.timedelta(seconds=30 * i), [])
        self.assertEqual(a, [])

    def test_eta_near_not_seen(self):
        st = {}
        a = []
        t = self.at("19:00:00")
        for i in range(0, 40):
            a += self.poll(st, t + dt.timedelta(seconds=30 * i), [])
        self.assertEqual([x["alert_type"] for x in a], ["eta-no-contact"])
        self.assertEqual(a[0]["time"], "19:10")  # never seen: 20 min heads-up
        self.assertEqual(a[0]["text"], "C-GFLZ B300 due 19:30 at YKF, ETA-based only, not visible on ADS-B")
        self.assertEqual(a[0]["basis"], "eta-only")

    def test_eta_seen_then_lost_fires_15_min_before(self):
        st = {}
        self.poll(st, self.at("18:40:00"), [_gflz(43.296654, -79.811881, 9000, 225, 241, 0)])
        a = []
        for i in range(1, 80):
            a += self.poll(st, self.at("18:40:00") + dt.timedelta(seconds=30 * i), [])
        kinds = [(x["alert_type"], x["time"]) for x in a]
        self.assertEqual(kinds, [("contact-lost", "18:45")])  # one alert: lost covers the ETA warning
        # seen far out (beyond lost_inbound_nm), then nothing: ETA warning 15 min before
        st, a = {}, []
        far = _gflz(44.2, -78.9, 15000, 250, 241, 0)  # ~78 NM
        self.poll(st, self.at("18:40:00"), [far])
        for i in range(1, 80):
            a += self.poll(st, self.at("18:40:00") + dt.timedelta(seconds=30 * i), [])
        self.assertEqual([(x["alert_type"], x["time"]) for x in a], [("eta-no-contact", "19:15")])
        self.assertIn("due 19:30 at YKF, no 10 NM alert yet, last seen 18:40, 78 NM out", a[0]["text"])
        self.assertEqual(a[0]["basis"], "last-position")

    def test_eta_visible_inbound_outside_10nm_no_eta_alert(self):
        st = {}
        a = []
        for i in range(0, 40):  # on time, visible at ~30 NM all along the warn window
            a += self.poll(st, self.at("19:00:00") + dt.timedelta(seconds=30 * i),
                           [_gflz(43.296654, -79.811881, 9000, 225, 241, 0)])
        self.assertEqual(a, [])

    def test_late_add_with_past_eta_no_alert(self):
        """2026-10-20: C-GQBF appeared in arrivals.json at 18:37 with ETA 17:36, and
        C-GFLZ's ETA was moved from 19:30 to 18:20 at the same write. Issue #3 then
        reported both as 'no 10 NM alert yet' after they were on the ramp."""
        r = Resolver("/tmp/ykf-test-regs.json", None)
        st = {}
        w1 = build_watch([{"tail": "CGFLZ", "type": "B300", "eta": "19:30"}], r, online=False)
        safety_net(st, self.sh, w1, self.at("18:31:40"), CFG)
        w2 = build_watch([{"tail": "CGQBF", "type": "Canadair CL-415", "eta": "17:36"},
                          {"tail": "CGFLZ", "type": "B300", "eta": "18:20"}], r, online=False)
        a = []
        for i in range(0, 20):
            a += safety_net(st, self.sh, w2, self.at("18:38:11") + dt.timedelta(seconds=30 * i), CFG)
        self.assertEqual(a, [])
        sn = st["shifts"][self.sh["id"]]["safety"]
        self.assertTrue(sn["CGQBF"]["late_add"] and sn["CGFLZ"]["late_add"])
        # an ETA that is moved LATER (delay) re-arms the warning
        w3 = build_watch([{"tail": "CGQBF", "type": "Canadair CL-415", "eta": "19:05"}], r, online=False)
        a = safety_net(st, self.sh, w3, self.at("18:50:00"), CFG)
        self.assertEqual([x["alert_type"] for x in a], ["eta-no-contact"])

    def test_no_adsb_flag_says_eta_only(self):
        from ykf.core import load_arrivals
        r = Resolver("/tmp/ykf-test-regs.json", None)
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "arrivals.json")
            json.dump({"updated": "2026-10-20T15:00:00-04:00", "arrivals": [
                {"tail": "C-GQBF", "type": "CL-415", "eta": "17:36", "adsb": False}]}, open(p, "w"))
            doc, _ = load_arrivals(p)
        self.assertIs(doc["arrivals"][0]["adsb"], False)
        w = build_watch(doc["arrivals"], r, online=False)
        st, a = {}, []
        for i in range(0, 40):
            a += safety_net(st, self.sh, w, self.at("17:00:00") + dt.timedelta(seconds=30 * i), CFG)
        self.assertEqual([(x["time"], x["text"]) for x in a],
                         [("17:16", "C-GQBF CL-415 due 17:36 at YKF, ETA-based only, this aircraft doesn't broadcast ADS-B")])
        # same via config list
        w = build_watch([{"tail": "CGQBF", "type": "CL-415", "eta": "17:36"}], r, online=False)
        a = safety_net({}, self.sh, w, self.at("17:16:00"), dict(CFG, no_adsb_tails=["C-GQBF"]))
        self.assertIn("doesn't broadcast ADS-B", a[0]["text"])
        self.assertFalse(a[0]["adsb_visible"])

    def test_catchup_stops_for_long_lost_track(self):
        from ykf import run as R
        with tempfile.TemporaryDirectory() as d:
            cfg = config.load(shared=d)
            tr = R.Tracker(cfg, online=False)
            tr.watch = self.watch
            st = tr.state
            self.climb_leg(st)
            sh = st["shifts"][self.sh["id"]]
            sh["last_poll"] = "2026-10-20T18:20:00-04:00"  # no gap
            self.assertTrue(tr._needs_catchup(self.sh, self.at("18:20:30")))
            sh["last_poll"] = "2026-10-20T18:45:00-04:00"
            self.assertFalse(tr._needs_catchup(self.sh, self.at("18:45:30")))  # 38 min since last seen

    def test_eta_no_alert_if_already_alerted(self):
        st = {}
        self.poll(st, self.at("19:15:00"), [_gflz(43.335571, -80.25353, 5550, 263.5, 266.3, -160)])
        self.assertEqual(self.poll(st, self.at("19:25:00"), []), [])

    def test_parked_at_field_at_shift_start_no_alert(self):
        st = {}
        a = self.poll(st, self.at("15:00:00"), [_gflz(43.4596, -80.3980, "ground", 0, 64, None)])
        a += self.poll(st, self.at("15:00:30"), [_gflz(43.4596, -80.3980, "ground", 0, 64, None)])
        self.assertEqual(a, [])

    def test_eta_datetime_past_midnight(self):
        from ykf.core import eta_datetime
        sh = shift("2026-10-20T15:00")
        self.assertEqual(eta_datetime(sh, "19:30").isoformat(), "2026-10-20T19:30:00-04:00")
        self.assertEqual(eta_datetime(sh, "00:45").isoformat(), "2026-10-21T00:45:00-04:00")
        self.assertEqual(eta_datetime(sh, "14:50").isoformat(), "2026-10-20T14:50:00-04:00")


class FeedsRobust(unittest.TestCase):
    def test_403_falls_back_and_total_timeout(self):
        import urllib.error
        from ykf import feeds as F
        calls = []

        class Resp:
            headers = {}
            def __init__(self, body): self.body = body
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self, n=-1):
                b, self.body = self.body, b""
                return b

        def fake_urlopen(req, timeout=None):
            calls.append((req.full_url, timeout))
            if "airplanes.live" in req.full_url:
                raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, None)
            return Resp(b'{"ac": [{"hex": "c05314"}], "now": 1792534000000}')

        real = F.urllib.request.urlopen
        F.urllib.request.urlopen = fake_urlopen
        try:
            st = {}
            fd = F.Feeds(dict(CFG, providers=["airplanes.live", "adsb.lol", "adsb.fi"]), st)
            name, _, ac = fd.fetch()
            self.assertEqual(name, "adsb.lol")
            self.assertEqual(st["providers"]["airplanes.live"]["last_error"], "HTTP 403")
            self.assertIn("airplanes.live: HTTP 403", fd.last_errors)
            self.assertEqual(calls[0][1], CFG["feed_timeout_s"])
            # second call skips the 403 provider without hitting it
            n0 = len(calls)
            fd.fetch()
            self.assertEqual(len(calls), n0 + 1)
            # all-provider hex lookup merges what any feed sees
            got = fd.fetch_hexes_all(["c05314"])
            self.assertEqual([a["hex"] for a in got], ["c05314"])
        finally:
            F.urllib.request.urlopen = real

    def test_slow_drip_response_is_cut_off(self):
        from ykf import feeds as F
        import time as _t

        class Drip:
            headers = {}
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self, n=-1):
                _t.sleep(0.3)
                return b" "

        real = F.urllib.request.urlopen
        F.urllib.request.urlopen = lambda req, timeout=None: Drip()
        try:
            fd = F.Feeds(dict(CFG, providers=["adsb.fi"], feed_total_timeout_s=1), {})
            t0 = _t.monotonic()
            with self.assertRaises(F.FeedError):
                fd.fetch()
            self.assertLess(_t.monotonic() - t0, 3)
        finally:
            F.urllib.request.urlopen = real


class Watchdog(unittest.TestCase):
    def test_feed_down_alerts_once_then_recovers(self):
        from ykf import run as R
        with tempfile.TemporaryDirectory() as d:
            cfg = config.load(shared=d)
            cfg = dict(cfg, publish=dict(cfg["publish"], enabled=False), issues=dict(cfg["issues"], enabled=False))
            tr = R.Tracker(cfg, online=False)
            sh = shift()
            t = dt.datetime(2026, 10, 20, 18, 10, tzinfo=TZ)
            tr.feed_ok_ts -= 200
            self.assertEqual(tr.feed_watchdog(sh, t, False, "x"), [])
            tr.feed_ok_ts -= 200
            a = tr.feed_watchdog(sh, t, False, "adsb.lol: timed out")
            self.assertEqual([x["alert_type"] for x in a], ["tracker-health"])
            self.assertIn("no ADS-B data for 7 min", a[0]["text"])
            self.assertEqual(tr.feed_watchdog(sh, t, False, "x"), [])
            self.assertEqual(tr.feed_watchdog(sh, t, True), [])
            self.assertFalse(tr.feed_down_alerted)


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
