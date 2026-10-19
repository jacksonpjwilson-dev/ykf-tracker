"""Pure tracking logic: match feed aircraft to the watch list / Skycare,
decide alerts (once per aircraft per shift), build live-page rows.
No network, no AI. Everything here is unit-tested."""
import json
import os
import re

from .geo import angle_diff, bearing_deg, distance_nm
from .regs import norm_tail, pretty_tail
from .timeutil import hhmm, iso

REG_LIKE = re.compile(r"^(C[FGI][A-Z]{3}|N[1-9][0-9A-Z]{0,4})$")


# ---------- inputs ----------

def load_arrivals(path):
    """Read Sunny's arrivals.json. Returns (doc, problems). Missing file -> empty list."""
    if not os.path.exists(path):
        return {"updated": None, "shift": None, "arrivals": []}, ["arrivals.json not found"]
    try:
        doc = json.load(open(path))
    except ValueError as e:
        return {"updated": None, "shift": None, "arrivals": []}, ["arrivals.json unreadable: %s" % e]
    problems, arrivals = [], []
    for i, a in enumerate(doc.get("arrivals") or []):
        tail = norm_tail(a.get("tail"))
        if not tail:
            problems.append("arrival %d has no tail" % i)
            continue
        eta = a.get("eta")
        if eta is not None and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", str(eta)):
            problems.append("%s: bad eta %r, treated as no ETA" % (tail, eta))
            eta = None
        arrivals.append({"tail": tail, "type": (a.get("type") or "").strip(), "eta": eta})
    doc["arrivals"] = arrivals
    return doc, problems


def build_watch(arrivals, resolver, online=True):
    """-> {"by_hex": {hex: entry}, "by_tail": {tail: entry}}."""
    by_hex, by_tail = {}, {}
    for a in arrivals:
        h, src = resolver.resolve(a["tail"], online=online)
        e = dict(a, hex=h, hex_source=src)
        by_tail[a["tail"]] = e
        if h:
            by_hex[h] = e
    return {"by_hex": by_hex, "by_tail": by_tail}


def load_skycare(path):
    with open(path) as f:
        doc = json.load(f)
    hexes = {f["hex"].lower(): f for f in doc["fleet"] if f.get("hex")}
    tails = {norm_tail(f["tail"]): f for f in doc["fleet"]}
    return {"hexes": hexes, "tails": tails,
            "callsign_prefixes": [p.upper() for p in doc.get("callsign_prefixes", [])],
            "name": doc.get("operator", "Skycare")}


# ---------- feed normalisation ----------

def norm_aircraft(a, ap):
    """Feed record -> compact dict, or None if no usable position."""
    lat, lon = a.get("lat"), a.get("lon")
    if lat is None or lon is None:
        lastpos = a.get("lastPosition") or {}
        lat, lon = lastpos.get("lat"), lastpos.get("lon")
    if lat is None or lon is None:
        return None
    hexid = (a.get("hex") or "").lower()
    if hexid.startswith("~"):
        hexid = ""  # non-ICAO TIS-B address, unusable for identity
    flight = (a.get("flight") or "").strip().upper()
    reg = norm_tail(a.get("r"))
    if not reg and REG_LIKE.match(flight):
        reg = flight
    alt = a.get("alt_baro")
    ground = alt == "ground"
    if ground:
        alt = None
    elif alt is None:
        alt = a.get("alt_geom")
    rate = a.get("baro_rate", a.get("geom_rate"))
    track = a.get("track", a.get("true_heading"))
    d = distance_nm(lat, lon, ap["lat"], ap["lon"])
    brg_to_field = bearing_deg(lat, lon, ap["lat"], ap["lon"])
    toward = None if track is None else angle_diff(track, brg_to_field)
    seen = a.get("seen_pos", a.get("seen", 0)) or 0
    return {"hex": hexid, "reg": reg, "flight": flight, "type": a.get("t") or "",
            "lat": lat, "lon": lon, "alt": alt, "ground": ground, "rate": rate,
            "track": track, "gs": a.get("gs"), "dist": d, "off_track_deg": toward, "seen_s": seen}


