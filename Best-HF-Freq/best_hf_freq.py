"""Best-HF-Freq: picks the HF frequency most likely to work right now, from live solar,
geomagnetic and ionosonde data, and shows how that changes over the next 24 hours.

    python best_hf_freq.py                     window
    python best_hf_freq.py --cli --to FK68     text report (see --help)
"""
import argparse
import json
import os
import queue
import sys
import threading
import traceback
from datetime import datetime, timezone

import hfprop as hp
import planner
import spacewx

APP = "Best-HF-Freq"
VERSION = "1.0"
REFRESH_MS = 15 * 60 * 1000
RATING_COLORS = {"Excellent": "#c8f0c8", "Good": "#e4f5c0", "Fair": "#fff2b3",
                 "Poor": "#fbd7b5", "Closed": "#eeeeee"}

HOW_IT_WORKS = """\
Best-HF-Freq estimates, for every channel in your list, the chance that a contact on it works now.

1. Live data (refreshed every 15 minutes)
   NOAA SWPC: solar flux (SFI), planetary K index, GOES X-ray flux, NOAA R/S/G scales.
   KC2G (prop.kc2g.com): the effective sunspot number fitted to ionosonde data, and the latest
   foF2 readings from GIRO/NOAA ionosondes (readings older than 90 minutes are ignored).

2. Ionosphere
   foF2 (the highest frequency the F2 layer returns straight up) comes from a model driven by the
   sunspot number and the sun's angle, corrected by nearby ionosondes. Storms (Kp 5 or more)
   lower it.

3. Path
   Each path is split into hops. The MUF (maximum usable frequency) is foF2 times the secant of
   the angle the signal meets the layer, at the worst reflection point. The FOT (optimum working
   frequency) is the frequency below the MUF on 90% of days. NVIS paths reflect almost straight
   up, so their MUF is close to foF2: when foF2 drops at night, the low channels take over.

4. Losses and LUF
   D-layer absorption (strongest at midday and at low frequencies, more during X-ray flares),
   spreading, extra ground reflections and noise. The LUF (lowest usable frequency) is where
   these losses use up the station's margin, set by mode, power and antenna gain.

5. Reliability
   Chance the channel is below the day's MUF x chance the signal clears its losses. Net mode
   averages over every NetLog member within the distance limit and counts how many are covered
   (50% or better).

The model is a planning aid for mid-latitudes, not a substitute for listening, soundings (ALE)
or official forecasts. Treat the numbers as a ranking, not a guarantee.
"""


def settings_path():
    return os.path.join(spacewx.data_dir(), "settings.json")


def load_settings():
    s = dict(planner.DEFAULTS)
    try:
        with open(settings_path(), encoding="utf-8") as f:
            s.update({k: v for k, v in json.load(f).items() if k in s})
    except (OSError, ValueError):
        pass
    if s["mode"] not in hp.MODES:
        s["mode"] = planner.DEFAULTS["mode"]
    return s


def save_settings(s):
    try:
        with open(settings_path(), "w", encoding="utf-8") as f:
            json.dump(s, f, indent=2)
    except OSError:
        pass


def load_members(settings):
    db = planner.find_netlog_db(settings.get("netlog_db", ""))
    return planner.netlog_members(db) if db else []


def fmt_age(dt):
    if not dt:
        return "?"
    mins = (datetime.now(timezone.utc) - dt).total_seconds() / 60
    return f"{mins:.0f} min ago" if mins < 120 else f"{mins / 60:.1f} h ago"


def conditions_text(c):
    """One-line summaries of the current conditions."""
    sc = c.scales or {}
    lines = [
        f"Solar flux {c.sfi:.0f}" if c.sfi is not None else "Solar flux ?",
        f"Sunspot number {c.ssn:.0f} ({c.ssn_source})" if c.ssn is not None else "Sunspot number ?",
        f"Kp {c.kp:.1f}   A {c.a_index:.0f}" if c.kp is not None and c.a_index is not None
        else f"Kp {c.kp:.1f}" if c.kp is not None else "Kp ?",
        f"X-ray {c.xray_class}",
        f"NOAA scales  R{sc.get('R', '?')}  S{sc.get('S', '?')}  G{sc.get('G', '?')}",
        f"{len(c.ionosondes)} live ionosondes",
    ]
    return lines


