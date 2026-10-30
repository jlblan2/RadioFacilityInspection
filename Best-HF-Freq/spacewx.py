"""Live space weather and ionosonde readings, with an on-disk cache for offline use.

Sources (all free, no key):
- NOAA SWPC: 10.7 cm solar flux, planetary K index, GOES X-ray flux, NOAA R/S/G scales
- KC2G (prop.kc2g.com): effective sunspot number and the latest GIRO/NOAA ionosonde readings
- Open-Meteo (open-meteo.com): where thunderstorms are now and over the next 24 hours, for
  atmospheric noise (static). Weather data by Open-Meteo.com, CC BY 4.0.
"""
import json
import math
import os
import ssl
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from hfprop import Ionosonde, Thunderstorms, sfi_to_ssn, xray_class

APP = "Best-HF-Freq"
USER_AGENT = f"{APP}/1.0 (amateur and auxiliary HF frequency planning)"
FEEDS = {
    "sfi": "https://services.swpc.noaa.gov/json/f107_cm_flux.json",
    "kp": "https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json",
    "xray": "https://services.swpc.noaa.gov/json/goes/primary/xrays-6-hour.json",
    "scales": "https://services.swpc.noaa.gov/products/noaa-scales.json",
    "essn": "https://prop.kc2g.com/api/essn.json",
    "stations": "https://prop.kc2g.com/api/stations.json",
}
THUNDER_URL = "https://api.open-meteo.com/v1/forecast"
THUNDER_CODES = {95, 96, 99}     # WMO weather codes: thunderstorm (with or without hail)
THUNDER_MAX_POINTS = 120         # grid points per request; Open-Meteo counts each one
THUNDER_TTL = 3600               # the forecast is hourly, so fetch at most once an hour
MAX_IONOSONDE_AGE = timedelta(minutes=90)
MIN_CONFIDENCE = 25          # KC2G autoscaling confidence score, 0-100 (negative = manually scaled)


def data_dir():
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    path = os.path.join(base, "N. Tesla", APP)
    os.makedirs(path, exist_ok=True)
    return path


def _ssl_context():
    try:
        import truststore   # uses the Windows certificate store when installed
        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except ImportError:
        return ssl.create_default_context()


