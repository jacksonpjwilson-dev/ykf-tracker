"""Early 'Skycare inbound' detection for unscheduled Skycare aircraft.

Per-aircraft flight records live in state["sky_flights"][hex-or-tail]. A new flight
starts when the aircraft is first seen, after a gap of more than flight_gap_min
minutes, or when it goes from on-ground to airborne.

Inbound (alert once per flight) when ALL of these hold:
  * airborne, ground speed >= min_gs_kt, more than alert_radius_nm out (inside that,
    the 10 NM alert covers it), and the flight is not a departure from CYKF;
  * either the feed's destination field says CYKF/YKF, or the last 3 observations
    (consecutive polls, about 1.5 min at 30 s) each have
      - track within track_tol_deg of the bearing to CYKF (track_tol_desc_deg if
        descending at desc_bonus_fpm or more: descent is a plus, not a requirement),
      - cross-track (closest approach on the current track) within max_cross_track_nm,
    and distance strictly decreasing across the 3.
Departure from CYKF: first airborne observation within depart_nm of CYKF and
climbing (> +300 fpm) or moving away (off-track > 90 deg, or the 2nd observation is
farther out), or the aircraft was on the ground within depart_nm just before.
No 'cancelled' messages: if it turns away before qualifying, nothing is sent; if it
turns away after, it quietly drops off the page.
"""
import math

from .regs import pretty_tail
from .timeutil import hhmm, iso


def _obs(ac, ts):
    off = ac["off_track_deg"]
    xt = None if off is None else abs(ac["dist"] * math.sin(math.radians(min(off, 90))))
    return {"ts": ts, "dist": round(ac["dist"], 2), "off": None if off is None else round(off, 1),
            "xt": None if xt is None else round(xt, 2), "rate": ac["rate"], "alt": ac["alt"],
            "gs": ac["gs"], "ground": ac["ground"]}


def _qualifies(o, h):
    if o["ground"] or o["off"] is None or o["xt"] is None:
        return False
    tol = h["track_tol_desc_deg"] if (o["rate"] is not None and o["rate"] <= h["desc_bonus_fpm"]) else h["track_tol_deg"]
    return o["off"] <= tol and o["xt"] <= h["max_cross_track_nm"] and (o["gs"] or 0) >= h["min_gs_kt"]


def observe(state, key, ac, now, cfg):
    """Update the flight record for one Skycare aircraft. Returns (record, fire_inbound)."""
    h = cfg["skycare_early"]
    ts = now.timestamp()
    flights = state.setdefault("sky_flights", {})
    rec = flights.get(key)
    new = (rec is None or ts - rec["last_ts"] > h["flight_gap_min"] * 60
           or (rec["obs"] and rec["obs"][-1]["ground"] and not ac["ground"]))
    if new:
        was_ground_near = bool(rec and rec["obs"] and rec["obs"][-1]["ground"]
                               and rec["obs"][-1]["dist"] <= h["depart_nm"] and ts - rec["last_ts"] <= h["flight_gap_min"] * 60)
        rec = {"flight_id": "%s-%d" % (key, ts), "first_seen": iso(now), "first_ts": ts, "last_ts": ts,
               "obs": [], "departed_ykf": was_ground_near, "inbound_alerted": None, "near_alerted": None,
               "turned_away": False}
        flights[key] = rec
    if rec["obs"] and rec["obs"][-1]["ts"] == ts:
        return rec, False  # already observed this poll
    o = _obs(ac, ts)
    rec["obs"] = (rec["obs"] + [o])[-6:]
    rec["last_ts"] = ts
    rec["callsign"] = ac["flight"] or rec.get("callsign")
    air = [x for x in rec["obs"] if not x["ground"]]
    # departure from CYKF?
    if len(air) >= 1 and not rec["departed_ykf"]:
        first = air[0]
        if first["dist"] <= h["depart_nm"] and len(air) <= 3:
            climbing = (first["rate"] or 0) > 300
            away = first["off"] is not None and first["off"] > 90
            farther = len(air) >= 2 and air[1]["dist"] > first["dist"]
            if climbing or away or farther:
                rec["departed_ykf"] = True
    if ac["ground"] or rec["departed_ykf"] or ac["dist"] <= cfg["alert_radius_nm"]:
        return rec, False
    last3 = rec["obs"][-3:]
    dest = (ac.get("dest") or "").upper()
    heading_in = False
    if dest:
        heading_in = dest in ("CYKF", "YKF")
    elif len(last3) == 3:
        heading_in = (all(_qualifies(x, h) for x in last3)
                      and last3[0]["dist"] > last3[1]["dist"] > last3[2]["dist"])
    # turned away: pointed well off the field on the last 3 observations
    rec["turned_away"] = len(last3) == 3 and all(x["off"] is not None and x["off"] > 45 for x in last3)
    fire = heading_in and not rec["inbound_alerted"]
    if fire:
        rec["inbound_alerted"] = iso(now)
    return rec, fire


def eta_minutes(dist_nm, gs_kt):
    if not gs_kt or gs_kt < 30:
        return None
    return max(1, int(round(dist_nm / gs_kt * 60)))


def round_alt(alt):
    return None if alt is None else int(round(alt / 100.0) * 100)


def inbound_text(tail, callsign, short_type, dist, eta_min, alt, airport_short="YKF"):
    head = "Skycare " + pretty_tail(tail)
    if callsign and callsign != tail:
        head += " (%s)" % callsign
    if short_type:
        head += " " + short_type
    parts = ["%s inbound to %s: %d NM out" % (head, airport_short, int(round(dist)))]
    if eta_min:
        parts.append("ETA ~%d min" % eta_min)
    parts.append("{:,} ft".format(round_alt(alt)) if alt is not None else "alt unknown")
    return ", ".join(parts)


def inbound_alert(rec, tail, typ, short_type, ac, now, shift, cfg):
    eta_min = eta_minutes(ac["dist"], ac["gs"])
    text = inbound_text(tail, rec.get("callsign"), short_type, ac["dist"], eta_min, ac["alt"], cfg["airport"]["short"])
    return {
        "id": "%s-inbound" % rec["flight_id"], "alert_type": "skycare-inbound",
        "created": iso(now), "time": hhmm(now),
        "shift": {"id": shift["id"], "start": iso(shift["start"]), "end": iso(shift["end"])},
        "tail": pretty_tail(tail), "hex": ac["hex"], "callsign": rec.get("callsign"), "type": typ,
        "distance_nm": int(round(ac["dist"])), "altitude_ft": round_alt(ac["alt"]),
        "vertical_rate_fpm": ac["rate"], "groundspeed_kt": ac["gs"],
        "eta": ("~%d min" % eta_min) if eta_min else "no ETA", "has_eta": bool(eta_min), "eta_minutes": eta_min,
        "on_flightbridge": False, "skycare": True, "flight_id": rec["flight_id"],
        "text": text, "sms_text": text[:160], "channel_hint": "in-app", "delivered": False,
    }


def prune(state, now, keep_h=24):
    f = state.get("sky_flights", {})
    for k in list(f):
        if now.timestamp() - f[k]["last_ts"] > keep_h * 3600:
            del f[k]
