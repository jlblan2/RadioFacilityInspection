"""HF propagation estimate: MUF, LUF and the chance each frequency works on a path.

A simplified, self-contained model meant for choosing a working frequency, not for
engineering a circuit:

- foF2 comes from a mid-latitude model driven by the sunspot number and the solar
  zenith angle, then corrected by live ionosonde readings near each reflection point
  (the observed/model ratio, weighted by distance).
- The path MUF is foF2 x the obliquity factor (secant law over a curved Earth) at the
  worst reflection point, or the E-layer MUF if that is higher on a short path.
- Absorption follows the shape of the CCIR/ITU D-layer formula; solar-flare absorption
  follows NOAA's D-RAP rule (highest affected frequency = 10 log10(X-ray flux) + 65 MHz).
- Atmospheric noise (static from lightning) at both ends of the path: louder at night,
  in summer, at low latitudes and near thunderstorms, and steepest at low frequencies
  (shaped after ITU-R P.372). It adds to a fixed man-made noise floor.
- A frequency's reliability = P(frequency below the day-to-day MUF) x P(signal clears
  absorption, spreading and noise with the station's margin).
"""
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

R_EARTH = 6371.0
GEOMAG_POLE = (80.8, -72.7)        # IGRF dipole north pole, about 2025
MIN_ELEVATION = 4.0                # degrees; lower take-off angles are rare on real antennas
D_LAYER_KM = 110.0
E_LAYER_KM = 110.0
DEFAULT_HMF2 = 300.0
MIN_DISTANCE = 30.0                # shorter paths are treated as 30 km NVIS (no ground wave model)
ABSORPTION_K = 400.0               # calibrated so 80 m NVIS at noon is marginal for 100 W SSB
GYRO_MHZ = 1.2

# Margin (dB) a 100 W station with 0 dBi antennas has over the model's losses, by mode.
MODES = {
    "SSB voice": 38.0,
    "CW": 48.0,
    "Digital (ALE / VARA / PSK)": 44.0,
    "FT8 / weak signal": 62.0,
}

TIE_BAND = 0.03
RATINGS = ((0.80, "Excellent"), (0.60, "Good"), (0.35, "Fair"), (0.15, "Poor"), (0.0, "Closed"))


# ── geography ─────────────────────────────────────────────────────────────────

def grid_to_latlon(grid):
    """Centre of a 4-, 6- or 8-character Maidenhead locator."""
    g = grid.strip()
    if len(g) not in (4, 6, 8) or not (g[:2].isalpha() and g[2:4].isdigit()):
        raise ValueError(f"not a grid locator: {grid!r}")
    g = g[:2].upper() + g[2:4] + g[4:6].lower() + g[6:8]
    if not ("A" <= g[0] <= "R" and "A" <= g[1] <= "R"):
        raise ValueError(f"not a grid locator: {grid!r}")
    lon = (ord(g[0]) - 65) * 20 - 180 + int(g[2]) * 2
    lat = (ord(g[1]) - 65) * 10 - 90 + int(g[3])
    lon_size, lat_size = 2.0, 1.0
    if len(g) >= 6:
        if not ("a" <= g[4] <= "x" and "a" <= g[5] <= "x"):
            raise ValueError(f"not a grid locator: {grid!r}")
        lon_size, lat_size = 2 / 24, 1 / 24
        lon += (ord(g[4]) - 97) * lon_size
        lat += (ord(g[5]) - 97) * lat_size
    if len(g) == 8:
        if not g[6:8].isdigit():
            raise ValueError(f"not a grid locator: {grid!r}")
        lon_size, lat_size = lon_size / 10, lat_size / 10
        lon += int(g[6]) * lon_size
        lat += int(g[7]) * lat_size
    return lat + lat_size / 2, lon + lon_size / 2


def latlon_to_grid(lat, lon):
    lon = (lon + 180) % 360
    lat = min(max(lat + 90, 0), 179.9999)
    a, lon = divmod(lon, 20)
    b, lat = divmod(lat, 10)
    c, lon = divmod(lon, 2)
    d, lat = divmod(lat, 1)
    return (chr(65 + int(a)) + chr(65 + int(b)) + str(int(c)) + str(int(d))
            + chr(97 + int(lon * 12)) + chr(97 + int(lat * 24)))


