"""Turns settings + conditions into rankings: shared by the window and the command line."""
import os
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from datetime import timedelta

import hfprop as hp

APP_DIR = os.path.dirname(os.path.abspath(__file__))

AMATEUR_BANDS = [
    ("160 m", 1.900), ("80 m", 3.750), ("60 m", 5.357), ("40 m", 7.150), ("30 m", 10.130),
    ("20 m", 14.200), ("17 m", 18.120), ("15 m", 21.300), ("12 m", 24.950), ("10 m", 28.400),
]

NETLOG_DB_CANDIDATES = [
    os.path.join(APP_DIR, "..", "Net Log", "netlog.db"),
    os.path.join(APP_DIR, "netlog.db"),
    r"Z:\Claude\Net Log\netlog.db",
]

DEFAULTS = {
    "location": "EL87",
    "path_mode": "nvis",           # nvis | station | net
    "nvis_km": 150,
    "target": "",
    "net_max_km": 1500,
    "mode": "SSB voice",
    "watts": 100,
    "gain_dbi": 0.0,
    "channel_set": "amateur",      # amateur | netlog | custom
    "custom_channels": "",
    "netlog_db": "",
}


def find_netlog_db(configured=""):
    for p in [configured] + NETLOG_DB_CANDIDATES:
        if p and os.path.isfile(p):
            return os.path.normpath(p)
    return ""


def _connect_ro(db_path):
    return closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True))


def parse_channel_lines(text):
    """'Name, 7.1535' / 'Name 7.1535 MHz' / '7153.5 kHz' / '7.1535' per line -> [Channel]."""
    out = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        m = re.search(r"([\d]+(?:\.\d+)?)\s*(mhz|khz)?\s*$", line, re.I)
        if not m:
            raise ValueError(f"no frequency on line: {line!r}")
        f = float(m.group(1))
        if (m.group(2) or "").lower() == "khz" or (not m.group(2) and f > 100):
            f /= 1000
        if not 1.5 <= f <= 30:
            raise ValueError(f"{f:g} MHz is outside 1.5-30 MHz: {line!r}")
        name = line[:m.start()].strip(" ,:;\t") or f"{f:g} MHz"
        out.append(hp.Channel(name, f))
    return out


def netlog_channels(db_path):
    """Channels from the NetLog 'frequencies' table, e.g. 'A2A:   2.1242 MHz'."""
    with _connect_ro(db_path) as con:
        rows = con.execute("SELECT Frequency FROM frequencies ORDER BY ID").fetchall()
    out = []
    for (text,) in rows:
        m = re.match(r"\s*([^:]+):\s*([\d.]+)\s*MHz", text or "", re.I)
        if m and 1.5 <= float(m.group(2)) <= 30:
            out.append(hp.Channel(m.group(1).strip(), float(m.group(2))))
    return out


@dataclass
class Station:
    callsign: str
    state: str
    lat: float
    lon: float


def netlog_members(db_path):
    """Members with a position. NetLog stores positive degrees plus N/S and E/W columns."""
    with _connect_ro(db_path) as con:
        rows = con.execute(
            "SELECT callsign, state, Latitude, Lat_Dir, Longitude, Long_Dir FROM members "
            "WHERE COALESCE(is_synthetic, 0) = 0 AND Latitude IS NOT NULL AND Longitude IS NOT NULL"
        ).fetchall()
    out = []
    for call, state, lat, lat_dir, lon, lon_dir in rows:
        try:
            lat = abs(float(lat)) * (-1 if (lat_dir or "N").strip().upper().startswith("S") else 1)
            lon = abs(float(lon)) * (-1 if (lon_dir or "W").strip().upper().startswith("W") else 1)
        except (TypeError, ValueError):
            continue
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            out.append(Station(call, state or "", lat, lon))
    return out


def load_channels(settings):
    kind = settings["channel_set"]
    if kind == "netlog":
        db = find_netlog_db(settings.get("netlog_db", ""))
        if not db:
            raise ValueError("NetLog database not found: choose it under Channels > NetLog database")
        chans = netlog_channels(db)
    elif kind == "custom":
        chans = parse_channel_lines(settings.get("custom_channels", ""))
    else:
        chans = [hp.Channel(n, f) for n, f in AMATEUR_BANDS]
    if not chans:
        raise ValueError("the channel list is empty")
    return chans


@dataclass
class Target:
    label: str
    lat: float
    lon: float
    state: str = ""


def resolve_targets(settings, home, members=None):
    """The far end(s) of the path for the current path mode."""
    mode = settings["path_mode"]
    if mode == "nvis":
        km = float(settings["nvis_km"])
        lat, lon = hp.destination(home[0], home[1], 0.0, km)
        return [Target(f"NVIS {km:g} km", lat, lon)]
    if mode == "station":
        text = settings.get("target", "").strip()
        for m in members or []:
            if m.callsign.upper() == text.upper():
                return [Target(m.callsign, m.lat, m.lon, m.state)]
        lat, lon = hp.parse_location(text)
        return [Target(text, lat, lon)]
    limit = float(settings["net_max_km"])
    out = [Target(m.callsign, m.lat, m.lon, m.state) for m in members or []
           if hp.distance_km(home[0], home[1], m.lat, m.lon) <= limit]
    if not out:
        raise ValueError(f"no NetLog members with a position within {limit:g} km")
    return out


@dataclass
class Result:
    when: object
    targets: list
    paths: list
    ranking: list               # [ChannelResult], best first
    margin: float

    @property
    def best(self):
        return self.ranking[0]

    def per_target(self, channel):
        return [p.reliability(channel.mhz, self.margin) for p in self.paths]

    def target_best(self, i, channels):
        """Best channel and its reliability for one target."""
        p = self.paths[i]
        best = hp.rank_channels([p], channels, self.margin)[0]
        return best.channel, best.reliability


def evaluate(iono, home, targets, channels, margin, when):
    paths = [hp.analyze_path(iono, home[0], home[1], t.lat, t.lon, when) for t in targets]
    return Result(when, targets, paths, hp.rank_channels(paths, channels, margin), margin)


@dataclass
class Plan:
    now: Result
    hourly: list = field(default_factory=list)   # Result per hour for the next 24 h


def build_plan(settings, conditions, members=None, now=None, hours=24):
    home = hp.parse_location(settings["location"])
    channels = load_channels(settings)
    targets = resolve_targets(settings, home, members)
    margin = hp.station_margin(settings["mode"], float(settings["watts"]), float(settings["gain_dbi"]))
    iono = hp.Ionosphere(conditions.model_ssn, conditions.kp, conditions.xray_flux,
                         conditions.ionosondes, now)
    plan = Plan(evaluate(iono, home, targets, channels, margin, iono.now))
    start = iono.now.replace(minute=0, second=0, microsecond=0)
    # Flares last minutes to hours: the forecast hours assume a quiet sun.
    quiet = hp.Ionosphere(conditions.model_ssn, conditions.kp, 0.0, conditions.ionosondes, iono.now)
    for h in range(1, hours + 1):
        t = start + timedelta(hours=h)
        plan.hourly.append(evaluate(quiet, home, targets, channels, margin, t))
    return plan
