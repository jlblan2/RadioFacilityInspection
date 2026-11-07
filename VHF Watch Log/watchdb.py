"""Data layer for the VHF Communications Watch Log (SQLite, standard library only).

Tables
  vessels         facility number, name, radio call sign, cell, ...
  members         member number, name, phone
  qualifications  member x (COXSWAIN | CREW | PWC | TCO) -> QUALIFIED | TRAINEE | REYR
  coxswains/crew  VIEWS giving the two lists in the shape originally asked for
  channels, contacts, settings
  watches         one radio watch (opened/closed, watchstander, time check, radio check ...)
  patrols         a vessel guarded during a watch (PO#, GAR, POB, float plan, interval)
  patrol_crew     who is aboard each patrol (COX / CREW, trainee flag at the time)
  log_entries     the chronological radio log

Log-keeping rules (COMDTINST M2000.3G ch. 8 D.2.e, AOPG Vol III 3-F) enforced here:
  * all times stored in UTC ("Z"); shown in UTC and local time
  * entries are never deleted or edited; a mistake is struck through (with initials)
    and a corrected entry is added next to it
  * a closed watch is locked read-only (SQLite triggers refuse changes)
  * retention: 1 year, 3 years if the watch logged distress traffic
"""
import json
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone

APP = "VHF Watch Log"
SCHEMA_VERSION = 2

QUALS = ("COXSWAIN", "CREW", "PWC", "TCO")
QUAL_STATUS = ("QUALIFIED", "TRAINEE", "REYR")

# Entry types. CONTACT_TYPES restart the ops-normal clock for that vessel (M2000.3G ch. 5, CG Boat Communications, 8.a:
# "A new 30 minute period can be initiated after any communication with the boat").
ENTRY_TYPES = (
    "OPS NORMAL", "POSITION", "UNDERWAY", "MOORED", "COMMS", "WEATHER", "PHONE",
    "DISTRESS", "URGENCY", "SAFETY", "LOST COMMS", "RADIO CHECK", "EQUIPMENT",
    "WATCH RELIEF", "OTHER",
)
CONTACT_TYPES = {"OPS NORMAL", "POSITION", "UNDERWAY", "MOORED", "COMMS", "WEATHER",
                 "PHONE", "DISTRESS", "URGENCY", "SAFETY", "RADIO CHECK"}
DISTRESS_TYPES = {"DISTRESS", "URGENCY"}

