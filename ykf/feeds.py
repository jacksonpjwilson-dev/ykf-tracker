"""Free ADS-B feeds: point query around CYKF with fallback and rate limiting."""
import json
import time
import urllib.error
import urllib.request

PROVIDERS = {
    # name: (url template, list key, min seconds between calls)
    "airplanes.live": ("https://api.airplanes.live/v2/point/{lat}/{lon}/{r}", "ac", 1.0),
    "adsb.lol": ("https://api.adsb.lol/v2/point/{lat}/{lon}/{r}", "ac", 1.0),
    "adsb.fi": ("https://opendata.adsb.fi/api/v2/lat/{lat}/lon/{lon}/dist/{r}", "aircraft", 1.0),
}


class FeedError(Exception):
    pass


class Feeds:
    def __init__(self, cfg, state):
        self.cfg = cfg
        self.state = state  # dict persisted by caller: {"providers": {...}}
        self.last_call = {}

    def _pstate(self, name):
        ps = self.state.setdefault("providers", {}).setdefault(name, {})
        day = time.strftime("%Y-%m-%d")
        if ps.get("day") != day:
            ps.update(day=day, count=0)
        return ps

    def _get(self, url):
        req = urllib.request.Request(url, headers={"User-Agent": self.cfg["user_agent"],
                                                   "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.load(r)

    def fetch(self):
        """Return (provider, now_epoch, aircraft list). Raises FeedError if all fail."""
        ap = self.cfg["airport"]
        errors = []
        for name in self.cfg["providers"]:
            tpl, key, gap = PROVIDERS[name]
            ps = self._pstate(name)
            if ps.get("skip_until", 0) > time.time():
                errors.append("%s: backing off" % name)
                continue
            budget = self.cfg.get("provider_daily_budget", {}).get(name)
            if budget is not None and ps["count"] >= budget:
                errors.append("%s: daily budget used" % name)
                continue
            wait = gap - (time.time() - self.last_call.get(name, 0))
            if wait > 0:
                time.sleep(wait)
            self.last_call[name] = time.time()
            ps["count"] += 1
            try:
                data = self._get(tpl.format(lat=ap["lat"], lon=ap["lon"], r=self.cfg["query_radius_nm"]))
                ac = data.get(key)
                if ac is None:
                    raise ValueError("no '%s' key" % key)
                ps["last_ok"] = time.time()
                ps.pop("last_error", None)
                now = data.get("now") or time.time()
                now = now / 1000.0 if now > 1e11 else now  # adsb.lol/airplanes.live use ms
                return name, now, ac
            except urllib.error.HTTPError as e:
                backoff = {401: 21600, 403: 21600, 429: 600}.get(e.code, 120)
                ps["skip_until"] = time.time() + backoff
                ps["last_error"] = "HTTP %s" % e.code
                errors.append("%s: HTTP %s" % (name, e.code))
            except Exception as e:  # timeout, DNS, bad JSON
                ps["skip_until"] = time.time() + 60
                ps["last_error"] = str(e)[:120]
                errors.append("%s: %s" % (name, str(e)[:80]))
        raise FeedError("; ".join(errors))