def parse_location(text):
    """'EL96wb', '27.9, -82.5', '27.9 -82.5' or '27.9N 82.5W' -> (lat, lon)."""
    t = text.strip()
    if not t:
        raise ValueError("no location given")
    if t[:2].isalpha():
        return grid_to_latlon(t)
    parts = t.replace(",", " ").split()
    if len(parts) != 2:
        raise ValueError(f"cannot read location {text!r}: use a grid square or 'lat, lon'")
    vals = []
    for p, (pos, neg) in zip(parts, (("N", "S"), ("E", "W"))):
        p = p.upper().replace("°", "")
        sign = 1
        if p[-1] in (pos, neg):
            sign = -1 if p[-1] == neg else 1
            p = p[:-1]
        vals.append(sign * float(p))
    lat, lon = vals
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError(f"location out of range: {text!r}")
    return lat, lon


def distance_km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R_EARTH * math.asin(min(1.0, math.sqrt(a)))


def intermediate_point(lat1, lon1, lat2, lon2, frac):
    p1, l1, p2, l2 = map(math.radians, (lat1, lon1, lat2, lon2))
    d = distance_km(lat1, lon1, lat2, lon2) / R_EARTH
    if d < 1e-9:
        return lat1, lon1
    a = math.sin((1 - frac) * d) / math.sin(d)
    b = math.sin(frac * d) / math.sin(d)
    x = a * math.cos(p1) * math.cos(l1) + b * math.cos(p2) * math.cos(l2)
    y = a * math.cos(p1) * math.sin(l1) + b * math.cos(p2) * math.sin(l2)
    z = a * math.sin(p1) + b * math.sin(p2)
    return math.degrees(math.atan2(z, math.hypot(x, y))), math.degrees(math.atan2(y, x))


def destination(lat, lon, bearing_deg, dist):
    p1, l1, b = math.radians(lat), math.radians(lon), math.radians(bearing_deg)
    d = dist / R_EARTH
    p2 = math.asin(math.sin(p1) * math.cos(d) + math.cos(p1) * math.sin(d) * math.cos(b))
    l2 = l1 + math.atan2(math.sin(b) * math.sin(d) * math.cos(p1),
                         math.cos(d) - math.sin(p1) * math.sin(p2))
    return math.degrees(p2), (math.degrees(l2) + 540) % 360 - 180


def geomag_lat(lat, lon):
    pp, pl = map(math.radians, GEOMAG_POLE)
    p, l = math.radians(lat), math.radians(lon)
    s = math.sin(p) * math.sin(pp) + math.cos(p) * math.cos(pp) * math.cos(l - pl)
    return math.degrees(math.asin(max(-1.0, min(1.0, s))))


# ── sun ───────────────────────────────────────────────────────────────────────

def solar_zenith(lat, lon, when):
    """Solar zenith angle in degrees (NOAA approximation, good to a fraction of a degree)."""
    when = when.astimezone(timezone.utc)
    hour = when.hour + when.minute / 60 + when.second / 3600
    g = 2 * math.pi / 365 * (when.timetuple().tm_yday - 1 + (hour - 12) / 24)
    decl = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g)
            - 0.006758 * math.cos(2 * g) + 0.000907 * math.sin(2 * g)
            - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))
    eqt = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g)
                    - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g))
    ha = math.radians((hour * 60 + eqt + 4 * lon) / 4 - 180)
    p = math.radians(lat)
    c = math.sin(p) * math.sin(decl) + math.cos(p) * math.cos(decl) * math.cos(ha)
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


def sfi_to_ssn(sfi):
    return max(0.0, (sfi - 63.7) / 0.73)


def xray_class(flux):
    if not flux or flux <= 0:
        return "?"
    for limit, letter in ((1e-4, "X"), (1e-5, "M"), (1e-6, "C"), (1e-7, "B")):
        if flux >= limit:
            return f"{letter}{flux / limit:.1f}"
    return f"A{flux / 1e-8:.1f}"


# ── ionosphere ────────────────────────────────────────────────────────────────

def model_fof2(lat, lon, when, ssn, kp=0.0):
    """Climatological foF2 (MHz) for a mid-latitude ionosphere."""
    ssn = min(max(ssn, 0.0), 250.0)
    chi = solar_zenith(lat, lon, when - timedelta(hours=1))   # F2 peaks after local noon
    day = min(max((math.cos(math.radians(chi)) + 0.1) / 1.1, 0.0), 1.0) ** 0.5
    noon = 6.0 + 0.045 * ssn
    night = 2.5 + 0.015 * ssn
    mlat = abs(geomag_lat(lat, lon))
    if mlat > 55:
        noon *= 0.85
    f = math.sqrt(night ** 2 + (noon ** 2 - night ** 2) * day)
    if kp >= 5:
        f *= 1 - 0.05 * (kp - 4) * min(1.0, mlat / 50)
    return f