def match(ac, watch, sky):
    """-> (watch entry or None, is_skycare)."""
    w = None
    if ac["hex"] and ac["hex"] in watch["by_hex"]:
        w = watch["by_hex"][ac["hex"]]
    elif ac["reg"] and ac["reg"] in watch["by_tail"]:
        w = watch["by_tail"][ac["reg"]]
    elif ac["flight"] and ac["flight"] in watch["by_tail"]:
        w = watch["by_tail"][ac["flight"]]
    is_sky = bool(
        (ac["hex"] and ac["hex"] in sky["hexes"])
        or (ac["reg"] and ac["reg"] in sky["tails"])
        or any(ac["flight"].startswith(p) for p in sky["callsign_prefixes"] if ac["flight"]))
    return w, is_sky


# ---------- decisions ----------

def scheduled_alert_ok(ac, radius):
    """FlightBridge arrival: alert when airborne inside radius, unless it is
    plainly departing (climbing >= 500 fpm and pointed away from the field)."""
    if ac["dist"] > radius or ac["ground"]:
        return False
    departing = (ac["rate"] or 0) >= 500 and (ac["off_track_deg"] or 0) > 90
    return not departing


def skycare_inbound(ac, radius, h):
    """Unscheduled Skycare: inside radius AND airborne AND heading roughly at the
    field (track within toward_deg of the bearing to CYKF, or within close_nm)
    AND (descending at descent_fpm or more, OR below max_alt_ft and not climbing
    faster than max_climb_fpm)."""
    if ac["dist"] > radius or ac["ground"]:
        return False
    toward = ac["dist"] <= h["close_nm"] or (ac["off_track_deg"] is not None and ac["off_track_deg"] <= h["toward_deg"])
    if not toward:
        return False
    rate = ac["rate"]
    descending = rate is not None and rate <= h["descent_fpm"]
    low = ac["alt"] is not None and ac["alt"] < h["max_alt_ft"] and (rate is None or rate <= h["max_climb_fpm"])
    return descending or low


def fmt_alt(alt, ground=False):
    if ground:
        return "on ground"
    if alt is None:
        return "alt unknown"
    return "{:,} ft".format(int(alt))


def alert_text(tail, typ, dist, alt, ground, eta, skycare, short="YKF", scheduled=True):
    parts = []
    head = pretty_tail(tail)
    if skycare:
        head = "Skycare " + head
    if typ:
        head += " " + typ
    parts.append("%s %.1f NM from %s" % (head, dist, short))
    parts.append(fmt_alt(alt, ground))
    if not scheduled:
        parts.append("not on FlightBridge")
    else:
        parts.append("ETA %s" % eta if eta else "no ETA")
    return ", ".join(parts)