def warnings_for(c, result):
    out = []
    if c.xray_flux and c.xray_flux >= 1e-5:
        out.append(f"Solar flare {c.xray_class} in progress: low frequencies on the daylit side may "
                   "fade out for minutes to hours (radio blackout). Go higher in frequency.")
    if c.kp is not None and c.kp >= 5:
        out.append(f"Geomagnetic storm (Kp {c.kp:.1f}): MUFs are lower and less predictable, "
                   "especially on northern paths.")
    if result.best.reliability < 0.35:
        out.append("No channel in the list is likely to work right now on this path.")
    elif len(result.paths) == 1:
        p = result.paths[0]
        if p.luf(result.margin) > p.fot:
            out.append("The LUF is above the FOT: the usable window is narrow or closed.")
    return out


# ── command line ──────────────────────────────────────────────────────────────

def run_cli(args):
    s = load_settings()
    if args.from_:
        s["location"] = args.from_
    if args.to:
        s["path_mode"], s["target"] = "station", args.to
    if args.nvis is not None:
        s["path_mode"], s["nvis_km"] = "nvis", args.nvis
    if args.net:
        s["path_mode"] = "net"
    if args.mode:
        matches = [m for m in hp.MODES if m.lower().startswith(args.mode.lower())]
        if not matches:
            sys.exit(f"unknown mode {args.mode!r}; choose from: {', '.join(hp.MODES)}")
        s["mode"] = matches[0]
    if args.watts:
        s["watts"] = args.watts
    if args.channels:
        s["channel_set"] = args.channels
    c = spacewx.get_conditions()
    for e in c.errors:
        print("warning:", e, file=sys.stderr)
    members = load_members(s) if s["path_mode"] in ("net", "station") else []
    plan = planner.build_plan(s, c, members)
    r = plan.now
    print(" | ".join(conditions_text(c)))
    print(f"From {s['location']}  {s['mode']}  {s['watts']} W  margin {r.margin:.0f} dB")
    if len(r.paths) == 1:
        p = r.paths[0]
        print(f"Path {r.targets[0].label}: {p.distance:.0f} km, {p.hops} hop(s), "
              f"MUF {p.muf:.1f}  FOT {p.fot:.1f}  LUF {p.luf(r.margin):.1f} MHz")
    else:
        print(f"Net: {len(r.targets)} stations")
    for w in warnings_for(c, r):
        print("!", w)
    print(f"\n{'Channel':<12}{'MHz':>9}{'Reliab.':>9}  {'Rating':<10}{'f/MUF':>7}{'Loss dB':>9}"
          + ("  Covered" if len(r.paths) > 1 else ""))
    for x in r.ranking:
        print(f"{x.channel.name:<12}{x.channel.mhz:>9.4f}{x.reliability * 100:>8.0f}%  "
              f"{hp.rating(x.reliability):<10}{x.muf_ratio:>7.2f}{x.loss_db:>9.1f}"
              + (f"  {x.covered}/{len(r.paths)}" if len(r.paths) > 1 else ""))
    print("\nNext 24 h (UTC hour: best channel, reliability)")
    print("  ".join(f"{h.when:%H}:{h.best.channel.name} {h.best.reliability * 100:.0f}%"
                    for h in plan.hourly))


# ── window ────────────────────────────────────────────────────────────────────