def model_foe(lat, lon, when, ssn):
    c = math.cos(math.radians(solar_zenith(lat, lon, when)))
    if c <= 0.02:
        return 0.5
    return max(0.5, 0.9 * ((180 + 1.44 * min(max(ssn, 0.0), 250.0)) * c) ** 0.25)


@dataclass
class Ionosonde:
    name: str
    lat: float
    lon: float
    fof2: float
    time: datetime
    hmf2: float = None
    ratio: float = 1.0      # observed / model foF2 at the time of the reading


class Ionosphere:
    """Space-weather inputs plus ionosonde corrections, for a given 'now'."""

    def __init__(self, ssn, kp=0.0, xray_flux=0.0, ionosondes=(), now=None, thunder=None):
        self.ssn = ssn
        self.kp = kp or 0.0
        self.xray_flux = xray_flux or 0.0
        self.now = now or datetime.now(timezone.utc)
        self.thunder = thunder          # Thunderstorms, or None for climatology only
        self.ionosondes = []
        for s in ionosondes:
            m = model_fof2(s.lat, s.lon, s.time, ssn, self.kp)
            s.ratio = min(max(s.fof2 / m, 0.5), 2.0)
            self.ionosondes.append(s)

    def correction(self, lat, lon, when):
        """(foF2 multiplier, hmF2, distance to the nearest ionosonde or None)."""
        if not self.ionosondes:
            return 1.0, DEFAULT_HMF2, None
        sw = swr = 0.0
        nearest, hm = None, DEFAULT_HMF2
        for s in self.ionosondes:
            d = distance_km(lat, lon, s.lat, s.lon)
            if nearest is None or d < nearest:
                nearest = d
                hm = s.hmf2 if s.hmf2 and 150 < s.hmf2 < 500 else DEFAULT_HMF2
            if d < 2500:
                w = 1 / (d * d + 300.0 ** 2)
                sw += w
                swr += w * s.ratio
        if not sw:
            return 1.0, DEFAULT_HMF2, nearest
        blend = math.exp(-(nearest / 1500) ** 2)
        hours_ahead = max(0.0, (when - self.now).total_seconds() / 3600)
        blend *= math.exp(-hours_ahead / 6)     # persistence fades into the model
        if nearest > 1500:
            hm = DEFAULT_HMF2
        return 1 + blend * (swr / sw - 1), hm, nearest

    def sigma(self, nearest_km, when):
        """Day-to-day spread of the MUF (log scale)."""
        s = 0.25                                  # model only
        if nearest_km is not None and nearest_km < 1500:
            measured = 0.12 + 0.06 * nearest_km / 1500
            hours_ahead = max(0.0, (when - self.now).total_seconds() / 3600)
            s -= (s - measured) * math.exp(-hours_ahead / 6)
        return s + 0.03 * max(0.0, self.kp - 4)

    def fof2(self, lat, lon, when):
        k, hm, near = self.correction(lat, lon, when)
        return model_fof2(lat, lon, when, self.ssn, self.kp) * k, hm, near


class Thunderstorms:
    """Where thunderstorms are, hour by hour: [(lat, lon, {hour_start_epoch: bool})]."""

    def __init__(self, points, source=""):
        self.points = points
        self.source = source

    def covers(self, when):
        key = int(when.timestamp()) // 3600 * 3600
        return any(key in hours for _, _, hours in self.points)

    def nearest_km(self, lat, lon, when):
        """Distance to the nearest thunderstorm within STORM_FAR_KM in that hour; None if
        there is none, or the hour is outside the data."""
        key = int(when.timestamp()) // 3600 * 3600
        best = None
        for plat, plon, hours in self.points:
            if hours.get(key):
                d = distance_km(lat, lon, plat, plon)
                if d < STORM_FAR_KM and (best is None or d < best):
                    best = d
        return best

    def active(self, when):
        key = int(when.timestamp()) // 3600 * 3600
        return [(la, lo) for la, lo, hours in self.points if hours.get(key)]


# ── path geometry ─────────────────────────────────────────────────────────────

def _theta(hop_km):
    return hop_km / (2 * R_EARTH)