def step(feed_ac, watch, sky, state, shift, now, cfg):
    """One poll. Mutates state['shifts'][shift_id]; returns list of new alert dicts."""
    ap = cfg["airport"]
    radius = cfg["alert_radius_nm"]
    sh = state.setdefault("shifts", {}).setdefault(shift["id"], {"alerted": {}, "tracks": {}})
    alerts = []
    for raw in feed_ac:
        ac = norm_aircraft(raw, ap)
        if not ac:
            continue
        w, is_sky = match(ac, watch, sky)
        if not w and not is_sky:
            continue  # overflights and everyone else: ignored entirely
        tail = (w or {}).get("tail") or ac["reg"] or ac["flight"] or ac["hex"]
        key = ac["hex"] or tail
        typ = (w or {}).get("type") or ac["type"]
        eta = (w or {}).get("eta")
        if w:
            fire = scheduled_alert_ok(ac, radius)
        else:
            fire = skycare_inbound(ac, radius, cfg["skycare_inbound"])
        tr = sh["tracks"].get(tail) or {}
        if not w and not fire and tail not in sh["tracks"]:
            continue  # Skycare passing by, not inbound: not shown
        tr.update(tail=tail, hex=ac["hex"], type=typ, eta=eta, scheduled=bool(w), skycare=is_sky,
                  dist_nm=round(ac["dist"], 1), alt_ft=ac["alt"], ground=ac["ground"],
                  rate_fpm=ac["rate"], gs_kt=ac["gs"], last_seen=iso(now))
        sh["tracks"][tail] = tr
        if fire and key not in sh["alerted"] and tail not in sh["alerted"]:
            sh["alerted"][key] = iso(now)
            sh["alerted"][tail] = iso(now)
            tr["alerted_at"] = iso(now)
            text = alert_text(tail, typ, ac["dist"], ac["alt"], ac["ground"], eta, is_sky,
                              ap["short"], scheduled=bool(w))
            alerts.append({
                "id": "%s-%s-%s" % (shift["id"].replace(":", ""), tail, now.strftime("%H%M%S")),
                "created": iso(now), "time": hhmm(now),
                "shift": {"id": shift["id"], "start": iso(shift["start"]), "end": iso(shift["end"])},
                "tail": pretty_tail(tail), "hex": ac["hex"], "type": typ,
                "distance_nm": round(ac["dist"], 1),
                "altitude_ft": "ground" if ac["ground"] else (int(ac["alt"]) if ac["alt"] is not None else None),
                "vertical_rate_fpm": ac["rate"], "groundspeed_kt": ac["gs"],
                "eta": eta or "no ETA", "has_eta": bool(eta),
                "on_flightbridge": bool(w), "skycare": is_sky,
                "text": text, "sms_text": text[:160],
                "channel_hint": "in-app", "delivered": False,
            })
    return alerts


def live_rows(state, shift, watch, now, cfg):
    """Rows for the live page. Only tail/type/distance/altitude/ETA/last seen/status."""
    sh = state.get("shifts", {}).get(shift["id"], {"tracks": {}, "alerted": {}})
    rows = {}
    for tail, w in watch["by_tail"].items():
        rows[tail] = {"tail": pretty_tail(tail), "type": w["type"], "eta": w["eta"], "dist_nm": None,
                      "alt": None, "last_seen": None, "status": "expected", "scheduled": True,
                      "skycare": False}
    for tail, t in sh["tracks"].items():
        age = now.timestamp() - _ts(t["last_seen"])
        if t.get("ground") and t["dist_nm"] <= 2:
            status = "on ground"
        elif age > cfg["lost_after_s"]:
            status = "landed?" if t.get("alerted_at") and t["dist_nm"] <= 4 else "lost signal"
        elif t["dist_nm"] <= cfg["alert_radius_nm"]:
            status = "inside 10 NM"
        else:
            status = "tracking"
        r = rows.get(tail) or {"tail": pretty_tail(tail), "type": t["type"], "eta": t.get("eta"),
                               "scheduled": t.get("scheduled", False)}
        r.update(dist_nm=t["dist_nm"], alt=fmt_alt(t["alt_ft"], t.get("ground")),
                 last_seen=t["last_seen"], status=status, skycare=t.get("skycare", False),
                 alerted_at=t.get("alerted_at"))
        rows[tail] = r
    order = {"inside 10 NM": 0, "tracking": 1, "expected": 2, "on ground": 3, "landed?": 4, "lost signal": 5}
    out = sorted(rows.values(), key=lambda r: (order.get(r["status"], 9), r["dist_nm"] if r["dist_nm"] is not None else 999, r["eta"] or "99"))
    if not cfg.get("page_show_operator"):
        for r in out:
            if r.pop("skycare", False):
                r["note"] = "unscheduled"
            if not r.get("scheduled"):
                r["note"] = "unscheduled"
    return out


def _ts(s):
    from .timeutil import parse_iso
    return parse_iso(s).timestamp()
