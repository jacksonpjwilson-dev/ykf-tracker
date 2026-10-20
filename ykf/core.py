"""Pure tracking logic: match feed aircraft to the watch list / Skycare,
decide alerts (once per aircraft per shift), build live-page rows.
No network, no AI. Everything here is unit-tested."""
import datetime as dt
import json
import os
import re

from . import skyflight
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
    shorts = doc.get("type_short", {})
    tails = {norm_tail(f["tail"]): f for f in doc["fleet"]}
    return {"hexes": hexes, "tails": tails,
            "callsign_prefixes": [p.upper() for p in doc.get("callsign_prefixes", [])],
            "name": doc.get("operator", "Skycare"), "type_short": shorts}


def sky_short_type(sky, hexid, reg, feed_type):
    f = sky["hexes"].get(hexid) or sky["tails"].get(reg) or {}
    code = f.get("icao_type") or feed_type
    return sky["type_short"].get(code, code or "")


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
    dest = ""
    for k in ("dest", "destination", "dest_icao"):
        if isinstance(a.get(k), str) and a.get(k).strip():
            dest = a[k].strip().upper()
            break
    return {"hex": hexid, "reg": reg, "flight": flight, "type": a.get("t") or "", "dest": dest,
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
    note_poll(sh, now, cfg)
    alerts = []
    seen_keys = set()
    for raw in feed_ac:
        ac = norm_aircraft(raw, ap)
        if not ac:
            continue
        w, is_sky = match(ac, watch, sky)
        if not w and not is_sky:
            continue  # overflights and everyone else: ignored entirely
        fleet = sky["hexes"].get(ac["hex"]) if not w else None
        tail = (w or {}).get("tail") or (fleet and norm_tail(fleet["tail"])) or ac["reg"] or ac["flight"] or ac["hex"]
        key = ac["hex"] or tail
        if key in seen_keys:
            continue  # same aircraft from a second query this poll
        seen_keys.add(key)
        typ = (w or {}).get("type") or ac["type"]
        eta = (w or {}).get("eta")
        rec = None
        kind = "near-10nm"
        prev = dict(sh["tracks"].get(tail) or {})
        if w:
            fire = scheduled_alert_ok(ac, radius)
            if not fire and landed_unalerted(ac, prev, sh, now, cfg):
                fire, kind = True, "landed-unalerted"
        else:
            # unscheduled Skycare: early inbound alert + 10 NM alert, each once per flight
            rec, early = skyflight.observe(state, key, ac, now, cfg)
            if early:
                short = sky_short_type(sky, ac["hex"], ac["reg"], ac["type"])
                alerts.append(skyflight.inbound_alert(rec, tail, typ, short, ac, now, shift, cfg))
            fire = skycare_inbound(ac, radius, cfg["skycare_inbound"]) and not rec["near_alerted"]
        tr = sh["tracks"].get(tail) or {}
        if rec is not None:
            showing = rec["inbound_alerted"] and not rec["turned_away"]
            if not fire and not showing and not rec["near_alerted"]:
                sh["tracks"].pop(tail, None)  # passing by / turned away: not shown
                continue
            tr["inbound"] = bool(showing and ac["dist"] > radius)
        elif w:
            tr["inbound"] = scheduled_inbound(ac, prev)
        tr.update(tail=tail, hex=ac["hex"], type=typ, eta=eta, scheduled=bool(w), skycare=is_sky,
                  dist_nm=round(ac["dist"], 1), alt_ft=ac["alt"], ground=ac["ground"],
                  rate_fpm=ac["rate"], gs_kt=ac["gs"], last_seen=iso(now))
        sh["tracks"][tail] = tr
        if rec is not None and fire:
            rec["near_alerted"] = iso(now)
        elif fire and (key in sh["alerted"] or tail in sh["alerted"]):
            fire = False
        if fire:
            sh["alerted"][key] = iso(now)
            sh["alerted"][tail] = iso(now)
            tr["alerted_at"] = iso(now)
            text = alert_text(tail, typ, ac["dist"], ac["alt"], ac["ground"], eta, is_sky,
                              ap["short"], scheduled=bool(w))
            if kind == "landed-unalerted":
                text = "%s on the ground at %s (%.1f NM), ETA %s, no 10 NM alert was sent%s" % (
                    pretty_tail(tail) + (" " + typ if typ else ""), ap["short"], ac["dist"], eta or "none",
                    _gap_note(sh, now, cfg))
            alerts.append({
                "id": "%s-%s-%s" % (shift["id"].replace(":", ""), tail, now.strftime("%H%M%S")),
                "alert_type": kind,
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


# ---------- blind spots: box pauses, feed outages, lost contact ----------

def note_poll(sh, now, cfg):
    """Record this successful poll. A gap since the previous one (box paused,
    feed down, process stalled) is kept in sh['last_gap'] so the safety net can
    say what may have been missed."""
    prev = sh.get("last_poll")
    sh["last_poll"] = iso(now)
    if not prev:
        return None
    blind = now.timestamp() - _ts(prev)
    if blind >= cfg["gap_alert_s"]:
        sh["last_gap"] = {"from": prev, "to": iso(now), "s": int(blind), "alerted": False}
        return sh["last_gap"]
    return None


def _recent_gap(sh, now, cfg):
    g = sh.get("last_gap")
    if g and now.timestamp() - _ts(g["to"]) <= cfg["gap_recent_s"]:
        return g
    return None


def _gap_note(sh, now, cfg):
    g = _recent_gap(sh, now, cfg)
    if not g:
        return ""
    return " (tracker was blind %d min, %s to %s)" % (round(g["s"] / 60.0), hhmm(_dt(g["from"])), hhmm(_dt(g["to"])))


def landed_unalerted(ac, prev, sh, now, cfg):
    """A scheduled arrival first seen ON THE GROUND at the field with no alert sent:
    we missed the approach (box paused, feed gap, low-altitude coverage hole).
    Only when it was seen airborne earlier this shift, or right after a blind gap,
    so an aircraft parked on the ramp at shift start does not alert."""
    if not ac["ground"] or ac["dist"] > cfg["landed_nm"]:
        return False
    was_airborne = bool(prev) and not prev.get("ground")
    return was_airborne or _recent_gap(sh, now, cfg) is not None


def scheduled_inbound(ac, prev):
    """Is a scheduled arrival heading for the field? Vertical rate is ignored on
    purpose: an aircraft climbing out of a nearby field toward CYKF (C-GFLZ on
    2026-10-20: +1,472 fpm at 26.6 NM, track at the field) is still inbound.
    Inbound = airborne and (pointed within 90 deg of the field, or closing)."""
    if ac["ground"]:
        return False
    if ac["off_track_deg"] is not None and ac["off_track_deg"] <= 90:
        return True
    pd = prev.get("dist_nm") if prev and not prev.get("ground") else None
    return pd is not None and ac["dist"] < pd - 0.2


def eta_datetime(shift, eta):
    """'19:30' -> aware datetime inside this shift (handles shifts past midnight)."""
    if not eta:
        return None
    h, m = map(int, eta.split(":"))
    base = shift.get("win_start") or shift["start"]
    t = shift["start"].replace(hour=h, minute=m, second=0, microsecond=0)
    if t < base - dt.timedelta(hours=2):
        t += dt.timedelta(days=1)
    return t


def _safety_alert(kind, tail, w, now, shift, text, tr=None):
    return {
        "id": "%s-%s-%s-%s" % (shift["id"].replace(":", ""), tail, kind, now.strftime("%H%M%S")),
        "alert_type": kind, "created": iso(now), "time": hhmm(now),
        "shift": {"id": shift["id"], "start": iso(shift["start"]), "end": iso(shift["end"])},
        "tail": pretty_tail(tail), "hex": (tr or {}).get("hex") or w.get("hex"), "type": w.get("type", ""),
        "distance_nm": (tr or {}).get("dist_nm"),
        "altitude_ft": ((tr or {}).get("alt_ft") if not (tr or {}).get("ground") else "ground"),
        "last_seen": (tr or {}).get("last_seen"),
        "eta": w.get("eta") or "no ETA", "has_eta": bool(w.get("eta")),
        "on_flightbridge": True, "skycare": False,
        "text": text, "sms_text": text[:160], "channel_hint": "in-app", "delivered": False,
    }


def safety_net(state, shift, watch, now, cfg):
    """Scheduled arrivals that never got a 10 NM alert. Run after step() on every
    good poll. Each kind fires at most once per arrival per shift:
      tracker-gap       the tracker was blind >= gap_alert_s and an arrival was
                        close or due during/just after the gap
      contact-lost      last seen airborne, heading in, within lost_inbound_nm,
                        and no position for lost_after_s
      eta-no-contact    within eta_warn_min of ETA and not seen inside 10 NM
    """
    sh = state.setdefault("shifts", {}).setdefault(shift["id"], {"alerted": {}, "tracks": {}})
    sn = sh.setdefault("safety", {})
    short = cfg["airport"]["short"]
    alerts = []
    pending = []
    for tail, w in watch["by_tail"].items():
        if tail in sh["alerted"] or (w.get("hex") and w["hex"] in sh["alerted"]):
            continue
        pending.append((tail, w, sh["tracks"].get(tail), sn.setdefault(tail, {})))

    g = _recent_gap(sh, now, cfg)
    if g and not g.get("alerted"):
        g["alerted"] = True
        g0, g1 = _dt(g["from"]), _dt(g["to"])
        hit = []
        for tail, w, tr, done in pending:
            eta = eta_datetime(shift, w.get("eta"))
            close = tr and not tr.get("ground") and tr["dist_nm"] <= cfg["lost_inbound_nm"] and tr.get("inbound", True)
            due = eta and g0 - dt.timedelta(minutes=30) <= eta <= g1 + dt.timedelta(minutes=60)
            if close or due:
                hit.append((tail, w, tr, done))
        if hit:
            names = "; ".join("%s%s%s" % (pretty_tail(t), " ETA " + w["eta"] if w.get("eta") else "",
                                          (", last seen %s %.0f NM out" % (hhmm(_dt(tr["last_seen"])), tr["dist_nm"]))
                                          if tr and tr.get("last_seen") else "") for t, w, tr, _ in hit)
            text = "YKF tracker was blind %d min (%s to %s), may have missed: %s" % (
                round(g["s"] / 60.0), hhmm(g0), hhmm(g1), names)
            a = _safety_alert("tracker-gap", hit[0][0], hit[0][1], now, shift, text, hit[0][2])
            a["tails"] = [pretty_tail(t) for t, _, _, _ in hit]
            alerts.append(a)
            for _, _, _, done in hit:
                done["gap"] = done["lost"] = iso(now)

    for tail, w, tr, done in pending:
        name = pretty_tail(tail) + (" " + w["type"] if w.get("type") else "")
        if tr and not tr.get("ground") and not done.get("lost"):
            age = now.timestamp() - _ts(tr["last_seen"])
            if age >= cfg["lost_after_s"] and tr["dist_nm"] <= cfg["lost_inbound_nm"] and tr.get("inbound"):
                done["lost"] = iso(now)
                text = "%s lost signal %.0f NM from %s at %s, heading in (last seen %s), ETA %s" % (
                    name, tr["dist_nm"], short, fmt_alt(tr.get("alt_ft")), hhmm(_dt(tr["last_seen"])),
                    w.get("eta") or "none")
                alerts.append(_safety_alert("contact-lost", tail, w, now, shift, text, tr))
                continue
        eta = eta_datetime(shift, w.get("eta"))
        if eta and not done.get("eta") and not done.get("gap"):
            lead = (eta - now).total_seconds() / 60.0
            fresh_inside = tr and now.timestamp() - _ts(tr["last_seen"]) < cfg["lost_after_s"] \
                and tr["dist_nm"] <= cfg["alert_radius_nm"]
            if -120 <= lead <= cfg["eta_warn_min"] and not fresh_inside:
                done["eta"] = iso(now)
                seen = ("last seen %s, %.0f NM out" % (hhmm(_dt(tr["last_seen"])), tr["dist_nm"])) if tr else "not seen on ADS-B yet"
                text = "%s due %s at %s, no 10 NM alert yet (%s)" % (name, w["eta"], short, seen)
                alerts.append(_safety_alert("eta-no-contact", tail, w, now, shift, text, tr))
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
        elif t.get("inbound"):
            status = "inbound, %d NM" % round(t["dist_nm"])
        else:
            status = "tracking"
        r = rows.get(tail) or {"tail": pretty_tail(tail), "type": t["type"], "eta": t.get("eta"),
                               "scheduled": t.get("scheduled", False)}
        r.update(dist_nm=t["dist_nm"], alt=fmt_alt(t["alt_ft"], t.get("ground")),
                 last_seen=t["last_seen"], status=status, skycare=t.get("skycare", False),
                 alerted_at=t.get("alerted_at"))
        rows[tail] = r
    order = {"inside 10 NM": 0, "inbound": 0.5, "tracking": 1, "expected": 2, "on ground": 3, "landed?": 4, "lost signal": 5}
    out = sorted(rows.values(), key=lambda r: (order.get(r["status"].split(",")[0], 9), r["dist_nm"] if r["dist_nm"] is not None else 999, r["eta"] or "99"))
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


def _dt(s):
    from .timeutil import parse_iso
    return parse_iso(s)