def run_gui():
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    import disclaimer

    if not disclaimer.require(APP):
        return

    class App:
        def __init__(self, root):
            self.root = root
            self.s = load_settings()
            self.conditions = None
            self.plan = None
            self.members = []
            self.channels = []
            self.jobs = queue.Queue()
            self.busy = False
            self.pending = False

            root.title(APP)
            root.geometry("1180x720")
            root.minsize(980, 600)
            style = ttk.Style(root)
            if "vista" in style.theme_names():
                style.theme_use("vista")
            style.configure("Big.TLabel", font=("Segoe UI", 13, "bold"))
            style.configure("Warn.TLabel", foreground="#a33000", font=("Segoe UI", 9, "bold"))
            style.configure("Treeview", rowheight=22)

            self._menu()
            self._conditions_bar()
            body = ttk.Frame(root, padding=(8, 0, 8, 4))
            body.pack(fill="both", expand=True)
            self._settings_panel(body)
            self._notebook(body)
            self.status = tk.StringVar(value="Starting...")
            ttk.Label(root, textvariable=self.status, relief="sunken", anchor="w",
                      padding=(6, 2)).pack(fill="x", side="bottom")

            root.protocol("WM_DELETE_WINDOW", self.quit)
            root.after(100, self._poll_jobs)
            self.reload_members()
            self.refresh()
            root.after(REFRESH_MS, self._auto_refresh)

        # ── layout ──
        def _menu(self):
            m = tk.Menu(self.root)
            f = tk.Menu(m, tearoff=False)
            f.add_command(label="Refresh data now", command=self.refresh, accelerator="F5")
            f.add_separator()
            f.add_command(label="Exit", command=self.quit)
            m.add_cascade(label="File", menu=f)
            h = tk.Menu(m, tearoff=False)
            h.add_command(label="How it works", command=lambda: self._text_window("How it works", HOW_IT_WORKS))
            h.add_command(label="Disclaimer", command=lambda: disclaimer.show(self.root, APP))
            h.add_command(label="About", command=lambda: messagebox.showinfo(
                "About", f"{APP} {VERSION}\n\nCopyright (c) 2026 N. Tesla, Inc. All rights reserved.\n\n"
                "Data: NOAA Space Weather Prediction Center; KC2G / GIRO ionosonde network.",
                parent=self.root))
            m.add_cascade(label="Help", menu=h)
            self.root.config(menu=m)
            self.root.bind("<F5>", lambda e: self.refresh())

        def _conditions_bar(self):
            bar = ttk.LabelFrame(self.root, text="Current conditions", padding=(8, 4))
            bar.pack(fill="x", padx=8, pady=(6, 6))
            self.cond_labels = []
            for i in range(6):
                lab = ttk.Label(bar, text="...", width=26 if i == 1 else 18)
                lab.grid(row=0, column=i, sticky="w", padx=(0, 10))
                self.cond_labels.append(lab)
            self.data_age = ttk.Label(bar, text="", foreground="#555")
            self.data_age.grid(row=1, column=0, columnspan=5, sticky="w")
            ttk.Button(bar, text="Refresh", command=self.refresh).grid(row=0, column=6, rowspan=2, padx=(10, 0))
            bar.columnconfigure(5, weight=1)

        def _settings_panel(self, parent):
            p = ttk.Frame(parent, width=300)
            p.pack(side="left", fill="y", padx=(0, 8))
            v = self.vars = {}
            for k in planner.DEFAULTS:
                if k != "custom_channels":
                    v[k] = tk.StringVar(value=str(self.s[k]))

            g = ttk.LabelFrame(p, text="My station", padding=8)
            g.pack(fill="x")
            ttk.Label(g, text="Location (grid or lat, lon)").grid(row=0, column=0, columnspan=2, sticky="w")
            e = ttk.Entry(g, textvariable=v["location"], width=30)
            e.grid(row=1, column=0, columnspan=2, sticky="we", pady=(0, 6))
            e.bind("<Return>", lambda ev: self.recompute())
            e.bind("<FocusOut>", lambda ev: self.recompute())
            ttk.Label(g, text="Mode").grid(row=2, column=0, sticky="w")
            cb = ttk.Combobox(g, textvariable=v["mode"], values=list(hp.MODES), state="readonly", width=26)
            cb.grid(row=3, column=0, columnspan=2, sticky="we", pady=(0, 6))
            cb.bind("<<ComboboxSelected>>", lambda ev: self.recompute())
            ttk.Label(g, text="Power (W)").grid(row=4, column=0, sticky="w")
            ttk.Label(g, text="Antenna gain (dBi)").grid(row=4, column=1, sticky="w")
            for col, key in ((0, "watts"), (1, "gain_dbi")):
                sp = ttk.Spinbox(g, textvariable=v[key], width=8,
                                 from_=1 if key == "watts" else -10, to=1500 if key == "watts" else 15,
                                 increment=10 if key == "watts" else 1, command=self.recompute)
                sp.grid(row=5, column=col, sticky="w")
                sp.bind("<Return>", lambda ev: self.recompute())
                sp.bind("<FocusOut>", lambda ev: self.recompute())

            g = ttk.LabelFrame(p, text="Path", padding=8)
            g.pack(fill="x", pady=8)
            ttk.Radiobutton(g, text="NVIS / local, radius (km)", value="nvis", variable=v["path_mode"],
                            command=self.recompute).grid(row=0, column=0, sticky="w")
            sp = ttk.Spinbox(g, textvariable=v["nvis_km"], from_=30, to=600, increment=25, width=6,
                             command=self.recompute)
            sp.grid(row=0, column=1, sticky="e")
            sp.bind("<Return>", lambda ev: self.recompute())
            ttk.Radiobutton(g, text="To station (grid, lat, lon or callsign)", value="station",
                            variable=v["path_mode"], command=self.recompute).grid(row=1, column=0,
                                                                                  columnspan=2, sticky="w")
            self.target_box = ttk.Combobox(g, textvariable=v["target"], width=28)
            self.target_box.grid(row=2, column=0, columnspan=2, sticky="we", padx=(20, 0), pady=(0, 4))
            self.target_box.bind("<Return>", lambda ev: self._choose("station"))
            self.target_box.bind("<<ComboboxSelected>>", lambda ev: self._choose("station"))
            ttk.Radiobutton(g, text="Whole net: NetLog members within (km)", value="net",
                            variable=v["path_mode"], command=self.recompute).grid(row=3, column=0, sticky="w")
            sp = ttk.Spinbox(g, textvariable=v["net_max_km"], from_=100, to=20000, increment=100, width=6,
                             command=self.recompute)
            sp.grid(row=3, column=1, sticky="e")
            sp.bind("<Return>", lambda ev: self.recompute())
            self.members_label = ttk.Label(g, text="", foreground="#555")
            self.members_label.grid(row=4, column=0, columnspan=2, sticky="w", padx=(20, 0))

            g = ttk.LabelFrame(p, text="Channels", padding=8)
            g.pack(fill="x")
            for i, (val, label) in enumerate((("amateur", "Amateur bands"),
                                              ("netlog", "NetLog channel list"),
                                              ("custom", "My own list"))):
                ttk.Radiobutton(g, text=label, value=val, variable=v["channel_set"],
                                command=self.recompute).grid(row=i, column=0, sticky="w")
            ttk.Button(g, text="Edit...", width=7, command=self.edit_custom).grid(row=2, column=1, sticky="e")
            ttk.Button(g, text="NetLog database...", command=self.choose_db).grid(
                row=3, column=0, columnspan=2, sticky="w", pady=(6, 0))
            self.db_label = ttk.Label(g, text="", foreground="#555", wraplength=260)
            self.db_label.grid(row=4, column=0, columnspan=2, sticky="w")
            g.columnconfigure(0, weight=1)

        def _notebook(self, parent):
            nb = self.nb = ttk.Notebook(parent)
            nb.pack(side="left", fill="both", expand=True)

            now = ttk.Frame(nb, padding=8)
            nb.add(now, text="Best now")
            self.best_label = ttk.Label(now, text="", style="Big.TLabel")
            self.best_label.pack(anchor="w")
            self.path_label = ttk.Label(now, text="")
            self.path_label.pack(anchor="w", pady=(2, 2))
            self.warn_label = ttk.Label(now, text="", style="Warn.TLabel", wraplength=780, justify="left")
            self.warn_label.pack(anchor="w", pady=(0, 6))
            cols = ("channel", "mhz", "rel", "rating", "ratio", "loss", "covered")
            heads = ("Channel", "MHz", "Reliability", "Rating", "Freq / MUF", "Loss dB", "Stations covered")
            widths = (130, 90, 90, 90, 90, 80, 120)
            self.tree = self._table(now, cols, heads, widths)

            hours = ttk.Frame(nb, padding=8)
            nb.add(hours, text="Next 24 hours")
            ttk.Label(hours, text="Reliability by hour. The outlined cell is the best channel for that hour. "
                      "Forecast hours assume a quiet sun (no flares); the ionosonde correction fades over "
                      "about 6 hours.", wraplength=820, foreground="#555").pack(anchor="w")
            frame = ttk.Frame(hours)
            frame.pack(fill="both", expand=True, pady=(6, 0))
            self.canvas = tk.Canvas(frame, background="white", highlightthickness=0)
            ys = ttk.Scrollbar(frame, orient="vertical", command=self.canvas.yview)
            xs = ttk.Scrollbar(frame, orient="horizontal", command=self.canvas.xview)
            self.canvas.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
            ys.pack(side="right", fill="y")
            xs.pack(side="bottom", fill="x")
            self.canvas.pack(fill="both", expand=True)

            st = ttk.Frame(nb, padding=8)
            nb.add(st, text="Stations")
            self.station_note = ttk.Label(st, text="", foreground="#555")
            self.station_note.pack(anchor="w", pady=(0, 6))
            cols = ("call", "state", "km", "hops", "muf", "rel", "rating", "own")
            heads = ("Station", "State", "Distance km", "Hops", "MUF MHz", "On best channel", "Rating",
                     "Its own best channel")
            widths = (100, 60, 90, 50, 80, 110, 80, 170)
            self.st_tree = self._table(st, cols, heads, widths)

        def _table(self, parent, cols, heads, widths):
            frame = ttk.Frame(parent)
            frame.pack(fill="both", expand=True)
            tree = ttk.Treeview(frame, columns=cols, show="headings", selectmode="browse")
            for c, h, w in zip(cols, heads, widths):
                tree.heading(c, text=h)
                tree.column(c, width=w, anchor="w" if c in ("channel", "call", "state", "own") else "e",
                            stretch=c in ("channel", "own"))
            for name, color in RATING_COLORS.items():
                tree.tag_configure(name, background=color)
            sb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
            tree.configure(yscrollcommand=sb.set)
            sb.pack(side="right", fill="y")
            tree.pack(fill="both", expand=True)
            return tree

        def _text_window(self, title, text):
            w = tk.Toplevel(self.root)
            w.title(title)
            t = tk.Text(w, wrap="word", width=96, height=34, font=("Segoe UI", 10), padx=10, pady=8)
            t.insert("1.0", text)
            t.configure(state="disabled")
            t.pack(fill="both", expand=True)
            ttk.Button(w, text="Close", command=w.destroy).pack(pady=6)

        # ── settings ──
        def _gather(self):
            s = dict(self.s)
            for k, var in self.vars.items():
                s[k] = var.get().strip()
            for k in ("nvis_km", "net_max_km", "watts", "gain_dbi"):
                try:
                    s[k] = float(s[k])
                except ValueError:
                    raise ValueError(f"{k.replace('_', ' ')} must be a number")
            if s["watts"] <= 0:
                raise ValueError("power must be more than 0 W")
            if not 30 <= s["nvis_km"] <= 1000:
                raise ValueError("NVIS radius must be 30 - 1000 km")
            return s

        def _choose(self, mode):
            self.vars["path_mode"].set(mode)
            self.recompute()

        def edit_custom(self):
            w = tk.Toplevel(self.root)
            w.title("My channel list")
            w.transient(self.root)
            ttk.Label(w, padding=8, justify="left", text=(
                "One channel per line: a name and a frequency, e.g.\n"
                "   Net primary, 5.3305\n   Night alt 3.996 MHz\n   7153.5 kHz\n"
                "Numbers above 100 are read as kHz. Text after # is ignored.")).pack(anchor="w")
            t = tk.Text(w, width=50, height=18, font=("Consolas", 10))
            t.insert("1.0", self.s.get("custom_channels", ""))
            t.pack(fill="both", expand=True, padx=8)

            def save():
                text = t.get("1.0", "end").strip()
                try:
                    planner.parse_channel_lines(text)
                except ValueError as e:
                    messagebox.showerror("My channel list", str(e), parent=w)
                    return
                self.s["custom_channels"] = text
                self.vars["channel_set"].set("custom")
                w.destroy()
                self.recompute()

            b = ttk.Frame(w, padding=8)
            b.pack(fill="x")
            ttk.Button(b, text="Cancel", command=w.destroy).pack(side="right")
            ttk.Button(b, text="Save", command=save).pack(side="right", padx=6)

        def choose_db(self):
            start = planner.find_netlog_db(self.vars["netlog_db"].get())
            path = filedialog.askopenfilename(
                parent=self.root, title="NetLog database (netlog.db)",
                initialdir=os.path.dirname(start) if start else planner.APP_DIR,
                filetypes=[("NetLog database", "*.db"), ("All files", "*.*")])
            if path:
                self.vars["netlog_db"].set(path)
                self.reload_members()
                self.recompute()

        def reload_members(self):
            db = planner.find_netlog_db(self.vars["netlog_db"].get())
            try:
                self.members = planner.netlog_members(db) if db else []
                self.db_label.config(text=db or "NetLog database not found")
            except Exception as e:
                self.members = []
                self.db_label.config(text=f"Cannot read {db}: {e}")
            self.target_box.configure(values=sorted(m.callsign for m in self.members))
            self.members_label.config(
                text=f"{len(self.members)} NetLog members have a position" if self.members
                else "Needs the NetLog database")

        # ── work ──
        def _run_bg(self, fn, done):
            def worker():
                try:
                    out, err = fn(), None
                except Exception as e:
                    out, err = None, e
                    traceback.print_exc()
                self.jobs.put((done, out, err))
            threading.Thread(target=worker, daemon=True).start()

        def _poll_jobs(self):
            try:
                while True:
                    done, out, err = self.jobs.get_nowait()
                    done(out, err)
            except queue.Empty:
                pass
            self.root.after(100, self._poll_jobs)

        def _auto_refresh(self):
            self.refresh()
            self.root.after(REFRESH_MS, self._auto_refresh)

        def refresh(self):
            self.status.set("Fetching space weather and ionosonde data...")
            self._run_bg(spacewx.get_conditions, self._got_conditions)

        def _got_conditions(self, c, err):
            if err:
                self.status.set(f"Could not get conditions: {err}")
                return
            self.conditions = c
            for lab, text in zip(self.cond_labels, conditions_text(c)):
                lab.config(text=text)
            age = f"Data fetched {fmt_age(c.fetched)}"
            if c.errors:
                age += "   |   " + "; ".join(c.errors)
            self.data_age.config(text=age)
            self.recompute()

        def recompute(self):
            if not self.conditions:
                return
            try:
                s = self._gather()
            except ValueError as e:
                self.status.set(str(e))
                return
            if self.busy:           # one at a time; the last request wins
                self.pending = True
                return
            self.busy = True
            self.status.set("Calculating...")
            c, members = self.conditions, list(self.members)
            self._run_bg(lambda: (s, planner.build_plan(s, c, members)), self._got_plan)

        def _got_plan(self, out, err):
            self.busy = False
            if self.pending:
                self.pending = False
                self.recompute()
                return
            if err:
                self.status.set(f"{err}")
                return
            s, plan = out
            self.s.update(s)
            save_settings(self.s)
            self.plan = plan
            self._show_now()
            self._show_hours()
            self._show_stations()
            self.status.set(f"Updated {datetime.now():%H:%M} local "
                            f"({datetime.now(timezone.utc):%H:%M} UTC). Data refreshes every 15 minutes.")

        # ── display ──
        def _show_now(self):
            r = self.plan.now
            b = r.best
            many = len(r.paths) > 1
            self.best_label.config(text=(
                f"Best: {b.channel.name}  {b.channel.mhz:g} MHz   "
                f"{b.reliability * 100:.0f}% ({hp.rating(b.reliability)})"
                + (f",  covers {b.covered} of {len(r.paths)} stations" if many else "")))
            if many:
                d = sorted(p.distance for p in r.paths)
                mufs = sorted(p.muf for p in r.paths)
                self.path_label.config(text=(
                    f"Net of {len(r.paths)} stations, {d[0]:.0f} - {d[-1]:.0f} km away.  "
                    f"Path MUFs {mufs[0]:.1f} - {mufs[-1]:.1f} MHz."))
            else:
                p = r.paths[0]
                src = (f"nearest ionosonde {p.nearest_ionosonde:.0f} km from the reflection point"
                       if p.nearest_ionosonde is not None and p.nearest_ionosonde < 1500
                       else "model only, no ionosonde within 1500 km")
                self.path_label.config(text=(
                    f"{r.targets[0].label}: {p.distance:.0f} km, {p.hops} hop{'s' if p.hops > 1 else ''}.   "
                    f"MUF {p.muf:.1f}   FOT {p.fot:.1f}   LUF {p.luf(r.margin):.1f} MHz   ({src})"))
            self.warn_label.config(text="\n".join(warnings_for(self.conditions, r)))
            self.tree.delete(*self.tree.get_children())
            for x in r.ranking:
                rate = hp.rating(x.reliability)
                self.tree.insert("", "end", tags=(rate,), values=(
                    x.channel.name, f"{x.channel.mhz:.4f}", f"{x.reliability * 100:.0f}%", rate,
                    f"{x.muf_ratio:.2f}", f"{x.loss_db:.1f}",
                    f"{x.covered} of {len(r.paths)}" if many else ""))

        def _show_hours(self):
            cv = self.canvas
            cv.delete("all")
            hours = self.plan.hourly
            chans = sorted((x.channel for x in hours[0].ranking), key=lambda c: c.mhz, reverse=True)
            left, top, cw, ch = 150, 46, 34, 22
            for j, h in enumerate(hours):
                x = left + j * cw
                cv.create_text(x + cw / 2, 12, text=f"{h.when:%H}", font=("Segoe UI", 9, "bold"))
                cv.create_text(x + cw / 2, 28, text=f"{h.when.astimezone():%H}", fill="#666",
                               font=("Segoe UI", 8))
            cv.create_text(left - 8, 12, text="UTC", anchor="e", font=("Segoe UI", 9, "bold"))
            cv.create_text(left - 8, 28, text="Local", anchor="e", fill="#666", font=("Segoe UI", 8))
            by_hour = [{x.channel.name + str(x.channel.mhz): x for x in h.ranking} for h in hours]
            for i, c in enumerate(chans):
                y = top + i * ch
                cv.create_text(left - 8, y + ch / 2, anchor="e", text=f"{c.name}  {c.mhz:g}",
                               font=("Segoe UI", 9))
                for j, h in enumerate(hours):
                    x = left + j * cw
                    rel = by_hour[j][c.name + str(c.mhz)].reliability
                    cv.create_rectangle(x, y, x + cw, y + ch, fill=self._heat(rel), outline="white")
                    if rel >= 0.10:
                        cv.create_text(x + cw / 2, y + ch / 2, text=f"{rel * 100:.0f}", font=("Segoe UI", 8))
                    best = h.best.channel
                    if best.name == c.name and best.mhz == c.mhz:
                        cv.create_rectangle(x + 1, y + 1, x + cw - 1, y + ch - 1, outline="#111", width=2)
            y = top + len(chans) * ch + 10
            if len(hours[0].paths) == 1:
                for label, fn in (("MUF", lambda h: h.paths[0].muf), ("FOT", lambda h: h.paths[0].fot),
                                  ("LUF", lambda h: h.paths[0].luf(h.margin))):
                    cv.create_text(left - 8, y + 9, anchor="e", text=f"{label} MHz", font=("Segoe UI", 9, "bold"))
                    for j, h in enumerate(hours):
                        cv.create_text(left + j * cw + cw / 2, y + 9, text=f"{fn(h):.1f}", font=("Segoe UI", 8))
                    y += 18
            else:
                cv.create_text(left - 8, y + 9, anchor="e", text="Covered", font=("Segoe UI", 9, "bold"))
                for j, h in enumerate(hours):
                    cv.create_text(left + j * cw + cw / 2, y + 9, text=f"{h.best.covered}", font=("Segoe UI", 8))
                y += 18
            lx = left
            for label, rel in (("Excellent", 0.9), ("Good", 0.7), ("Fair", 0.45), ("Poor", 0.2), ("Closed", 0.0)):
                cv.create_rectangle(lx, y + 12, lx + 14, y + 26, fill=self._heat(rel), outline="#999")
                cv.create_text(lx + 18, y + 19, anchor="w", text=label, font=("Segoe UI", 8))
                lx += 80
            cv.configure(scrollregion=(0, 0, left + len(hours) * cw + 10, y + 34))

        @staticmethod
        def _heat(rel):
            # grey (closed) -> orange -> yellow -> green
            stops = ((0.0, (235, 235, 235)), (0.15, (245, 190, 140)), (0.45, (255, 230, 120)),
                     (0.7, (190, 230, 120)), (1.0, (80, 190, 90)))
            for (a, ca), (b, cb) in zip(stops, stops[1:]):
                if rel <= b:
                    t = (rel - a) / (b - a)
                    return "#%02x%02x%02x" % tuple(int(x + (y - x) * t) for x, y in zip(ca, cb))
            return "#50be5a"

        def _show_stations(self):
            r = self.plan.now
            self.st_tree.delete(*self.st_tree.get_children())
            if r.targets and r.targets[0].label.startswith("NVIS"):
                self.station_note.config(text="Choose a station or the whole net to list stations.")
                return
            best = r.best.channel
            chans = [x.channel for x in r.ranking]
            self.station_note.config(text=f"Reliability on {best.name} ({best.mhz:g} MHz), and the channel "
                                     "that would suit each station best by itself.")
            rows = []
            for i, (t, p) in enumerate(zip(r.targets, r.paths)):
                rel = p.reliability(best.mhz, r.margin)
                own, own_rel = r.target_best(i, chans)
                rows.append((rel, t, p, own, own_rel))
            rows.sort(key=lambda x: x[0])
            for rel, t, p, own, own_rel in rows:
                rate = hp.rating(rel)
                self.st_tree.insert("", "end", tags=(rate,), values=(
                    t.label, t.state, f"{p.distance:.0f}", p.hops, f"{p.muf:.1f}",
                    f"{rel * 100:.0f}%", rate, f"{own.name} {own.mhz:g} ({own_rel * 100:.0f}%)"))

        def quit(self):
            try:
                self.s.update(self._gather())
            except ValueError:
                pass
            save_settings(self.s)
            self.root.destroy()

    root = tk.Tk()
    App(root)
    root.mainloop()


def main():
    ap = argparse.ArgumentParser(description=f"{APP}: best HF frequency from live conditions")
    ap.add_argument("--cli", action="store_true", help="print a report instead of opening the window")
    ap.add_argument("--from", dest="from_", help="my location: grid square or 'lat, lon'")
    ap.add_argument("--to", help="far station: grid, 'lat, lon' or NetLog callsign")
    ap.add_argument("--nvis", type=float, metavar="KM", help="NVIS / local coverage radius in km")
    ap.add_argument("--net", action="store_true", help="whole net: NetLog members with a position")
    ap.add_argument("--mode", help="SSB, CW, Digital or FT8")
    ap.add_argument("--watts", type=float)
    ap.add_argument("--channels", choices=("amateur", "netlog", "custom"))
    args = ap.parse_args()
    if args.cli:
        run_cli(args)
    else:
        run_gui()


if __name__ == "__main__":
    main()