def sec_incidence(hop_km, h_km):
    """Secant of the angle of incidence at height h for one hop of the given ground length."""
    t = _theta(hop_km)
    return 1 / math.cos(math.atan(math.sin(t) / (1 + h_km / R_EARTH - math.cos(t))))


def elevation(hop_km, h_km):
    t = _theta(hop_km)
    if t < 1e-9:
        return 90.0
    return math.degrees(math.atan((math.cos(t) - R_EARTH / (R_EARTH + h_km)) / math.sin(t)))


def slant_km(hop_km, h_km):
    t = _theta(hop_km)
    r, rh = R_EARTH, R_EARTH + h_km
    return 2 * math.sqrt(r * r + rh * rh - 2 * r * rh * math.cos(t))


def hops_needed(dist, h_km):
    n = 1
    while elevation(dist / n, h_km) < MIN_ELEVATION:
        n += 1
    return n


def normal_cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


STORM_NEAR_KM = 250                # thunderstorm static is loud on every low band
STORM_FAR_KM = 800                 # still heard, mostly at night
QUIET = -99.0                      # no atmospheric noise worth counting


def atmospheric_level(lat, lon, when, storm_km=None):
    """Atmospheric noise at 10 MHz, in dB over the man-made noise floor.

    By day the D layer absorbs static from distant storms; at night it travels far, so
    the level rises. Summer and low latitudes have more thunderstorms. A storm within
    STORM_NEAR_KM dominates everything."""
    chi = solar_zenith(lat, lon, when)
    night = min(max((chi - 85) / 15, 0.0), 1.0)          # 0 = day, 1 = night, ramps at dusk
    level = -20 + 18 * night
    north_summer = 5 <= when.month <= 9
    if (lat >= 0) == north_summer:
        level += 4
    if abs(lat) < 30:
        level += 3
    if storm_km is not None:
        if storm_km < STORM_NEAR_KM:
            storm = 15.0
        elif storm_km < STORM_FAR_KM:
            storm = 4 + 4 * night
        else:
            storm = QUIET
        level = 10 * math.log10(10 ** (level / 10) + 10 ** (storm / 10))
    return level


def noise_db(f, atmospheric=QUIET):
    """Noise relative to the man-made floor at 10 MHz: man-made noise rises 18 dB per decade
    below 10 MHz, atmospheric noise about 35 dB per decade."""
    manmade = max(0.0, 18 * math.log10(10 / f))
    atmos = atmospheric + 35 * math.log10(10 / f)
    return 10 * math.log10(10 ** (manmade / 10) + 10 ** (atmos / 10))


@dataclass
class Path:
    distance: float
    hops: int
    hmf2: float
    muf: float              # median path MUF, MHz
    sigma: float
    f2_muf: float
    e_muf: float
    absorption_index: float  # sum over hops of I(chi) x sec(i)
    flares: list = field(default_factory=list)   # (highest affected MHz, sec) per sunlit hop
    spread_db: float = 0.0     # spreading over a 100 km path, scaled down for antenna gain at low angles
    ground_db: float = 0.0
    nearest_ionosonde: float = None
    atmospheric_db: float = QUIET   # noisier end of the path (see atmospheric_level)
    storm_km: float = None          # nearest thunderstorm to either end, if any is known

    @property
    def fot(self):
        """Frequency below the MUF on 90 % of days."""
        return self.muf * math.exp(-1.2816 * self.sigma)

    def loss_db(self, f):
        a = ABSORPTION_K * self.absorption_index / ((f + GYRO_MHZ) ** 1.98 + 10.2)
        for haf, sec in self.flares:
            if haf > 0:
                a += (haf / f) ** 1.5 * sec
        return a + self.spread_db + self.ground_db + noise_db(f, self.atmospheric_db)

    def reliability(self, f, margin_db):
        p_muf = normal_cdf(math.log(self.muf / f) / self.sigma)
        p_snr = normal_cdf((margin_db - self.loss_db(f)) / 6.0)
        return p_muf * p_snr

    def luf(self, margin_db):
        """Lowest frequency at which the signal clears its losses on a median day."""
        f = 1.5
        while f < 30 and self.loss_db(f) > margin_db:
            f += 0.05
        return f


