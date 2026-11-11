"""VHF Communications Watch Log - Tkinter desktop app (standard library only).

    python watchlog.py                 normal use (database in %APPDATA%\\N. Tesla\\VHF Watch Log)
    python watchlog.py --db FILE       use another database file
    python watchlog.py --demo FILE     create FILE with made-up vessels/members to try the app
"""
import argparse
import os
import sys
import tkinter as tk
import tkinter.font as tkfont
import webbrowser
from datetime import datetime, timedelta, timezone
from tkinter import filedialog, messagebox, simpledialog, ttk

import reports
import watchdb as wdb
from watchdb import (APP, ENTRY_TYPES, QUAL_STATUS, LogError, WatchDB, fmt_local, fmt_z,
                     from_iso, parse_time, to_iso, utcnow)

VERSION = "1.0"
COLORS = {"OVERDUE": "#f6c3bd", "DUE SOON": "#ffe7a0", "OK": "#d8f0d2", "MOORED": "#e4e4e4",
          "PLANNED": "#ffffff"}


def print_dir():
    d = os.path.join(os.path.dirname(wdb.default_db_path()), "prints")
    os.makedirs(d, exist_ok=True)
    return d


def open_html(text, name):
    path = os.path.join(print_dir(), name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    webbrowser.open("file:///" + path.replace("\\", "/"))
    return path


def member_label(m, status=None):
    s = f"{m['last_name']}"
    if m["first_name"]:
        s += f", {m['first_name']}"
    s += f" ({m['member_no']})"
    if status and status != "QUALIFIED":
        s += f" [{status.title() if status != 'REYR' else 'REYR'}]"
    return s


def center(win, parent):
    win.update_idletasks()
    x = parent.winfo_rootx() + (parent.winfo_width() - win.winfo_reqwidth()) // 2
    y = parent.winfo_rooty() + (parent.winfo_height() - win.winfo_reqheight()) // 3
    win.geometry(f"+{max(0, x)}+{max(0, y)}")


class Dialog(tk.Toplevel):
    """Modal dialog with OK/Cancel. Subclasses fill self.body and implement apply()."""

    def __init__(self, parent, title, ok_text="OK"):
        super().__init__(parent)
        self.withdraw()
        self.title(title)
        self.transient(parent)
        self.resizable(False, False)
        self.result = None
        self.body = ttk.Frame(self, padding=12)
        self.body.pack(fill="both", expand=True)
        self.build()
        bar = ttk.Frame(self, padding=(12, 0, 12, 12))
        bar.pack(fill="x")
        ttk.Button(bar, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(bar, text=ok_text, command=self._ok, default="active").pack(side="right", padx=6)
        self.bind("<Escape>", lambda e: self.destroy())
        center(self, parent)
        self.deiconify()
        self.grab_set()

    def build(self):
        pass

    def _ok(self):
        try:
            self.result = self.apply()
        except (LogError, ValueError) as e:
            messagebox.showerror(self.title(), str(e), parent=self)
            return
        if self.result is not False:
            self.destroy()

    def apply(self):
        return True

    def row(self, r, label, widget, hint=None):
        ttk.Label(self.body, text=label).grid(row=r, column=0, sticky="w", pady=3, padx=(0, 8))
        widget.grid(row=r, column=1, sticky="we", pady=3)
        if hint:
            ttk.Label(self.body, text=hint, foreground="#666").grid(row=r, column=2, sticky="w", padx=6)
        return widget


# ---------------------------------------------------------------- dialogs

def rws_label(r):
    s = WatchDB.watchstander_label(r)
    if r["tco_date"]:
        s += f" - TCO {wdb.fmt_date(r['tco_date'])}"
    return s


class WatchPeoplePicker:
    """Station and watchstander choices shared by the Open-watch and Watch-details dialogs."""

    def _setup_pickers(self):
        db = self.db
        self.stations = db.stations(active_only=True)
        self.rws = db.watchstanders(active_only=True)
        self.station_box = ttk.Combobox(self.body, width=40, values=[s["call_sign"] for s in self.stations])
        if self.rws:
            self.ws_values = {rws_label(r): ("rws", r["id"]) for r in self.rws}
        else:  # no Radio Watch Standers yet: offer members, TCO-qualified first
            members = db.members(active_only=True)
            tco = {m["id"] for m in members if db.quals(m["id"]).get("TCO") == "QUALIFIED"}
            members.sort(key=lambda m: (m["id"] not in tco, m["last_name"], m["first_name"]))
            self.ws_values = {member_label(m) + (" - TCO" if m["id"] in tco else ""): ("member", m["id"])
                              for m in members}
        self.ws = ttk.Combobox(self.body, width=40, values=list(self.ws_values))

    def _station_id(self):
        cs = self.station_box.get().strip()
        return next((s["id"] for s in self.stations if s["call_sign"] == cs), None), cs

    def _watchstander(self):
        name = self.ws.get().strip()
        if not name:
            raise LogError("Enter the watchstander")
        kind, ident = self.ws_values.get(name, (None, None))
        if kind == "rws":
            return {"rws_id": ident, "watchstander": WatchDB.watchstander_label(self.db.watchstander(ident))}
        if kind == "member":
            return {"watchstander_id": ident, "watchstander": name.removesuffix(" - TCO")}
        return {"watchstander": name}


class OpenWatchDialog(Dialog, WatchPeoplePicker):
    def __init__(self, parent, db):
        self.db = db
        super().__init__(parent, "Open watch", "Open watch")

    def build(self):
        db = self.db
        self._setup_pickers()
        # Default station: the one used last, else the station call sign in Settings.
        last = db.con.execute("SELECT station_call_sign FROM watches ORDER BY opened_utc DESC LIMIT 1").fetchone()
        self.station_box.set(last[0] if last else db.setting("station_call_sign"))
        self.chan = ttk.Entry(self.body, width=40)
        self.chan.insert(0, db.setting("default_channel") + ", 16")
        self.rc = ttk.Entry(self.body, width=40)
        self.eq = ttk.Entry(self.body, width=40)
        self.eq.insert(0, "All equipment operational")
        self.row(0, "Radio station", self.station_box,
                 "from Data > Radio stations" if self.stations else "add stations in Data > Radio stations")
        self.row(1, "Watchstander", self.ws,
                 "from Data > Radio watch standers" if self.rws else "add them in Data > Radio watch standers")
        self.row(2, "Channels guarded", self.chan)
        f = ttk.Frame(self.body)
        self.tc = ttk.Entry(f, width=30)
        self.tc.pack(side="left", fill="x", expand=True)
        ttk.Button(f, text="time.gov", width=9, command=lambda: webbrowser.open("https://time.gov")).pack(side="left", padx=(4, 0))
        ttk.Button(f, text="Checked", width=9, command=self._checked).pack(side="left", padx=(4, 0))
        self.row(3, "Time check", f, "M2000.3G: official time check at start of watch")
        self.row(4, "Radio check", self.rc, "e.g. Loud and clear with Sta Mayport Ch 83A")
        self.row(5, "Equipment status", self.eq)
        self.ws.focus_set()

    def _checked(self):
        self.tc.delete(0, "end")
        self.tc.insert(0, f"Clock checked against time.gov at {utcnow():%H%MZ}")

    def apply(self):
        who = self._watchstander()
        sid, cs = self._station_id()
        if not cs:
            raise LogError("Enter the radio station call sign")
        if sid is None and self.stations and not messagebox.askyesno(
                "Open watch", f"\"{cs}\" is not in the Radio stations list, so no address or position "
                              "will print on the log.\n\nOpen the watch anyway?", parent=self):
            return False
        return self.db.open_watch(
            channels=self.chan.get().strip(), time_check=self.tc.get().strip(),
            radio_check=self.rc.get().strip(), equipment_status=self.eq.get().strip(),
            station_call_sign=cs, station_id=sid, **who)


class WatchInfoDialog(Dialog, WatchPeoplePicker):
    def __init__(self, parent, db, wid):
        self.db, self.wid = db, wid
        super().__init__(parent, "Watch details", "Save")

    def build(self):
        w = self.db.watch(self.wid)
        self._setup_pickers()
        self.station_box.set(w["station_call_sign"])
        self.ws.set(next((k for k, v in self.ws_values.items()
                          if (v[0] == "rws" and v[1] == w["rws_id"]) or
                          (v[0] == "member" and v[1] == w["watchstander_id"])), w["watchstander"]))
        self.row(0, "Radio station", self.station_box)
        self.row(1, "Watchstander", self.ws)
        self.fields = {}
        for r, (key, label) in enumerate([("channels", "Channels guarded"), ("time_check", "Time check"),
                                          ("radio_check", "Radio check"), ("equipment_status", "Equipment status"),
                                          ("notes", "Notes")], 2):
            e = ttk.Entry(self.body, width=50)
            e.insert(0, w[key] or "")
            self.fields[key] = self.row(r, label, e)

    def apply(self):
        w = self.db.watch(self.wid)
        sid, cs = self._station_id()
        if not cs:
            raise LogError("Enter the radio station call sign")
        fields = {k: e.get().strip() for k, e in self.fields.items()}
        fields.update({"rws_id": None, "watchstander_id": None})
        fields.update(self._watchstander())
        if sid is not None and sid != w["station_id"]:
            self.db.set_watch_station(self.wid, sid)
        elif sid is None:
            fields["station_call_sign"] = cs
            if cs != w["station_call_sign"]:
                self.db._run("UPDATE watches SET station_id=NULL, station_info='' WHERE id=?", (self.wid,))
        self.db.update_watch(self.wid, **fields)
        return True


class PatrolDialog(Dialog):
    def __init__(self, parent, db, wid, pid=None):
        self.db, self.wid, self.pid = db, wid, pid
        super().__init__(parent, "Edit patrol" if pid else "Add patrol", "Save")

    def build(self):
        db = self.db
        self.vessels = db.vessels(active_only=True)
        self.coxs = db.coxswains()
        self.crew = db.crew()
        b = self.body
        self.vessel = ttk.Combobox(b, width=42, state="readonly",
                                   values=[f"{v['facility_no']}  {v['name']}" for v in self.vessels])
        self.vessel.bind("<<ComboboxSelected>>", self._vessel_picked)
        self.po = ttk.Entry(b, width=44)
        self.gar = ttk.Entry(b, width=44)
        self.cell = ttk.Entry(b, width=44)
        self.interval = ttk.Spinbox(b, from_=5, to=120, increment=5, width=8)
        # Choices come from Data > Patrol types; typing another mission is still allowed.
        self.mission = ttk.Combobox(b, width=42, values=[t["name"] for t in db.patrol_types()])
        self.fp = ttk.Entry(b, width=44)
        self.cox = ttk.Combobox(b, width=42, state="readonly",
                                values=["(none)"] + [member_label(c, c["status"]) for c in self.coxs])
        self.cox.bind("<<ComboboxSelected>>", lambda e: (self._sync_pob(), self._default_cell()))
        lf = ttk.Frame(b)
        self.crew_list = tk.Listbox(lf, selectmode="multiple", height=8, width=44, exportselection=False)
        sb = ttk.Scrollbar(lf, command=self.crew_list.yview)
        self.crew_list.configure(yscrollcommand=sb.set)
        self.crew_list.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        for c in self.crew:
            self.crew_list.insert("end", member_label(c, c["status"]))
        self.crew_list.bind("<<ListboxSelect>>", lambda e: self._sync_pob())
        self.pob = ttk.Spinbox(b, from_=0, to=99, width=8)
        self.row(0, "Vessel", self.vessel)
        self.row(1, "Patrol order (PO#)", self.po)
        self.row(2, "GAR score", self.gar, "GAR 2.0 total at the start; later changes go on log entries")
        self.row(3, "Coxswain", self.cox)
        self.row(4, "Crew (click to select)", lf)
        self.row(5, "POB", self.pob, "counted from the people picked; change if others are aboard")
        self.row(6, "Cell #", self.cell, "the coxswain's phone; the boat's cell if none")
        self.row(7, "Ops normal every (min)", self.interval, "M2000.3G: 30 min unless ordered otherwise")
        self.row(8, "Mission", self.mission)
        self.row(9, "Float plan / area", self.fp)
        self.interval.set(db.interval_min())
        self.pob.set(0)
        self.cox.current(0)
        if self.pid:
            p = db.patrol(self.pid)
            ids = [v["id"] for v in self.vessels]
            if p["vessel_id"] in ids:
                self.vessel.current(ids.index(p["vessel_id"]))
            for w, k in ((self.po, "patrol_order"), (self.gar, "gar"), (self.cell, "cell"), (self.fp, "float_plan")):
                w.insert(0, p[k])
            self.mission.set(p["mission"])
            self.interval.set(p["interval_min"])
            crew = db.patrol_crew(self.pid)
            cox_ids = [c["id"] for c in self.coxs]
            crew_ids = [c["id"] for c in self.crew]
            for c in crew:
                if c["role"] == "COX" and c["member_id"] in cox_ids:
                    self.cox.current(cox_ids.index(c["member_id"]) + 1)
                elif c["member_id"] in crew_ids:
                    self.crew_list.selection_set(crew_ids.index(c["member_id"]))
            self.pob.set(p["pob"])
        if not self.vessels:
            ttk.Label(b, text="Add vessels first: Data > Vessels.", foreground="#a33000").grid(
                row=10, column=0, columnspan=3, sticky="w")

    def _vessel_picked(self, _e=None):
        self._default_cell()

    def _default_cell(self):
        """Cell # defaults to the coxswain's phone, or the vessel's cell when the coxswain has none."""
        phone = self.coxs[self.cox.current() - 1]["phone"] if self.cox.current() > 0 else ""
        if not phone and self.vessel.current() >= 0:
            phone = self.vessels[self.vessel.current()]["cell"]
        if phone:
            self.cell.delete(0, "end")
            self.cell.insert(0, phone)

    def _picked(self):
        cox = self.coxs[self.cox.current() - 1]["id"] if self.cox.current() > 0 else None
        crew = [self.crew[i]["id"] for i in self.crew_list.curselection()]
        return cox, [c for c in crew if c != cox]

    def _sync_pob(self):
        cox, crew = self._picked()
        self.pob.set(len(crew) + (1 if cox else 0))

    def apply(self):
        if self.vessel.current() < 0:
            raise LogError("Pick a vessel")
        cox, crew = self._picked()
        try:
            pob = int(self.pob.get())
            interval = int(self.interval.get())
        except ValueError:
            raise LogError("POB and interval must be numbers") from None
        if interval < 1:
            raise LogError("Interval must be at least 1 minute")
        args = dict(vessel_id=self.vessels[self.vessel.current()]["id"], patrol_order=self.po.get().strip(),
                    gar=self.gar.get().strip(), pob=pob, cell=self.cell.get().strip(),
                    mission=self.mission.get().strip(), float_plan=self.fp.get().strip(), interval_min=interval)
        if self.pid:
            self.db.update_patrol(self.pid, coxswain_id=cox, crew_ids=crew, **args)
            return self.pid
        vid = args.pop("vessel_id")
        return self.db.add_patrol(self.wid, vid, cox, crew, **args)


class StrikeDialog(Dialog):
    def __init__(self, parent, db, entry_id):
        self.db, self.eid = db, entry_id
        self.e = db.entry(entry_id)
        super().__init__(parent, "Strike / correct entry", "Strike and enter correction")

    def build(self):
        e, b = self.e, self.body
        ttk.Label(b, text=f"Entry at {fmt_z(e['time_utc'])} ({fmt_local(e['time_utc'])} local), "
                          f"{e['station']}, {e['entry_type']}", font=("Segoe UI", 10, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 6))
        ttk.Label(b, text="The original stays in the log, struck through with your initials\n"
                          "(M2000.3G: entries are never erased).", foreground="#555").grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(0, 8))
        self.init = self.row(2, "Your initials", ttk.Entry(b, width=10))
        self.time = self.row(3, "Time", ttk.Entry(b, width=46), "HHMM local or HHMMZ")
        self.time.insert(0, fmt_z(e["time_utc"]))
        self.type = self.row(4, "Type", ttk.Combobox(b, width=44, values=ENTRY_TYPES, state="readonly"))
        self.type.set(e["entry_type"])
        self.chan = self.row(5, "Channel", ttk.Combobox(b, width=44, values=[c["name"] for c in self.db.channels()]))
        self.chan.set(e["channel"])
        self.f = {}
        fields = [("position", "Position"), ("heading", "Heading"), ("status", "Status"), ("remarks", "Remarks")]
        if e["patrol_id"]:
            fields.insert(2, ("gar", "GAR"))
        for r, (k, label) in enumerate(fields, 6):
            w = self.row(r, label, ttk.Entry(b, width=46))
            w.insert(0, e[k])
            self.f[k] = w
        ttk.Button(self.body, text="Strike only (no correction)", command=self._strike_only).grid(
            row=6 + len(fields), column=1, sticky="w", pady=(8, 0))
        self.init.focus_set()

    def _strike_only(self):
        try:
            self.db.strike(self.eid, self.init.get())
        except LogError as ex:
            messagebox.showerror(self.title(), str(ex), parent=self)
            return
        self.result = True
        self.destroy()

    def apply(self):
        when = parse_time(self.time.get(), now=utcnow())
        return self.db.correct(self.eid, self.init.get(), time=when, entry_type=self.type.get(),
                               channel=self.chan.get().strip(), **{k: w.get().strip() for k, w in self.f.items()})


class SettingsDialog(Dialog):
    KEYS = [("station_call_sign", "Station call sign", "AOPG Vol III 3-E: e.g. \"Coast Guard Auxiliary Amelia Radio\""),
            ("default_channel", "Default working channel", ""),
            ("ops_interval_min", "Ops normal interval (min)", "30 per M2000.3G ch. 5 (boat reports)"),
            ("due_soon_min", "Warn this many min before due", ""),
            ("incident_name", "ICS 309 incident name", "")]

    def __init__(self, parent, db):
        self.db = db
        super().__init__(parent, "Settings", "Save")

    def build(self):
        self.e = {}
        for r, (k, label, hint) in enumerate(self.KEYS):
            w = ttk.Entry(self.body, width=36)
            w.insert(0, self.db.setting(k))
            self.e[k] = self.row(r, label, w, hint)
        self.cell = tk.BooleanVar(value=self.db.setting("print_cell_numbers") == "1")
        ttk.Checkbutton(self.body, text="Print vessel cell numbers on the log (logs are releasable under FOIA)",
                        variable=self.cell).grid(row=len(self.KEYS), column=0, columnspan=3, sticky="w", pady=6)

    def apply(self):
        for k in ("ops_interval_min", "due_soon_min"):
            v = self.e[k].get().strip()
            if not v.isdigit() or int(v) < 1:
                raise LogError("Interval values must be whole minutes")
        for k, w in self.e.items():
            self.db.set_setting(k, w.get().strip())
        self.db.set_setting("print_cell_numbers", "1" if self.cell.get() else "0")
        return True


# ---------------------------------------------------------------- data editors

class VesselsWindow(tk.Toplevel):
    FIELDS = [("facility_no", "Facility #", 9), ("name", "Name", 22), ("call_sign", "Radio call sign", 26),
              ("cell", "Cell #", 14), ("owner", "Owner", 16), ("description", "Type / length", 16),
              ("mmsi", "MMSI", 11), ("inspection_expires", "Inspection expires", 12)]

    def __init__(self, parent, db, on_change):
        super().__init__(parent)
        self.db, self.on_change = db, on_change
        self.title("Vessels (facilities)")
        self.geometry("1100x520")
        self.transient(parent)
        cols = [f[0] for f in self.FIELDS] + ["active"]
        self.tree = ttk.Treeview(self, columns=cols, show="headings", selectmode="browse")
        for k, label, w in self.FIELDS:
            self.tree.heading(k, text=label)
            self.tree.column(k, width=w * 7)
        self.tree.heading("active", text="Active")
        self.tree.column("active", width=50)
        self.tree.pack(fill="both", expand=True, padx=10, pady=(10, 4))
        self.tree.bind("<<TreeviewSelect>>", self._load)
        form = ttk.Frame(self, padding=10)
        form.pack(fill="x")
        self.e = {}
        for i, (k, label, w) in enumerate(self.FIELDS):
            ttk.Label(form, text=label).grid(row=(i // 4) * 2, column=i % 4, sticky="w", padx=4)
            self.e[k] = ttk.Entry(form, width=w + 10)
            self.e[k].grid(row=(i // 4) * 2 + 1, column=i % 4, sticky="we", padx=4, pady=(0, 6))
        self.active = tk.BooleanVar(value=True)
        ttk.Checkbutton(form, text="Active", variable=self.active).grid(row=4, column=0, sticky="w", padx=4)
        bar = ttk.Frame(self, padding=(10, 0, 10, 10))
        bar.pack(fill="x")
        ttk.Button(bar, text="New", command=self._new).pack(side="left")
        ttk.Button(bar, text="Save", command=self._save).pack(side="left", padx=6)
        ttk.Button(bar, text="Import CSV...", command=self._import).pack(side="left", padx=(20, 0))
        ttk.Label(bar, text="Call sign per AOPG Vol I 5-C (e.g. \"Coast Guard Auxiliary Vessel 1675\").",
                  foreground="#666").pack(side="left", padx=12)
        ttk.Button(bar, text="Close", command=self.destroy).pack(side="right")
        self.vid = None
        self._fill()

    def _fill(self):
        self.tree.delete(*self.tree.get_children())
        for v in self.db.vessels():
            self.tree.insert("", "end", iid=str(v["id"]),
                             values=[v[f[0]] for f in self.FIELDS] + ["Yes" if v["active"] else "No"])

    def _load(self, _e=None):
        sel = self.tree.selection()
        if not sel:
            return
        v = self.db.vessel(int(sel[0]))
        self.vid = v["id"]
        for k, w in self.e.items():
            w.delete(0, "end")
            w.insert(0, v[k])
        self.active.set(bool(v["active"]))

    def _new(self):
        self.vid = None
        self.tree.selection_remove(self.tree.selection())
        for w in self.e.values():
            w.delete(0, "end")
        self.active.set(True)
        self.e["facility_no"].focus_set()

    def _save(self):
        data = {k: w.get().strip() for k, w in self.e.items()}
        data["active"] = int(self.active.get())
        try:
            self.vid = self.db.save_vessel(data, self.vid)
        except LogError as ex:
            messagebox.showerror("Vessels", str(ex), parent=self)
            return
        self._fill()
        self.tree.selection_set(str(self.vid))
        self.on_change()

    def _import(self):
        path = filedialog.askopenfilename(parent=self, title="Import vessels CSV",
                                          filetypes=[("CSV", "*.csv"), ("All files", "*.*")])
        if not path:
            return
        try:
            a, u = self.db.import_vessels_csv(path)
        except (LogError, OSError, ValueError) as ex:
            messagebox.showerror("Import", str(ex), parent=self)
            return
        messagebox.showinfo("Import", f"{a} added, {u} updated.", parent=self)
        self._fill()
        self.on_change()


class MembersWindow(tk.Toplevel):
    QUAL_COLS = [("COXSWAIN", "Coxswain"), ("CREW", "Crew"), ("PWC", "PWC"), ("TCO", "TCO"), ("QE", "QE")]

    def __init__(self, parent, db, on_change):
        super().__init__(parent)
        self.db, self.on_change = db, on_change
        self.title("Members and qualifications")
        self.geometry("1060x600")
        self.transient(parent)
        top = ttk.Frame(self, padding=(10, 10, 10, 0))
        top.pack(fill="x")
        ttk.Label(top, text="Show:").pack(side="left")
        self.show = ttk.Combobox(top, state="readonly", width=24,
                                 values=["All members", "Coxswains", "Crew", "QEs", "Trainees", "Inactive"])
        self.show.current(0)
        self.show.pack(side="left", padx=6)
        self.show.bind("<<ComboboxSelected>>", lambda e: self._fill())
        cols = ["member_no", "last_name", "first_name", "phone"] + [q for q, _ in self.QUAL_COLS] + ["active"]
        self.tree = ttk.Treeview(self, columns=cols, show="headings", selectmode="browse")
        for c, label, w in [("member_no", "Member #", 80), ("last_name", "Last name", 140),
                            ("first_name", "First name", 120), ("phone", "Phone", 120)]:
            self.tree.heading(c, text=label)
            self.tree.column(c, width=w)
        for q, label in self.QUAL_COLS:
            self.tree.heading(q, text=label)
            self.tree.column(q, width=85)
        self.tree.heading("active", text="Active")
        self.tree.column("active", width=50)
        self.tree.pack(fill="both", expand=True, padx=10, pady=6)
        self.tree.bind("<<TreeviewSelect>>", self._load)
        form = ttk.Frame(self, padding=10)
        form.pack(fill="x")
        self.e = {}
        for i, (k, label, w) in enumerate([("member_no", "Member # (7 digits)", 12), ("last_name", "Last name", 20),
                                           ("first_name", "First name", 18), ("phone", "Phone", 16)]):
            ttk.Label(form, text=label).grid(row=0, column=i, sticky="w", padx=4)
            self.e[k] = ttk.Entry(form, width=w)
            self.e[k].grid(row=1, column=i, sticky="w", padx=4)
        self.q = {}
        for i, (q, label) in enumerate(self.QUAL_COLS):
            ttk.Label(form, text=label).grid(row=2, column=i, sticky="w", padx=4, pady=(8, 0))
            self.q[q] = ttk.Combobox(form, state="readonly", width=12, values=["", *[s.title() if s != "REYR" else s for s in QUAL_STATUS]])
            self.q[q].grid(row=3, column=i, sticky="w", padx=4)
        self.active = tk.BooleanVar(value=True)
        ttk.Checkbutton(form, text="Active", variable=self.active).grid(row=3, column=len(self.QUAL_COLS), sticky="w", padx=8)
        bar = ttk.Frame(self, padding=(10, 0, 10, 10))
        bar.pack(fill="x")
        ttk.Button(bar, text="New", command=self._new).pack(side="left")
        ttk.Button(bar, text="Save", command=self._save).pack(side="left", padx=6)
        ttk.Button(bar, text="Import CSV...", command=self._import).pack(side="left", padx=(20, 0))
        ttk.Label(bar, text="Blank status = not held. Qualified / Trainee / REYR (required yearly) as in AUXDATA.",
                  foreground="#666").pack(side="left", padx=12)
        ttk.Button(bar, text="Close", command=self.destroy).pack(side="right")
        self.mid = None
        self._fill()

    def _fill(self):
        self.tree.delete(*self.tree.get_children())
        show = self.show.get()
        for m in self.db.members():
            q = self.db.quals(m["id"])
            if show == "Coxswains" and "COXSWAIN" not in q:
                continue
            if show == "Crew" and "CREW" not in q:
                continue
            if show == "QEs" and "QE" not in q:
                continue
            if show == "Trainees" and "TRAINEE" not in q.values():
                continue
            if show == "Inactive" and m["active"]:
                continue
            if show in ("Coxswains", "Crew", "QEs", "Trainees") and not m["active"]:
                continue
            vals = [m["member_no"], m["last_name"], m["first_name"], m["phone"]]
            vals += [(q.get(k, "").title() if q.get(k) != "REYR" else "REYR") for k, _ in self.QUAL_COLS]
            vals.append("Yes" if m["active"] else "No")
            self.tree.insert("", "end", iid=str(m["id"]), values=vals)

    def _load(self, _e=None):
        sel = self.tree.selection()
        if not sel:
            return
        m = self.db.member(int(sel[0]))
        self.mid = m["id"]
        for k, w in self.e.items():
            w.delete(0, "end")
            w.insert(0, m[k])
        q = self.db.quals(m["id"])
        for k, w in self.q.items():
            s = q.get(k, "")
            w.set(s if s == "REYR" else s.title())
        self.active.set(bool(m["active"]))

    def _new(self):
        self.mid = None
        self.tree.selection_remove(self.tree.selection())
        for w in self.e.values():
            w.delete(0, "end")
        for w in self.q.values():
            w.set("")
        self.active.set(True)
        self.e["member_no"].focus_set()

    def _save(self):
        data = {k: w.get().strip() for k, w in self.e.items()}
        data["active"] = int(self.active.get())
        quals = {k: w.get().upper() for k, w in self.q.items()}
        try:
            self.mid = self.db.save_member(data, quals, self.mid)
        except LogError as ex:
            messagebox.showerror("Members", str(ex), parent=self)
            return
        self._fill()
        if self.tree.exists(str(self.mid)):
            self.tree.selection_set(str(self.mid))
        self.on_change()

    def _import(self):
        path = filedialog.askopenfilename(parent=self, title="Import members CSV",
                                          filetypes=[("CSV", "*.csv"), ("All files", "*.*")])
        if not path:
            return
        try:
            a, u = self.db.import_members_csv(path)
        except (LogError, OSError, ValueError) as ex:
            messagebox.showerror("Import", str(ex), parent=self)
            return
        messagebox.showinfo("Import", f"{a} added, {u} updated.", parent=self)
        self._fill()
        self.on_change()


class RecordWindow(tk.Toplevel):
    """List + form editor for a simple table (Radio watch standers, Radio stations)."""

    def __init__(self, parent, title, fields, rows_fn, get_fn, save_fn, import_fn, note, on_change,
                 display=None, size="1000x520"):
        super().__init__(parent)
        self.title(title)
        self.geometry(size)
        self.transient(parent)
        self.fields, self.rows_fn, self.get_fn, self.save_fn = fields, rows_fn, get_fn, save_fn
        self.import_fn, self.on_change, self.display = import_fn, on_change, display or {}
        cols = [f[0] for f in fields] + ["active"]
        self.tree = ttk.Treeview(self, columns=cols, show="headings", selectmode="browse")
        for k, label, w in fields:
            self.tree.heading(k, text=label)
            self.tree.column(k, width=w * 7)
        self.tree.heading("active", text="Active")
        self.tree.column("active", width=50)
        self.tree.pack(fill="both", expand=True, padx=10, pady=(10, 4))
        self.tree.bind("<<TreeviewSelect>>", self._load)
        form = ttk.Frame(self, padding=10)
        form.pack(fill="x")
        self.e = {}
        per_row = 5
        for i, (k, label, w) in enumerate(fields):
            ttk.Label(form, text=label).grid(row=(i // per_row) * 2, column=i % per_row, sticky="w", padx=4)
            self.e[k] = ttk.Entry(form, width=w + 6)
            self.e[k].grid(row=(i // per_row) * 2 + 1, column=i % per_row, sticky="we", padx=4, pady=(0, 6))
        self.active = tk.BooleanVar(value=True)
        ttk.Checkbutton(form, text="Active", variable=self.active).grid(
            row=((len(fields) - 1) // per_row) * 2 + 2, column=0, sticky="w", padx=4)
        bar = ttk.Frame(self, padding=(10, 0, 10, 10))
        bar.pack(fill="x")
        ttk.Button(bar, text="New", command=self._new).pack(side="left")
        ttk.Button(bar, text="Save", command=self._save).pack(side="left", padx=6)
        ttk.Button(bar, text="Import CSV...", command=self._import).pack(side="left", padx=(20, 0))
        ttk.Label(bar, text=note, foreground="#666").pack(side="left", padx=12)
        ttk.Button(bar, text="Close", command=self.destroy).pack(side="right")
        self.rid = None
        self._fill()

    def _show(self, k, row):
        fn = self.display.get(k)
        v = row[k]
        return fn(v) if fn else ("" if v is None else v)

    def _fill(self):
        self.tree.delete(*self.tree.get_children())
        for r in self.rows_fn():
            self.tree.insert("", "end", iid=str(r["id"]),
                             values=[self._show(f[0], r) for f in self.fields] + ["Yes" if r["active"] else "No"])

    def _load(self, _e=None):
        sel = self.tree.selection()
        if not sel:
            return
        r = self.get_fn(int(sel[0]))
        self.rid = r["id"]
        for k, w in self.e.items():
            w.delete(0, "end")
            w.insert(0, self._show(k, r))
        self.active.set(bool(r["active"]))

    def _new(self):
        self.rid = None
        self.tree.selection_remove(self.tree.selection())
        for w in self.e.values():
            w.delete(0, "end")
        self.active.set(True)
        self.e[self.fields[0][0]].focus_set()

    def _save(self):
        data = {k: w.get().strip() for k, w in self.e.items()}
        data["active"] = int(self.active.get())
        try:
            self.rid = self.save_fn(data, self.rid)
        except LogError as ex:
            messagebox.showerror(self.title(), str(ex), parent=self)
            return
        self._fill()
        self.tree.selection_set(str(self.rid))
        self.on_change()

    def _import(self):
        path = filedialog.askopenfilename(parent=self, title="Import CSV",
                                          filetypes=[("CSV", "*.csv"), ("All files", "*.*")])
        if not path:
            return
        try:
            a, u = self.import_fn(path)
        except (LogError, OSError, ValueError) as ex:
            messagebox.showerror("Import", str(ex), parent=self)
            return
        messagebox.showinfo("Import", f"{a} added, {u} updated.", parent=self)
        self._fill()
        self.on_change()


def watchstanders_window(parent, db, on_change):
    return RecordWindow(
        parent, "Radio watch standers",
        [("last_name", "Last name", 16), ("first_name", "First name", 14), ("member_no", "Member # (7 digits)", 11),
         ("phone", "Telephone", 14), ("tco_date", "TCO date", 11)],
        db.watchstanders, db.watchstander, db.save_watchstander, db.import_watchstanders_csv,
        "TCO date = date qualified as Telecommunications Operator (MM/DD/YYYY). "
        "CSV columns: member_no, last_name, first_name, phone, tco_date.",
        on_change, display={"tco_date": wdb.fmt_date}, size="900x480")


def stations_window(parent, db, on_change):
    return RecordWindow(
        parent, "Radio stations",
        [("call_sign", "Call sign", 18), ("street", "Street", 24), ("city", "City", 16), ("state", "State", 5),
         ("zip", "ZIP", 8), ("latitude", "Latitude", 13), ("longitude", "Longitude", 14), ("phone", "Contact phone", 14)],
        db.stations, db.station, db.save_station, db.import_stations_csv,
        "Lat/Long as 30 40.09 N / 081 27.67 W, or decimal (30.6682 / -81.4612). "
        "CSV: call_sign, street, city, state, zip, latitude, longitude, phone.",
        on_change, display={"latitude": lambda v: wdb.fmt_coord(v, "lat"),
                            "longitude": lambda v: wdb.fmt_coord(v, "lon")}, size="1150x480")


class ListEditor(tk.Toplevel):
    """Ordered list of rows with a few text columns (patrol types, channels, contacts)."""

    def __init__(self, parent, title, columns, rows, save):
        super().__init__(parent)
        self.title(title)
        self.transient(parent)
        self.geometry("760x480")
        self.columns, self.save = columns, save
        self.tree = ttk.Treeview(self, columns=[c for c, _ in columns], show="headings", selectmode="browse")
        for c, w in columns:
            self.tree.heading(c, text=c)
            self.tree.column(c, width=w)
        self.tree.pack(fill="both", expand=True, padx=10, pady=10)
        self.tree.bind("<<TreeviewSelect>>", self._load)
        for r in rows:
            self.tree.insert("", "end", values=r)
        form = ttk.Frame(self, padding=(10, 0))
        form.pack(fill="x")
        self.e = []
        for i, (c, w) in enumerate(columns):
            ttk.Label(form, text=c).grid(row=0, column=i, sticky="w", padx=4)
            e = ttk.Entry(form, width=max(10, w // 7))
            e.grid(row=1, column=i, sticky="we", padx=4)
            self.e.append(e)
        bar = ttk.Frame(self, padding=10)
        bar.pack(fill="x")
        for text, cmd in [("Add", self._add), ("Update", self._update), ("Remove", self._remove),
                          ("Up", lambda: self._move(-1)), ("Down", lambda: self._move(1))]:
            ttk.Button(bar, text=text, command=cmd, width=8).pack(side="left", padx=(0, 4))
        ttk.Button(bar, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(bar, text="Save", command=self._save).pack(side="right", padx=6)

    def _vals(self):
        return [e.get().strip() for e in self.e]

    def _load(self, _e=None):
        sel = self.tree.selection()
        if sel:
            for e, v in zip(self.e, self.tree.item(sel[0], "values")):
                e.delete(0, "end")
                e.insert(0, v)

    def _add(self):
        if self._vals()[0]:
            self.tree.insert("", "end", values=self._vals())

    def _update(self):
        sel = self.tree.selection()
        if sel and self._vals()[0]:
            self.tree.item(sel[0], values=self._vals())

    def _remove(self):
        for s in self.tree.selection():
            self.tree.delete(s)

    def _move(self, d):
        sel = self.tree.selection()
        if sel:
            i = self.tree.index(sel[0])
            self.tree.move(sel[0], "", max(0, i + d))

    def _save(self):
        rows = [list(self.tree.item(i, "values")) + [""] * len(self.columns) for i in self.tree.get_children()]
        self.save([tuple(r[:len(self.columns)]) for r in rows])
        self.destroy()


class WatchesWindow(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app.root)
        self.app, self.db = app, app.db
        self.title("Watches")
        self.geometry("1100x460")
        self.transient(app.root)
        cols = [("date", "Date (local)", 90), ("opened", "Opened", 70), ("closed", "Closed", 70),
                ("station", "Station", 130), ("ws", "Watchstander", 200), ("patrols", "Patrols", 60),
                ("entries", "Entries", 60), ("distress", "Distress", 60), ("retain", "Retain until", 100),
                ("state", "State", 80)]
        self.tree = ttk.Treeview(self, columns=[c[0] for c in cols], show="headings")
        for c, label, w in cols:
            self.tree.heading(c, text=label)
            self.tree.column(c, width=w)
        self.tree.tag_configure("expired", background="#eeeeee", foreground="#777")
        self.tree.pack(fill="both", expand=True, padx=10, pady=10)
        self.tree.bind("<Double-1>", lambda e: self._view())
        today = utcnow().date()
        for w in self.db.watches():
            n = len(self.db.entries(w["id"]))
            retain = self.db.retain_until(w["id"]) if w["locked"] else None
            tags = ("expired",) if retain and retain < today else ()
            self.tree.insert("", "end", iid=str(w["id"]), tags=tags, values=[
                from_iso(w["opened_utc"]).astimezone().strftime("%m/%d/%Y"), fmt_z(w["opened_utc"]),
                fmt_z(w["closed_utc"]), w["station_call_sign"], w["watchstander"],
                len(self.db.patrols(w["id"])), n, "Yes" if self.db.has_distress(w["id"]) else "",
                f"{retain:%d %b %Y}" if retain else "", "Locked" if w["locked"] else "OPEN"])
        bar = ttk.Frame(self, padding=(10, 0, 10, 10))
        bar.pack(fill="x")
        ttk.Button(bar, text="View", command=self._view).pack(side="left")
        ttk.Button(bar, text="Print log", command=lambda: self._each(self.app.print_log)).pack(side="left", padx=6)
        ttk.Button(bar, text="Save PDF...",
                   command=lambda: self._each(lambda w: self.app.save_log(w, ".pdf", self))).pack(side="left")
        ttk.Button(bar, text="Save Word...",
                   command=lambda: self._each(lambda w: self.app.save_log(w, ".docx", self))).pack(side="left", padx=6)
        ttk.Button(bar, text="ICS 309", command=lambda: self._each(self.app.print_ics309)).pack(side="left")
        ttk.Button(bar, text="7030 CSV for selected...", command=self._csv).pack(side="left", padx=6)
        ttk.Label(bar, text="Grey = past retention date (AOPG Vol III 3-F.3)", foreground="#666").pack(side="left", padx=8)
        ttk.Button(bar, text="Close", command=self.destroy).pack(side="right")

    def _sel(self):
        return [int(s) for s in self.tree.selection()]

    def _view(self):
        s = self._sel()
        if s:
            self.app.show_watch(s[0])
            self.destroy()

    def _each(self, fn):
        for wid in self._sel():
            fn(wid)

    def _csv(self):
        s = self._sel()
        if not s:
            messagebox.showinfo("7030", "Select one or more watches first.", parent=self)
            return
        self.app.export_csv(s, parent=self)


# ---------------------------------------------------------------- main window

class App:
    def __init__(self, root, db):
        self.root, self.db = root, db
        self.wid = None
        self.viewing_past = False
        self.alerted = {}
        self.last_bell = datetime.min.replace(tzinfo=timezone.utc)
        root.title(f"{APP} {VERSION}")
        root.geometry("1280x860")
        root.minsize(1100, 700)
        style = ttk.Style(root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Clock.TLabel", font=("Consolas", 18, "bold"))
        style.configure("Big.TLabel", font=("Segoe UI", 12, "bold"))
        style.configure("Treeview", rowheight=22)
        style.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"))
        self.strike_font = tkfont.Font(family="Segoe UI", size=9, overstrike=1)
        self.bold_font = tkfont.Font(family="Segoe UI", size=9, weight="bold")
        self._menu()
        self._build()
        cur = db.current_watch()
        self.show_watch(cur["id"] if cur else None)
        self._tick()

    # ---- layout
    def _menu(self):
        m = tk.Menu(self.root)
        f = tk.Menu(m, tearoff=0)
        f.add_command(label="Open new watch...", command=self.open_watch)
        f.add_command(label="Close and sign watch...", command=self.close_watch)
        f.add_command(label="Watches (past logs)...", command=lambda: WatchesWindow(self))
        f.add_separator()
        f.add_command(label="Print watch log", accelerator="Ctrl+P", command=lambda: self.print_log(self.wid))
        f.add_command(label="Save watch log as PDF...", command=lambda: self.save_log(self.wid, ".pdf"))
        f.add_command(label="Save watch log as Word (.docx)...", command=lambda: self.save_log(self.wid, ".docx"))
        f.add_command(label="ICS 309 communications log", command=lambda: self.print_ics309(self.wid))
        f.add_command(label="7030 activity CSV (this watch)...", command=lambda: self.wid and self.export_csv([self.wid]))
        f.add_command(label="Contact sheet", command=lambda: open_html(reports.contact_sheet_html(self.db), "contacts.html"))
        f.add_separator()
        f.add_command(label="Back up database...", command=self.backup)
        f.add_separator()
        f.add_command(label="Exit", command=self.root.destroy)
        m.add_cascade(label="File", menu=f)
        d = tk.Menu(m, tearoff=0)
        d.add_command(label="Vessels...", command=lambda: VesselsWindow(self.root, self.db, self.refresh))
        d.add_command(label="Members and qualifications...", command=lambda: MembersWindow(self.root, self.db, self.refresh))
        d.add_command(label="Radio watch standers...", command=lambda: watchstanders_window(self.root, self.db, self.refresh))
        d.add_command(label="Radio stations...", command=lambda: stations_window(self.root, self.db, self.refresh))
        d.add_command(label="Patrol types...", command=self.edit_patrol_types)
        d.add_command(label="Channels...", command=self.edit_channels)
        d.add_command(label="Contacts...", command=self.edit_contacts)
        d.add_separator()
        d.add_command(label="Settings...", command=lambda: (SettingsDialog(self.root, self.db), self.refresh()))
        m.add_cascade(label="Data", menu=d)
        h = tk.Menu(m, tearoff=0)
        h.add_command(label="Log-keeping rules", command=self.rules)
        h.add_command(label="Disclaimer", command=lambda: __import__("disclaimer").show(self.root, APP))
        h.add_command(label="About", command=lambda: messagebox.showinfo(
            "About", f"{APP} {VERSION}\nCopyright (c) 2026 N. Tesla, Inc. All rights reserved.\n\n"
                     f"Database:\n{self.db.path}", parent=self.root))
        m.add_cascade(label="Help", menu=h)
        self.root.config(menu=m)
        self.root.bind("<Control-p>", lambda e: self.print_log(self.wid))

    def _build(self):
        r = self.root
        top = ttk.Frame(r, padding=(10, 8))
        top.pack(fill="x")
        info = ttk.Frame(top)
        info.pack(side="left", fill="x", expand=True)
        self.station_lbl = ttk.Label(info, text="", style="Big.TLabel")
        self.station_lbl.pack(anchor="w")
        self.watch_lbl = ttk.Label(info, text="")
        self.watch_lbl.pack(anchor="w")
        right = ttk.Frame(top)
        right.pack(side="right")
        clocks = ttk.Frame(right)
        clocks.pack(anchor="e")
        self.utc_lbl = ttk.Label(clocks, style="Clock.TLabel")
        self.utc_lbl.grid(row=0, column=0, padx=10)
        self.loc_lbl = ttk.Label(clocks, style="Clock.TLabel", foreground="#335")
        self.loc_lbl.grid(row=0, column=1, padx=(10, 0))
        btns = ttk.Frame(right)
        btns.pack(anchor="e", pady=(4, 0))
        self.open_btn = ttk.Button(btns, text="Open watch...", command=self.open_watch)
        self.open_btn.pack(side="left")
        self.info_btn = ttk.Button(btns, text="Watch details...", command=self.watch_details)
        self.info_btn.pack(side="left", padx=4)
        self.close_btn = ttk.Button(btns, text="Close and sign...", command=self.close_watch)
        self.close_btn.pack(side="left")
        self.back_btn = ttk.Button(btns, text="Back to current watch", command=self._back)

        self.banner = tk.Label(r, text="", bg="#c62828", fg="white", font=("Segoe UI", 12, "bold"), pady=6)

        pw = ttk.PanedWindow(r, orient="vertical")
        pw.pack(fill="both", expand=True, padx=10, pady=(0, 6))

        # patrol board
        board = ttk.LabelFrame(pw, text="Patrol board", padding=6)
        cols = [("call_sign", "Call sign", 200), ("name", "Name", 150), ("po", "PO#", 70), ("cox", "Coxswain", 230),
                ("crew", "Crew", 260), ("pob", "POB", 40), ("gar", "GAR", 40), ("state", "Status", 80),
                ("last", "Last contact", 110), ("due", "Next report due", 120), ("left", "Min", 50)]
        bf = ttk.Frame(board)
        bf.pack(fill="both", expand=True)
        self.board = ttk.Treeview(bf, columns=[c[0] for c in cols], show="headings", height=4, selectmode="browse")
        for c, label, w in cols:
            self.board.heading(c, text=label)
            self.board.column(c, width=w, stretch=c in ("crew", "name"))
        for state, color in COLORS.items():
            self.board.tag_configure(state, background=color)
        self.board.pack(side="left", fill="both", expand=True)
        self.board.bind("<<TreeviewSelect>>", self._patrol_selected)
        self.board.bind("<Double-1>", lambda e: self.edit_patrol())
        pb = ttk.Frame(bf, padding=(8, 0, 0, 0))
        pb.pack(side="left", fill="y")
        self.patrol_btns = []
        for text, cmd in [("Add patrol...", self.add_patrol), ("Edit patrol...", self.edit_patrol),
                          ("Remove patrol", self.remove_patrol)]:
            b = ttk.Button(pb, text=text, command=cmd, width=16)
            b.pack(fill="x", pady=1)
            self.patrol_btns.append(b)
        ttk.Separator(pb).pack(fill="x", pady=4)
        for text, t in [("Underway", "UNDERWAY"), ("Ops normal", "OPS NORMAL"), ("Moored", "MOORED")]:
            b = ttk.Button(pb, text=text, width=16, command=lambda t=t: self.quick(t))
            b.pack(fill="x", pady=1)
            self.patrol_btns.append(b)
        pw.add(board, weight=1)

        # entry form
        lower = ttk.Frame(pw)
        form = ttk.LabelFrame(lower, text="New log entry   (Time is taken when you start the entry; "
                                          "Enter logs it)", padding=8)
        form.pack(fill="x")
        self.f = {}

        def field(col, label, widget, width_weight=0):
            ttk.Label(form, text=label).grid(row=0, column=col, sticky="w", padx=3)
            widget.grid(row=1, column=col, sticky="we", padx=3)
            form.columnconfigure(col, weight=width_weight)
            return widget

        self.f["time"] = field(0, "Time", ttk.Entry(form, width=8))
        self.f["station"] = field(1, "Vessel / station", ttk.Combobox(form, width=22))
        self.f["direction"] = field(2, "From / to", ttk.Combobox(form, width=7, state="readonly",
                                                                  values=["From", "To"]))
        self.f["direction"].set("To")
        self.f["channel"] = field(3, "Ch", ttk.Combobox(form, width=6))
        self.f["type"] = field(4, "Type", ttk.Combobox(form, width=14, state="readonly", values=ENTRY_TYPES))
        self.f["type"].set("OPS NORMAL")
        self.f["position"] = field(5, "Position (Loc)", ttk.Entry(form, width=28), 2)
        self.f["heading"] = field(6, "Hdg", ttk.Entry(form, width=8))
        # GAR: filled in with the vessel's latest GAR; type a new score to record a change.
        self.f["gar"] = field(7, "GAR", ttk.Entry(form, width=5))
        self.f["status"] = field(8, "Status", ttk.Entry(form, width=22), 1)
        self.f["remarks"] = field(9, "Remarks", ttk.Entry(form, width=26), 2)
        self.log_btn = ttk.Button(form, text="Log", command=self.log_entry, width=8)
        self.log_btn.grid(row=1, column=10, padx=(6, 0))
        for w in self.f.values():
            w.bind("<Return>", lambda e: self.log_entry())
        self.f["station"].bind("<<ComboboxSelected>>", lambda e: self._station_picked())
        self.f["station"].bind("<FocusOut>", lambda e: self._station_picked(), add="+")
        # Picking a type sets From / to: "To" for the watch stander's routine calls, "From" when
        # the vessel calls in with an issue (distress, urgency, safety). It can still be changed.
        self.f["type"].bind("<<ComboboxSelected>>", lambda e: self._default_direction())
        # The entry's time is the moment the entry is started (first keystroke or pick in the
        # form), not when Enter is pressed, so typing doesn't delay it. It shows in Time.
        self._stamp, self._stamp_text = None, ""
        for k, w in self.f.items():
            if k != "time":
                w.bind("<Key>", self._start_entry, add="+")
                if isinstance(w, ttk.Combobox):
                    w.bind("<<ComboboxSelected>>", self._start_entry, add="+")
        self.hint = ttk.Label(lower, text="", foreground="#666")
        self.hint.pack(anchor="w", pady=(2, 0))

        # log
        lf = ttk.LabelFrame(lower, text="Radio log", padding=6)
        lf.pack(fill="both", expand=True, pady=(4, 0))
        lcols = [("z", "Time Z", 60), ("loc", "Local", 50), ("st", "Vessel / station", 130), ("ch", "Ch", 40),
                 ("type", "Type", 100), ("pos", "Position", 220), ("hdg", "Hdg", 60), ("gar", "GAR", 70),
                 ("status", "Status", 160),
                 ("rem", "Remarks", 260), ("init", "Struck by", 70)]
        inner = ttk.Frame(lf)
        inner.pack(fill="both", expand=True)
        self.log = ttk.Treeview(inner, columns=[c[0] for c in lcols], show="headings", selectmode="browse")
        for c, label, w in lcols:
            self.log.heading(c, text=label)
            self.log.column(c, width=w, stretch=c in ("pos", "rem", "status"))
        self.log.tag_configure("struck", foreground="#888", font=self.strike_font)
        self.log.tag_configure("distress", foreground="#b00020", font=self.bold_font)
        self.log.tag_configure("note", foreground="#555")
        sb = ttk.Scrollbar(inner, command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        self.log.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        self.log.bind("<Double-1>", lambda e: self.strike())
        lb = ttk.Frame(lf)
        lb.pack(fill="x", pady=(6, 0))
        self.strike_btn = ttk.Button(lb, text="Strike / correct selected...", command=self.strike)
        self.strike_btn.pack(side="left")
        ttk.Button(lb, text="Print log", command=lambda: self.print_log(self.wid)).pack(side="left", padx=6)
        ttk.Button(lb, text="Save PDF...", command=lambda: self.save_log(self.wid, ".pdf")).pack(side="left")
        ttk.Button(lb, text="Save Word...", command=lambda: self.save_log(self.wid, ".docx")).pack(side="left", padx=6)
        ttk.Button(lb, text="ICS 309", command=lambda: self.print_ics309(self.wid)).pack(side="left")
        self.count_lbl = ttk.Label(lb, text="", foreground="#555")
        self.count_lbl.pack(side="right")
        pw.add(lower, weight=4)

        self.status = ttk.Label(r, text="", relief="sunken", anchor="w", padding=(6, 2))
        self.status.pack(fill="x", side="bottom")

    # ---- state
    def show_watch(self, wid):
        self.wid = wid
        w = self.db.watch(wid) if wid else None
        cur = self.db.current_watch()
        self.viewing_past = bool(w and w["locked"])
        if self.viewing_past and cur:
            self.back_btn.pack(side="left", padx=(8, 0))
        else:
            self.back_btn.pack_forget()
        self.refresh()

    def _back(self):
        cur = self.db.current_watch()
        self.show_watch(cur["id"] if cur else None)

    def editable(self):
        w = self.db.watch(self.wid) if self.wid else None
        return bool(w and not w["locked"])

    def refresh(self):
        w = self.db.watch(self.wid) if self.wid else None
        ed = self.editable()
        if w:
            self.station_lbl.config(text=f"{w['station_call_sign']}   ·   Ch {w['channels']}"
                                         + ("   ·   CLOSED (read only)" if w["locked"] else ""))
            opened = from_iso(w["opened_utc"])
            self.watch_lbl.config(text=f"Watchstander: {w['watchstander']}    Opened {opened:%H%MZ} "
                                       f"({opened.astimezone():%m/%d/%Y %H%M} local)"
                                       + (f"    Closed {fmt_z(w['closed_utc'])}, signed {w['signed_by']}" if w["locked"] else "")
                                       + (f"    Time check: {w['time_check']}" if w["time_check"] else "    Time check: NOT LOGGED"))
        else:
            self.station_lbl.config(text=f"{self.db.setting('station_call_sign')}   ·   no watch open")
            self.watch_lbl.config(text="Open a watch to start logging (File > Open new watch, or the button).")
        self.open_btn.state(["!disabled"] if not self.db.current_watch() else ["disabled"])
        for b in [self.close_btn, self.info_btn, self.log_btn, self.strike_btn, *self.patrol_btns]:
            b.state(["!disabled"] if ed else ["disabled"])
        for wdg in self.f.values():
            wdg.state(["!disabled"] if ed else ["disabled"])
            if ed and isinstance(wdg, ttk.Combobox) and wdg in (self.f["type"], self.f["direction"]):
                wdg.state(["readonly"])
        self.f["channel"]["values"] = [c["name"] for c in self.db.channels()]
        if not self.f["channel"].get():
            self.f["channel"].set(self.db.setting("default_channel"))
        self._fill_board()
        self._fill_log()
        retain = f"   ·   Retain until {self.db.retain_until(self.wid):%d %b %Y}" if w and w["locked"] else ""
        self.status.config(text=f"Database: {self.db.path}{retain}")

    def _fill_board(self):
        """Rebuild the patrol board, updating rows in place so the selection (and the
        entry form it fills) is not disturbed by the 5-second refresh."""
        self.patrol_ids = {}
        stations = []
        if not self.wid:
            self.board.delete(*self.board.get_children())
            self.f["station"]["values"] = []
            return
        now = utcnow()
        rows = self.db.schedule(self.wid, now)
        wanted = [str(r["patrol"]["id"]) for r in rows]
        if list(self.board.get_children()) != wanted:
            self.board.delete(*self.board.get_children())
            for iid in wanted:
                self.board.insert("", "end", iid=iid)
        for row in rows:
            p = row["patrol"]
            crew = self.db.patrol_crew(p["id"])
            cox = next((c for c in crew if c["role"] == "COX"), None)
            mates = ", ".join(c["last_name"] + (" (T)" if c["trainee"] else "") for c in crew if c["role"] == "CREW")
            left = ""
            if row["due"]:
                mins = int((row["due"] - now).total_seconds() // 60)
                left = f"{mins:+d}" if mins < 0 else str(mins)
            self.board.item(str(p["id"]), tags=(row["state"],), values=[
                # Call sign from Data > Vessels; the facility # stands in until one is entered.
                p["call_sign"] or f"Facility {p['facility_no']}", p["vessel_name"], p["patrol_order"],
                (cox["last_name"] + (" (T)" if cox["trainee"] else "") + (f"  {cox['phone']}" if cox["phone"] else ""))
                if cox else "", mates, p["pob"], self.db.current_gar(p["id"]),
                row["state"], f"{fmt_z(row['last'])} / {fmt_local(row['last'])}" if row["last"] else "",
                f"{fmt_z(row['due'])} / {fmt_local(row['due'])}" if row["due"] else "", left])
            label = f"{p['facility_no']} {p['vessel_name']}".strip()
            self.patrol_ids[label] = p["id"]
            stations.append(label)
        stations += [c["name"] for c in self.db.contacts() if c["name"].upper().startswith("COAST GUARD")][:4]
        self.f["station"]["values"] = stations

    def _fill_log(self):
        self.log.delete(*self.log.get_children())
        if not self.wid:
            self.count_lbl.config(text="")
            return
        entries = self.db.entries(self.wid)
        times = {e["id"]: e["time_utc"] for e in entries}
        changes = self.db.gar_changes(self.wid)
        for e in entries:
            # GAR column: "22 → 18" where the score changed; otherwise the score at that report.
            gar = f"{changes[e['id']][0] or '-'} → {changes[e['id']][1]}" if e["id"] in changes else e["gar"]
            tag = "struck" if e["struck"] else ("distress" if e["entry_type"] in wdb.DISTRESS_TYPES else "")
            rem = e["remarks"]
            if e["corrects_id"]:
                rem = (rem + "  " if rem else "") + f"(corrects {fmt_z(times.get(e['corrects_id'], ''))})"
            self.log.insert("", "end", iid=str(e["id"]), tags=(tag,), values=[
                fmt_z(e["time_utc"]), fmt_local(e["time_utc"]),
                e["station"] + (" (to)" if e["direction"] == "TO" else ""), e["channel"], e["entry_type"],
                e["position"], e["heading"], gar, e["status"], rem, e["struck_by"]])
        kids = self.log.get_children()
        if kids:
            self.log.see(kids[-1])
        struck = sum(1 for e in entries if e["struck"])
        self.count_lbl.config(text=f"{len(entries)} entries" + (f", {struck} struck" if struck else ""))

    # ---- clock / alerts
    def _tick(self):
        now = utcnow()
        self.utc_lbl.config(text=f"{now:%H:%M:%S}Z")
        self.loc_lbl.config(text=f"{now.astimezone():%H:%M:%S} {wdb.tz_abbr(now.astimezone())}")
        if now.second % 5 == 0 and self.wid and self.editable():
            self._fill_board()
            self._check_alerts(now)
        self.root.after(1000 - datetime.now().microsecond // 1000, self._tick)

    def _check_alerts(self, now):
        overdue = [r for r in self.db.schedule(self.wid, now) if r["state"] == "OVERDUE"]
        if not overdue:
            self.banner.pack_forget()
            return
        names = ", ".join(f"{r['patrol']['facility_no']} ({r['overdue_min']} min)" for r in overdue)
        self.banner.config(text=f"OPS NORMAL OVERDUE: {names} — call the vessel; if no answer, "
                                f"start lost-communications procedures and notify the Coast Guard station.")
        if not self.banner.winfo_ismapped():
            self.banner.pack(fill="x", padx=10, pady=(0, 6), after=self.station_lbl.master.master)
        if now - self.last_bell >= timedelta(seconds=60):
            self.last_bell = now
            self.root.bell()
            for _ in range(2):
                self.root.after(300, self.root.bell)
            try:
                self.root.deiconify()
                self.root.lift()
            except tk.TclError:
                pass

    # ---- actions
    def open_watch(self):
        if self.db.current_watch():
            messagebox.showinfo(APP, "A watch is already open. Close and sign it first.", parent=self.root)
            return
        d = OpenWatchDialog(self.root, self.db)
        self.root.wait_window(d)
        if d.result:
            self.show_watch(d.result)

    def watch_details(self):
        if self.editable():
            d = WatchInfoDialog(self.root, self.db, self.wid)
            self.root.wait_window(d)
            self.refresh()

    def close_watch(self):
        if not self.editable():
            return
        under = [r["patrol"]["facility_no"] for r in self.db.schedule(self.wid) if r["state"] not in ("MOORED", "PLANNED")]
        if under and not messagebox.askyesno(
                APP, f"Still underway: {', '.join(under)}.\n\nClose the watch anyway? "
                     "Log a WATCH RELIEF entry first if the guard is being passed to another station.",
                parent=self.root):
            return
        if not self.db.watch(self.wid)["time_check"] and not messagebox.askyesno(
                APP, "No time check is recorded for this watch. Close anyway?", parent=self.root):
            return
        sig = simpledialog.askstring(
            "Close and sign", "Closing locks this log permanently (read only).\n\n"
                              "Watchstander name or initials:", parent=self.root)
        if not sig:
            return
        try:
            self.db.close_watch(self.wid, sig)
        except LogError as ex:
            messagebox.showerror(APP, str(ex), parent=self.root)
            return
        wid = self.wid
        self.banner.pack_forget()
        self.show_watch(wid)
        ans = messagebox.askyesnocancel(
            APP, "Watch closed and locked.\n\nSave the completed log as a PDF now?\n\n"
                 "Yes = PDF    No = Word (.docx)    Cancel = not now\n"
                 "(It can be saved or printed later from File > Watches.)", parent=self.root)
        if ans is not None:
            self.save_log(wid, ".pdf" if ans else ".docx")

    def _selected_patrol(self, warn=True):
        sel = self.board.selection()
        if not sel:
            if warn:
                messagebox.showinfo(APP, "Select a vessel on the patrol board first.", parent=self.root)
            return None
        return int(sel[0])

    def add_patrol(self):
        if not self.editable():
            return
        d = PatrolDialog(self.root, self.db, self.wid)
        self.root.wait_window(d)
        self.refresh()

    def edit_patrol(self):
        pid = self._selected_patrol()
        if pid and self.editable():
            d = PatrolDialog(self.root, self.db, self.wid, pid)
            self.root.wait_window(d)
            self.refresh()

    def remove_patrol(self):
        pid = self._selected_patrol()
        if not pid or not messagebox.askyesno(APP, "Remove this patrol from the board?", parent=self.root):
            return
        try:
            self.db.remove_patrol(pid)
        except LogError as ex:
            messagebox.showerror(APP, f"{ex}.\nUse Moored instead.", parent=self.root)
        self.refresh()

    def _patrol_selected(self, _e=None):
        pid = self._selected_patrol(warn=False)
        if pid:
            label = next((k for k, v in self.patrol_ids.items() if v == pid), "")
            self.f["station"].set(label)
            self._station_picked()

    def _station_picked(self):
        pid = self.patrol_ids.get(self.f["station"].get())
        if pid != getattr(self, "_gar_pid", None):
            # New vessel: GAR starts at that vessel's latest score (blank for other stations).
            self._gar_pid = pid
            self.f["gar"].delete(0, "end")
            if pid:
                self.f["gar"].insert(0, self.db.current_gar(pid))
        if pid:
            p = self.db.patrol(pid)
            self.hint.config(text=f"{p['facility_no']} {p['vessel_name']}  ·  call sign {p['call_sign'] or '-'}  ·  "
                                  f"cell {p['cell'] or '-'}  ·  GAR {self.db.current_gar(pid) or '-'}  ·  "
                                  f"float plan: {p['float_plan'] or '-'}")
        else:
            self.hint.config(text="")

    def _start_entry(self, event=None):
        """Stamp the time when an entry is started, if Time is still empty. Keys that don't type
        anything (Enter, Tab, arrows, Shift...) don't start an entry."""
        if event is not None and event.type == tk.EventType.KeyPress:
            if not ((event.char and event.char.isprintable()) or event.keysym in ("BackSpace", "Delete")):
                return
        if self.f["time"].get().strip() or not self.editable():
            return
        self._stamp = utcnow()
        self._stamp_text = fmt_z(self._stamp)
        self.f["time"].insert(0, self._stamp_text)

    def _default_direction(self):
        self.f["direction"].set("From" if wdb.default_direction(self.f["type"].get()) == "FROM" else "To")

    def quick(self, entry_type):
        pid = self._selected_patrol()
        if not pid:
            return
        self._patrol_selected()
        self._start_entry()               # the Underway / Ops normal / Moored click starts the entry
        self.f["type"].set(entry_type)
        self._default_direction()
        if entry_type == "OPS NORMAL" and not self.f["status"].get():
            self.f["status"].insert(0, "Ops normal")
        self.f["position"].focus_set()
        self.hint.config(text=self.hint.cget("text") + "   ← add position / heading, then press Enter")

    def log_entry(self):
        if not self.editable():
            return
        f = self.f
        station = f["station"].get().strip()
        try:
            text = f["time"].get().strip()
            # The stamp taken when the entry was started (to the second), unless Time was changed by
            # hand; a blank Time (nothing typed before Enter) is now.
            when = self._stamp if self._stamp and text == self._stamp_text else parse_time(text)
            pid = self.patrol_ids.get(station)
            gar = f["gar"].get().strip()
            if pid and gar and gar != self.db.current_gar(pid) and not messagebox.askyesno(
                    "GAR change", f"Record a GAR change for {station}: "
                    f"{self.db.current_gar(pid) or '(none)'} → {gar}?", parent=self.root):
                f["gar"].focus_set()
                return
            self.db.add_entry(
                self.wid, f["type"].get(), time=when, patrol_id=pid,
                station="" if pid else station, direction="TO" if f["direction"].get() == "To" else "FROM",
                channel=f["channel"].get().strip(), position=f["position"].get().strip(),
                heading=f["heading"].get().strip(), status=f["status"].get().strip(),
                remarks=f["remarks"].get().strip(), gar=gar)
        except (LogError, ValueError) as ex:
            messagebox.showerror("Log entry", str(ex), parent=self.root)
            return
        for k in ("time", "position", "heading", "status", "remarks"):
            f[k].delete(0, "end")
        self._stamp, self._stamp_text = None, ""   # the next entry gets its own time
        self._gar_pid = None         # refill GAR with the (possibly new) latest score
        self._station_picked()
        f["type"].set("OPS NORMAL")
        self._default_direction()
        self.refresh()
        self._check_alerts(utcnow())
        f["position"].focus_set()

    def strike(self):
        sel = self.log.selection()
        if not sel or not self.editable():
            return
        e = self.db.entry(int(sel[0]))
        if e["struck"]:
            messagebox.showinfo(APP, "That entry is already struck through.", parent=self.root)
            return
        d = StrikeDialog(self.root, self.db, e["id"])
        self.root.wait_window(d)
        self.refresh()

    def print_log(self, wid):
        if not wid:
            return
        w = self.db.watch(wid)
        name = f"watchlog_{from_iso(w['opened_utc']).astimezone():%Y%m%d_%H%M}_{wid}.html"
        open_html(reports.watch_log_html(self.db, wid), name)

    def save_log(self, wid, ext, parent=None):
        """Save one watch's log as .pdf or .docx where the user chooses."""
        if not wid:
            return None
        parent = parent or self.root
        w = self.db.watch(wid)
        opened = from_iso(w["opened_utc"]).astimezone()
        name = f"Watch Log {opened:%Y-%m-%d %H%M} {w['station_call_sign']}".replace("/", "-")
        kinds = {".pdf": ("PDF document", "*.pdf"), ".docx": ("Word document", "*.docx")}
        last_dir = self.db.setting("save_dir") or os.path.expanduser("~\\Documents")
        path = filedialog.asksaveasfilename(parent=parent, title=f"Save watch log as {ext[1:].upper()}",
                                            defaultextension=ext, filetypes=[kinds[ext]],
                                            initialdir=last_dir, initialfile=name + ext)
        if not path:
            return None
        if not path.lower().endswith(ext):
            path += ext
        if not w["locked"] and not messagebox.askyesno(
                APP, "This watch is still open, so the saved copy is not final and will say so.\n\nSave anyway?",
                parent=parent):
            return None
        parent.config(cursor="watch")
        if ext == ".pdf":
            self.status.config(text="Making the PDF (this takes about 10-20 seconds)...")
        parent.update_idletasks()
        try:
            reports.save_watch_log(self.db, wid, path)
        except (RuntimeError, OSError, ValueError) as ex:
            messagebox.showerror(APP, f"Could not save:\n{ex}", parent=parent)
            return None
        finally:
            parent.config(cursor="")
            self.refresh()
        self.db.set_setting("save_dir", os.path.dirname(path))
        if messagebox.askyesno(APP, f"Saved:\n{path}\n\nOpen it now?", parent=parent):
            try:
                os.startfile(path)
            except (OSError, AttributeError):
                webbrowser.open("file:///" + path.replace("\\", "/"))
        return path

    def print_ics309(self, wid):
        if not wid:
            return
        w = self.db.watch(wid)
        open_html(reports.ics309_html(self.db, wid), f"ics309_{from_iso(w['opened_utc']).astimezone():%Y%m%d_%H%M}_{wid}.html")

    def export_csv(self, wids, parent=None):
        path = filedialog.asksaveasfilename(parent=parent or self.root, title="7030 activity CSV",
                                            defaultextension=".csv", filetypes=[("CSV", "*.csv")],
                                            initialfile="watch_activity.csv")
        if path:
            reports.activity_csv(self.db, wids, path)
            messagebox.showinfo("7030", f"Saved {path}", parent=parent or self.root)

    def backup(self):
        path = filedialog.asksaveasfilename(parent=self.root, title="Back up database",
                                            defaultextension=".db", filetypes=[("SQLite database", "*.db")],
                                            initialfile=f"watchlog_backup_{datetime.now():%Y%m%d}.db")
        if path:
            self.db.backup(path)
            messagebox.showinfo(APP, f"Backed up to {path}\n\nIt holds member names, numbers and phone "
                                     "numbers: keep it private.", parent=self.root)

    def edit_patrol_types(self):
        ListEditor(self.root, "Patrol types", [("Patrol type", 260), ("Description", 350)],
                   [(t["name"], t["description"]) for t in self.db.patrol_types()],
                   lambda rows: (self.db.set_patrol_types(rows), self.refresh()))

    def edit_channels(self):
        ListEditor(self.root, "Channels", [("Channel", 90), ("Use", 520)],
                   [(c["name"], c["purpose"]) for c in self.db.channels()],
                   lambda rows: (self.db.set_channels(rows), self.refresh()))

    def edit_contacts(self):
        ListEditor(self.root, "Contacts", [("Name", 300), ("Phone", 140), ("Notes", 220)],
                   [(c["name"], c["phone"], c["notes"]) for c in self.db.contacts()],
                   lambda rows: (self.db.set_contacts(rows), self.refresh()))

    def rules(self):
        messagebox.showinfo("Log-keeping rules", (
            "From the CG Communications Manual (COMDTINST M2000.3G, ch. 8 D.2) and AOPG Vol III 3-F:\n\n"
            "• Log unit name, call sign, date/time in UTC, channel, the traffic, and equipment status/checks.\n"
            "• Get an official time check (time.gov) at the start of each watch.\n"
            "• Never erase. Correct by striking through with your initials and entering the correction alongside.\n"
            "• Log ALL distress, urgency and safety traffic, with originator, channel, time and a short synopsis, "
            "including cell-phone calls about them.\n"
            "• Boats report ops normal every 30 minutes (position, status, weather/sea changes) unless the "
            "operational commander sets another interval. Any contact restarts the clock.\n"
            "• Keep logs 1 year, or 3 years when distress traffic was logged (longer if needed for an investigation).\n"
            "• Logs are releasable under FOIA: refer any request to the Director and FOIA coordinator.\n"
            "• If logging would be unsafe, log afterwards from memory."), parent=self.root)


# ---------------------------------------------------------------- demo data

def make_demo(path):
    """Made-up vessels and members (no real people) for trying the app."""
    if os.path.exists(path):
        os.remove(path)
    db = WatchDB(path)
    for no, name, cell in [("1675", "Sea Breeze", "(904) 555-0101"), ("2210", "Pelican", "(904) 555-0102"),
                           ("3318", "Osprey", "(904) 555-0103")]:
        db.save_vessel({"facility_no": no, "name": name, "cell": cell, "call_sign": f"Coast Guard Auxiliary Vessel {no}"})
    people = [("1000001", "Anchor", "Alex", {"COXSWAIN": "QUALIFIED", "CREW": "QUALIFIED"}),
              ("1000002", "Bowline", "Blake", {"CREW": "QUALIFIED"}),
              ("1000003", "Cleat", "Casey", {"CREW": "TRAINEE"}),
              ("1000004", "Davit", "Drew", {"CREW": "QUALIFIED", "TCO": "QUALIFIED", "QE": "QUALIFIED"}),
              ("1000005", "Ensign", "Emery", {"COXSWAIN": "QUALIFIED", "CREW": "QUALIFIED"}),
              ("1000006", "Fender", "Finley", {"CREW": "QUALIFIED"}),
              ("1000007", "Gunwale", "Gray", {"TCO": "QUALIFIED"})]
    ids = {}
    for no, last, first, q in people:
        ids[last] = db.save_member({"member_no": no, "last_name": last, "first_name": first,
                                    "phone": "(904) 555-01" + no[-2:]}, q)
    sid = db.save_station({"call_sign": "Amelia 9", "street": "100 Example Way", "city": "Fernandina Beach",
                           "state": "FL", "zip": "32034", "latitude": "30 40.09 N", "longitude": "081 27.67 W",
                           "phone": "(904) 555-0190"})
    rws = db.save_watchstander({"member_no": "1000007", "last_name": "Gunwale", "first_name": "Gray",
                                "phone": "(904) 555-0107", "tco_date": "03/15/2024"})
    db.save_watchstander({"member_no": "1000004", "last_name": "Davit", "first_name": "Drew",
                          "phone": "(904) 555-0104", "tco_date": "06/01/2022"})
    now = utcnow()
    opened = now - timedelta(hours=2)
    wid = db.open_watch(rws_id=rws, station_id=sid, time_check=f"Clock checked against time.gov at {opened:%H%MZ}",
                        radio_check="Loud and clear with Station Mayport Ch 83A", when=opened)
    vessels = {v["facility_no"]: v["id"] for v in db.vessels()}
    p1 = db.add_patrol(wid, vessels["1675"], ids["Anchor"], [ids["Bowline"], ids["Cleat"], ids["Davit"]],
                       patrol_order="284083", gar="22", mission="Safety patrol",
                       float_plan="Amelia River to St Marys Inlet")
    p2 = db.add_patrol(wid, vessels["2210"], ids["Ensign"], [ids["Fender"]], patrol_order="284091", gar="18",
                       mission="Safety patrol", float_plan="Nassau Sound")
    t = now - timedelta(minutes=110)
    db.add_entry(wid, "UNDERWAY", time=t, patrol_id=p1, position="U/W from Amelia Island Marina", heading="North")
    db.add_entry(wid, "UNDERWAY", time=t + timedelta(minutes=10), patrol_id=p2, position="Fernandina Harbor Marina", heading="South")
    db.add_entry(wid, "OPS NORMAL", time=t + timedelta(minutes=30), patrol_id=p1, position="St Marys Inlet",
                 heading="East", status="Ops normal", remarks="Seas 1-2 ft")
    e = db.add_entry(wid, "OPS NORMAL", time=t + timedelta(minutes=40), patrol_id=p2, position="Nassau Sound",
                     heading="S", status="Ops normal")
    db.correct(e, "GG", position="Nassau Sound, Bird Island", heading="SW")
    db.add_entry(wid, "OPS NORMAL", time=t + timedelta(minutes=62), patrol_id=p1, position="Fort Clinch",
                 heading="West", status="Ops normal")
    db.add_entry(wid, "COMMS", time=t + timedelta(minutes=72), patrol_id=p2, position="Nassau Sound",
                 status="Assisting disabled 18 ft skiff, 2 POB, no injuries", remarks="Tow requested from TowBoatUS")
    db.add_entry(wid, "OPS NORMAL", time=now - timedelta(minutes=26), patrol_id=p1, position="Amelia River R \"14\"",
                 heading="South", status="Ops normal")
    db.close()


def main():
    ap = argparse.ArgumentParser(description=APP)
    ap.add_argument("--db", help="database file (default: %%APPDATA%%\\N. Tesla\\VHF Watch Log\\watchlog.db)")
    ap.add_argument("--demo", metavar="FILE", help="create FILE with made-up data and open it")
    args = ap.parse_args()
    path = args.db
    if args.demo:
        make_demo(args.demo)
        path = args.demo
    import disclaimer
    if not disclaimer.require(APP):
        return 0
    db = WatchDB(path)
    root = tk.Tk()
    App(root, db)
    root.mainloop()
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
