"""Registration (tail) <-> ICAO 24-bit hex.

Order of resolution (see resolve_hex):
 1. regs_cache.json overrides (manual, or filled by earlier lookups)
 2. Transport Canada CCAR cache (cache/ccar/carscurr.txt), if present
 3. Algorithmic mapping for C-Fxxx / C-Gxxx and US N-numbers
 4. Online fallback: airplanes.live /v2/reg/ then adsb.lol /v2/reg/
    (only finds aircraft currently being tracked)
The tracker also matches live aircraft by the feed's 'r' (registration)
and 'flight' (callsign) fields, so a missing hex is not fatal.
"""
import csv
import glob
import json
import os
import re

CA_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
US_CHARSET = "ABCDEFGHJKLMNPQRSTUVWXYZ"  # no I, O
US_DIGITS = "0123456789"
US_SUFFIX = 1 + len(US_CHARSET) * (1 + len(US_CHARSET))  # 601
US_B4 = 1 + len(US_CHARSET) + len(US_DIGITS)  # 35
US_B3 = len(US_DIGITS) * US_B4 + US_SUFFIX  # 951
US_B2 = len(US_DIGITS) * US_B3 + US_SUFFIX  # 10111
US_B1 = len(US_DIGITS) * US_B2 + US_SUFFIX  # 101711


def norm_tail(t):
    """'C-GGIB' / 'cggib ' / 'C GGIB' -> 'CGGIB'."""
    return re.sub(r"[^A-Z0-9]", "", (t or "").upper())


def pretty_tail(t):
    """'CGGIB' -> 'C-GGIB', 'N123AB' stays."""
    n = norm_tail(t)
    if re.fullmatch(r"C[FGI][A-Z]{3}", n):
        return n[0] + "-" + n[1:]
    return n


def canada_hex(tail):
    """C-Fxxx / C-Gxxx -> hex using Canada's sequential allocation
    (C-FAAA = C00001). Returns None for other forms (C-I ultralights etc.)."""
    n = norm_tail(tail)
    if not re.fullmatch(r"C[FG][A-Z]{3}", n):
        return None
    idx = (0 if n[1] == "F" else 1) * 26 ** 3
    idx += CA_LETTERS.index(n[2]) * 26 ** 2 + CA_LETTERS.index(n[3]) * 26 + CA_LETTERS.index(n[4])
    return "%06x" % (0xC00001 + idx)


def _us_suffix_offset(s):
    if not s:
        return 0
    if len(s) > 2 or any(c not in US_CHARSET for c in s):
        raise ValueError(s)
    count = (len(US_CHARSET) + 1) * US_CHARSET.index(s[0]) + 1
    if len(s) == 2:
        count += US_CHARSET.index(s[1]) + 1
    return count


def us_hex(tail):
    """US N-number -> hex (FAA sequential allocation, A00001 = N1)."""
    n = norm_tail(tail)
    if not n.startswith("N"):
        return None
    n = n[1:]
    if not (1 <= len(n) <= 5) or n[0] not in "123456789":
        return None
    try:
        out = 0xA00001 + (int(n[0]) - 1) * US_B1
        if len(n) == 1:
            return "%06x" % out
        buckets = [US_B2, US_B3, US_B4]
        for i in range(1, 4):
            if i >= len(n):
                return "%06x" % out
            c = n[i]
            if c in US_CHARSET:
                return "%06x" % (out + _us_suffix_offset(n[i:]))
            out += US_SUFFIX + int(c) * buckets[i - 1]
        if len(n) == 4:
            return "%06x" % out
        c = n[4]
        if c in US_CHARSET:
            out += 1 + US_CHARSET.index(c)
        else:
            out += 1 + len(US_CHARSET) + int(c)
        return "%06x" % out
    except (ValueError, IndexError):
        return None


def algorithmic_hex(tail):
    return canada_hex(tail) or us_hex(tail)


def load_ccar(ccar_dir):
    """Parse TC CCAR 'carscurr.txt' (comma separated, quoted) into
    {tail: hex}. Column positions per TC's layout file: col 0 = mark
    (without C-), the 24-bit address is in the 'Mode S Transponder' column
    stored as binary/octal/hex depending on vintage; we accept hex-looking
    values only. Returns {} when no cache exists."""
    out = {}
    files = glob.glob(os.path.join(ccar_dir, "carscurr*.txt"))
    for f in files:
        with open(f, encoding="latin-1", newline="") as fh:
            for row in csv.reader(fh):
                if not row:
                    continue
                mark = norm_tail(row[0])
                if not mark:
                    continue
                tail = mark if mark.startswith("C") else "C" + mark
                for cell in row[1:]:
                    v = cell.strip()
                    if re.fullmatch(r"[0-9A-Fa-f]{6}", v) and v.upper().startswith("C"):
                        out[tail] = v.lower()
                        break
                    if re.fullmatch(r"[01]{24}", v):
                        out[tail] = "%06x" % int(v, 2)
                        break
    return out


class Resolver:
    def __init__(self, cache_path, ccar_dir, http=None):
        self.cache_path = cache_path
        self.cache = {}
        if os.path.exists(cache_path):
            try:
                self.cache = json.load(open(cache_path))
            except ValueError:
                self.cache = {}
        self.ccar = load_ccar(ccar_dir) if ccar_dir else {}
        self.http = http  # callable(url) -> dict or None

    def resolve(self, tail, online=True):
        """Return (hex or None, source)."""
        t = norm_tail(tail)
        if not t:
            return None, "none"
        if t in self.cache and self.cache[t].get("hex"):
            return self.cache[t]["hex"], self.cache[t].get("source", "cache")
        if t in self.ccar:
            return self.ccar[t], "ccar"
        h = algorithmic_hex(t)
        if h:
            return h, "algorithm"
        if online and self.http:
            for url in ("https://api.airplanes.live/v2/reg/%s", "https://api.adsb.lol/v2/reg/%s"):
                data = self.http(url % pretty_tail(t))
                ac = (data or {}).get("ac") or (data or {}).get("aircraft") or []
                if ac and ac[0].get("hex"):
                    h = ac[0]["hex"].lower()
                    self.cache[t] = {"hex": h, "source": url.split("/")[2]}
                    self._save()
                    return h, url.split("/")[2]
        return None, "unresolved"

    def _save(self):
        tmp = self.cache_path + ".tmp"
        json.dump(self.cache, open(tmp, "w"), indent=1, sort_keys=True)
        os.replace(tmp, self.cache_path)