def analyze_path(iono, lat1, lon1, lat2, lon2, when):
    dist = max(distance_km(lat1, lon1, lat2, lon2), MIN_DISTANCE)
    # First pass at the default height to find the reflection points.
    hops = hops_needed(dist, DEFAULT_HMF2)
    fracs = [(k + 0.5) / hops for k in range(hops)]
    if len(fracs) > 5:   # long paths: ends and middle carry the information
        fracs = [fracs[0], fracs[1], fracs[len(fracs) // 2], fracs[-2], fracs[-1]]
    points = [intermediate_point(lat1, lon1, lat2, lon2, fr) for fr in fracs]
    samples = [iono.fof2(lat, lon, when) for lat, lon in points]
    hm = sum(s[1] for s in samples) / len(samples)
    hops = hops_needed(dist, hm)
    hop = dist / hops
    sec_f2 = sec_incidence(hop, hm)
    f2_muf = min(s[0] for s in samples) * sec_f2
    near = [s[2] for s in samples if s[2] is not None]
    nearest = max(near) if near else None   # the worst-served reflection point

    ends_storm = [iono.thunder.nearest_km(lat, lon, when) if iono.thunder else None
                  for lat, lon in ((lat1, lon1), (lat2, lon2))]
    known = [d for d in ends_storm if d is not None]
    atmospheric = max(atmospheric_level(lat, lon, when, d)
                      for (lat, lon), d in zip(((lat1, lon1), (lat2, lon2)), ends_storm))

    e_muf = 0.0
    if elevation(dist, E_LAYER_KM) >= MIN_ELEVATION:
        lat, lon = intermediate_point(lat1, lon1, lat2, lon2, 0.5)
        e_muf = model_foe(lat, lon, when, iono.ssn) * sec_incidence(dist, E_LAYER_KM)

    sec_d = min(sec_incidence(hop, D_LAYER_KM), 3.0)
    absorption_index = 0.0
    flares = []
    haf = 10 * math.log10(iono.xray_flux) + 65 if iono.xray_flux >= 1e-6 else 0.0
    for lat, lon in points:
        chi = solar_zenith(lat, lon, when)
        c = math.cos(math.radians(0.881 * chi)) if chi < 102 else 0.0
        index = (1 + 0.0037 * iono.ssn) * max(c, 0.0) ** 1.3 + 0.05
        absorption_index += index * sec_d
        if haf and chi < 90:
            flares.append((haf * math.cos(math.radians(chi)) ** 0.75, sec_d))
    absorption_index *= hops / len(points)
    flares = [(h, s * hops / len(points)) for h, s in flares]

    return Path(
        distance=dist, hops=hops, hmf2=hm,
        muf=max(f2_muf, e_muf), sigma=iono.sigma(nearest, when),
        f2_muf=f2_muf, e_muf=e_muf,
        absorption_index=absorption_index, flares=flares,
        spread_db=10 * math.log10(hops * slant_km(hop, hm) / 100),
        ground_db=2.0 * (hops - 1),
        nearest_ionosonde=nearest,
        atmospheric_db=atmospheric,
        storm_km=min(known) if known else None,
    )


def station_margin(mode, watts=100.0, gain_dbi=0.0):
    return MODES[mode] + 10 * math.log10(max(watts, 0.1) / 100) + gain_dbi


def rating(rel):
    for limit, name in RATINGS:
        if rel >= limit:
            return name
    return "Closed"


# ── choosing frequencies ──────────────────────────────────────────────────────

@dataclass
class Channel:
    name: str
    mhz: float


@dataclass
class ChannelResult:
    channel: Channel
    reliability: float          # mean over the targets
    worst: float
    covered: int                # targets with reliability >= 50 %
    loss_db: float              # mean loss
    muf_ratio: float            # mean frequency / MUF


def rank_channels(paths, channels, margin_db):
    """Score each channel across one or more paths; best first."""
    out = []
    for ch in channels:
        rels = [p.reliability(ch.mhz, margin_db) for p in paths]
        out.append(ChannelResult(
            channel=ch,
            reliability=sum(rels) / len(rels),
            worst=min(rels),
            covered=sum(r >= 0.5 for r in rels),
            loss_db=sum(p.loss_db(ch.mhz) for p in paths) / len(paths),
            muf_ratio=sum(ch.mhz / p.muf for p in paths) / len(paths),
        ))
    # Far below the MUF many channels score nearly the same. Within TIE_BAND of the top the
    # higher frequency goes first: it has less absorption and noise.
    top = max(r.reliability for r in out)
    out.sort(key=lambda r: (r.reliability >= top - TIE_BAND,
                            r.channel.mhz if r.reliability >= top - TIE_BAND else r.reliability),
             reverse=True)
    return out