def fetch_json(name, timeout=20, url=None):
    """(data, fetched_at_epoch, error). Falls back to the cached copy if the fetch fails."""
    cache = os.path.join(data_dir(), "cache", f"{name}.json")
    try:
        req = urllib.request.Request(url or FEEDS[name], headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as r:
            raw = r.read()
        data = json.loads(raw)
        os.makedirs(os.path.dirname(cache), exist_ok=True)
        with open(cache, "wb") as f:
            f.write(raw)
        return data, time.time(), None
    except Exception as e:   # network down, feed changed, bad JSON ...
        try:
            with open(cache, "rb") as f:
                return json.loads(f.read()), os.path.getmtime(cache), f"{name}: {e} (using cached copy)"
        except (OSError, ValueError):
            return None, None, f"{name}: {e}"


def _utc(text):
    t = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


@dataclass
class Conditions:
    sfi: float = None
    ssn: float = None
    ssn_source: str = ""
    kp: float = None
    a_index: float = None
    xray_flux: float = None
    scales: dict = field(default_factory=dict)    # {"R": "0", "S": "0", "G": "0"}
    ionosondes: list = field(default_factory=list)
    fetched: datetime = None
    oldest_data: datetime = None
    errors: list = field(default_factory=list)

    @property
    def xray_class(self):
        return xray_class(self.xray_flux)

    @property
    def model_ssn(self):
        if self.ssn is not None:
            return self.ssn
        if self.sfi is not None:
            return sfi_to_ssn(self.sfi)
        return 50.0


def parse_ionosondes(stations, now):
    out = []
    for s in stations or []:
        try:
            fof2 = _float(s.get("fof2"))
            cs = _float(s.get("cs"))
            when = _utc(s["time"])
            st = s["station"]
            lat = float(st["latitude"])
            lon = (float(st["longitude"]) + 180) % 360 - 180
        except (KeyError, TypeError, ValueError):
            continue
        if not fof2 or not (0.5 < fof2 < 25) or now - when > MAX_IONOSONDE_AGE or when > now + timedelta(minutes=10):
            continue
        if cs is not None and 0 <= cs < MIN_CONFIDENCE:
            continue
        out.append(Ionosonde(st.get("name") or st.get("code", "?"), lat, lon, fof2, when,
                             _float(s.get("hmf2"))))
    return out


def get_conditions(now=None):
    now = now or datetime.now(timezone.utc)
    c = Conditions(fetched=now)
    times = []

    def load(name):
        data, at, err = fetch_json(name)
        if err:
            c.errors.append(err)
        if at:
            times.append(datetime.fromtimestamp(at, timezone.utc))
        return data

    try:
        c.sfi = _float(load("sfi")[0]["flux"])
    except (TypeError, KeyError, IndexError):
        pass
    try:
        latest = load("kp")[-1]
        c.kp, c.a_index = _float(latest["Kp"]), _float(latest.get("a_running"))
    except (TypeError, KeyError, IndexError):
        pass
    try:
        long_band = [x for x in load("xray") if x.get("energy") == "0.1-0.8nm"]
        c.xray_flux = _float(long_band[-1]["flux"])
    except (TypeError, KeyError, IndexError):
        pass
    try:
        now_scales = load("scales")["0"]
        c.scales = {k: (now_scales[k].get("Scale") or "0") for k in ("R", "S", "G")}
    except (TypeError, KeyError, AttributeError):
        pass
    try:
        latest = load("essn")["24h"][-1]
        c.ssn, c.ssn_source = _float(latest["ssn"]), "effective SSN (KC2G)"
    except (TypeError, KeyError, IndexError):
        pass
    if c.ssn is None and c.sfi is not None:
        c.ssn, c.ssn_source = sfi_to_ssn(c.sfi), "from solar flux"
    c.ionosondes = parse_ionosondes(load("stations"), now)
    c.oldest_data = min(times) if times else None
    return c


_thunder_memo = {}   # bbox -> (fetched_epoch, Thunderstorms)


def thunder_grid(lat0, lat1, lon0, lon1):
    """Grid points covering the box, spaced at least 2 degrees, at most THUNDER_MAX_POINTS."""
    lat0, lat1 = max(lat0, -70.0), min(lat1, 70.0)
    step = 2.0
    while True:
        lats = [lat0 + i * step for i in range(int((lat1 - lat0) / step) + 1)]
        lons = [lon0 + i * step for i in range(int((lon1 - lon0) / step) + 1)]
        if len(lats) * len(lons) <= THUNDER_MAX_POINTS:
            return [(round(a, 2), round((b + 180) % 360 - 180, 2)) for a in lats for b in lons]
        step *= 1.25


def parse_thunder(data):
    out = []
    for loc in data if isinstance(data, list) else [data]:
        try:
            times = loc["hourly"]["time"]
            codes = loc["hourly"]["weather_code"]
            hours = {int(_utc(t).timestamp()): (c in THUNDER_CODES) for t, c in zip(times, codes)}
            out.append((float(loc["latitude"]), float(loc["longitude"]), hours))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def get_thunderstorms(lat0, lat1, lon0, lon1):
    """Thunderstorm areas (now and the next 24 h) in and around the box, as Thunderstorms;
    None if neither the network nor the cache has them."""
    key = tuple(round(v) for v in (lat0, lat1, lon0, lon1))
    hit = _thunder_memo.get(key)
    if hit and time.time() - hit[0] < THUNDER_TTL:
        return hit[1]
    pts = thunder_grid(lat0, lat1, lon0, lon1)
    url = (f"{THUNDER_URL}?latitude={','.join(str(a) for a, _ in pts)}"
           f"&longitude={','.join(str(b) for _, b in pts)}"
           "&hourly=weather_code&past_hours=1&forecast_hours=26&timezone=UTC")
    data, at, err = fetch_json("thunder", url=url, timeout=30)
    points = parse_thunder(data) if data else []
    if not points:
        return None
    t = Thunderstorms(points, "Open-Meteo" + (" (cached)" if err else ""))
    if not err:
        _thunder_memo[key] = (time.time(), t)
    return t


def thunderstorms_around(points, pad_km=700):
    """Thunderstorms for the area holding all the (lat, lon) points, plus a margin."""
    lats = [p[0] for p in points]
    lons = [p[1] for p in points]
    pad_lat = pad_km / 111.0
    pad_lon = pad_km / (111.0 * max(0.2, math.cos(math.radians(sum(lats) / len(lats)))))
    return get_thunderstorms(min(lats) - pad_lat, max(lats) + pad_lat,
                             min(lons) - pad_lon, max(lons) + pad_lon)