DEFAULT_SETTINGS = {
    "station_call_sign": "Amelia 9",
    "default_channel": "83A",
    "ops_interval_min": "30",
    "due_soon_min": "5",
    "print_cell_numbers": "1",
    "incident_name": "Patrol communications guard",
}
DEFAULT_CHANNELS = [
    ("16", "Distress, safety and calling"),
    ("22A", "Coast Guard liaison / Maritime Safety Information"),
    ("83A", "Coast Guard / Auxiliary working"),
    ("21A", "Coast Guard working"),
    ("81A", "Coast Guard working"),
    ("9", "Calling (recreational) / Nassau Port"),
]
DEFAULT_CONTACTS = [
    ("COAST GUARD Station Mayport Comm", "904-564-7516", ""),
    ("COAST GUARD Station Mayport OOD", "904-564-7509", ""),
    ("COAST GUARD Sector Jacksonville", "904-714-7565", ""),
    ("Oil Spill (National Response Center)", "1-800-424-8802", ""),
    ("EMERGENCY SERVICE", "911", ""),
    ("NASSAU SHERIFF", "225-5174", ""),
    ("FERNANDINA BEACH POLICE", "277-7342", "NON-emergency only"),
    ("FERNANDINA BEACH Fire", "277-7331", ""),
    ("FERNANDINA TOW BOAT US", "912-638-7751", ""),
    ("SEA TOW", "904-220-7500", ""),
    ("Nassau Port (VHF 9)", "277-7004", ""),
    ("FWC Law Enforcement dispatch", "888-404-3922", ""),
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS vessels(
    id INTEGER PRIMARY KEY,
    facility_no TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL DEFAULT '',
    call_sign TEXT NOT NULL DEFAULT '',
    cell TEXT NOT NULL DEFAULT '',
    owner TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    mmsi TEXT NOT NULL DEFAULT '',
    inspection_expires TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1);

CREATE TABLE IF NOT EXISTS members(
    id INTEGER PRIMARY KEY,
    member_no TEXT NOT NULL UNIQUE,
    last_name TEXT NOT NULL,
    first_name TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1);

CREATE TABLE IF NOT EXISTS qualifications(
    member_id INTEGER NOT NULL REFERENCES members(id),
    qual TEXT NOT NULL CHECK (qual IN ('COXSWAIN','CREW','PWC','TCO')),
    status TEXT NOT NULL CHECK (status IN ('QUALIFIED','TRAINEE','REYR')),
    as_of TEXT NOT NULL DEFAULT '',
    PRIMARY KEY(member_id, qual));

CREATE VIEW IF NOT EXISTS coxswains AS
    SELECT m.id, m.last_name, m.first_name, m.member_no, m.phone, q.status
    FROM members m JOIN qualifications q ON q.member_id = m.id AND q.qual = 'COXSWAIN'
    WHERE m.active = 1;
CREATE VIEW IF NOT EXISTS crew AS
    SELECT m.id, m.last_name, m.first_name, m.member_no, q.status
    FROM members m JOIN qualifications q ON q.member_id = m.id AND q.qual = 'CREW'
    WHERE m.active = 1;

CREATE TABLE IF NOT EXISTS channels(
    name TEXT PRIMARY KEY, purpose TEXT NOT NULL DEFAULT '', sort INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS contacts(
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, phone TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '', sort INTEGER NOT NULL DEFAULT 0);

CREATE TABLE IF NOT EXISTS watches(
    id INTEGER PRIMARY KEY,
    station_call_sign TEXT NOT NULL,
    watchstander_id INTEGER REFERENCES members(id),
    watchstander TEXT NOT NULL DEFAULT '',
    channels TEXT NOT NULL DEFAULT '',
    opened_utc TEXT NOT NULL,
    closed_utc TEXT,
    time_check TEXT NOT NULL DEFAULT '',
    radio_check TEXT NOT NULL DEFAULT '',
    equipment_status TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    signed_by TEXT NOT NULL DEFAULT '',
    locked INTEGER NOT NULL DEFAULT 0,
    rws_id INTEGER REFERENCES radio_watch_standers(id),
    station_id INTEGER REFERENCES radio_stations(id),
    station_info TEXT NOT NULL DEFAULT '');

-- Radio Watch Standers: TCO-qualified operators who stand the radio watch.
CREATE TABLE IF NOT EXISTS radio_watch_standers(
    id INTEGER PRIMARY KEY,
    member_no TEXT NOT NULL UNIQUE,
    last_name TEXT NOT NULL,
    first_name TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL DEFAULT '',
    tco_date TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1);

-- Radio Stations: Auxiliary fixed land radio facilities that keep the watch.
CREATE TABLE IF NOT EXISTS radio_stations(
    id INTEGER PRIMARY KEY,
    call_sign TEXT NOT NULL UNIQUE,
    street TEXT NOT NULL DEFAULT '',
    city TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT '',
    zip TEXT NOT NULL DEFAULT '',
    latitude REAL,
    longitude REAL,
    phone TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1);

CREATE TABLE IF NOT EXISTS patrols(
    id INTEGER PRIMARY KEY,
    watch_id INTEGER NOT NULL REFERENCES watches(id),
    vessel_id INTEGER NOT NULL REFERENCES vessels(id),
    patrol_order TEXT NOT NULL DEFAULT '',
    gar TEXT NOT NULL DEFAULT '',
    pob INTEGER NOT NULL DEFAULT 0,
    cell TEXT NOT NULL DEFAULT '',
    mission TEXT NOT NULL DEFAULT '',
    float_plan TEXT NOT NULL DEFAULT '',
    interval_min INTEGER NOT NULL DEFAULT 30,
    start_utc TEXT,
    end_utc TEXT,
    status TEXT NOT NULL DEFAULT 'PLANNED' CHECK (status IN ('PLANNED','UNDERWAY','MOORED')));

CREATE TABLE IF NOT EXISTS patrol_crew(
    patrol_id INTEGER NOT NULL REFERENCES patrols(id),
    member_id INTEGER NOT NULL REFERENCES members(id),
    role TEXT NOT NULL CHECK (role IN ('COX','CREW')),
    trainee INTEGER NOT NULL DEFAULT 0,
    seq INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(patrol_id, member_id));

CREATE TABLE IF NOT EXISTS log_entries(
    id INTEGER PRIMARY KEY,
    watch_id INTEGER NOT NULL REFERENCES watches(id),
    patrol_id INTEGER REFERENCES patrols(id),
    station TEXT NOT NULL DEFAULT '',
    direction TEXT NOT NULL DEFAULT 'FROM' CHECK (direction IN ('FROM','TO')),
    time_utc TEXT NOT NULL,
    channel TEXT NOT NULL DEFAULT '',
    entry_type TEXT NOT NULL,
    position TEXT NOT NULL DEFAULT '',
    heading TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT '',
    remarks TEXT NOT NULL DEFAULT '',
    created_utc TEXT NOT NULL,
    corrects_id INTEGER REFERENCES log_entries(id),
    struck INTEGER NOT NULL DEFAULT 0,
    struck_by TEXT NOT NULL DEFAULT '',
    struck_utc TEXT);
CREATE INDEX IF NOT EXISTS log_by_watch ON log_entries(watch_id, time_utc);

-- Entries are never deleted, and only the strike-through columns may change.
CREATE TRIGGER IF NOT EXISTS log_no_delete BEFORE DELETE ON log_entries
BEGIN SELECT RAISE(ABORT, 'Log entries cannot be deleted; strike them through instead'); END;
CREATE TRIGGER IF NOT EXISTS log_no_edit BEFORE UPDATE OF
    watch_id, patrol_id, station, direction, time_utc, channel, entry_type, position,
    heading, status, remarks, created_utc, corrects_id ON log_entries
BEGIN SELECT RAISE(ABORT, 'Log entries cannot be edited; strike through and re-enter'); END;
CREATE TRIGGER IF NOT EXISTS log_no_unstrike BEFORE UPDATE OF struck ON log_entries
WHEN OLD.struck = 1
BEGIN SELECT RAISE(ABORT, 'A struck entry stays struck'); END;

-- A locked watch is read-only.
CREATE TRIGGER IF NOT EXISTS log_locked_insert BEFORE INSERT ON log_entries
WHEN (SELECT locked FROM watches WHERE id = NEW.watch_id) = 1
BEGIN SELECT RAISE(ABORT, 'This watch is closed and locked'); END;
CREATE TRIGGER IF NOT EXISTS log_locked_strike BEFORE UPDATE ON log_entries
WHEN (SELECT locked FROM watches WHERE id = OLD.watch_id) = 1
BEGIN SELECT RAISE(ABORT, 'This watch is closed and locked'); END;
CREATE TRIGGER IF NOT EXISTS watch_locked BEFORE UPDATE ON watches
WHEN OLD.locked = 1
BEGIN SELECT RAISE(ABORT, 'This watch is closed and locked'); END;
CREATE TRIGGER IF NOT EXISTS watch_no_delete BEFORE DELETE ON watches
BEGIN SELECT RAISE(ABORT, 'Watches cannot be deleted'); END;
CREATE TRIGGER IF NOT EXISTS patrol_locked_insert BEFORE INSERT ON patrols
WHEN (SELECT locked FROM watches WHERE id = NEW.watch_id) = 1
BEGIN SELECT RAISE(ABORT, 'This watch is closed and locked'); END;
CREATE TRIGGER IF NOT EXISTS patrol_locked_update BEFORE UPDATE ON patrols
WHEN (SELECT locked FROM watches WHERE id = OLD.watch_id) = 1
BEGIN SELECT RAISE(ABORT, 'This watch is closed and locked'); END;
CREATE TRIGGER IF NOT EXISTS patrol_no_delete BEFORE DELETE ON patrols
WHEN (SELECT locked FROM watches WHERE id = OLD.watch_id) = 1
   OR EXISTS (SELECT 1 FROM log_entries WHERE patrol_id = OLD.id)
BEGIN SELECT RAISE(ABORT, 'A patrol with log entries cannot be removed'); END;
CREATE TRIGGER IF NOT EXISTS crew_locked_ins BEFORE INSERT ON patrol_crew
WHEN (SELECT w.locked FROM watches w JOIN patrols p ON p.watch_id = w.id WHERE p.id = NEW.patrol_id) = 1
BEGIN SELECT RAISE(ABORT, 'This watch is closed and locked'); END;
CREATE TRIGGER IF NOT EXISTS crew_locked_del BEFORE DELETE ON patrol_crew
WHEN (SELECT w.locked FROM watches w JOIN patrols p ON p.watch_id = w.id WHERE p.id = OLD.patrol_id) = 1
BEGIN SELECT RAISE(ABORT, 'This watch is closed and locked'); END;
"""


# ---------------------------------------------------------------- time helpers

def utcnow():
    return datetime.now(timezone.utc).replace(microsecond=0)


def to_iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def from_iso(s):
    if not s:
        return None
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def fmt_z(s_or_dt):
    dt = from_iso(s_or_dt) if isinstance(s_or_dt, str) else s_or_dt
    return dt.strftime("%H%MZ") if dt else ""


def fmt_local(s_or_dt, tz=None):
    dt = from_iso(s_or_dt) if isinstance(s_or_dt, str) else s_or_dt
    return dt.astimezone(tz).strftime("%H%M") if dt else ""


def parse_date(text):
    """'03/15/2024', '3/15/24', '2024-03-15' or '15MAR2024' -> '2024-03-15'; '' stays ''."""
    t = str(text or "").strip()
    if not t:
        return ""
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%d%b%Y", "%d %b %Y", "%m-%d-%Y"):
        try:
            return datetime.strptime(t, fmt).date().isoformat()
        except ValueError:
            pass
    raise LogError(f"Not a date: {text!r} (use MM/DD/YYYY)")


def fmt_date(iso):
    return datetime.strptime(iso, "%Y-%m-%d").strftime("%m/%d/%Y") if iso else ""


def parse_coord(text, kind):
    """Latitude/longitude in decimal degrees (30.6681, -81.4612), degrees and decimal minutes
    (30 40.09 N, 081° 27.67' W) or degrees-minutes-seconds. Returns signed degrees or None."""
    if text is None or (isinstance(text, str) and not text.strip()):
        return None
    if isinstance(text, (int, float)):
        val = float(text)
    else:
        t = text.strip().upper()
        hemi = re.findall(r"[NSEW]", t)
        nums = [float(n) for n in re.findall(r"-?\d+(?:\.\d+)?", t)]
        if not nums or len(nums) > 3 or len(hemi) > 1:
            raise LogError(f"Cannot read {'latitude' if kind == 'lat' else 'longitude'} {text!r}")
        sign = -1 if nums[0] < 0 else 1
        val = abs(nums[0]) + (nums[1] / 60 if len(nums) > 1 else 0) + (nums[2] / 3600 if len(nums) > 2 else 0)
        if hemi:
            if (kind == "lat") != (hemi[0] in "NS"):
                raise LogError(f"{text!r}: use N/S for latitude and E/W for longitude")
            sign = -1 if hemi[0] in "SW" else 1
        val *= sign
    limit = 90 if kind == "lat" else 180
    if not -limit <= val <= limit:
        raise LogError(f"{'Latitude' if kind == 'lat' else 'Longitude'} out of range: {text!r}")
    return round(val, 6)


def fmt_coord(val, kind):
    """Degrees and decimal minutes, as on a chart: 30°40.09'N / 081°27.67'W."""
    if val is None or val == "":
        return ""
    val = float(val)
    hemi = ("N" if val >= 0 else "S") if kind == "lat" else ("E" if val >= 0 else "W")
    a = abs(val)
    d = int(a)
    m = round((a - d) * 60, 2)
    if m >= 60:
        d, m = d + 1, 0.0
    return f"{d:0{2 if kind == 'lat' else 3}d}°{m:05.2f}'{hemi}"


def station_lines(s):
    """Text lines for a radio-station block (dict or Row with the station fields)."""
    if not s:
        return []
    addr2 = ", ".join(x for x in (s["city"], " ".join(x for x in (s["state"], s["zip"]) if x)) if x)
    lines = [("Call sign", s["call_sign"]),
             ("Address", ", ".join(x for x in (s["street"], addr2) if x)),
             ("Lat / Long", (f"{fmt_coord(s['latitude'], 'lat')}  {fmt_coord(s['longitude'], 'lon')}"
                             f"  ({s['latitude']:.5f}, {s['longitude']:.5f})")
              if s["latitude"] is not None and s["longitude"] is not None else ""),
             ("Contact phone", s["phone"])]
    return [(k, v) for k, v in lines if v]


def tz_abbr(dt):
    """'EST'/'EDT' even where Windows reports 'Eastern Standard Time'."""
    name = dt.strftime("%Z") or "local"
    return "".join(w[0] for w in name.split()) if " " in name else name


def parse_time(text, now=None, tz=None):
    """Turn a typed time into a UTC datetime.

    "" -> now.  "1432" / "14:32" -> local time today.  "1832Z" -> UTC today.
    A time more than 10 minutes in the future is taken to be yesterday (late entry
    across midnight).
    """
    now = now or utcnow()
    t = (text or "").strip().upper().replace(":", "")
    if not t:
        return now
    is_z = t.endswith("Z")
    if is_z:
        t = t[:-1].strip()
    if not (t.isdigit() and len(t) in (3, 4)):
        raise ValueError(f"Time must be HHMM (local) or HHMMZ (UTC), not {text!r}")
    hh, mm = int(t[:-2]), int(t[-2:])
    if hh > 23 or mm > 59:
        raise ValueError(f"Not a valid time: {text!r}")
    if is_z:
        dt = now.replace(hour=hh, minute=mm, second=0)
    else:
        local_now = now.astimezone(tz)
        dt = local_now.replace(hour=hh, minute=mm, second=0).astimezone(timezone.utc)
    if dt - now > timedelta(minutes=10):
        dt -= timedelta(days=1)
    return dt


def default_db_path():
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, "N. Tesla", APP, "watchlog.db")


# ---------------------------------------------------------------- database

class LogError(Exception):
    pass


class WatchDB:
    def __init__(self, path=None):
        self.path = path or default_db_path()
        if self.path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        self.con = sqlite3.connect(self.path)
        self.con.row_factory = sqlite3.Row
        self.con.execute("PRAGMA foreign_keys = ON")
        self._init()

    def close(self):
        self.con.close()

    def _init(self):
        fresh = not self.con.execute(
            "SELECT 1 FROM sqlite_master WHERE name='meta'").fetchone()
        self.con.executescript(SCHEMA)
        if fresh:
            with self.con:
                self.con.execute("INSERT INTO meta VALUES('schema_version', ?)", (str(SCHEMA_VERSION),))
                self.con.executemany("INSERT OR IGNORE INTO settings VALUES(?,?)", DEFAULT_SETTINGS.items())
                self.con.executemany("INSERT OR IGNORE INTO channels VALUES(?,?,?)",
                                     [(n, p, i) for i, (n, p) in enumerate(DEFAULT_CHANNELS)])
                self.con.executemany("INSERT INTO contacts(name, phone, notes, sort) VALUES(?,?,?,?)",
                                     [(n, ph, no, i) for i, (n, ph, no) in enumerate(DEFAULT_CONTACTS)])
        else:
            self._migrate()
            with self.con:
                self.con.executemany("INSERT OR IGNORE INTO settings VALUES(?,?)", DEFAULT_SETTINGS.items())

    def _migrate(self):
        """Bring an older database up to SCHEMA_VERSION without touching logged data."""
        cols = {r["name"] for r in self.con.execute("PRAGMA table_info(watches)")}
        with self.con:
            for name, decl in (("rws_id", "INTEGER REFERENCES radio_watch_standers(id)"),
                               ("station_id", "INTEGER REFERENCES radio_stations(id)"),
                               ("station_info", "TEXT NOT NULL DEFAULT ''")):
                if name not in cols:
                    self.con.execute(f"ALTER TABLE watches ADD COLUMN {name} {decl}")
            self.con.execute("INSERT OR REPLACE INTO meta VALUES('schema_version', ?)", (str(SCHEMA_VERSION),))

    def _run(self, sql, args=()):
        try:
            with self.con:
                return self.con.execute(sql, args)
        except sqlite3.IntegrityError as e:
            raise LogError(str(e)) from None
        except sqlite3.DatabaseError as e:
            if "locked" in str(e) or "cannot" in str(e) or "struck" in str(e):
                raise LogError(str(e)) from None
            raise

    def backup(self, dest):
        out = sqlite3.connect(dest)
        with out:
            self.con.backup(out)
        out.close()

    # ---- settings
    def setting(self, key):
        r = self.con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return r["value"] if r else DEFAULT_SETTINGS.get(key, "")

    def set_setting(self, key, value):
        self._run("INSERT OR REPLACE INTO settings VALUES(?,?)", (key, str(value)))

    def interval_min(self):
        try:
            return max(1, int(self.setting("ops_interval_min")))
        except ValueError:
            return 30

    # ---- vessels
    VESSEL_FIELDS = ("facility_no", "name", "call_sign", "cell", "owner", "description",
                     "mmsi", "inspection_expires", "active")

    def vessels(self, active_only=False):
        sql = "SELECT * FROM vessels" + (" WHERE active=1" if active_only else "")
        return self.con.execute(sql + " ORDER BY facility_no").fetchall()

    def vessel(self, vid):
        return self.con.execute("SELECT * FROM vessels WHERE id=?", (vid,)).fetchone()

    def save_vessel(self, data, vid=None):
        data = {k: data.get(k, "") for k in self.VESSEL_FIELDS if k in data}
        if not str(data.get("facility_no", "")).strip() and vid is None:
            raise LogError("Facility number is required")
        if vid is None:
            cols = ",".join(data)
            cur = self._run(f"INSERT INTO vessels({cols}) VALUES({','.join('?' * len(data))})",
                            tuple(data.values()))
            return cur.lastrowid
        sets = ",".join(f"{k}=?" for k in data)
        self._run(f"UPDATE vessels SET {sets} WHERE id=?", (*data.values(), vid))
        return vid

    # ---- members and qualifications
    def members(self, active_only=False):
        sql = "SELECT * FROM members" + (" WHERE active=1" if active_only else "")
        return self.con.execute(sql + " ORDER BY last_name, first_name").fetchall()

    def member(self, mid):
        return self.con.execute("SELECT * FROM members WHERE id=?", (mid,)).fetchone()

    def member_by_no(self, member_no):
        return self.con.execute("SELECT * FROM members WHERE member_no=?",
                                (str(member_no).strip(),)).fetchone()

    def save_member(self, data, quals=None, mid=None):
        """data: member_no, last_name, first_name, phone, active.
        quals: {qual: status or ''}; '' removes the qualification."""
        no = str(data.get("member_no", "")).strip()
        if mid is None and not no:
            raise LogError("Member number is required")
        if no and not (no.isdigit() and len(no) == 7):
            raise LogError(f"Member number must be 7 digits, not {no!r}")
        if mid is None and not str(data.get("last_name", "")).strip():
            raise LogError("Last name is required")
        fields = {k: data[k] for k in ("member_no", "last_name", "first_name", "phone", "active") if k in data}
        try:
            with self.con:
                if mid is None:
                    cur = self.con.execute(
                        f"INSERT INTO members({','.join(fields)}) VALUES({','.join('?' * len(fields))})",
                        tuple(fields.values()))
                    mid = cur.lastrowid
                elif fields:
                    self.con.execute(f"UPDATE members SET {','.join(f'{k}=?' for k in fields)} WHERE id=?",
                                     (*fields.values(), mid))
                for qual, status in (quals or {}).items():
                    qual = qual.upper()
                    status = (status or "").upper()
                    if qual not in QUALS:
                        raise LogError(f"Unknown qualification {qual}")
                    if not status:
                        self.con.execute("DELETE FROM qualifications WHERE member_id=? AND qual=?", (mid, qual))
                    else:
                        if status not in QUAL_STATUS:
                            raise LogError(f"Status must be one of {', '.join(QUAL_STATUS)}")
                        self.con.execute(
                            "INSERT INTO qualifications(member_id, qual, status, as_of) VALUES(?,?,?,?) "
                            "ON CONFLICT(member_id, qual) DO UPDATE SET status=excluded.status, "
                            "as_of=CASE WHEN qualifications.status=excluded.status THEN qualifications.as_of "
                            "ELSE excluded.as_of END",
                            (mid, qual, status, utcnow().date().isoformat()))
        except sqlite3.IntegrityError as e:
            raise LogError("That member number is already in the list" if "UNIQUE" in str(e) else str(e)) from None
        return mid

    def quals(self, mid):
        return {r["qual"]: r["status"] for r in
                self.con.execute("SELECT qual, status FROM qualifications WHERE member_id=?", (mid,))}

    def coxswains(self):
        return self.con.execute("SELECT * FROM coxswains ORDER BY status, last_name, first_name").fetchall()

    def crew(self):
        return self.con.execute("SELECT * FROM crew ORDER BY last_name, first_name").fetchall()

    # ---- channels / contacts
    def channels(self):
        return self.con.execute("SELECT * FROM channels ORDER BY sort, name").fetchall()

    def set_channels(self, rows):
        with self.con:
            self.con.execute("DELETE FROM channels")
            self.con.executemany("INSERT INTO channels VALUES(?,?,?)",
                                 [(n.strip(), p.strip(), i) for i, (n, p) in enumerate(rows) if n.strip()])

    def contacts(self):
        return self.con.execute("SELECT * FROM contacts ORDER BY sort, id").fetchall()

    def set_contacts(self, rows):
        with self.con:
            self.con.execute("DELETE FROM contacts")
            self.con.executemany("INSERT INTO contacts(name, phone, notes, sort) VALUES(?,?,?,?)",
                                 [(n.strip(), ph.strip(), no.strip(), i)
                                  for i, (n, ph, no) in enumerate(rows) if n.strip()])

    # ---- radio watch standers
    RWS_FIELDS = ("member_no", "last_name", "first_name", "phone", "tco_date", "active")

    def watchstanders(self, active_only=False):
        sql = "SELECT * FROM radio_watch_standers" + (" WHERE active=1" if active_only else "")
        return self.con.execute(sql + " ORDER BY last_name, first_name").fetchall()

    def watchstander(self, rid):
        return self.con.execute("SELECT * FROM radio_watch_standers WHERE id=?", (rid,)).fetchone()

    def save_watchstander(self, data, rid=None):
        data = {k: data[k] for k in self.RWS_FIELDS if k in data}
        if "member_no" in data:
            no = str(data["member_no"]).strip()
            if not (no.isdigit() and len(no) == 7):
                raise LogError(f"Member number must be 7 digits, not {no!r}")
            data["member_no"] = no
        if rid is None and not str(data.get("last_name", "")).strip():
            raise LogError("Last name is required")
        if rid is None and "member_no" not in data:
            raise LogError("Member number is required")
        if "tco_date" in data:
            data["tco_date"] = parse_date(data["tco_date"])
        try:
            with self.con:
                if rid is None:
                    return self.con.execute(
                        f"INSERT INTO radio_watch_standers({','.join(data)}) VALUES({','.join('?' * len(data))})",
                        tuple(data.values())).lastrowid
                self.con.execute(f"UPDATE radio_watch_standers SET {','.join(f'{k}=?' for k in data)} WHERE id=?",
                                 (*data.values(), rid))
                return rid
        except sqlite3.IntegrityError as e:
            raise LogError("That member number is already in the list" if "UNIQUE" in str(e) else str(e)) from None

    @staticmethod
    def watchstander_label(r):
        s = r["last_name"] + (f", {r['first_name']}" if r["first_name"] else "") + f" ({r['member_no']})"
        return s

    # ---- radio stations
    STATION_FIELDS = ("call_sign", "street", "city", "state", "zip", "latitude", "longitude", "phone", "active")

    def stations(self, active_only=False):
        sql = "SELECT * FROM radio_stations" + (" WHERE active=1" if active_only else "")
        return self.con.execute(sql + " ORDER BY call_sign").fetchall()

    def station(self, sid):
        return self.con.execute("SELECT * FROM radio_stations WHERE id=?", (sid,)).fetchone()

    def save_station(self, data, sid=None):
        data = {k: data[k] for k in self.STATION_FIELDS if k in data}
        if "call_sign" in data:
            data["call_sign"] = str(data["call_sign"]).strip()
        if sid is None and not data.get("call_sign"):
            raise LogError("Call sign is required")
        if "latitude" in data:
            data["latitude"] = parse_coord(data["latitude"], "lat")
        if "longitude" in data:
            data["longitude"] = parse_coord(data["longitude"], "lon")
        if "state" in data:
            data["state"] = str(data["state"]).strip().upper()
        try:
            with self.con:
                if sid is None:
                    return self.con.execute(
                        f"INSERT INTO radio_stations({','.join(data)}) VALUES({','.join('?' * len(data))})",
                        tuple(data.values())).lastrowid
                self.con.execute(f"UPDATE radio_stations SET {','.join(f'{k}=?' for k in data)} WHERE id=?",
                                 (*data.values(), sid))
                return sid
        except sqlite3.IntegrityError as e:
            raise LogError("That call sign is already in the list" if "UNIQUE" in str(e) else str(e)) from None

    def _station_snapshot(self, sid):
        s = self.station(sid)
        if s is None:
            raise LogError("Unknown radio station")
        return s, json.dumps({k: s[k] for k in self.STATION_FIELDS if k != "active"})

    def watch_station(self, wid):
        """The station details as they were when the watch was opened (dict), or None."""
        w = self.watch(wid)
        try:
            return json.loads(w["station_info"]) if w and w["station_info"] else None
        except ValueError:
            return None

    def set_watch_station(self, wid, sid):
        s, snap = self._station_snapshot(sid)
        self._run("UPDATE watches SET station_id=?, station_info=?, station_call_sign=? WHERE id=?",
                  (sid, snap, s["call_sign"], wid))

    # ---- watches
    def open_watch(self, watchstander_id=None, watchstander="", channels="", time_check="",
                   radio_check="", equipment_status="", station_call_sign=None, when=None,
                   rws_id=None, station_id=None):
        if rws_id and not watchstander:
            watchstander = self.watchstander_label(self.watchstander(rws_id))
        if watchstander_id and not watchstander:
            m = self.member(watchstander_id)
            watchstander = f"{m['last_name']}, {m['first_name']} ({m['member_no']})".replace(", (", " (")
        info = ""
        if station_id:
            s, info = self._station_snapshot(station_id)
            station_call_sign = station_call_sign or s["call_sign"]
        cur = self._run(
            "INSERT INTO watches(station_call_sign, watchstander_id, watchstander, channels, opened_utc,"
            " time_check, radio_check, equipment_status, rws_id, station_id, station_info)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (station_call_sign or self.setting("station_call_sign"), watchstander_id, watchstander,
             channels or self.setting("default_channel"), to_iso(when or utcnow()),
             time_check, radio_check, equipment_status, rws_id, station_id, info))
        return cur.lastrowid

    def watch(self, wid):
        return self.con.execute("SELECT * FROM watches WHERE id=?", (wid,)).fetchone()

    def watches(self):
        return self.con.execute("SELECT * FROM watches ORDER BY opened_utc DESC").fetchall()

    def current_watch(self):
        return self.con.execute(
            "SELECT * FROM watches WHERE locked=0 ORDER BY opened_utc DESC LIMIT 1").fetchone()

    def update_watch(self, wid, **fields):
        allowed = {"station_call_sign", "watchstander_id", "watchstander", "rws_id", "channels",
                   "time_check", "radio_check", "equipment_status", "notes"}
        bad = set(fields) - allowed
        if bad:
            raise LogError(f"Cannot change {', '.join(bad)}")
        if fields:
            self._run(f"UPDATE watches SET {','.join(f'{k}=?' for k in fields)} WHERE id=?",
                      (*fields.values(), wid))

    def close_watch(self, wid, signed_by, when=None):
        if not (signed_by or "").strip():
            raise LogError("The watchstander must sign (name or initials) to close the watch")
        self._run("UPDATE watches SET closed_utc=?, signed_by=?, locked=1 WHERE id=?",
                  (to_iso(when or utcnow()), signed_by.strip(), wid))

    def has_distress(self, wid):
        q = ",".join("?" * len(DISTRESS_TYPES))
        return bool(self.con.execute(
            f"SELECT 1 FROM log_entries WHERE watch_id=? AND entry_type IN ({q}) LIMIT 1",
            (wid, *DISTRESS_TYPES)).fetchone())

    def retain_until(self, wid):
        """AOPG Vol III 3-F.3: one year, three years when distress traffic was logged."""
        w = self.watch(wid)
        end = from_iso(w["closed_utc"] or w["opened_utc"])
        years = 3 if self.has_distress(wid) else 1
        try:
            return end.replace(year=end.year + years).date()
        except ValueError:  # 29 Feb
            return (end + timedelta(days=365 * years + 1)).date()

    def past_retention(self, today=None):
        today = today or utcnow().date()
        return [w for w in self.watches() if w["locked"] and self.retain_until(w["id"]) < today]

    # ---- patrols
    def add_patrol(self, wid, vessel_id, coxswain_id=None, crew_ids=(), patrol_order="", gar="",
                   pob=None, cell=None, mission="", float_plan="", interval_min=None):
        v = self.vessel(vessel_id)
        if v is None:
            raise LogError("Unknown vessel")
        people = ([coxswain_id] if coxswain_id else []) + [c for c in crew_ids if c != coxswain_id]
        try:
            with self.con:
                cur = self.con.execute(
                    "INSERT INTO patrols(watch_id, vessel_id, patrol_order, gar, pob, cell, mission,"
                    " float_plan, interval_min) VALUES(?,?,?,?,?,?,?,?,?)",
                    (wid, vessel_id, patrol_order, gar, pob if pob is not None else len(people),
                     v["cell"] if cell is None else cell, mission, float_plan,
                     interval_min or self.interval_min()))
                pid = cur.lastrowid
                self._set_crew(pid, coxswain_id, crew_ids)
        except sqlite3.DatabaseError as e:
            raise LogError(str(e)) from None
        return pid

    def _set_crew(self, pid, coxswain_id, crew_ids):
        self.con.execute("DELETE FROM patrol_crew WHERE patrol_id=?", (pid,))
        seq = 0
        if coxswain_id:
            q = self.quals(coxswain_id)
            self.con.execute("INSERT INTO patrol_crew VALUES(?,?,?,?,?)",
                             (pid, coxswain_id, "COX", int(q.get("COXSWAIN") == "TRAINEE"), seq))
        for mid in crew_ids:
            if mid == coxswain_id:
                continue
            seq += 1
            q = self.quals(mid)
            self.con.execute("INSERT INTO patrol_crew VALUES(?,?,?,?,?)",
                             (pid, mid, "CREW", int(q.get("CREW", "TRAINEE") == "TRAINEE"), seq))

    def update_patrol(self, pid, coxswain_id=None, crew_ids=None, **fields):
        allowed = {"vessel_id", "patrol_order", "gar", "pob", "cell", "mission", "float_plan", "interval_min"}
        bad = set(fields) - allowed
        if bad:
            raise LogError(f"Cannot change {', '.join(bad)}")
        try:
            with self.con:
                if fields:
                    self.con.execute(f"UPDATE patrols SET {','.join(f'{k}=?' for k in fields)} WHERE id=?",
                                     (*fields.values(), pid))
                if crew_ids is not None:
                    self._set_crew(pid, coxswain_id, crew_ids)
        except sqlite3.DatabaseError as e:
            raise LogError(str(e)) from None

    def remove_patrol(self, pid):
        try:
            with self.con:
                self.con.execute("DELETE FROM patrol_crew WHERE patrol_id=?", (pid,))
                self.con.execute("DELETE FROM patrols WHERE id=?", (pid,))
        except sqlite3.DatabaseError as e:
            raise LogError(str(e)) from None

    def patrol(self, pid):
        return self.con.execute(
            "SELECT p.*, v.facility_no, v.name AS vessel_name, v.call_sign FROM patrols p "
            "JOIN vessels v ON v.id = p.vessel_id WHERE p.id=?", (pid,)).fetchone()

    def patrols(self, wid):
        return self.con.execute(
            "SELECT p.*, v.facility_no, v.name AS vessel_name, v.call_sign FROM patrols p "
            "JOIN vessels v ON v.id = p.vessel_id WHERE p.watch_id=? ORDER BY p.id", (wid,)).fetchall()

    def patrol_crew(self, pid):
        return self.con.execute(
            "SELECT pc.*, m.last_name, m.first_name, m.member_no, m.phone FROM patrol_crew pc "
            "JOIN members m ON m.id = pc.member_id WHERE pc.patrol_id=? ORDER BY pc.seq", (pid,)).fetchall()

    # ---- log entries
    def add_entry(self, wid, entry_type, time=None, patrol_id=None, station="", direction="FROM",
                  channel=None, position="", heading="", status="", remarks="", corrects_id=None):
        entry_type = entry_type.upper()
        if entry_type not in ENTRY_TYPES:
            raise LogError(f"Unknown entry type {entry_type}")
        if entry_type in DISTRESS_TYPES and not (status or remarks):
            raise LogError("Distress and urgency entries need a short synopsis (status or remarks)")
        when = time if isinstance(time, datetime) else (from_iso(time) if time else utcnow())
        p = None
        if patrol_id:
            p = self.patrol(patrol_id)
            if p is None or p["watch_id"] != wid:
                raise LogError("That patrol is not part of this watch")
            station = station or p["facility_no"]
        if not station:
            raise LogError("Say which vessel or station the entry is for")
        try:
            with self.con:
                cur = self.con.execute(
                    "INSERT INTO log_entries(watch_id, patrol_id, station, direction, time_utc, channel,"
                    " entry_type, position, heading, status, remarks, created_utc, corrects_id)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (wid, patrol_id, station, direction, to_iso(when),
                     channel if channel is not None else self.setting("default_channel"),
                     entry_type, position, heading, status, remarks, to_iso(utcnow()), corrects_id))
                # Underway / moored entries move the patrol along.
                if p is not None and entry_type == "UNDERWAY":
                    self.con.execute(
                        "UPDATE patrols SET status='UNDERWAY', start_utc=COALESCE(start_utc, ?), end_utc=NULL"
                        " WHERE id=?", (to_iso(when), patrol_id))
                elif p is not None and entry_type == "MOORED":
                    self.con.execute("UPDATE patrols SET status='MOORED', end_utc=? WHERE id=?",
                                     (to_iso(when), patrol_id))
        except sqlite3.DatabaseError as e:
            raise LogError(str(e)) from None
        return cur.lastrowid

    def strike(self, entry_id, initials):
        if not (initials or "").strip():
            raise LogError("Initials are required to strike an entry")
        e = self.entry(entry_id)
        if e is None:
            raise LogError("No such entry")
        if e["struck"]:
            raise LogError("That entry is already struck")
        self._run("UPDATE log_entries SET struck=1, struck_by=?, struck_utc=? WHERE id=?",
                  (initials.strip().upper(), to_iso(utcnow()), entry_id))

    def correct(self, entry_id, initials, **new):
        """Strike an entry and add its replacement in one step."""
        e = self.entry(entry_id)
        if e is None:
            raise LogError("No such entry")
        fields = {k: e[k] for k in ("entry_type", "patrol_id", "station", "direction", "channel",
                                    "position", "heading", "status", "remarks")}
        fields["time"] = e["time_utc"]
        fields.update(new)
        if e["struck"]:
            raise LogError("That entry is already struck")
        if not (initials or "").strip():
            raise LogError("Initials are required to strike an entry")
        new_id = self.add_entry(e["watch_id"], corrects_id=entry_id, **fields)
        self.strike(entry_id, initials)
        return new_id

    def entry(self, eid):
        return self.con.execute("SELECT * FROM log_entries WHERE id=?", (eid,)).fetchone()

    def entries(self, wid):
        return self.con.execute(
            "SELECT * FROM log_entries WHERE watch_id=? ORDER BY time_utc, id", (wid,)).fetchall()

    # ---- ops-normal schedule
    def last_contact(self, pid):
        q = ",".join("?" * len(CONTACT_TYPES))
        r = self.con.execute(
            f"SELECT MAX(time_utc) AS t FROM log_entries WHERE patrol_id=? AND struck=0"
            f" AND entry_type IN ({q})", (pid, *CONTACT_TYPES)).fetchone()
        return from_iso(r["t"]) if r and r["t"] else None

    def schedule(self, wid, now=None):
        """One row per patrol: last contact, next report due, and state.

        state: PLANNED, MOORED, OK, DUE SOON, OVERDUE.
        """
        now = now or utcnow()
        soon = timedelta(minutes=int(self.setting("due_soon_min") or 5))
        out = []
        for p in self.patrols(wid):
            last = self.last_contact(p["id"])
            due = None
            if p["status"] == "UNDERWAY":
                base = max(filter(None, [last, from_iso(p["start_utc"])]))
                due = base + timedelta(minutes=p["interval_min"])
                if now > due:
                    state = "OVERDUE"
                elif due - now <= soon:
                    state = "DUE SOON"
                else:
                    state = "OK"
            else:
                state = p["status"]
            out.append({"patrol": p, "last": last, "due": due, "state": state,
                        "overdue_min": int((now - due).total_seconds() // 60) if state == "OVERDUE" else 0})
        return out

    # ---- summaries
    def patrol_summary(self, wid):
        """Numbers for the ANSC 7030 mission activity report."""
        rows = []
        for p in self.patrols(wid):
            crew = self.patrol_crew(p["id"])
            start, end = from_iso(p["start_utc"]), from_iso(p["end_utc"])
            hours = round((end - start).total_seconds() / 3600, 1) if start and end else None
            rows.append({
                "facility_no": p["facility_no"], "vessel": p["vessel_name"],
                "patrol_order": p["patrol_order"], "gar": p["gar"], "pob": p["pob"],
                "coxswain": next((f"{c['last_name']} ({c['member_no']})" for c in crew if c["role"] == "COX"), ""),
                "crew": "; ".join(f"{c['last_name']} ({c['member_no']}){' T' if c['trainee'] else ''}"
                                  for c in crew if c["role"] == "CREW"),
                "underway_utc": p["start_utc"] or "", "moored_utc": p["end_utc"] or "",
                "hours": hours, "mission": p["mission"],
            })
        return rows

    # ---- CSV import
    def import_members_csv(self, path):
        """Columns (header row, any order, case-insensitive): member_no, last_name, first_name,
        phone, coxswain, crew, pwc, tco. Qualification columns hold Qualified/Trainee/REYR or blank.
        Existing members (same member number) are updated. Returns (added, updated)."""
        import csv
        added = updated = 0
        with open(path, newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                row = {(k or "").strip().lower().replace(" ", "_"): (v or "").strip() for k, v in row.items()}
                no = row.get("member_no") or row.get("member_number") or row.get("emplid") or ""
                if not no:
                    continue
                data = {"member_no": no, "last_name": row.get("last_name") or row.get("last", ""),
                        "first_name": row.get("first_name") or row.get("first", ""),
                        "phone": row.get("phone", "")}
                quals = {q: row[q.lower()].upper() for q in QUALS if row.get(q.lower())}
                existing = self.member_by_no(no)
                if existing:
                    data = {k: v for k, v in data.items() if v}
                    self.save_member(data, quals, mid=existing["id"])
                    updated += 1
                else:
                    self.save_member(data, quals)
                    added += 1
        return added, updated

    def import_vessels_csv(self, path):
        """Columns: facility_no, name, call_sign, cell, owner, description, mmsi, inspection_expires."""
        import csv
        added = updated = 0
        with open(path, newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                row = {(k or "").strip().lower().replace(" ", "_"): (v or "").strip() for k, v in row.items()}
                no = row.get("facility_no") or row.get("facility_number") or ""
                if not no:
                    continue
                data = {k: row[k] for k in self.VESSEL_FIELDS if k in row and k != "active"}
                data["facility_no"] = no
                existing = self.con.execute("SELECT id FROM vessels WHERE facility_no=?", (no,)).fetchone()
                if existing:
                    self.save_vessel(data, existing["id"])
                    updated += 1
                else:
                    self.save_vessel(data)
                    added += 1
        return added, updated

    def _import_csv(self, path, key_names, key_field, fields, table, key_col, saver):
        import csv
        added = updated = 0
        with open(path, newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                row = {(k or "").strip().lower().replace(" ", "_").replace("/", "_"): (v or "").strip()
                       for k, v in row.items()}
                key = next((row[k] for k in key_names if row.get(k)), "")
                if not key:
                    continue
                data = {k: row[k] for k in fields if k in row and row[k] != ""}
                data[key_field] = key
                existing = self.con.execute(f"SELECT id FROM {table} WHERE {key_col}=?", (key,)).fetchone()
                if existing:
                    saver(data, existing["id"])
                    updated += 1
                else:
                    saver(data)
                    added += 1
        return added, updated

    def import_watchstanders_csv(self, path):
        """Columns: member_no, last_name, first_name, phone, tco_date."""
        return self._import_csv(path, ("member_no", "member_number", "emplid"), "member_no",
                                ("last_name", "first_name", "phone", "tco_date"),
                                "radio_watch_standers", "member_no", self.save_watchstander)

    def import_stations_csv(self, path):
        """Columns: call_sign, street, city, state, zip, latitude, longitude, phone."""
        return self._import_csv(path, ("call_sign", "callsign"), "call_sign",
                                ("street", "city", "state", "zip", "latitude", "longitude", "phone"),
                                "radio_stations", "call_sign", self.save_station)
