#!/usr/bin/env python3
"""
USCG Auxiliary Southeast District 7 HF Contingency Net Log — GUI
Tkinter interface backed by netlog.py data layer.

Usage:
    python netlog_gui.py                       # new session
    python netlog_gui.py --load session.json   # resume saved session
"""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from datetime import datetime, date
import os, sys, io, pathlib

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

from netlog import (
    NetSession, CheckIn, rank_checkins,
    lookup_operator, lookup_city, lookup_state, lookup_sector,
    lookup_coords, find_matching_callsigns,
    lookup_distance, lookup_bearing,
    count_by_sector, format_freq_display,
    save_to_archive, load_archives, get_db, get_archive_db, load_matrices,
    get_freq_display, get_sectors, get_transceivers, get_net_names,
    get_digital_modes,
    LOCATIONS, PROPAGATIONS, NOISE_LEVELS,
    ANTENNAS, ARCHIVE_FILE,
)

# Lists loaded from netlog.db at startup
FREQ_DISPLAY   = get_freq_display()   # → frequencies table
SECTORS        = get_sectors()        # → sectors table
TRANSCEIVERS   = get_transceivers()   # → transceivers table
NET_NAMES      = get_net_names()      # → Net_Names table
DIGITAL_MODES  = get_digital_modes()  # → Digital_Modes table

# ── Application password ──────────────────────────────────────────────────────
_APP_PASSWORD = 'Thunder'

# ─────────────────────────────────────────────────────────────────────────────
# VISUAL CONSTANTS  (match Excel color scheme)
# ─────────────────────────────────────────────────────────────────────────────
HDR_BG   = '#1F4E79'   # Navy header (Excel dark-blue cell fills)
HDR_FG   = '#FFFFFF'
HDR_DIM  = '#9DC3E6'   # Dimmed label inside header
ENTRY_BG = '#FFFFC0'   # Light yellow  (Excel data-entry cells)
AUTO_BG  = '#F5F5F5'   # Formula/auto-computed cells
SIG_ON   = '#FF8C00'   # Orange  (Excel double-click signal highlight)
SIG_OFF  = '#D8D8D8'
ROW_ODD  = '#FFFFFF'
ROW_EVN  = '#EEF4FF'
OK_GRN   = '#006400'
ERR_RED  = '#CC0000'

# ── Cross-platform font families ─────────────────────────────────────────────
if sys.platform == 'darwin':               # macOS
    _FF = 'Helvetica'
    _FM = 'Menlo'
elif sys.platform.startswith('linux'):     # Linux
    _FF = 'DejaVu Sans'
    _FM = 'DejaVu Sans Mono'
else:                                      # Windows (default)
    _FF = 'Arial'
    _FM = 'Consolas'

FA = (_FF, 10)
FB = (_FF, 10, 'bold')
FT = (_FF, 13, 'bold')
FS = (_FF,  9)
FM = (_FM, 10)          # monospace for callsigns

SIG_CODES = ['L','G','W','VW','F','C','R','UR','D','WI','I','NH']
SIG_TIP   = {'L':'Loud','G':'Good','W':'Weak','VW':'V.Weak','F':'Fading',
             'C':'Clear','R':'Readable','UR':'Unreadable',
             'D':'Distorted','WI':'W/Interference','I':'Intermittent',
             'NH':'Nothing Heard'}


# ─────────────────────────────────────────────────────────────────────────────
# SIGNAL TOGGLE BUTTON
# ─────────────────────────────────────────────────────────────────────────────
class SigBtn(tk.Button):
    """Orange-highlight toggle — emulates Excel double-click cell highlight."""
    def __init__(self, parent, code, **kw):
        self._on = False
        self.code = code
        super().__init__(parent,
                         text=code, width=4,
                         font=(_FF, 9, 'bold'),
                         bg=SIG_OFF, fg='#444',
                         activebackground=SIG_ON,
                         relief=tk.RIDGE, bd=1,
                         command=self._toggle, **kw)

    def _toggle(self):
        self._on = not self._on
        self._apply()

    def _apply(self):
        self.config(
            bg=SIG_ON if self._on else SIG_OFF,
            fg='#FFF' if self._on else '#444',
            relief=tk.SUNKEN if self._on else tk.RIDGE,
        )

    def get(self)        -> bool: return self._on
    def set(self, v)            : self._on = bool(v); self._apply()
    def reset(self)             : self.set(False)


# ─────────────────────────────────────────────────────────────────────────────
# MEMBER ADD / EDIT DIALOG
# ─────────────────────────────────────────────────────────────────────────────
class MemberDialog(tk.Toplevel):
    """Modal dialog for adding or editing a member row in netlog.db."""

    def __init__(self, parent, title='Member', row=None):
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.grab_set()
        self.result = None
        self._build(row)
        self.transient(parent)
        self.update_idletasks()
        # Center over parent
        px, py = parent.winfo_rootx(), parent.winfo_rooty()
        pw, ph = parent.winfo_width(), parent.winfo_height()
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        self.geometry(f'+{px + (pw-w)//2}+{py + (ph-h)//2}')
        self.wait_window()

    def _lbl(self, r, text):
        tk.Label(self, text=text, font=FA, anchor='e').grid(
            row=r, column=0, sticky='e', padx=(12, 5), pady=3)

    def _ent(self, r, var, width=30, disabled=False):
        st = 'disabled' if disabled else 'normal'
        bg = AUTO_BG if disabled else ENTRY_BG
        e = tk.Entry(self, textvariable=var, bg=bg, font=FA, width=width, state=st)
        e.grid(row=r, column=1, sticky='ew', padx=(0, 12), pady=3)
        return e

    def _cmb(self, r, var, values, width=28):
        c = ttk.Combobox(self, textvariable=var, values=values,
                         state='readonly', width=width, font=FA)
        c.grid(row=r, column=1, sticky='ew', padx=(0, 12), pady=3)
        return c

    def _build(self, row):
        is_edit = row is not None

        def v(field, fallback=''):
            val = row[field] if is_edit and row[field] else fallback
            return tk.StringVar(value=val if val else '')

        self.v_cs    = v('callsign')
        self.v_name  = v('operator_name')
        self.v_city  = v('city')
        self.v_state = v('state')
        self.v_email = v('email')
        self.v_sec   = v('sector')
        self.v_phone = v('cell_phone')
        self.v_ham   = v('ham_callsign')
        self.v_wl    = v('winlink_cs')
        self.v_vara  = v('vara_hf_cs')
        self.v_shr   = v('shares_cs')
        self.v_notes = v('notes')

        # Location fields — REAL columns need special None-vs-0 handling
        self.v_lat     = tk.StringVar(value=f'{row["Latitude"]:.4f}'  if (is_edit and row['Latitude']  is not None) else '')
        self.v_lat_dir = tk.StringVar(value=row['Lat_Dir']  or ''     if  is_edit                                   else '')
        self.v_lon     = tk.StringVar(value=f'{row["Longitude"]:.4f}' if (is_edit and row['Longitude'] is not None) else '')
        self.v_lon_dir = tk.StringVar(value=row['Long_Dir'] or ''     if  is_edit                                   else '')

        sector_choices = SECTORS + [f'({s})' for s in SECTORS]

        fields = [
            ('Callsign:',         self.v_cs,      'entry',  False,             is_edit),
            ('Operator Name:',    self.v_name,    'entry',  False,             False),
            ('City:',             self.v_city,    'entry',  False,             False),
            ('State (2-char):',   self.v_state,   'entry',  False,             False),
            ('Email:',            self.v_email,   'entry',  False,             False),
            ('Sector:',           self.v_sec,     'combo',  sector_choices,    False),
            ('Cell Phone:',       self.v_phone,   'entry',  False,             False),
            ('Ham Callsign:',     self.v_ham,     'entry',  False,             False),
            ('Winlink CS:',       self.v_wl,      'entry',  False,             False),
            ('VARA HF CS:',       self.v_vara,    'entry',  False,             False),
            ('SHARES CS:',        self.v_shr,     'entry',  False,             False),
            ('Notes:',            self.v_notes,   'entry',  False,             False),
            ('Latitude (0-90):',  self.v_lat,     'entry',  False,             False),
            ('Lat Direction:',    self.v_lat_dir, 'combo',  ['', 'N', 'S'],   False),
            ('Longitude (0-180):',self.v_lon,     'entry',  False,             False),
            ('Long Direction:',   self.v_lon_dir, 'combo',  ['', 'E', 'W'],   False),
        ]

        for i, (lbl, var, wtype, opts, dis) in enumerate(fields):
            self._lbl(i, lbl)
            if wtype == 'combo':
                self._cmb(i, var, opts)
            else:
                self._ent(i, var, disabled=dis)

        bf = tk.Frame(self)
        bf.grid(row=len(fields), column=0, columnspan=2, pady=10)
        tk.Button(bf, text='  Save  ', font=FB,
                  bg=HDR_BG, fg='#FFF', padx=8, pady=3,
                  command=self._save).pack(side='left', padx=8)
        tk.Button(bf, text='Cancel', font=FA, padx=8,
                  command=self.destroy).pack(side='left', padx=8)

    def _save(self):
        cs = self.v_cs.get().strip().upper()
        if not cs:
            messagebox.showwarning('Required', 'Callsign cannot be empty.', parent=self)
            return

        # ── Validate Latitude ─────────────────────────────────────────────────
        lat = None
        lat_str = self.v_lat.get().strip()
        if lat_str:
            try:
                lat = round(float(lat_str), 4)
                if not (0 <= lat <= 90):
                    messagebox.showwarning('Invalid Latitude',
                        'Latitude must be between 0 and 90.', parent=self)
                    return
            except ValueError:
                messagebox.showwarning('Invalid Latitude',
                    'Latitude must be a decimal number (e.g. 25.7617).', parent=self)
                return

        # ── Validate Longitude ────────────────────────────────────────────────
        lon = None
        lon_str = self.v_lon.get().strip()
        if lon_str:
            try:
                lon = round(float(lon_str), 4)
                if not (0 <= lon <= 180):
                    messagebox.showwarning('Invalid Longitude',
                        'Longitude must be between 0 and 180.', parent=self)
                    return
            except ValueError:
                messagebox.showwarning('Invalid Longitude',
                    'Longitude must be a decimal number (e.g. 80.1918).', parent=self)
                return

        self.result = {
            'callsign':      cs,
            'operator_name': self.v_name.get().strip(),
            'city':          self.v_city.get().strip(),
            'state':         self.v_state.get().strip().upper(),
            'email':         self.v_email.get().strip(),
            'sector':        self.v_sec.get().strip(),
            'cell_phone':    self.v_phone.get().strip(),
            'ham_callsign':  self.v_ham.get().strip(),
            'winlink_cs':    self.v_wl.get().strip()       or None,
            'vara_hf_cs':    self.v_vara.get().strip()     or None,
            'shares_cs':     self.v_shr.get().strip()      or None,
            'notes':         self.v_notes.get().strip(),
            'Latitude':      lat,
            'Lat_Dir':       self.v_lat_dir.get().strip()  or None,
            'Longitude':     lon,
            'Long_Dir':      self.v_lon_dir.get().strip()  or None,
        }
        self.destroy()


# ─────────────────────────────────────────────────────────────────────────────
# CALLSIGN PICKER DIALOG
# ─────────────────────────────────────────────────────────────────────────────
class CallsignPickerDialog(tk.Toplevel):
    """Modal dialog shown when a partial callsign matches multiple members."""

    def __init__(self, parent, matches):
        super().__init__(parent)
        self.title('Select Callsign')
        self.resizable(False, False)
        self.result = None

        cols = ('Callsign', 'Name', 'City', 'State', 'Sector')
        widths = (100, 220, 110, 55, 130)
        tree = ttk.Treeview(self, columns=cols, show='headings', height=min(len(matches), 12))
        for col, w in zip(cols, widths):
            tree.heading(col, text=col)
            tree.column(col, width=w, anchor='w')
        for row in matches:
            tree.insert('', 'end', values=(
                row['callsign'],
                row['operator_name'] or '',
                row['city'] or '',
                row['state'] or '',
                row['sector'] or '',
            ))
        tree.pack(side='top', fill='both', expand=True, padx=8, pady=(8, 4))
        tree.focus_set()
        if tree.get_children():
            first = tree.get_children()[0]
            tree.selection_set(first)
            tree.focus(first)

        def _confirm(_evt=None):
            sel = tree.selection()
            if sel:
                self.result = tree.set(sel[0], 'Callsign')
            self.destroy()

        def _cancel(_evt=None):
            self.destroy()

        tree.bind('<Double-1>', _confirm)
        tree.bind('<Return>', _confirm)

        btn_frame = tk.Frame(self)
        btn_frame.pack(side='bottom', pady=(0, 8))
        tk.Button(btn_frame, text='Select', width=10, command=_confirm).pack(side='left', padx=6)
        tk.Button(btn_frame, text='Cancel', width=10, command=_cancel).pack(side='left', padx=6)
        self.bind('<Escape>', _cancel)

        self.transient(parent)
        self.grab_set()
        self.update_idletasks()
        # Centre over parent
        px, py = parent.winfo_rootx(), parent.winfo_rooty()
        pw, ph = parent.winfo_width(), parent.winfo_height()
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        self.geometry(f'+{px + (pw - w) // 2}+{py + (ph - h) // 2}')
        parent.wait_window(self)


def _resolve_callsign_partial(typed: str, entry_var: tk.StringVar,
                               parent: tk.Widget) -> str:
    """Shared helper: auto-fill or show picker for a partial callsign.
    Returns the resolved callsign, or `typed` unchanged if no match / cancelled."""
    if not typed:
        return typed
    matches = find_matching_callsigns(typed)
    if not matches or any(r['callsign'].upper() == typed for r in matches):
        return typed          # no hits, or already exact
    if len(matches) == 1:
        resolved = matches[0]['callsign']
        entry_var.set(resolved)
        return resolved
    dlg = CallsignPickerDialog(parent.winfo_toplevel(), matches)
    if dlg.result:
        entry_var.set(dlg.result)
        return dlg.result
    return typed


# ─────────────────────────────────────────────────────────────────────────────
# TAB 1 — NET LOG  (main operational screen)
# ─────────────────────────────────────────────────────────────────────────────
class LogTab(ttk.Frame):

    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self._cs_resolving = False   # guard against recursive FocusOut during picker
        self._build_header()
        self._build_body()
        self._build_add_panel()

    # ── Header ────────────────────────────────────────────────────────────────
    def _build_header(self):
        hf = tk.Frame(self, bg=HDR_BG, pady=6)
        hf.pack(fill='x', padx=4, pady=(4, 2))

        # Info grid — 4 pairs per row × 2 rows
        info = tk.Frame(hf, bg=HDR_BG)
        info.pack(fill='x', padx=8)

        pairs_r0 = [('Net:','v_net'), ('Date:','v_date'), ('NCS:','v_ncs'), ('ANCS:','v_ancs'), ('Active Freq:','v_active_freq')]
        pairs_r1 = [('Location:','v_loc'), ('Transceiver:','v_xcvr'), ('Propagation:','v_prop'), ('Noise:','v_noise')]

        for row_idx, pairs in enumerate([pairs_r0, pairs_r1]):
            for col, (lbl, attr) in enumerate(pairs):
                tk.Label(info, text=lbl, bg=HDR_BG, fg=HDR_DIM,
                         font=FS, anchor='e').grid(
                    row=row_idx, column=col * 2, sticky='e', padx=(12, 3))
                v = tk.StringVar(value='—')
                setattr(self, attr, v)
                tk.Label(info, textvariable=v, bg=HDR_BG, fg=HDR_FG,
                         font=FB, anchor='w').grid(
                    row=row_idx, column=col * 2 + 1, sticky='w', padx=(0, 12))

        # Row 2 — Frequency selector: radio buttons for PRIMARY / 1ST ALT / 2ND ALT
        self.v_freq_type = tk.StringVar(value='PRIMARY')
        self.v_freq_prim = tk.StringVar(value='—')
        self.v_freq_alt1 = tk.StringVar(value='—')
        self.v_freq_alt2 = tk.StringVar(value='—')

        frf = tk.Frame(info, bg=HDR_BG)
        frf.grid(row=2, column=0, columnspan=8, sticky='w', pady=(4, 0))

        rb_kw = dict(bg=HDR_BG, fg=HDR_FG, selectcolor='#003366',
                     activebackground=HDR_BG, font=FS, pady=0, bd=0,
                     command=self._on_freq_select)
        for i, (code, lbl_text, fvar) in enumerate([
            ('PRIMARY', 'Primary:',  self.v_freq_prim),
            ('1ST ALT', '1st Alt:',  self.v_freq_alt1),
            ('2ND ALT', '2nd Alt:',  self.v_freq_alt2),
        ]):
            left_pad = 12 if i == 0 else 20
            tk.Radiobutton(frf, variable=self.v_freq_type, value=code,
                           text='', **rb_kw).pack(side='left', padx=(left_pad, 1))
            tk.Label(frf, text=lbl_text, bg=HDR_BG, fg=HDR_DIM,
                     font=FS).pack(side='left', padx=(0, 3))
            tk.Label(frf, textvariable=fvar, bg=HDR_BG, fg=HDR_FG,
                     font=FB, anchor='w', width=22).pack(side='left')

        # Controls row
        ctrl = tk.Frame(hf, bg=HDR_BG)
        ctrl.pack(fill='x', padx=8, pady=(5, 0))

        self.v_showdb = tk.BooleanVar()
        self.v_manual = tk.BooleanVar()
        cb_kw = dict(bg=HDR_BG, fg=HDR_FG, selectcolor='#003366',
                     activebackground=HDR_BG, font=FA, pady=1)
        tk.Checkbutton(ctrl, text='Show My Station D&B',
                       variable=self.v_showdb,
                       command=self._toggle_showdb, **cb_kw).pack(side='left', padx=(0, 18))
        tk.Checkbutton(ctrl, text='Manual Time Entry',
                       variable=self.v_manual,
                       command=self._toggle_manual, **cb_kw).pack(side='left', padx=(0, 18))

        for txt, cmd, bg in [
            ('Archive & Close', self._do_archive, '#8B0000'),
            ('Save',            self._do_save,    '#005500'),
            ('Edit Session',    self._edit_session, '#2A5F8F'),
        ]:
            tk.Button(ctrl, text=txt, font=FS,
                      bg=bg, fg='#FFF', padx=7, pady=2,
                      command=cmd).pack(side='right', padx=4)

    # ── Body: check-in table + sector counts ─────────────────────────────────
    def _build_body(self):
        body = tk.Frame(self)
        body.pack(fill='both', expand=True, padx=4, pady=2)
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=0)

        # Check-in treeview
        tf = tk.Frame(body)
        tf.grid(row=0, column=0, sticky='nsew')
        tf.rowconfigure(0, weight=1)
        tf.rowconfigure(1, weight=0)
        tf.columnconfigure(0, weight=1)

        cols    = ('#', 'FROM', 'Operator', 'TO', 'Time', 'Signal', 'City', 'ST', 'Sector', 'Dist', 'Brg')
        widths  = ( 40,    88,        178,   88,    80,       80,    108,    32,     120,      70,    66)
        anchors = ('e', 'center', 'w', 'center', 'center', 'center', 'w', 'center', 'w', 'e', 'e')

        self.tree = ttk.Treeview(tf, columns=cols, show='headings',
                                  selectmode='browse', height=11)
        for col, w, anc in zip(cols, widths, anchors):
            self.tree.heading(col, text=col)
            self.tree.column(col, width=w, minwidth=28,
                             anchor=anc, stretch=False)

        vsb = ttk.Scrollbar(tf, orient='vertical',   command=self.tree.yview)
        hsb = ttk.Scrollbar(tf, orient='horizontal', command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.tree.grid(row=0, column=0, sticky='nsew')
        vsb.grid(row=0, column=1, sticky='ns')
        hsb.grid(row=1, column=0, sticky='ew')
        self.tree.tag_configure('odd',     background=ROW_ODD)
        self.tree.tag_configure('evn',     background=ROW_EVN)
        self.tree.tag_configure('pending', background='#D4DCF0', foreground='#5060A0')

        # Left-click → edit; Delete key or right-click → delete; right-click → menu
        self.tree.bind('<<TreeviewSelect>>', self._on_checkin_select)
        self.tree.bind('<Delete>', lambda _: self._delete_checkin())
        self._tree_menu = tk.Menu(self, tearoff=0)
        self._tree_menu.add_command(label='Delete selected check-in', command=self._delete_checkin)
        self.tree.bind('<Button-3>', self._show_tree_menu)

        # Sector counts panel (fixed width, matching Excel G9:G16)
        sc = tk.LabelFrame(body, text=' Sector Counts ', font=FB,
                            padx=10, pady=6, width=160)
        sc.grid(row=0, column=1, sticky='nsew', padx=(5, 0))
        sc.grid_propagate(False)

        self.v_counts = {}
        for i, s in enumerate(SECTORS):
            tk.Label(sc, text=s + ':', font=FS, anchor='e', width=15).grid(
                row=i, column=0, sticky='e', pady=1)
            v = tk.StringVar(value='0')
            self.v_counts[s] = v
            tk.Label(sc, textvariable=v, font=FB, anchor='w', width=4).grid(
                row=i, column=1, sticky='w')

        ttk.Separator(sc, orient='horizontal').grid(
            row=len(SECTORS), column=0, columnspan=2, sticky='ew', pady=4)

        tk.Label(sc, text='TOTAL:', font=FB, anchor='e', width=15).grid(
            row=len(SECTORS) + 1, column=0, sticky='e', pady=1)
        v = tk.StringVar(value='0')
        self.v_counts['TOTAL'] = v
        tk.Label(sc, textvariable=v, font=(_FF, 11, 'bold'),
                 fg=HDR_BG, anchor='w', width=4).grid(
            row=len(SECTORS) + 1, column=1, sticky='w')

    # ── Add check-in panel ────────────────────────────────────────────────────
    def _build_add_panel(self):
        af = tk.LabelFrame(self, text=' Add Check-In ', font=FB, padx=8, pady=6)
        af.pack(fill='x', padx=4, pady=(2, 4))

        # Row 0 — FROM
        tk.Label(af, text='FROM:', font=FB, anchor='e').grid(
            row=0, column=0, sticky='e', padx=(0, 4))
        self.v_from = tk.StringVar()
        self.e_from = tk.Entry(af, textvariable=self.v_from,
                               font=FM, bg=ENTRY_BG, width=11, justify='center')
        self.e_from.grid(row=0, column=1, padx=(0, 6))
        self.e_from.bind('<Return>', lambda _: self._lookup_from(focus_next=True))
        self.e_from.bind('<Tab>',    lambda _: self._lookup_from(focus_next=False))
        self.e_from.bind('<FocusOut>', lambda _: self._lookup_from())

        self.lbl_from = tk.Label(af, text='— enter callsign then press Enter —',
                                 font=FS, fg='#666', anchor='w', width=50)
        self.lbl_from.grid(row=0, column=2, columnspan=3, sticky='w', padx=4)

        # Row 0 — TO (right side)
        tk.Label(af, text='TO:', font=FB, anchor='e').grid(
            row=0, column=5, sticky='e', padx=(14, 4))
        self.v_to = tk.StringVar()
        self.e_to = tk.Entry(af, textvariable=self.v_to,
                             font=FM, bg=ENTRY_BG, width=11, justify='center')
        self.e_to.grid(row=0, column=6, padx=(0, 6))
        self.e_to.bind('<FocusOut>', lambda _: self._lookup_to())
        self.e_to.bind('<Return>',   lambda _: self._lookup_to())

        self.lbl_to = tk.Label(af, text='', font=FS, fg='#666', anchor='w', width=44)
        self.lbl_to.grid(row=0, column=7, sticky='w', padx=4)

        # Row 1 — Time
        tk.Label(af, text='Time:', font=FB, anchor='e').grid(
            row=1, column=0, sticky='e', padx=(0, 4), pady=(6, 2))
        self.v_time = tk.StringVar()
        self.e_time = tk.Entry(af, textvariable=self.v_time,
                               font=FM, bg=AUTO_BG, width=11,
                               state='readonly', justify='center')
        self.e_time.grid(row=1, column=1, pady=(6, 2))

        # Row 1 — Signal buttons
        sf = tk.Frame(af)
        sf.grid(row=1, column=2, columnspan=6, sticky='w', pady=(6, 2), padx=4)
        tk.Label(sf, text='Signal: ', font=FB).pack(side='left')
        self.sig_btns = {}
        for code in SIG_CODES:
            b = SigBtn(sf, code)
            b.pack(side='left', padx=2)
            self.sig_btns[code] = b
            if code in ('F', 'I'):  # separators between signal groups
                tk.Label(sf, text='|', font=FA, fg='#AAA').pack(side='left', padx=2)

        tk.Label(sf, text='  Prop:', font=FB).pack(side='left', padx=(12, 3))
        self.v_checkin_prop = tk.StringVar(value='')
        self._prop_cmb = ttk.Combobox(sf, textvariable=self.v_checkin_prop,
                                       values=[''] + PROPAGATIONS,
                                       state='readonly', width=14, font=FS)
        self._prop_cmb.pack(side='left')

        # Row 2 — Traffic
        trf = tk.Frame(af)
        trf.grid(row=2, column=0, columnspan=8, sticky='w', pady=(4, 2))
        self.v_traffic = tk.BooleanVar()
        tk.Checkbutton(trf, text='Has Traffic', variable=self.v_traffic,
                       command=self._toggle_traffic, font=FA).pack(side='left', padx=(0, 8))
        tk.Label(trf, text='Dest callsign:', font=FA).pack(side='left')
        self.v_dest = tk.StringVar()
        self.e_dest = tk.Entry(trf, textvariable=self.v_dest,
                               font=FM, bg=ENTRY_BG, width=11, state='disabled')
        self.e_dest.pack(side='left', padx=(3, 12))
        self.e_dest.bind('<Return>',   lambda _: self._lookup_dest())
        self.e_dest.bind('<FocusOut>', lambda _: self._lookup_dest())
        tk.Label(trf, text='Notes:', font=FA).pack(side='left')
        self.v_notes = tk.StringVar()
        self.e_notes = tk.Entry(trf, textvariable=self.v_notes,
                                font=FA, bg=AUTO_BG, width=36, state='disabled')
        self.e_notes.pack(side='left', padx=3)

        # Row 3 — Action buttons
        bf = tk.Frame(af)
        bf.grid(row=3, column=0, columnspan=8, pady=(6, 0))
        self._editing_idx = None   # None = add mode; int = edit mode
        self._btn_main = tk.Button(bf, text='  ADD CHECK-IN  ',
                                   font=(_FF, 11, 'bold'),
                                   bg=HDR_BG, fg='#FFF', padx=10, pady=4,
                                   command=self._do_add)
        self._btn_main.pack(side='left', padx=8)
        tk.Button(bf, text='Reset Form', font=FA, padx=6,
                  command=self._reset_form).pack(side='left', padx=4)
        self._btn_del = tk.Button(bf, text='Delete Check-In', font=FA, padx=6,
                                  bg='#8B0000', fg='#FFF',
                                  command=self._delete_checkin, state='disabled')
        self._btn_del.pack(side='left', padx=4)

    # ── Event handlers ────────────────────────────────────────────────────────
    def _on_checkin_select(self, _event=None):
        """Handle click on a check-in row.

        Pending row (from PRIMARY, not yet on this alt freq):
          Pre-fill FROM / TO, auto-stamp time, set propagation → ready to confirm.
        Confirmed row:
          Populate time and propagation from that stored check-in.
        """
        sel = self.tree.selection()
        if not sel:
            return
        iid = sel[0]

        if iid.startswith('p:'):
            # ── Pending station from PRIMARY ──────────────────────────────────
            # Exit edit mode if we were in it
            self._set_add_mode()
            idx = int(iid[2:])
            pending = self._get_pending_stations()
            if idx >= len(pending):
                return
            ci = pending[idx]
            # Clear time first so auto-timestamp fires fresh
            self.v_time.set('')
            self.v_from.set(ci.get('from_callsign', ''))
            self.v_to.set(ci.get('to_callsign', ''))
            self._lookup_from()   # shows ✓ + sets auto-timestamp
            self._lookup_to()
            # Propagation: default to session-level setting
            self.v_checkin_prop.set(self.app.session.propagation or '')
            # In manual-time mode, focus the time entry for quick keyboard input
            if self.app.session.manual_time:
                self.e_time.focus_set()
        else:
            # ── Confirmed check-in — enter edit mode ──────────────────────────
            idx = int(iid[2:])
            checkins = self.app.session.get_active_checkins()
            if idx >= len(checkins):
                return
            ci = checkins[idx]
            # Populate time (set before _lookup_from so auto-stamp doesn't fire)
            self.v_time.set(ci.get('checkin_time', ''))
            # Populate FROM / TO with lookup labels
            self.v_from.set(ci.get('from_callsign', ''))
            self._lookup_from()
            self.v_to.set(ci.get('to_callsign', ''))
            self._lookup_to()
            # Signal buttons
            for code in SIG_CODES:
                self.sig_btns[code].set(ci.get(f'signal_{code}', False))
            # Propagation
            self.v_checkin_prop.set(ci.get('propagation', ''))
            # Traffic / Notes
            has_traf = bool(ci.get('has_traffic', False))
            self.v_traffic.set(has_traf)
            self.v_dest.set(ci.get('traffic_destination', '') or '')
            self.v_notes.set(ci.get('traffic_notes', '') or '')
            dest_state = 'normal' if has_traf else 'disabled'
            dest_bg    = ENTRY_BG if has_traf else AUTO_BG
            self.e_dest.config(state=dest_state, bg=dest_bg)
            # Notes always editable for an existing check-in
            self.e_notes.config(state='normal', bg=ENTRY_BG)
            # Switch button to UPDATE mode and enable Delete
            self._editing_idx = idx
            self._btn_main.config(text='  UPDATE CHECK-IN  ',
                                  bg='#2A6099', command=self._do_update)
            self._btn_del.config(state='normal')

    def _resolve_callsign(self, typed: str, entry_var: tk.StringVar) -> str:
        """Wraps _resolve_callsign_partial with a re-entrancy guard (FocusOut fires
        when the picker dialog opens, which would otherwise trigger a second call)."""
        self._cs_resolving = True
        try:
            return _resolve_callsign_partial(typed, entry_var, self)
        finally:
            self._cs_resolving = False

    def _lookup_from(self, focus_next=False):
        if self._cs_resolving:
            return
        cs = self.v_from.get().strip().upper()
        self.v_from.set(cs)
        if not cs:
            self.lbl_from.config(text='— enter callsign then press Enter —', fg='#666')
            return
        cs = self._resolve_callsign(cs, self.v_from)
        name   = lookup_operator(cs)
        city   = lookup_city(cs)
        state  = lookup_state(cs)
        sector = lookup_sector(cs)
        if name == 'Callsign FROM Not Found':
            self.lbl_from.config(text=f'✗  {cs}  — not in member DB', fg=ERR_RED)
        else:
            self.lbl_from.config(
                text=f'✓  {name}   |   {city}, {state}   |   {sector}',
                fg=OK_GRN)
        # Set auto-timestamp when callsign is entered
        if not self.app.session.manual_time and not self.v_time.get():
            self.v_time.set(datetime.now().strftime('%H:%M:%S'))
        # Refresh D&B if TO is already filled
        to_cs = self.v_to.get().strip().upper()
        if to_cs:
            self._refresh_db_label(cs, to_cs)
        if focus_next:
            self.e_to.focus_set()
            return 'break'

    def _refresh_db_label(self, from_cs: str, to_cs: str):
        """Recompute and display distance/bearing in lbl_to."""
        if not to_cs:
            return
        s = self.app.session
        my_cs = (s.station_callsign or s.ncs_callsign or '').strip().upper()
        dist = lookup_distance(from_cs, to_cs, self.app.dist_matrix,
                               ncs_cs=my_cs, show_my_db=s.show_my_db)
        brg  = lookup_bearing(from_cs, to_cs, self.app.bear_matrix,
                              ncs_cs=my_cs, show_my_db=s.show_my_db)
        name = lookup_operator(to_cs)
        dist_str = f'{dist:.1f} mi' if isinstance(dist, float) else str(dist)
        brg_str  = f'{brg:.1f}°'   if isinstance(brg,  float) else str(brg)
        if name == 'Callsign FROM Not Found':
            self.lbl_to.config(text=f'✗  {to_cs}   {dist_str}  /  {brg_str}', fg=ERR_RED)
        else:
            self.lbl_to.config(text=f'✓  {name}   |   {dist_str}  /  {brg_str}', fg=OK_GRN)

    def _lookup_to(self):
        if self._cs_resolving:
            return
        cs = self.v_to.get().strip().upper()
        self.v_to.set(cs)
        if not cs:
            self.lbl_to.config(text='')
            return
        cs = self._resolve_callsign(cs, self.v_to)
        from_cs = self.v_from.get().strip().upper()
        self._refresh_db_label(from_cs, cs)

    def _lookup_dest(self):
        if self._cs_resolving:
            return
        cs = self.v_dest.get().strip().upper()
        self.v_dest.set(cs)
        if cs:
            self._resolve_callsign(cs, self.v_dest)

    def _toggle_traffic(self):
        on    = self.v_traffic.get()
        state = 'normal' if on else 'disabled'
        bg    = ENTRY_BG if on else AUTO_BG
        self.e_dest.config(state=state, bg=bg)
        self.e_notes.config(state=state, bg=bg)

    def _toggle_showdb(self):
        self.app.session.show_my_db = self.v_showdb.get()
        self.app.session.recompute_all(self.app.dist_matrix, self.app.bear_matrix)
        self.refresh_table()
        self.app.set_status(f"Show My D&B: {'ON' if self.app.session.show_my_db else 'OFF'} — D&B recomputed")

    def _toggle_manual(self):
        self.app.session.manual_time = self.v_manual.get()
        if self.app.session.manual_time:
            self.e_time.config(state='normal', bg=ENTRY_BG)
        else:
            self.e_time.config(state='readonly', bg=AUTO_BG)
            self.v_time.set('')
        self.app.set_status(f"Manual time entry: {'ON' if self.app.session.manual_time else 'OFF'}")

    def _on_freq_select(self):
        """Radio-button callback — switch frequency log and update display."""
        ft = self.v_freq_type.get()
        self.app.session.freq_type = ft
        s = self.app.session
        if ft == 'PRIMARY':
            self.v_active_freq.set(s.primary_freq or '—')
        elif ft == '1ST ALT':
            self.v_active_freq.set(s.alt_freq_1 or '—')
        else:
            self.v_active_freq.set(s.alt_freq_2 or '—')
        self._ensure_ncs_pending()  # guarantee NCS is first on every tab switch
        self.refresh_table()   # load this frequency's check-in list
        n = len(s.get_active_checkins())
        self.app.set_status(f'Active frequency: {ft}  |  {n} check-in{"s" if n != 1 else ""} on this frequency')

    def _do_add(self):
        from_cs = self.v_from.get().strip().upper()
        if not from_cs:
            messagebox.showwarning('Required', 'FROM callsign is required.')
            self.e_from.focus_set()
            return

        to_cs = self.v_to.get().strip().upper()

        if self.app.session.manual_time:
            t = self.v_time.get().strip()
            if not t:
                messagebox.showwarning('Required', 'Enter a time in manual mode.')
                self.e_time.focus_set()
                return
            checkin_time = t
        else:
            checkin_time = datetime.now().strftime('%H:%M:%S')

        signals = {f'signal_{c}': self.sig_btns[c].get() for c in SIG_CODES}
        has_traf    = self.v_traffic.get()
        traf_dest   = self.v_dest.get().strip().upper() if has_traf else ''
        traf_notes  = self.v_notes.get().strip()
        propagation = self.v_checkin_prop.get().strip()

        self.app.session.add_checkin(
            self.app.dist_matrix, self.app.bear_matrix,
            from_callsign=from_cs, to_callsign=to_cs,
            checkin_time=checkin_time,
            manual_time=self.app.session.manual_time,
            has_traffic=has_traf,
            traffic_destination=traf_dest,
            traffic_notes=traf_notes,
            propagation=propagation,
            **signals,
        )

        # ── Notify if the FROM station is a known member with no lat/lon ──────
        lat, _ = lookup_coords(from_cs)
        if lat is None and lookup_operator(from_cs) != 'Callsign FROM Not Found':
            messagebox.showinfo(
                'No Lat/Long Information',
                f'No latitude / longitude data is on file for  {from_cs}.\n\n'
                f'Distance and bearing cannot be calculated for this check-in.\n'
                f'Add coordinates in the Members tab to enable great-circle D&B.',
                parent=self)
            # Blank dist/bearing in the just-added check-in dict
            active = self.app.session.get_active_checkins()
            if active:
                active[-1]['distance_miles']  = ''
                active[-1]['bearing_degrees'] = ''

        # Propagate confirmed station to downstream ALT lists (preserves PRIMARY sequence)
        active = self.app.session.get_active_checkins()
        if active:
            self._propagate_to_alt_lists(active[-1])

        self.refresh_table()
        self._reset_form()
        n = len(self.app.session.get_active_checkins())
        ft = self.app.session.freq_type
        self.app.set_status(
            f'Check-in #{n} added [{ft}]: {from_cs} -> {to_cs or "(none)"} @ {checkin_time}')

    def _set_add_mode(self):
        """Return the main action button to ADD CHECK-IN mode."""
        self._editing_idx = None
        self._btn_main.config(text='  ADD CHECK-IN  ',
                              bg=HDR_BG, command=self._do_add)
        self._btn_del.config(state='disabled')

    def _do_update(self):
        """Write edited form fields back to the selected check-in (no new row)."""
        idx = self._editing_idx
        if idx is None:
            return
        checkins = self.app.session.get_active_checkins()
        if idx >= len(checkins):
            self._reset_form()
            return
        ci = checkins[idx]

        # Update callsigns and recompute all auto-fields (D&B, operator, city, sector)
        ci['from_callsign'] = self.v_from.get().strip().upper()
        ci['to_callsign']   = self.v_to.get().strip().upper()
        temp = CheckIn(**{k: ci.get(k, v.default if hasattr(v, 'default') else '')
                          for k, v in CheckIn.__dataclass_fields__.items()})
        temp.compute_auto_fields(self.app.session,
                                 self.app.dist_matrix, self.app.bear_matrix)
        ci.update(temp.__dict__)

        # User-controlled fields always win over the recomputed values
        t = self.v_time.get().strip()
        if t:
            ci['checkin_time'] = t
            if ci.pop('pending', None):   # NCS auto-entry now confirmed
                rank_checkins(self.app.session.get_active_checkins())
        ci['propagation']         = self.v_checkin_prop.get().strip()
        has_traf                  = self.v_traffic.get()
        ci['has_traffic']         = has_traf
        ci['traffic_destination'] = self.v_dest.get().strip().upper() if has_traf else ''
        ci['traffic_notes']       = self.v_notes.get().strip()
        for code in SIG_CODES:
            ci[f'signal_{code}'] = self.sig_btns[code].get()
        self.refresh_table()
        self._reset_form()
        self.app.set_status(
            f'Check-in for {ci.get("from_callsign", "?")} updated.')

    def _reset_form(self):
        self._set_add_mode()      # return button to ADD mode if we were editing
        self.v_from.set('')
        self.v_to.set('')
        self.v_time.set('')
        self.lbl_from.config(text='— enter callsign then press Enter —', fg='#666')
        self.lbl_to.config(text='')
        for b in self.sig_btns.values():
            b.reset()
        self.v_traffic.set(False)
        self.v_dest.set('')
        self.e_dest.config(state='disabled', bg=AUTO_BG)
        self.v_notes.set('')
        # Reset propagation to session default so the next check-in inherits it
        self.v_checkin_prop.set(self.app.session.propagation or '')
        self.e_from.focus_set()

    def _show_tree_menu(self, event):
        if self.tree.identify_row(event.y):
            self.tree.selection_set(self.tree.identify_row(event.y))
            self._tree_menu.post(event.x_root, event.y_root)

    def _delete_checkin(self):
        sel = self.tree.selection()
        if not sel:
            return
        iid = sel[0]
        if iid.startswith('p:'):
            messagebox.showinfo(
                'Pending Station',
                'This station has not yet checked in on this frequency.\n'
                'Click the row to pre-fill the form, then log their check-in.')
            return
        idx = int(iid[2:])
        s   = self.app.session
        lst = s.get_active_checkins()
        if idx >= len(lst):
            return
        ci  = lst[idx]
        cs  = ci.get('from_callsign', '?')
        label = '(pending) ' if ci.get('pending') else ''
        if not messagebox.askyesno('Delete Check-In',
                                   f'Remove {label}check-in for  {cs}?'):
            return

        del lst[idx]
        rank_checkins(lst)

        # Cascade: remove pending placeholders for this station from downstream ALT lists
        freq = s.freq_type
        if freq == 'PRIMARY':
            cascade = [s.checkins_alt1, s.checkins_alt2]
        elif freq == '1ST ALT':
            cascade = [s.checkins_alt2]
        else:
            cascade = []
        for alt_lst in cascade:
            alt_lst[:] = [c for c in alt_lst
                          if not (c.get('pending') and
                                  c.get('from_callsign', '').upper() == cs.upper())]

        self._set_add_mode()
        self._ensure_ncs_pending()   # re-pin NCS if it was the deleted entry
        self.refresh_table()
        self.app.set_status(f'Check-in for {cs} removed.')

    def _edit_session(self):
        self.app.notebook.select(1)

    def _do_save(self):
        path = filedialog.asksaveasfilename(
            title='Save Session', defaultextension='.json',
            filetypes=[('JSON', '*.json'), ('All', '*.*')],
            initialfile='netlog_session.json', initialdir=BASE)
        if path:
            self.app.session.save(path)
            self.app.set_status(f'Saved → {os.path.basename(path)}')

    def _do_archive(self):
        n = len(self.app.session.all_checkins())
        msg = (f'Archive this session ({n} check-in{"s" if n != 1 else ""} across all frequencies) and close?'
               if n else 'Archive empty session and close?')
        if messagebox.askyesno('Archive & Close', msg):
            if not self.app.session.secured_time:
                self.app.session.secured_time = datetime.now().strftime('%H%M')
            save_to_archive(self.app.session)
            self.app.set_status('Session archived.')
            self.app.destroy()

    # ── Display refresh ───────────────────────────────────────────────────────
    def _get_pending_stations(self) -> list:
        """
        Stations from higher-priority frequencies not yet confirmed on the
        current frequency.

        1ST ALT  → stations from PRIMARY not yet on 1ST ALT
        2ND ALT  → stations from PRIMARY *and* 1ST ALT not yet on 2ND ALT
                   (deduplicated by callsign; PRIMARY entry takes precedence)
        """
        s = self.app.session
        if s.freq_type == 'PRIMARY':
            return []

        confirmed = {ci.get('from_callsign', '').upper()
                     for ci in s.get_active_checkins()}

        if s.freq_type == '1ST ALT':
            return [ci for ci in s.checkins
                    if ci.get('from_callsign', '').upper() not in confirmed]

        # 2ND ALT — merge PRIMARY then 1ST ALT, skip duplicates and already-confirmed
        seen    = set()
        pending = []
        for ci in s.checkins + s.checkins_alt1:
            cs = ci.get('from_callsign', '').upper()
            if cs and cs not in confirmed and cs not in seen:
                seen.add(cs)
                pending.append(ci)
        return pending

    def refresh_table(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        s = self.app.session

        # ── Pending rows: PRIMARY stations not yet confirmed on this alt freq ──
        if s.freq_type != 'PRIMARY':
            for i, ci in enumerate(self._get_pending_stations()):
                sig = ' '.join(c for c in SIG_CODES if ci.get(f'signal_{c}'))
                self.tree.insert('', 'end', iid=f'p:{i}', tag='pending', values=(
                    ci.get('checkin_order', ''),
                    ci.get('from_callsign', ''),
                    ci.get('from_operator', ''),
                    ci.get('to_callsign', ''),
                    '— pending —',
                    sig,
                    ci.get('city', ''), ci.get('state', ''), ci.get('sector', ''),
                    '', '',
                ))

        # ── Confirmed check-ins (and pending NCS auto-entry) for this frequency ─
        for i, ci in enumerate(s.get_active_checkins()):
            sig  = ' '.join(c for c in SIG_CODES if ci.get(f'signal_{c}'))
            dist = ci.get('distance_miles', '')
            brg  = ci.get('bearing_degrees', '')
            ds   = f'{dist:.1f}' if isinstance(dist, float) else str(dist)[:8]
            bs   = f'{brg:.1f}°' if isinstance(brg,  float) else str(brg)[:7]
            is_ncs_pending = ci.get('pending', False)
            tag      = 'pending' if is_ncs_pending else ('evn' if i % 2 else 'odd')
            time_val = '— pending —' if is_ncs_pending else ci.get('checkin_time', '')
            self.tree.insert('', 'end', iid=f'a:{i}', tag=tag, values=(
                ci.get('checkin_order', ''),
                ci.get('from_callsign', ''),
                ci.get('from_operator', ''),
                ci.get('to_callsign', ''),
                time_val,
                sig, ci.get('city', ''), ci.get('state', ''),
                ci.get('sector', ''), ds, bs,
            ))
        self._refresh_counts()

    def _propagate_to_alt_lists(self, ci_dict: dict):
        """When a station is confirmed on the current frequency, pre-insert a
        pending placeholder into downstream ALT frequency lists so the same
        sequence of stations appears across all three frequencies."""
        s  = self.app.session
        cs = ci_dict.get('from_callsign', '').upper()
        if not cs:
            return
        if s.freq_type == 'PRIMARY':
            targets = [s.checkins_alt1, s.checkins_alt2]
        elif s.freq_type == '1ST ALT':
            targets = [s.checkins_alt2]
        else:
            return
        for lst in targets:
            if any(ci.get('from_callsign', '').upper() == cs for ci in lst):
                continue
            ci_obj = CheckIn(from_callsign=cs)
            ci_obj.compute_auto_fields(s, self.app.dist_matrix, self.app.bear_matrix)
            pending_dict = ci_obj.__dict__.copy()
            pending_dict['pending'] = True
            pending_dict['checkin_time'] = ''
            lst.append(pending_dict)

    def _ensure_ncs_pending(self):
        """Guarantee the NCS callsign is always position 0 on every frequency list.

        Three cases per list:
          • NCS not present at all  → insert pending entry at front
          • NCS present but not first → move that entry (confirmed or pending) to front
          • NCS already first        → nothing to do
        """
        s = self.app.session
        ncs_cs = (s.ncs_callsign or '').strip().upper()
        if not ncs_cs:
            return
        for lst in [s.checkins, s.checkins_alt1, s.checkins_alt2]:
            ncs_indices = [i for i, ci in enumerate(lst)
                           if ci.get('from_callsign', '').upper() == ncs_cs]
            if not ncs_indices:
                # NCS is absent — insert a pending placeholder at the front
                ci_obj = CheckIn(from_callsign=ncs_cs)
                ci_obj.compute_auto_fields(s, self.app.dist_matrix, self.app.bear_matrix)
                ci_dict = ci_obj.__dict__.copy()
                ci_dict['pending'] = True
                ci_dict['checkin_time'] = ''
                lst.insert(0, ci_dict)
            elif ncs_indices[0] != 0:
                # NCS is somewhere in the list but not first — move it to front
                lst.insert(0, lst.pop(ncs_indices[0]))
            # else: NCS is already at position 0 — nothing to do

    def _refresh_counts(self):
        c = self.app.session.sector_counts
        for s in SECTORS:
            self.v_counts[s].set(str(c.get(s, 0)))
        self.v_counts['TOTAL'].set(str(c.get('TOTAL', 0)))

    def update_session_display(self):
        s = self.app.session
        self.v_net.set(s.net_name      or '—')
        self.v_date.set(s.net_date     or '—')
        self.v_ncs.set(s.ncs_callsign  or '—')
        self.v_ancs.set(s.ancs_callsign or '—')
        self.v_loc.set(s.location      or '—')
        self.v_xcvr.set(s.transceiver or '—')
        self.v_prop.set(s.propagation or '—')
        self.v_noise.set(s.noise_level or '—')
        # Frequency selector row
        self.v_freq_prim.set(s.primary_freq or '—')
        self.v_freq_alt1.set(s.alt_freq_1   or '—')
        self.v_freq_alt2.set(s.alt_freq_2   or '—')
        ft = s.freq_type or 'PRIMARY'
        self.v_freq_type.set(ft)
        if ft == 'PRIMARY':
            self.v_active_freq.set(s.primary_freq or '—')
        elif ft == '1ST ALT':
            self.v_active_freq.set(s.alt_freq_1 or '—')
        else:
            self.v_active_freq.set(s.alt_freq_2 or '—')
        self.v_showdb.set(s.show_my_db)
        self.v_manual.set(s.manual_time)
        # Keep propagation default in sync with session setting
        if not self.v_checkin_prop.get():
            self.v_checkin_prop.set(s.propagation or '')
        self._ensure_ncs_pending()
        self.refresh_table()


# ─────────────────────────────────────────────────────────────────────────────
# TAB 2 — SESSION SETUP
# ─────────────────────────────────────────────────────────────────────────────
class SessionTab(ttk.Frame):

    _FIELDS = [
        ('Net Name:',           'net_name',          'combo', NET_NAMES),
        ('Net Date:',           'net_date',           'entry', None),
        ('Start Time (HHMM):',  'start_time',         'entry', None),
        ('Station Callsign:',   'station_callsign',   'entry', None),
        ('NCS Callsign:',       'ncs_callsign',       'entry', None),
        ('ANCS Callsign:',      'ancs_callsign',      'entry', None),
        ('Transceiver:',        'transceiver',        'combo', TRANSCEIVERS),
        ('Antenna:',            'antenna',         'combo', ANTENNAS),
        ('Location:',           'location',        'combo', LOCATIONS),
        ('Primary Freq:',       'primary_freq',    'combo', FREQ_DISPLAY),
        ('1st Alt Freq:',       'alt_freq_1',      'combo', FREQ_DISPLAY),
        ('2nd Alt Freq:',       'alt_freq_2',      'combo', FREQ_DISPLAY),
        ('Digital Mode:',       'digital_mode',    'combo', DIGITAL_MODES),
        ('Propagation:',        'propagation',     'combo', PROPAGATIONS),
        ('Noise Level:',        'noise_level',     'combo', NOISE_LEVELS),
        ('Secured Time:',       'secured_time',    'entry', None),
        ('Antenna #1 Desc:',    'antenna_1_desc',  'entry', None),
        ('Antenna #2 Desc:',    'antenna_2_desc',  'entry', None),
    ]

    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self._vars = {}
        self._cs_resolving = False
        self._cs_entries = {}    # key -> Entry widget for callsign fields
        self._freq_combos = {}   # key -> combo widget for the three freq fields
        self._build()
        self.load_from_session()

    def _build(self):
        # Scrollable canvas for the form
        canvas = tk.Canvas(self, borderwidth=0, highlightthickness=0)
        vsb = ttk.Scrollbar(self, orient='vertical', command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side='right', fill='y')
        canvas.pack(side='left', fill='both', expand=True)

        inner = tk.Frame(canvas)
        canvas.create_window((0, 0), window=inner, anchor='nw')
        inner.bind('<Configure>', lambda e: canvas.configure(
            scrollregion=canvas.bbox('all')))

        tk.Label(inner, text='SESSION SETUP', font=FT,
                 fg=HDR_BG).grid(row=0, column=0, columnspan=2, pady=(16, 8))

        _FREQ_KEYS = {'primary_freq', 'alt_freq_1', 'alt_freq_2'}
        _CS_KEYS   = {'ncs_callsign', 'station_callsign', 'ancs_callsign'}
        for i, (lbl, key, wtype, vals) in enumerate(self._FIELDS, 1):
            tk.Label(inner, text=lbl, font=FA, anchor='e').grid(
                row=i, column=0, sticky='e', padx=(40, 8), pady=5)
            v = tk.StringVar()
            self._vars[key] = v
            if wtype == 'combo':
                is_freq = key in _FREQ_KEYS
                w = ttk.Combobox(inner, textvariable=v, values=vals,
                                 state='normal' if is_freq else 'readonly',
                                 width=36, font=FA)
                if is_freq:
                    self._freq_combos[key] = w
                    w.bind('<KeyRelease>',
                           lambda e, _w=w, _v=v: self._freq_keyrelease(_w, _v))
                    w.bind('<Return>',
                           lambda e, _w=w, _v=v: self._freq_confirm(_w, _v))
                    w.bind('<FocusOut>',
                           lambda e, _w=w, _v=v: _w.after(
                               150, lambda: self._freq_confirm(_w, _v)))
                    w.bind('<<ComboboxSelected>>',
                           lambda e, _w=w: self._freq_selected(_w))
            else:
                w = tk.Entry(inner, textvariable=v,
                             font=FA, bg=ENTRY_BG, width=38)
            w.grid(row=i, column=1, sticky='w', padx=(0, 40), pady=5)
            if key in _CS_KEYS:
                self._cs_entries[key] = w
                w.bind('<Return>',   lambda _, _k=key: self._lookup_session_cs(_k))
                w.bind('<FocusOut>', lambda _, _k=key: self._lookup_session_cs(_k))

        n = len(self._FIELDS) + 1
        tk.Button(inner, text='  Apply Session Settings  ', font=FB,
                  bg=HDR_BG, fg='#FFF', padx=12, pady=5,
                  command=self._apply).grid(row=n, column=0, columnspan=2, pady=20)

    def _lookup_session_cs(self, key: str):
        if self._cs_resolving:
            return
        v = self._vars[key]
        typed = v.get().strip().upper()
        v.set(typed)
        self._cs_resolving = True
        try:
            _resolve_callsign_partial(typed, v, self)
        finally:
            self._cs_resolving = False

    def _freq_keyrelease(self, combo, var):
        """Filter the combobox dropdown list as the user types."""
        typed = var.get()
        if not typed:
            combo['values'] = FREQ_DISPLAY
            return
        up = typed.upper()
        matches = [f for f in FREQ_DISPLAY if up in f.upper()]
        combo['values'] = matches if matches else FREQ_DISPLAY

    def _freq_confirm(self, combo, var):
        """On Return / FocusOut: resolve a partial to the unique match, or clear if none."""
        typed = var.get().strip()
        if not typed or typed in FREQ_DISPLAY:
            combo['values'] = FREQ_DISPLAY
            return
        up = typed.upper()
        matches = [f for f in FREQ_DISPLAY if up in f.upper()]
        if len(matches) == 1:
            var.set(matches[0])
            combo['values'] = FREQ_DISPLAY
        elif not matches:
            var.set('')
            combo['values'] = FREQ_DISPLAY
        # 2+ matches: leave filtered list in place so user can open dropdown to choose

    def _freq_selected(self, combo):
        """After a dropdown selection, restore the full list for next time."""
        combo['values'] = FREQ_DISPLAY

    def load_from_session(self):
        s = self.app.session
        for k, v in self._vars.items():
            v.set(getattr(s, k, '') or '')

    def _apply(self):
        s = self.app.session
        for k, v in self._vars.items():
            setattr(s, k, v.get().strip())
        s.recompute_all(self.app.dist_matrix, self.app.bear_matrix)
        self.app.log_tab.update_session_display()
        self.app.set_status('Session settings applied and formulas recomputed.')
        messagebox.showinfo('Applied', 'Session settings saved.\nSwitch to Net Log tab to continue.')


# ─────────────────────────────────────────────────────────────────────────────
# TAB 3 — MEMBERS  (netlog.db browser + CRUD)
# ─────────────────────────────────────────────────────────────────────────────
class MembersTab(ttk.Frame):

    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self._build()
        self.refresh()

    def _build(self):
        # Search / filter bar
        sf = tk.Frame(self)
        sf.pack(fill='x', padx=8, pady=(8, 4))

        tk.Label(sf, text='Search:', font=FA).pack(side='left', padx=(0, 4))
        self.v_search = tk.StringVar()
        self.v_search.trace_add('write', lambda *_: self.refresh())
        tk.Entry(sf, textvariable=self.v_search, font=FA,
                 bg=ENTRY_BG, width=20).pack(side='left')

        tk.Label(sf, text='  Sector:', font=FA).pack(side='left', padx=(14, 4))
        self.v_sect_filter = tk.StringVar(value='All')
        self.v_sect_filter.trace_add('write', lambda *_: self.refresh())
        ttk.Combobox(sf, textvariable=self.v_sect_filter,
                     values=['All'] + SECTORS,
                     state='readonly', width=20, font=FA).pack(side='left')

        tk.Label(sf, text='  Source: netlog.db',
                 font=FS, fg='#666').pack(side='right')

        # Treeview
        cols   = ('Callsign', 'Name', 'City', 'ST', 'Sector', 'Ham CS', 'WL', 'VARA', 'Latitude', 'Longitude', 'Notes')
        widths = (       95,    190,    120,    32,     120,       85,    36,    36,       90,          95,        190)

        tf = tk.Frame(self)
        tf.pack(fill='both', expand=True, padx=8, pady=4)
        tf.rowconfigure(0, weight=1)
        tf.rowconfigure(1, weight=0)
        tf.columnconfigure(0, weight=1)

        self.tree = ttk.Treeview(tf, columns=cols, show='headings',
                                  selectmode='browse', height=18)
        for col, w in zip(cols, widths):
            self.tree.heading(col, text=col)
            self.tree.column(col, width=w, minwidth=28, stretch=False)

        vsb = ttk.Scrollbar(tf, orient='vertical',   command=self.tree.yview)
        hsb = ttk.Scrollbar(tf, orient='horizontal', command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.tree.grid(row=0, column=0, sticky='nsew')
        vsb.grid(row=0, column=1, sticky='ns')
        hsb.grid(row=1, column=0, sticky='ew')

        self.tree.tag_configure('odd',   background=ROW_ODD)
        self.tree.tag_configure('evn',   background=ROW_EVN)
        self.tree.tag_configure('synth', background='#F0F0F0', foreground='#888')
        self.tree.bind('<Double-1>', lambda _: self._do_edit())

        # Button bar
        bf = tk.Frame(self)
        bf.pack(fill='x', padx=8, pady=(4, 8))
        for txt, cmd, bg in [
            ('Add Member',    self._do_add,    HDR_BG),
            ('Edit Member',   self._do_edit,   '#2A6099'),
            ('Delete Member', self._do_delete, '#8B0000'),
        ]:
            tk.Button(bf, text=txt, font=FA, bg=bg, fg='#FFF',
                      padx=8, pady=3, command=cmd).pack(side='left', padx=6)
        tk.Label(bf, text='Double-click row to edit',
                 font=FS, fg='#666').pack(side='right')

    def refresh(self):
        search = self.v_search.get().lower()
        sect   = self.v_sect_filter.get()
        for item in self.tree.get_children():
            self.tree.delete(item)

        conn = get_db()
        rows = conn.execute(
            'SELECT * FROM members ORDER BY is_synthetic DESC, UPPER(callsign)'
        ).fetchall()

        j = 0
        for row in rows:
            if search:
                haystack = ' '.join(filter(None, [
                    row['callsign'], row['operator_name'],
                    row['city'], row['email'], row['notes'],
                ])).lower()
                if search not in haystack:
                    continue
            if sect != 'All' and sect not in (row['sector'] or ''):
                continue

            wl   = '✓' if row['winlink_cs'] else ''
            vara = '✓' if row['vara_hf_cs'] else ''
            tag  = 'synth' if row['is_synthetic'] else ('evn' if j % 2 else 'odd')
            lat_s = (f"{row['Latitude']:.4f} {row['Lat_Dir'] or ''}".strip()
                     if row['Latitude'] is not None else '')
            lon_s = (f"{row['Longitude']:.4f} {row['Long_Dir'] or ''}".strip()
                     if row['Longitude'] is not None else '')
            self.tree.insert('', 'end', iid=row['callsign'], tag=tag, values=(
                row['callsign'],
                row['operator_name'] or '',
                row['city']          or '',
                row['state']         or '',
                row['sector']        or '',
                row['ham_callsign']  or '',
                wl, vara,
                lat_s, lon_s,
                row['notes']         or '',
            ))
            j += 1

    def _selected(self):
        sel = self.tree.selection()
        return sel[0] if sel else None

    def _do_add(self):
        dlg = MemberDialog(self, 'Add New Member')
        if not dlg.result:
            return
        d = dlg.result
        try:
            get_db().execute(
                """INSERT INTO members
                   (callsign, operator_name, city, state, email, sector,
                    cell_phone, ham_callsign, winlink_cs, vara_hf_cs, shares_cs, notes,
                    Latitude, Lat_Dir, Longitude, Long_Dir, is_synthetic)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)""",
                (d['callsign'], d['operator_name'], d['city'], d['state'],
                 d['email'], d['sector'], d['cell_phone'], d['ham_callsign'],
                 d['winlink_cs'], d['vara_hf_cs'], d['shares_cs'], d['notes'],
                 d['Latitude'], d['Lat_Dir'], d['Longitude'], d['Long_Dir'])
            )
            get_db().commit()
            self.refresh()
            self.app.set_status(f"Member {d['callsign']} added.")
        except Exception as e:
            messagebox.showerror('DB Error', str(e))

    def _do_edit(self):
        cs = self._selected()
        if not cs:
            messagebox.showinfo('Select Member', 'Select a member row first.')
            return
        row = get_db().execute('SELECT * FROM members WHERE callsign=?', (cs,)).fetchone()
        if not row:
            return
        if row['is_synthetic']:
            messagebox.showinfo('System Entry',
                                f'{cs} is a system entry and cannot be edited.')
            return
        dlg = MemberDialog(self, f'Edit {cs}', row)
        if not dlg.result:
            return
        d = dlg.result
        get_db().execute(
            """UPDATE members SET
               operator_name=?, city=?, state=?, email=?, sector=?,
               cell_phone=?, ham_callsign=?, winlink_cs=?, vara_hf_cs=?, shares_cs=?, notes=?,
               Latitude=?, Lat_Dir=?, Longitude=?, Long_Dir=?
               WHERE callsign=?""",
            (d['operator_name'], d['city'], d['state'], d['email'], d['sector'],
             d['cell_phone'], d['ham_callsign'], d['winlink_cs'], d['vara_hf_cs'],
             d['shares_cs'], d['notes'],
             d['Latitude'], d['Lat_Dir'], d['Longitude'], d['Long_Dir'],
             d['callsign'])
        )
        get_db().commit()
        self.refresh()
        self.app.set_status(f"Member {d['callsign']} updated.")

    def _do_delete(self):
        cs = self._selected()
        if not cs:
            messagebox.showinfo('Select Member', 'Select a member row first.')
            return
        row = get_db().execute(
            'SELECT callsign, operator_name, is_synthetic FROM members WHERE callsign=?',
            (cs,)).fetchone()
        if row and row['is_synthetic']:
            messagebox.showinfo('System Entry',
                                f'{cs} is a system entry and cannot be deleted.')
            return
        name = row['operator_name'] if row else cs
        if messagebox.askyesno('Confirm Delete',
                               f'Delete  {cs} — {name}?\n\nThis cannot be undone.'):
            get_db().execute('DELETE FROM members WHERE callsign=?', (cs,))
            get_db().commit()
            self.refresh()
            self.app.set_status(f'{cs} removed from member DB.')


# ─────────────────────────────────────────────────────────────────────────────
# TAB 4 — ARCHIVES
# ─────────────────────────────────────────────────────────────────────────────
class ArchivesTab(ttk.Frame):

    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self._data = []
        self._build()
        self.refresh()

    def _build(self):
        # Archive list (top half)
        arc_cols   = ('Date', 'Net', 'Type', 'NCS', 'Check-Ins', 'Archived')
        arc_widths = (  100,   220,    80,    100,       85,        165)

        top = tk.Frame(self)
        top.pack(fill='both', expand=True, padx=8, pady=(8, 3))
        top.rowconfigure(0, weight=1)
        top.columnconfigure(0, weight=1)

        self.arc_tree = ttk.Treeview(top, columns=arc_cols, show='headings',
                                      selectmode='browse', height=9)
        for col, w in zip(arc_cols, arc_widths):
            self.arc_tree.heading(col, text=col)
            self.arc_tree.column(col, width=w, minwidth=40, stretch=(col == 'Net'))

        vsb1 = ttk.Scrollbar(top, orient='vertical', command=self.arc_tree.yview)
        self.arc_tree.configure(yscrollcommand=vsb1.set)
        self.arc_tree.grid(row=0, column=0, sticky='nsew')
        vsb1.grid(row=0, column=1, sticky='ns')
        self.arc_tree.bind('<<TreeviewSelect>>', self._on_select)

        # Check-in detail (bottom half)
        det_f = tk.LabelFrame(self, text=' Session Check-Ins ', font=FB, padx=5, pady=4)
        det_f.pack(fill='both', expand=True, padx=8, pady=(3, 4))
        det_f.rowconfigure(0, weight=1)
        det_f.columnconfigure(0, weight=1)

        det_cols   = ('#', 'Freq', 'FROM', 'Operator', 'TO', 'Time', 'Signal', 'City', 'Sector', 'Dist', 'Brg')
        det_widths = ( 36,   62,    82,       155,    82,     80,      72,     100,     110,      60,     60)

        self.det_tree = ttk.Treeview(det_f, columns=det_cols, show='headings',
                                      selectmode='none', height=8)
        for col, w in zip(det_cols, det_widths):
            self.det_tree.heading(col, text=col)
            self.det_tree.column(col, width=w, minwidth=28, stretch=(col == 'Operator'))

        vsb2 = ttk.Scrollbar(det_f, orient='vertical', command=self.det_tree.yview)
        self.det_tree.configure(yscrollcommand=vsb2.set)
        self.det_tree.grid(row=0, column=0, sticky='nsew')
        vsb2.grid(row=0, column=1, sticky='ns')

        # Buttons
        bf = tk.Frame(self)
        bf.pack(fill='x', padx=8, pady=(0, 6))
        tk.Button(bf, text='Refresh',      font=FA, padx=6,
                  command=self.refresh).pack(side='left', padx=4)
        tk.Button(bf, text='Load Session', font=FA, bg=HDR_BG, fg='#FFF', padx=8,
                  command=self._do_load).pack(side='left', padx=4)
        tk.Label(bf, text='Select a session row to view its check-ins',
                 font=FS, fg='#666').pack(side='right')

    def refresh(self):
        for item in self.arc_tree.get_children():
            self.arc_tree.delete(item)
        for item in self.det_tree.get_children():
            self.det_tree.delete(item)
        self._data = load_archives()
        for i, s in enumerate(self._data):
            total = (len(s.get('checkins', [])) +
                     len(s.get('checkins_alt1', [])) +
                     len(s.get('checkins_alt2', [])))
            self.arc_tree.insert('', 'end', iid=str(i), values=(
                s.get('net_date', ''),
                s.get('net_name', ''),
                s.get('freq_type', ''),
                s.get('ncs_callsign', ''),
                total,
                s.get('archived_at', '')[:16],
            ))

    def _on_select(self, _evt):
        sel = self.arc_tree.selection()
        if not sel:
            return
        s = self._data[int(sel[0])]
        for item in self.det_tree.get_children():
            self.det_tree.delete(item)
        freq_lists = [
            ('PRIMARY',  s.get('checkins',      [])),
            ('1ST ALT',  s.get('checkins_alt1', [])),
            ('2ND ALT',  s.get('checkins_alt2', [])),
        ]
        for freq_label, ci_list in freq_lists:
            for ci in ci_list:
                sig  = ' '.join(c for c in SIG_CODES if ci.get(f'signal_{c}'))
                dist = ci.get('distance_miles', '')
                brg  = ci.get('bearing_degrees', '')
                self.det_tree.insert('', 'end', values=(
                    ci.get('checkin_order', ''),
                    freq_label,
                    ci.get('from_callsign', ''),
                    ci.get('from_operator', ''),
                    ci.get('to_callsign', ''),
                    ci.get('checkin_time', ''),
                    sig, ci.get('city', ''), ci.get('sector', ''),
                    f'{dist:.1f}' if isinstance(dist, float) else str(dist)[:8],
                    f'{brg:.1f}°' if isinstance(brg,  float) else str(brg)[:7],
                ))

    def _do_load(self):
        sel = self.arc_tree.selection()
        if not sel:
            messagebox.showinfo('Select Session', 'Select an archived session row.')
            return
        s = self._data[int(sel[0])]
        if not messagebox.askyesno('Load Archived Session',
                f'Load session from {s.get("net_date", "")} '
                f'({len(s.get("checkins", []))} check-ins)?\n\n'
                f'This will replace the current session.'):
            return
        new = NetSession()
        _skip = {'checkins', 'checkins_alt1', 'checkins_alt2', 'archived_at'}
        for k, v in s.items():
            if k not in _skip:
                try:
                    setattr(new, k, v)
                except AttributeError:
                    pass
        new.checkins      = s.get('checkins',      [])
        new.checkins_alt1 = s.get('checkins_alt1', [])
        new.checkins_alt2 = s.get('checkins_alt2', [])
        self.app.session = new
        self.app.log_tab.update_session_display()
        self.app.session_tab.load_from_session()
        self.app.notebook.select(0)
        self.app.set_status(
            f'Loaded archived session: {s.get("net_name", "")} {s.get("net_date", "")} '
            f'— {len(new.checkins)} check-ins')


# ─────────────────────────────────────────────────────────────────────────────
# GENERIC TABLE ROW DIALOG  (add / edit for simple DB tables)
# ─────────────────────────────────────────────────────────────────────────────
class _TableRowDialog(tk.Toplevel):
    """
    Modal add/edit dialog that adapts to any table schema.

    fields: list of (label_str, col_name, readonly_bool)
    row:    sqlite3.Row or dict for edit mode; None for add mode.
    """
    def __init__(self, parent, title, fields, row=None):
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.grab_set()
        self.result = None
        self._fields = fields
        self._vars   = {}
        self._build(row)
        self.transient(parent)
        self.update_idletasks()
        px, py = parent.winfo_rootx(), parent.winfo_rooty()
        pw, ph = parent.winfo_width(),  parent.winfo_height()
        w,  h  = self.winfo_reqwidth(), self.winfo_reqheight()
        self.geometry(f'+{px+(pw-w)//2}+{py+(ph-h)//2}')
        self.wait_window()

    def _build(self, row):
        for i, (lbl, col, readonly) in enumerate(self._fields):
            tk.Label(self, text=lbl + ':', font=FA, anchor='e').grid(
                row=i, column=0, sticky='e', padx=(16, 6), pady=4)
            val = ''
            if row is not None:
                val = row[col] if row[col] is not None else ''
            v = tk.StringVar(value=str(val))
            self._vars[col] = v
            bg = AUTO_BG if readonly else ENTRY_BG
            st = 'readonly' if readonly else 'normal'
            tk.Entry(self, textvariable=v, bg=bg, font=FA,
                     width=36, state=st).grid(
                row=i, column=1, sticky='ew', padx=(0, 16), pady=4)

        n  = len(self._fields)
        bf = tk.Frame(self)
        bf.grid(row=n, column=0, columnspan=2, pady=10)
        tk.Button(bf, text='  Save  ', font=FB,
                  bg=HDR_BG, fg='#FFF', padx=8, pady=3,
                  command=self._save).pack(side='left', padx=8)
        tk.Button(bf, text='Cancel', font=FA, padx=8,
                  command=self.destroy).pack(side='left', padx=8)

    def _save(self):
        result = {}
        for lbl, col, readonly in self._fields:
            result[col] = self._vars[col].get().strip()
        for lbl, col, readonly in self._fields:
            if not readonly and not result[col]:
                messagebox.showwarning('Required',
                    f'{lbl} cannot be empty.', parent=self)
                return
        self.result = result
        self.destroy()


# ─────────────────────────────────────────────────────────────────────────────
# TAB 5 — MAINTENANCE  (view / add / edit / delete / print all DB tables)
# ─────────────────────────────────────────────────────────────────────────────
class MaintenanceTab(ttk.Frame):
    """Full CRUD + print access to every table in netlog.db."""

    # ── Table configurations ──────────────────────────────────────────────────
    # display_cols / display_keys: shown in treeview
    # print_cols   / print_keys:   shown in printed report (can include extra fields)
    # all_query: fetches every column needed (SELECT * for members)
    _CFG = {
        'Members': {
            'table':       'members',
            'pk':          'callsign',
            'pk_auto':     False,
            'db_func':     get_db,
            'db_name':     'netlog.db',
            'all_query':   ('SELECT * FROM members '
                            'ORDER BY is_synthetic DESC, UPPER(callsign)'),
            'display_cols': ('Callsign','Name','City','ST','Sector',
                             'Ham CS','Winlink CS','VARA CS',
                             'Latitude','Lat Dir','Longitude','Long Dir','Notes'),
            'display_keys': ('callsign','operator_name','city','state','sector',
                             'ham_callsign','winlink_cs','vara_hf_cs',
                             'Latitude','Lat_Dir','Longitude','Long_Dir','notes'),
            'display_w':    (90,180,110,30,120,80,85,75,80,55,85,60,180),
            'print_cols':  ('Callsign','Operator Name','City','ST','Email','Sector',
                            'Cell Phone','Ham CS','Winlink CS','VARA CS',
                            'Latitude','Lat Dir','Longitude','Long Dir','Notes'),
            'print_keys':  ('callsign','operator_name','city','state','email','sector',
                            'cell_phone','ham_callsign','winlink_cs','vara_hf_cs',
                            'Latitude','Lat_Dir','Longitude','Long_Dir','notes'),
            'print_query': ('SELECT * FROM members WHERE is_synthetic=0 '
                            'ORDER BY UPPER(callsign)'),
        },
        'Frequencies': {
            'table':       'frequencies',
            'pk':          'ID',
            'pk_auto':     True,
            'db_func':     get_db,
            'db_name':     'netlog.db',
            'all_query':   'SELECT ID, Frequency FROM frequencies ORDER BY ID',
            'display_cols': ('ID', 'Frequency'),
            'display_keys': ('ID', 'Frequency'),
            'display_w':    (45, 400),
            'print_cols':  ('ID', 'Frequency'),
            'print_keys':  ('ID', 'Frequency'),
            'print_query': 'SELECT ID, Frequency FROM frequencies ORDER BY ID',
        },
        'Sectors': {
            'table':       'sectors',
            'pk':          'Sector',
            'pk_auto':     False,
            'db_func':     get_db,
            'db_name':     'netlog.db',
            'all_query':   'SELECT Sector FROM sectors ORDER BY rowid',
            'display_cols': ('Sector',),
            'display_keys': ('Sector',),
            'display_w':    (420,),
            'print_cols':  ('Sector',),
            'print_keys':  ('Sector',),
            'print_query': 'SELECT Sector FROM sectors ORDER BY rowid',
        },
        'Transceivers': {
            'table':       'transceivers',
            'pk':          'Transceiver',
            'pk_auto':     False,
            'db_func':     get_db,
            'db_name':     'netlog.db',
            'all_query':   'SELECT Transceiver FROM transceivers ORDER BY rowid',
            'display_cols': ('Transceiver',),
            'display_keys': ('Transceiver',),
            'display_w':    (480,),
            'print_cols':  ('Transceiver',),
            'print_keys':  ('Transceiver',),
            'print_query': 'SELECT Transceiver FROM transceivers ORDER BY rowid',
        },
        'Net Names': {
            'table':       'Net_Names',
            'pk':          'Nets',
            'pk_auto':     False,
            'db_func':     get_db,
            'db_name':     'netlog.db',
            'all_query':   'SELECT Nets FROM Net_Names ORDER BY rowid',
            'display_cols': ('Net Name',),
            'display_keys': ('Nets',),
            'display_w':    (480,),
            'print_cols':  ('Net Name',),
            'print_keys':  ('Nets',),
            'print_query': 'SELECT Nets FROM Net_Names ORDER BY rowid',
        },
        'Digital Modes': {
            'table':       'Digital_Modes',
            'pk':          'DigitalMode',
            'pk_auto':     False,
            'db_func':     get_db,
            'db_name':     'netlog.db',
            'all_query':   'SELECT DigitalMode FROM Digital_Modes ORDER BY rowid',
            'display_cols': ('Digital Mode',),
            'display_keys': ('DigitalMode',),
            'display_w':    (480,),
            'print_cols':  ('Digital Mode',),
            'print_keys':  ('DigitalMode',),
            'print_query': 'SELECT DigitalMode FROM Digital_Modes ORDER BY rowid',
        },
        # ── net_archives.db tables (view + delete only) ────────────────────────
        'Arc Sessions': {
            'table':       'sessions',
            'pk':          'session_id',
            'pk_auto':     True,
            'db_func':     get_archive_db,
            'db_name':     'net_archives.db',
            'can_add':     False,
            'can_edit':    False,
            'all_query':   (
                'SELECT s.session_id, s.net_date, s.net_name, s.freq_type, '
                '       s.ncs_callsign, s.primary_freq, s.archived_at, '
                '       (SELECT COUNT(*) FROM checkins c '
                '        WHERE c.session_id=s.session_id) AS total_checkins '
                'FROM sessions s ORDER BY s.session_id DESC'
            ),
            'display_cols': ('ID','Date','Net Name','Type','NCS',
                             'Primary Freq','Archived At','Check-Ins'),
            'display_keys': ('session_id','net_date','net_name','freq_type',
                             'ncs_callsign','primary_freq','archived_at','total_checkins'),
            'display_w':    (40, 90, 200, 75, 90, 140, 148, 68),
            'print_cols':  ('ID','Date','Net Name','Type','NCS',
                            'Primary Freq','Transceiver','Archived At','Check-Ins'),
            'print_keys':  ('session_id','net_date','net_name','freq_type',
                            'ncs_callsign','primary_freq','transceiver','archived_at','total_checkins'),
            'print_query': (
                'SELECT s.session_id, s.net_date, s.net_name, s.freq_type, '
                '       s.ncs_callsign, s.primary_freq, s.transceiver, s.archived_at, '
                '       (SELECT COUNT(*) FROM checkins c '
                '        WHERE c.session_id=s.session_id) AS total_checkins '
                'FROM sessions s ORDER BY s.session_id DESC'
            ),
        },
        'Arc Check-ins': {
            'table':       'checkins',
            'pk':          'checkin_id',
            'pk_auto':     True,
            'db_func':     get_archive_db,
            'db_name':     'net_archives.db',
            'can_add':     False,
            'can_edit':    False,
            'all_query':   (
                'SELECT checkin_id, session_id, freq_type, from_callsign, '
                '       to_callsign, checkin_time, sector, city, checkin_order '
                'FROM checkins ORDER BY session_id DESC, checkin_order'
            ),
            'display_cols': ('CK ID','Sess','Freq','FROM','TO',
                             'Time','Sector','City','Order'),
            'display_keys': ('checkin_id','session_id','freq_type',
                             'from_callsign','to_callsign',
                             'checkin_time','sector','city','checkin_order'),
            'display_w':    (55, 45, 75, 90, 90, 75, 110, 110, 50),
            'print_cols':  ('CK ID','Session','Freq','FROM','Operator',
                            'TO','Time','Sector','City','Order'),
            'print_keys':  ('checkin_id','session_id','freq_type',
                            'from_callsign','from_operator','to_callsign',
                            'checkin_time','sector','city','checkin_order'),
            'print_query': (
                'SELECT checkin_id, session_id, freq_type, from_callsign, from_operator, '
                '       to_callsign, checkin_time, sector, city, checkin_order '
                'FROM checkins ORDER BY session_id DESC, checkin_order'
            ),
        },
    }
    _TABLE_ORDER = ['Members', 'Frequencies', 'Sectors', 'Transceivers',
                    'Net Names', 'Digital Modes', 'Arc Sessions', 'Arc Check-ins']

    # ── Constructor ───────────────────────────────────────────────────────────
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app       = app
        self._cur_name = 'Members'
        self._all_rows = []   # full sqlite3.Row list for current table
        self._build()
        self.refresh()

    # ── Layout ────────────────────────────────────────────────────────────────
    def _build(self):
        # ── Title bar ─────────────────────────────────────────────────────────
        hf = tk.Frame(self, bg=HDR_BG, pady=5)
        hf.pack(fill='x')
        tk.Label(hf, text='  DATABASE MAINTENANCE',
                 font=FT, bg=HDR_BG, fg=HDR_FG).pack(side='left', padx=8)
        self.v_db_label = tk.StringVar(value='netlog.db')
        tk.Label(hf, textvariable=self.v_db_label,
                 font=FS, bg=HDR_BG, fg=HDR_DIM).pack(side='right', padx=12)

        # ── Toolbar ───────────────────────────────────────────────────────────
        tb = tk.Frame(self)
        tb.pack(fill='x', padx=8, pady=(8, 4))

        tk.Label(tb, text='Table:', font=FB).pack(side='left', padx=(0, 4))
        self.v_table = tk.StringVar(value='Members')
        self.v_table.trace_add('write', lambda *_: self._on_table_change())
        ttk.Combobox(tb, textvariable=self.v_table,
                     values=self._TABLE_ORDER,
                     state='readonly', width=16, font=FA).pack(side='left', padx=(0, 18))

        tk.Label(tb, text='Search:', font=FA).pack(side='left', padx=(0, 4))
        self.v_search = tk.StringVar()
        self.v_search.trace_add('write', lambda *_: self._apply_filter())
        tk.Entry(tb, textvariable=self.v_search,
                 font=FA, bg=ENTRY_BG, width=24).pack(side='left')

        tk.Button(tb, text='✕', font=FS, width=2, relief=tk.FLAT,
                  command=lambda: self.v_search.set('')).pack(side='left', padx=2)

        self.lbl_count = tk.Label(tb, text='', font=FS, fg='#666')
        self.lbl_count.pack(side='right')

        # ── Treeview ──────────────────────────────────────────────────────────
        tv_f = tk.Frame(self)
        tv_f.pack(fill='both', expand=True, padx=8, pady=(0, 4))
        tv_f.rowconfigure(0, weight=1)
        tv_f.columnconfigure(0, weight=1)

        self.tree = ttk.Treeview(tv_f, show='headings',
                                  selectmode='browse', height=20)
        vsb = ttk.Scrollbar(tv_f, orient='vertical',   command=self.tree.yview)
        hsb = ttk.Scrollbar(tv_f, orient='horizontal',  command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.tree.grid(row=0, column=0, sticky='nsew')
        vsb.grid(row=0, column=1, sticky='ns')
        hsb.grid(row=1, column=0, sticky='ew')

        self.tree.tag_configure('odd',   background=ROW_ODD)
        self.tree.tag_configure('evn',   background=ROW_EVN)
        self.tree.tag_configure('synth', background='#F0F0F0', foreground='#888')
        self.tree.bind('<Double-1>', lambda _: self._do_edit())

        # ── Button bar ────────────────────────────────────────────────────────
        bf = tk.Frame(self)
        bf.pack(fill='x', padx=8, pady=(0, 8))

        for txt, cmd, bg in [
            ('Add',    self._do_add,    HDR_BG),
            ('Edit',   self._do_edit,   '#2A6099'),
            ('Delete', self._do_delete, '#8B0000'),
        ]:
            tk.Button(bf, text=txt, font=FA, bg=bg, fg='#FFF',
                      padx=10, pady=3, command=cmd).pack(side='left', padx=4)

        ttk.Separator(bf, orient='vertical').pack(side='left', fill='y', padx=10)

        for txt, cmd, bg in [
            ('Print',   self._do_print, '#4A4A6A'),
            ('Refresh', self.refresh,   '#3A3A3A'),
        ]:
            tk.Button(bf, text=txt, font=FA, bg=bg, fg='#FFF',
                      padx=10, pady=3, command=cmd).pack(side='left', padx=4)

        tk.Label(bf, text='Double-click a row to edit',
                 font=FS, fg='#666').pack(side='right')

    # ── Table switching ───────────────────────────────────────────────────────
    def _cfg(self):
        return self._CFG[self._cur_name]

    def _db(self):
        """Return the DB connection for the currently selected table."""
        return self._cfg().get('db_func', get_db)()

    def _on_table_change(self):
        self._cur_name = self.v_table.get()
        self._all_rows = []   # clear before trace fires so _apply_filter
        self.v_search.set('') # sees no stale rows from the previous table
        self.v_db_label.set(self._cfg().get('db_name', 'netlog.db'))
        self.refresh()

    def _reconfigure_tree(self):
        cfg = self._cfg()
        cols = cfg['display_cols']
        self.tree.configure(columns=cols)
        for col, w in zip(cols, cfg['display_w']):
            self.tree.heading(col, text=col,
                              command=lambda c=col: self._sort_by(c))
            self.tree.column(col, width=w, minwidth=28,
                             stretch=(col == cols[-1]))

    # ── Data loading ──────────────────────────────────────────────────────────
    def refresh(self):
        self._reconfigure_tree()
        cfg = self._cfg()
        self._all_rows = self._db().execute(cfg['all_query']).fetchall()
        self._apply_filter()

    def _apply_filter(self):
        search = self.v_search.get().strip().lower()
        cfg    = self._cfg()
        for item in self.tree.get_children():
            self.tree.delete(item)
        vis = 0
        for orig_idx, row in enumerate(self._all_rows):
            if search:
                haystack = ' '.join(str(v or '') for v in row).lower()
                if search not in haystack:
                    continue
            vals = tuple(str(row[k] or '') for k in cfg['display_keys'])
            is_synth = (self._cur_name == 'Members' and bool(row['is_synthetic']))
            tag = 'synth' if is_synth else ('evn' if vis % 2 else 'odd')
            # Store original index as IID so _selected_row() works after filtering
            self.tree.insert('', 'end', iid=str(orig_idx), tag=tag, values=vals)
            vis += 1
        total = len(self._all_rows)
        self.lbl_count.config(
            text=f'{vis} of {total} rows' if search else f'{total} rows')

    def _sort_by(self, col):
        items = [(self.tree.set(k, col), k) for k in self.tree.get_children()]
        items.sort(key=lambda x: x[0].lower())
        for i, (_, k) in enumerate(items):
            self.tree.move(k, '', i)

    # ── Selection helper ──────────────────────────────────────────────────────
    def _selected_row(self):
        sel = self.tree.selection()
        if not sel:
            return None
        return self._all_rows[int(sel[0])]

    # ── Field builder for generic dialog ─────────────────────────────────────
    def _build_fields(self, row):
        """Return fields list for _TableRowDialog based on current table."""
        cfg    = self._cfg()
        pk     = cfg['pk']
        fields = []
        for key in cfg['display_keys']:
            if key == pk and cfg['pk_auto']:
                # Auto-increment PK: read-only in edit, omitted in add
                if row is not None:
                    fields.append((key, key, True))
            else:
                fields.append((key, key, False))
        return fields

    # ── CRUD ─────────────────────────────────────────────────────────────────
    def _do_add(self):
        if not self._cfg().get('can_add', True):
            messagebox.showinfo('View Only',
                f'{self._cur_name} rows are written automatically and cannot be added here.',
                parent=self)
            return
        if self._cur_name == 'Members':
            dlg = MemberDialog(self, 'Add New Member')
            if not dlg.result:
                return
            d = dlg.result
            try:
                get_db().execute(
                    """INSERT INTO members
                       (callsign, operator_name, city, state, email, sector,
                        cell_phone, ham_callsign, winlink_cs, vara_hf_cs,
                        shares_cs, notes,
                        Latitude, Lat_Dir, Longitude, Long_Dir, is_synthetic)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)""",
                    (d['callsign'], d['operator_name'], d['city'], d['state'],
                     d['email'], d['sector'], d['cell_phone'], d['ham_callsign'],
                     d['winlink_cs'], d['vara_hf_cs'], d['shares_cs'], d['notes'],
                     d['Latitude'], d['Lat_Dir'], d['Longitude'], d['Long_Dir'])
                )
                get_db().commit()
                self.refresh()
                self.app.set_status(f"Maintenance: member {d['callsign']} added.")
            except Exception as e:
                messagebox.showerror('DB Error', str(e), parent=self)
            return

        cfg    = self._cfg()
        fields = self._build_fields(row=None)
        dlg    = _TableRowDialog(self, f'Add — {self._cur_name}', fields, row=None)
        if not dlg.result:
            return
        try:
            self._insert_row(cfg, dlg.result)
            self.refresh()
            self.app.set_status(
                f'Maintenance: row added to {cfg["table"]}.')
        except Exception as e:
            messagebox.showerror('DB Error', str(e), parent=self)

    def _do_edit(self):
        if not self._cfg().get('can_edit', True):
            messagebox.showinfo('View Only',
                f'{self._cur_name} rows are written automatically and cannot be edited here.',
                parent=self)
            return
        row = self._selected_row()
        if row is None:
            messagebox.showinfo('Select Row',
                'Select a row to edit.', parent=self)
            return

        if self._cur_name == 'Members':
            if row['is_synthetic']:
                messagebox.showinfo('System Entry',
                    f'{row["callsign"]} is a system entry and cannot be edited.',
                    parent=self)
                return
            dlg = MemberDialog(self, f'Edit — {row["callsign"]}', row)
            if not dlg.result:
                return
            d = dlg.result
            get_db().execute(
                """UPDATE members SET
                   operator_name=?, city=?, state=?, email=?, sector=?,
                   cell_phone=?, ham_callsign=?, winlink_cs=?, vara_hf_cs=?,
                   shares_cs=?, notes=?,
                   Latitude=?, Lat_Dir=?, Longitude=?, Long_Dir=?
                   WHERE callsign=?""",
                (d['operator_name'], d['city'], d['state'], d['email'],
                 d['sector'], d['cell_phone'], d['ham_callsign'],
                 d['winlink_cs'], d['vara_hf_cs'], d['shares_cs'],
                 d['notes'],
                 d['Latitude'], d['Lat_Dir'], d['Longitude'], d['Long_Dir'],
                 d['callsign'])
            )
            get_db().commit()
            self.refresh()
            self.app.set_status(
                f"Maintenance: member {d['callsign']} updated.")
            return

        cfg    = self._cfg()
        fields = self._build_fields(row=row)
        dlg    = _TableRowDialog(self,
                     f'Edit — {self._cur_name}', fields, row=row)
        if not dlg.result:
            return
        try:
            self._update_row(cfg, dlg.result, row)
            self.refresh()
            self.app.set_status(
                f'Maintenance: row updated in {cfg["table"]}.')
        except Exception as e:
            messagebox.showerror('DB Error', str(e), parent=self)

    def _do_delete(self):
        row = self._selected_row()
        if row is None:
            messagebox.showinfo('Select Row',
                'Select a row to delete.', parent=self)
            return

        if self._cur_name == 'Members':
            if row['is_synthetic']:
                messagebox.showinfo('System Entry',
                    f'{row["callsign"]} is a system entry and cannot be deleted.',
                    parent=self)
                return
            label = f'{row["callsign"]}  —  {row["operator_name"] or ""}'
            if not messagebox.askyesno('Confirm Delete',
                    f'Delete member:\n{label}\n\nThis cannot be undone.',
                    parent=self):
                return
            get_db().execute('DELETE FROM members WHERE callsign=?',
                             (row['callsign'],))
            get_db().commit()
            self.refresh()
            self.app.set_status(
                f'Maintenance: {row["callsign"]} deleted.')
            return

        # ── Archive session cascade-delete ────────────────────────────────────
        if self._cur_name == 'Arc Sessions':
            sid = row['session_id']
            ck_count = get_archive_db().execute(
                'SELECT COUNT(*) FROM checkins WHERE session_id=?', (sid,)
            ).fetchone()[0]
            if not messagebox.askyesno('Confirm Delete',
                    f'Delete archived session {sid}:\n'
                    f'  {row["net_date"]}  {row["net_name"]}\n'
                    f'  NCS: {row["ncs_callsign"]}\n\n'
                    f'This will also delete {ck_count} check-in(s).\n'
                    f'This cannot be undone.',
                    parent=self):
                return
            get_archive_db().execute(
                'DELETE FROM checkins WHERE session_id=?', (sid,))
            get_archive_db().execute(
                'DELETE FROM sessions WHERE session_id=?', (sid,))
            get_archive_db().commit()
            self.refresh()
            self.app.set_status(
                f'Maintenance: archive session {sid} and its check-ins deleted.')
            return

        cfg    = self._cfg()
        pk_val = row[cfg['pk']]
        if not messagebox.askyesno('Confirm Delete',
                f'Delete from {cfg["table"]}:\n"{pk_val}"\n\nThis cannot be undone.',
                parent=self):
            return
        self._db().execute(
            f'DELETE FROM {cfg["table"]} WHERE "{cfg["pk"]}"=?', (pk_val,))
        self._db().commit()
        self.refresh()
        self.app.set_status(
            f'Maintenance: "{pk_val}" deleted from {cfg["table"]}.')

    # ── Print ─────────────────────────────────────────────────────────────────
    def _do_print(self):
        import tempfile, webbrowser
        cfg  = self._cfg()
        rows = self._db().execute(cfg['print_query']).fetchall()
        pcols = cfg['print_cols']
        pkeys = cfg['print_keys']
        ts    = datetime.now().strftime('%Y-%m-%d  %H:%M')

        th = ''.join(f'<th>{c}</th>' for c in pcols)
        tb_rows = ''
        for i, row in enumerate(rows):
            bg = '#EEF4FF' if i % 2 else '#FFFFFF'
            tds = ''.join(
                f'<td>{str(row[k] or "")}</td>' for k in pkeys)
            tb_rows += f'<tr style="background:{bg}">{tds}</tr>\n'

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>USCG Aux D7 Net Log — {self._cur_name}</title>
<style>
  body  {{ font-family: Arial, sans-serif; font-size: 11px; margin: 24px; color: #222; }}
  h2    {{ color: #1F4E79; margin-bottom: 2px; font-size: 15px; }}
  .sub  {{ color: #555; font-size: 10px; margin-top: 0; margin-bottom: 12px; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 11px; }}
  thead th {{ background: #1F4E79; color: #FFF; padding: 5px 8px;
              text-align: left; border: 1px solid #144369; }}
  tbody td {{ padding: 4px 8px; border: 1px solid #DDD; }}
  @media print {{
    body {{ margin: 10px; }}
    button {{ display: none; }}
  }}
</style>
</head>
<body>
<h2>USCG Auxiliary — Southeast District 7 — HF Contingency Net Log</h2>
<p class="sub">
  Table: <strong>{self._cur_name}</strong> &nbsp;|&nbsp;
  Generated: {ts} &nbsp;|&nbsp;
  {len(rows)} record{"s" if len(rows) != 1 else ""}
</p>
<table>
  <thead><tr>{th}</tr></thead>
  <tbody>{tb_rows}</tbody>
</table>
<p class="sub" style="margin-top:10px">
  Source: {cfg.get('db_name', 'netlog.db')} &nbsp;|&nbsp; USCG Auxiliary District 7
</p>
</body></html>"""

        with tempfile.NamedTemporaryFile(mode='w', suffix='.html',
                                          delete=False,
                                          encoding='utf-8') as fh:
            fh.write(html)
            tmp = fh.name
        webbrowser.open(pathlib.Path(tmp).as_uri())
        self.app.set_status(
            f'Print preview opened — {self._cur_name} ({len(rows)} rows). '
            f'Use Ctrl+P in your browser to print.')

    # ── DB helpers ────────────────────────────────────────────────────────────
    def _insert_row(self, cfg, values):
        tbl  = cfg['table']
        cols = [k for k in cfg['display_keys']
                if not (k == cfg['pk'] and cfg['pk_auto'])]
        ph   = ','.join('?' * len(cols))
        self._db().execute(
            f'INSERT INTO {tbl} ({",".join(cols)}) VALUES ({ph})',
            [values[c] for c in cols])
        self._db().commit()

    def _update_row(self, cfg, new_vals, old_row):
        tbl   = cfg['table']
        pk    = cfg['pk']
        old_pk = old_row[pk]
        # Update all editable columns (exclude auto-PK)
        cols  = [k for k in cfg['display_keys']
                 if not (k == pk and cfg['pk_auto'])]
        set_c = ', '.join(f'"{c}"=?' for c in cols)
        self._db().execute(
            f'UPDATE {tbl} SET {set_c} WHERE "{pk}"=?',
            [new_vals[c] for c in cols] + [old_pk])
        self._db().commit()


# ─────────────────────────────────────────────────────────────────────────────
# OPENING / LOGIN SCREEN
# ─────────────────────────────────────────────────────────────────────────────
class LoginDialog(tk.Frame):
    """
    Opening-screen frame embedded directly in the main Tk window.
    Uses a BooleanVar (v_done) to signal completion so the parent can
    wait_variable() without any Toplevel / transient / withdraw tricks.

    result = True  → authenticated, proceed to build the main application
    result = False → user exited, application should terminate
    """

    _W, _H = 520, 440

    def __init__(self, parent):
        super().__init__(parent)
        self.result    = False
        self._attempts = 0
        self.v_done    = tk.BooleanVar(value=False)
        self._build()

    def _build(self):
        # ── Top banner ────────────────────────────────────────────────────────
        top = tk.Frame(self, bg=HDR_BG)
        top.pack(fill='x')
        tk.Label(top,
                 text='UNITED STATES COAST GUARD AUXILIARY',
                 font=(_FF, 12, 'bold'), bg=HDR_BG, fg=HDR_FG,
                 pady=14).pack()
        tk.Label(top,
                 text='Seventh District  —  Southeast',
                 font=(_FF, 10), bg=HDR_BG, fg=HDR_DIM).pack(pady=(0, 10))

        # ── App title strip ───────────────────────────────────────────────────
        title_strip = tk.Frame(self, bg='#163D5E')
        title_strip.pack(fill='x')
        tk.Label(title_strip,
                 text='HF Contingency Communications Net Log',
                 font=(_FF, 14, 'bold'), bg='#163D5E', fg='#FFD966',
                 pady=10).pack()

        # ── Body ──────────────────────────────────────────────────────────────
        body = tk.Frame(self, bg='#EEF4FA', pady=28, padx=55)
        body.pack(fill='both', expand=True)

        tk.Label(body,
                 text='Authorized Personnel Only — Enter Password to Continue',
                 font=(_FF, 9, 'italic'), bg='#EEF4FA', fg='#555'
                 ).pack(pady=(0, 22))

        # Password row
        pw_row = tk.Frame(body, bg='#EEF4FA')
        pw_row.pack()
        tk.Label(pw_row, text='Password:', font=FB, bg='#EEF4FA',
                 anchor='e', width=10).grid(row=0, column=0,
                                            sticky='e', padx=(0, 10))
        self._v_pw = tk.StringVar()
        self._entry = tk.Entry(pw_row, textvariable=self._v_pw, show='*',
                               font=FA, bg=ENTRY_BG, width=26,
                               relief=tk.SOLID, bd=1)
        self._entry.grid(row=0, column=1, ipady=5)
        self._entry.focus_set()
        self._entry.bind('<Return>', lambda _: self._check())

        # Login button
        tk.Button(body,
                  text='   Login   ', font=FB,
                  bg=HDR_BG, fg='#FFFFFF',
                  padx=16, pady=6, relief=tk.FLAT, cursor='hand2',
                  command=self._check).pack(pady=(20, 4))

        # ── Error message — red bold, hidden until a wrong attempt ────────────
        self._lbl_err = tk.Label(body,
                                  text='',
                                  font=(_FF, 11, 'bold'),
                                  fg='#CC0000', bg='#EEF4FA',
                                  pady=6)
        self._lbl_err.pack()

        # ── Exit button — dark red, hidden until first wrong attempt ──────────
        self._btn_exit = tk.Button(body,
                                    text='Exit Program',
                                    font=FB,
                                    bg='#8B0000', fg='#FFFFFF',
                                    padx=14, pady=5,
                                    relief=tk.FLAT, cursor='hand2',
                                    command=self._exit)
        # NOT packed here — revealed only after the first wrong password

    def _check(self):
        if self._v_pw.get() == _APP_PASSWORD:
            self.result = True
            self.v_done.set(True)          # signal parent to proceed
        else:
            self._attempts += 1
            self._lbl_err.config(
                text='✖  Incorrect password — please try again')
            self._v_pw.set('')
            self._entry.focus_set()
            if self._attempts == 1:        # reveal Exit button on first failure
                self._btn_exit.pack(pady=(2, 0))

    def _exit(self):
        self.result = False
        self.v_done.set(True)              # signal parent to terminate


# ─────────────────────────────────────────────────────────────────────────────
# Popup calendar date-picker (no third-party dependency)
# ─────────────────────────────────────────────────────────────────────────────
class _DatePickerButton(tk.Frame):
    """Button that opens a month-grid popup and exposes a .date property."""

    _MON = ['January','February','March','April','May','June',
            'July','August','September','October','November','December']

    def __init__(self, parent, initial_date, on_change=None, **kw):
        super().__init__(parent, **kw)
        self._date     = initial_date
        self._on_change = on_change
        self._popup    = None
        self._btn = tk.Button(self, text=str(initial_date), font=FA,
                              bg=ENTRY_BG, relief='groove', width=12,
                              command=self._toggle)
        self._btn.pack()

    @property
    def date(self):
        return self._date

    def set_date(self, d):
        self._date = d
        self._btn.config(text=str(d))

    def _toggle(self):
        if self._popup and self._popup.winfo_exists():
            self._popup.destroy()
            self._popup = None
        else:
            self._open()

    def _open(self):
        import calendar as _cal
        popup = tk.Toplevel(self)
        popup.title('')
        popup.resizable(False, False)
        popup.transient(self.winfo_toplevel())
        popup.grab_set()
        self._popup = popup

        self.update_idletasks()
        bx = self._btn.winfo_rootx()
        by = self._btn.winfo_rooty() + self._btn.winfo_height() + 2
        popup.geometry(f'+{bx}+{by}')

        vy = [self._date.year]
        vm = [self._date.month]
        body = tk.Frame(popup, bg='#EBEBEB', padx=4, pady=4)
        body.pack()

        def draw():
            for w in body.winfo_children():
                w.destroy()

            # Navigation row
            nav = tk.Frame(body, bg='#EBEBEB')
            nav.pack(fill='x', pady=(0, 2))
            tk.Button(nav, text=' < ', font=FS, relief='flat',
                      command=prev_m).pack(side='left')
            tk.Label(nav, text=f'{self._MON[vm[0]-1]} {vy[0]}',
                     font=FB, bg='#EBEBEB', width=17).pack(side='left', expand=True)
            tk.Button(nav, text=' > ', font=FS, relief='flat',
                      command=next_m).pack(side='right')

            # Day-of-week header
            hdr = tk.Frame(body, bg='#EBEBEB')
            hdr.pack()
            for h in ('Mo','Tu','We','Th','Fr','Sa','Su'):
                tk.Label(hdr, text=h, font=FS, width=3, anchor='center',
                         bg=HDR_BG, fg='white').pack(side='left', padx=1, pady=1)

            # Day grid
            for week in _cal.monthcalendar(vy[0], vm[0]):
                row = tk.Frame(body, bg='#EBEBEB')
                row.pack()
                for day in week:
                    if day == 0:
                        tk.Label(row, text='', width=3, font=FS,
                                 bg='#EBEBEB').pack(side='left', padx=1, pady=1)
                    else:
                        selected = (day == self._date.day and
                                    vm[0] == self._date.month and
                                    vy[0] == self._date.year)
                        bg = HDR_BG if selected else '#F8F8F8'
                        fg = '#FFF'  if selected else '#000'
                        tk.Button(row, text=str(day), width=3, font=FS,
                                  bg=bg, fg=fg, relief='flat', activebackground='#6A90BB',
                                  command=lambda d=day: pick(d)).pack(side='left',
                                                                       padx=1, pady=1)

        def prev_m():
            m, y = vm[0]-1, vy[0]
            if m < 1: m, y = 12, y-1
            vm[0], vy[0] = m, y
            draw()

        def next_m():
            m, y = vm[0]+1, vy[0]
            if m > 12: m, y = 1, y+1
            vm[0], vy[0] = m, y
            draw()

        def pick(day):
            from datetime import date
            self._date = date(vy[0], vm[0], day)
            self._btn.config(text=str(self._date))
            popup.destroy()
            self._popup = None
            if self._on_change:
                self._on_change()

        draw()


# ─────────────────────────────────────────────────────────────────────────────
# TAB 6 — REPORTS
# ─────────────────────────────────────────────────────────────────────────────
class ReportsTab(ttk.Frame):
    """Attendance-hours report with one column per net type, plus a total column.
    Date range chosen via popup calendar pickers."""

    # Fixed columns; net-type columns are inserted dynamically at run time
    _FIXED = ('Callsign', 'Operator')
    _TOTAL = 'Total Hrs'

    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self._rows      = []
        self._col_names = list(self._FIXED) + [self._TOTAL]  # updated at run time
        self._sort_col  = None
        self._sort_rev  = False
        self._build()

    # ── Layout ────────────────────────────────────────────────────────────────
    def _build(self):
        tk.Label(self, text='STATION ATTENDANCE REPORT', font=FT,
                 fg=HDR_BG).pack(pady=(14, 4))

        # ── Date-range controls ───────────────────────────────────────────────
        ctrl = tk.Frame(self)
        ctrl.pack(fill='x', padx=20, pady=(0, 8))

        from datetime import date, timedelta
        today = date.today()

        tk.Label(ctrl, text='From:', font=FA).pack(side='left')
        self._from_picker = _DatePickerButton(ctrl, today - timedelta(days=90))
        self._from_picker.pack(side='left', padx=(4, 14))

        tk.Label(ctrl, text='To:', font=FA).pack(side='left')
        self._to_picker = _DatePickerButton(ctrl, today)
        self._to_picker.pack(side='left', padx=(4, 14))

        tk.Button(ctrl, text='  Run Report  ', font=FB,
                  bg=HDR_BG, fg='#FFF', padx=8, pady=3,
                  command=self._run).pack(side='left', padx=4)

        tk.Button(ctrl, text='Export CSV', font=FA, padx=6,
                  command=self._export_csv).pack(side='left', padx=4)

        self.v_status_lbl = tk.StringVar(value='')
        tk.Label(ctrl, textvariable=self.v_status_lbl,
                 font=FS, fg='#555').pack(side='left', padx=12)

        # ── Treeview container (columns built dynamically) ────────────────────
        tf = tk.Frame(self)
        tf.pack(fill='both', expand=True, padx=20, pady=(0, 8))
        tf.rowconfigure(0, weight=1)
        tf.columnconfigure(0, weight=1)

        self.tree = ttk.Treeview(tf, columns=self._col_names, show='headings',
                                 selectmode='browse', height=20)
        self._apply_col_config()

        self._vsb = ttk.Scrollbar(tf, orient='vertical',   command=self.tree.yview)
        self._hsb = ttk.Scrollbar(tf, orient='horizontal', command=self.tree.xview)
        self.tree.configure(yscrollcommand=self._vsb.set, xscrollcommand=self._hsb.set)
        self.tree.grid(row=0, column=0, sticky='nsew')
        self._vsb.grid(row=0, column=1, sticky='ns')
        self._hsb.grid(row=1, column=0, sticky='ew')

        self.tree.tag_configure('odd',   background=ROW_ODD)
        self.tree.tag_configure('evn',   background=ROW_EVN)
        self.tree.tag_configure('total', background='#CCD9EF',
                                font=(_FF, 10, 'bold'))

    def _apply_col_config(self):
        """Set heading/column properties for the current self._col_names list."""
        self.tree['columns'] = self._col_names
        for col in self._col_names:
            fixed = col in self._FIXED
            w   = 200 if col == 'Operator' else (110 if fixed else 90)
            anc = 'w' if fixed else 'e'
            self.tree.heading(col, text=col,
                              command=lambda c=col: self._sort(c))
            self.tree.column(col, width=w, minwidth=40, anchor=anc, stretch=False)

    # ── Report engine ─────────────────────────────────────────────────────────
    def _run(self):
        from_date = self._from_picker.date
        to_date   = self._to_picker.date
        if from_date > to_date:
            messagebox.showwarning('Invalid Range',
                '"From" date must be on or before "To" date.', parent=self)
            return
        from_str, to_str = str(from_date), str(to_date)

        conn = get_archive_db()

        sessions = conn.execute("""
            SELECT session_id, net_date, net_name, ncs_callsign, secured_time
            FROM   sessions
            WHERE  net_date BETWEEN ? AND ?
              AND  secured_time != ''
            ORDER  BY net_date, session_id
        """, (from_str, to_str)).fetchall()

        for item in self.tree.get_children():
            self.tree.delete(item)

        if not sessions:
            self.v_status_lbl.set('No archived sessions with a secured time in that range.')
            self._rows = []
            return

        # ── Collect all distinct net types (preserving insertion order → sorted) ─
        net_types_seen = {}
        for sess in sessions:
            nt = (sess['net_name'] or 'UNKNOWN').strip() or 'UNKNOWN'
            net_types_seen[nt] = True
        net_types = sorted(net_types_seen.keys())

        # station_data[cs] = {operator, net_hours: {net_type: float}, total: float}
        station_data = {}

        for sess in sessions:
            sid    = sess['session_id']
            ncs_cs = (sess['ncs_callsign'] or '').strip().upper()
            nt     = (sess['net_name'] or 'UNKNOWN').strip() or 'UNKNOWN'
            sec_str = (sess['secured_time'] or '').strip()

            if len(sec_str) == 4 and sec_str.isdigit():
                sec_h, sec_m = int(sec_str[:2]), int(sec_str[2:])
            else:
                continue

            if not ncs_cs:
                continue
            ncs_row = conn.execute("""
                SELECT MIN(checkin_time) AS ncs_start
                FROM   checkins
                WHERE  session_id = ? AND from_callsign = ?
                  AND  checkin_time != ''
            """, (sid, ncs_cs)).fetchone()

            ncs_start_str = ncs_row['ncs_start'] if ncs_row else None
            if not ncs_start_str:
                continue

            try:
                parts = ncs_start_str.split(':')
                ncs_h, ncs_m = int(parts[0]), int(parts[1])
            except (ValueError, IndexError):
                continue

            duration_h = (sec_h * 60 + sec_m - ncs_h * 60 - ncs_m) / 60.0
            if duration_h <= 0:
                continue

            checkin_rows = conn.execute("""
                SELECT DISTINCT from_callsign, from_operator
                FROM   checkins
                WHERE  session_id = ?
                  AND  checkin_time != ''
                  AND  from_callsign != ''
            """, (sid,)).fetchall()

            for row in checkin_rows:
                cs = (row['from_callsign'] or '').strip().upper()
                op = (row['from_operator'] or '').strip()
                if not cs:
                    continue
                if cs not in station_data:
                    station_data[cs] = {'operator': op,
                                        'net_hours': {nt: 0.0 for nt in net_types},
                                        'total': 0.0}
                elif op and not station_data[cs]['operator']:
                    station_data[cs]['operator'] = op
                station_data[cs]['net_hours'][nt] = (
                    station_data[cs]['net_hours'].get(nt, 0.0) + duration_h)
                station_data[cs]['total'] += duration_h

        if not station_data:
            self.v_status_lbl.set('No check-ins found in that date range.')
            self._rows = []
            return

        # ── Rebuild treeview columns: Callsign | Operator | <net types> | Total ─
        self._col_names = list(self._FIXED) + net_types + [self._TOTAL]
        self._apply_col_config()

        # Sort by total hours descending
        sorted_rows = sorted(station_data.items(), key=lambda x: -x[1]['total'])

        self._rows      = []
        grand_by_type   = {nt: 0.0 for nt in net_types}
        grand_total     = 0.0

        for i, (cs, d) in enumerate(sorted_rows):
            tag  = 'evn' if i % 2 else 'odd'
            vals = [cs, d['operator']]
            for nt in net_types:
                h = d['net_hours'].get(nt, 0.0)
                vals.append(f'{h:.1f}' if h else '')
                grand_by_type[nt] += h
            vals.append(f'{d["total"]:.1f}')
            grand_total += d['total']
            t = tuple(vals)
            self.tree.insert('', 'end', tag=tag, values=t)
            self._rows.append(t)

        # Totals row
        tot_vals = ['TOTAL', f'{len(station_data)} stations']
        for nt in net_types:
            tot_vals.append(f'{grand_by_type[nt]:.1f}')
        tot_vals.append(f'{grand_total:.1f}')
        t = tuple(tot_vals)
        self.tree.insert('', 'end', tag='total', values=t)
        self._rows.append(t)

        n_sess = len(sessions)
        self.v_status_lbl.set(
            f'{len(station_data)} stations  ·  {n_sess} session{"s" if n_sess != 1 else ""}  '
            f'·  {len(net_types)} net type{"s" if len(net_types) != 1 else ""}  '
            f'·  {grand_total:.1f} total hrs')
        self._sort_col = None

    # ── Column sort ───────────────────────────────────────────────────────────
    def _sort(self, col):
        items = [(self.tree.set(k, col), k)
                 for k in self.tree.get_children()
                 if 'total' not in (self.tree.item(k, 'tags') or ())]
        rev = (self._sort_col == col and not self._sort_rev)
        try:
            items.sort(key=lambda x: float(x[0]) if x[0] else -1e9, reverse=rev)
        except ValueError:
            items.sort(key=lambda x: x[0].lower(), reverse=rev)
        for idx, (_, k) in enumerate(items):
            self.tree.move(k, '', idx)
            self.tree.item(k, tags=('evn' if idx % 2 else 'odd',))
        for k in self.tree.get_children():
            if 'total' in (self.tree.item(k, 'tags') or ()):
                self.tree.move(k, '', 'end')
        self._sort_col = col
        self._sort_rev = rev

    # ── CSV export ────────────────────────────────────────────────────────────
    def _export_csv(self):
        if not self._rows:
            messagebox.showinfo('No Data', 'Run the report first.', parent=self)
            return
        path = filedialog.asksaveasfilename(
            title='Export Attendance Report',
            defaultextension='.csv',
            filetypes=[('CSV', '*.csv'), ('All', '*.*')],
            initialfile='attendance_report.csv',
            initialdir=BASE)
        if not path:
            return
        import csv
        with open(path, 'w', newline='', encoding='utf-8') as f:
            w = csv.writer(f)
            w.writerow(self._col_names)
            w.writerows(self._rows)
        self.app.set_status(f'Report exported → {os.path.basename(path)}')

    def refresh(self):
        pass


# ─────────────────────────────────────────────────────────────────────────────
# MAIN APPLICATION WINDOW
# ─────────────────────────────────────────────────────────────────────────────
class NetLogApp(tk.Tk):

    def __init__(self, load_path=None):
        super().__init__()

        # ── Opening screen / password gate ───────────────────────────────────
        self.title('USCG Auxiliary — HF Net Log')
        self.resizable(False, False)
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        W, H = LoginDialog._W, LoginDialog._H
        self.geometry(f'{W}x{H}+{(sw - W) // 2}+{(sh - H) // 2}')

        login = LoginDialog(self)
        login.pack(fill='both', expand=True)
        self.protocol('WM_DELETE_WINDOW', login._exit)
        self.wait_variable(login.v_done)   # runs event loop until authenticated or exited
        login.destroy()

        if not login.result:
            self.destroy()
            raise SystemExit(0)

        # ── Authenticated — build the full application ────────────────────────
        self.resizable(True, True)
        self.protocol('WM_DELETE_WINDOW', self.destroy)
        self.title('USCG Auxiliary Southeast HF Contingency Net Log')
        self.geometry('1220x840')
        self.minsize(950, 640)

        self.session      = NetSession()
        self.session.net_date = str(date.today())
        self.dist_matrix  = {}
        self.bear_matrix  = {}

        self._load_matrices()

        if load_path and os.path.exists(load_path):
            try:
                self.session = NetSession.load(load_path)
            except Exception as e:
                messagebox.showwarning('Load Error', f'Could not load {load_path}:\n{e}')

        self._build_title_bar()
        self._build_menu()
        self._build_notebook()
        self._build_status_bar()

        # Keyboard shortcuts (Control on Windows/Linux, Command on macOS)
        self.bind('<Control-s>', lambda _: self.log_tab._do_save())
        self.bind('<Control-n>', lambda _: self._new_session())
        self.bind('<F5>',        lambda _: self._refresh_all())
        self.bind('<Control-a>', lambda _: [self.notebook.select(0),
                                             self.log_tab.e_from.focus_set()])
        if sys.platform == 'darwin':   # macOS — Command key equivalents
            self.bind('<Command-s>', lambda _: self.log_tab._do_save())
            self.bind('<Command-n>', lambda _: self._new_session())
            self.bind('<Command-a>', lambda _: [self.notebook.select(0),
                                                self.log_tab.e_from.focus_set()])

        self.log_tab.update_session_display()

        db_n = get_db().execute(
            'SELECT COUNT(*) FROM members WHERE is_synthetic=0'
        ).fetchone()[0]
        d_n  = len(self.dist_matrix)
        b_n  = len(self.bear_matrix)
        mat_msg = (f'{d_n} distance / {b_n} bearing pairs loaded'
                   if d_n else 'D&B matrices not loaded — distance/bearing unavailable')
        self.set_status(f'Ready  |  {db_n} members in DB  |  {mat_msg}')

        if not self.session.net_name:
            self.notebook.select(1)  # Open Session Setup on first run

    # ── Matrix loading ────────────────────────────────────────────────────────
    def _load_matrices(self):
        """Try pre-decrypted xlsx first; fall back to live decryption of NetLog.xlsm."""
        decrypted = os.path.join(BASE, 'NetLog_decrypted.xlsx')
        if os.path.exists(decrypted):
            try:
                self.dist_matrix, self.bear_matrix = load_matrices(decrypted)
                return
            except Exception:
                pass

        encrypted = os.path.join(BASE, 'NetLog.xlsm')
        if not os.path.exists(encrypted):
            return
        try:
            import msoffcrypto, tempfile
            with open(encrypted, 'rb') as f:
                of = msoffcrypto.OfficeFile(f)
                of.load_key(password='thunder')
                buf = io.BytesIO()
                of.decrypt(buf)
            with tempfile.NamedTemporaryFile(
                    suffix='.xlsx', delete=False) as tmp:
                tmp.write(buf.getvalue())
                tmp_path = tmp.name
            self.dist_matrix, self.bear_matrix = load_matrices(tmp_path)
            os.unlink(tmp_path)
        except Exception:
            pass   # matrices stay empty — app still fully functional

    # ── UI construction ───────────────────────────────────────────────────────
    def _build_title_bar(self):
        tk.Label(self,
                 text='  USCG AUXILIARY SOUTHEAST HF CONTINGENCY NET LOG  ',
                 font=(_FF, 14, 'bold'),
                 bg=HDR_BG, fg=HDR_FG, pady=7).pack(fill='x')

    def _build_menu(self):
        mb = tk.Menu(self)
        self.config(menu=mb)

        fm = tk.Menu(mb, tearoff=0)
        mb.add_cascade(label='File', menu=fm)
        fm.add_command(label='New Session',
                       accelerator='Ctrl+N', command=self._new_session)
        fm.add_command(label='Load Session…',
                       command=self._load_session_dialog)
        fm.add_command(label='Save Session…',
                       accelerator='Ctrl+S',
                       command=lambda: self.log_tab._do_save())
        fm.add_separator()
        fm.add_command(label='Archive & Close Session',
                       command=lambda: self.log_tab._do_archive())
        fm.add_separator()
        fm.add_command(label='Exit', command=self.quit)

        sm = tk.Menu(mb, tearoff=0)
        mb.add_cascade(label='Session', menu=sm)
        sm.add_command(label='Edit Session Settings',
                       command=lambda: self.notebook.select(1))
        sm.add_command(label='Toggle Show My D&B',
                       command=lambda: [
                           self.log_tab.v_showdb.set(
                               not self.log_tab.v_showdb.get()),
                           self.log_tab._toggle_showdb()])
        sm.add_command(label='Toggle Manual Time',
                       command=lambda: [
                           self.log_tab.v_manual.set(
                               not self.log_tab.v_manual.get()),
                           self.log_tab._toggle_manual()])
        sm.add_separator()
        sm.add_command(label='Reset All Check-Ins', command=self._reset_checkins)

        mm = tk.Menu(mb, tearoff=0)
        mb.add_cascade(label='Members', menu=mm)
        mm.add_command(label='Browse Members',
                       command=lambda: self.notebook.select(2))
        mm.add_command(label='Add Member',
                       command=lambda: [self.notebook.select(2),
                                        self.members_tab._do_add()])

        hm = tk.Menu(mb, tearoff=0)
        mb.add_cascade(label='Help', menu=hm)
        hm.add_command(label='Keyboard Shortcuts', command=self._show_shortcuts)
        hm.add_separator()
        hm.add_command(label='About', command=self._show_about)

    def _build_notebook(self):
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill='both', expand=True, padx=4, pady=4)

        self.log_tab      = LogTab(self.notebook, self)
        self.session_tab  = SessionTab(self.notebook, self)
        self.members_tab  = MembersTab(self.notebook, self)
        self.archives_tab = ArchivesTab(self.notebook, self)

        self.maintenance_tab = MaintenanceTab(self.notebook, self)
        self.reports_tab     = ReportsTab(self.notebook, self)

        self.notebook.add(self.log_tab,          text='   Net Log   ')
        self.notebook.add(self.session_tab,      text='   Session Setup   ')
        self.notebook.add(self.members_tab,      text='   Members   ')
        self.notebook.add(self.archives_tab,     text='   Archives   ')
        self.notebook.add(self.maintenance_tab,  text='   Maintenance   ')
        self.notebook.add(self.reports_tab,      text='   Reports   ')

        self.notebook.bind('<<NotebookTabChanged>>', self._on_tab)

    def _build_status_bar(self):
        self.v_status = tk.StringVar(value='Ready')
        tk.Label(self, textvariable=self.v_status,
                 font=FS, relief=tk.SUNKEN, anchor='w',
                 padx=8, pady=3).pack(fill='x', side='bottom')

    # ── Tab-change handler ────────────────────────────────────────────────────
    def _on_tab(self, _evt):
        idx = self.notebook.index('current')
        if idx == 0:
            self.log_tab.update_session_display()
        elif idx == 2:
            self.members_tab.refresh()
        elif idx == 3:
            self.archives_tab.refresh()
        elif idx == 4:
            self.maintenance_tab.refresh()
        elif idx == 5:
            self.reports_tab.refresh()

    # ── App-level actions ─────────────────────────────────────────────────────
    def set_status(self, msg: str):
        self.v_status.set(msg)

    def _new_session(self):
        if self.session.checkins:
            if not messagebox.askyesno(
                    'New Session',
                    'Start a new session?\n'
                    'Current check-ins will be lost unless you save first.'):
                return
        self.session = NetSession()
        self.session.net_date = str(date.today())
        self.log_tab.update_session_display()
        self.session_tab.load_from_session()
        self.notebook.select(1)
        self.set_status('New session — fill in Session Setup then switch to Net Log.')

    def _load_session_dialog(self):
        path = filedialog.askopenfilename(
            title='Load Session',
            filetypes=[('JSON', '*.json'), ('All', '*.*')],
            initialdir=BASE)
        if not path:
            return
        try:
            self.session = NetSession.load(path)
            self.log_tab.update_session_display()
            self.session_tab.load_from_session()
            self.notebook.select(0)
            self.set_status(
                f'Loaded {os.path.basename(path)} — {len(self.session.checkins)} check-ins')
        except Exception as e:
            messagebox.showerror('Load Error', str(e))

    def _reset_checkins(self):
        if messagebox.askyesno('Reset Check-Ins',
                               'Clear all check-ins for this session?'):
            self.session.checkins = []
            self.log_tab.refresh_table()
            self.set_status('All check-ins cleared.')

    def _refresh_all(self):
        self.log_tab.update_session_display()
        self.members_tab.refresh()
        self.set_status('Refreshed.')

    def _show_shortcuts(self):
        messagebox.showinfo('Keyboard Shortcuts',
            'Ctrl+A  — Focus FROM callsign field\n'
            'Ctrl+S  — Save session\n'
            'Ctrl+N  — New session\n'
            'F5      — Refresh all\n\n'
            'In FROM field:\n'
            '  Enter  — Lookup + move to TO field\n'
            '  Tab    — Lookup callsign\n\n'
            'Check-in table:\n'
            '  Right-click — Delete check-in')

    def _show_about(self):
        messagebox.showinfo('About',
            'USCG Auxiliary Southeast District 7\n'
            'HF Contingency Net Log  —  GUI Edition\n\n'
            'Data layer:   netlog.py\n'
            'Member DB:    netlog.db       (SQLite)\n'
            'Archives DB:  net_archives.db (SQLite)\n\n'
            'python netlog_gui.py\n'
            'python netlog_gui.py --load session.json')


# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────
def main():
    args = sys.argv[1:]
    load_path = None
    if '--load' in args:
        idx = args.index('--load')
        if idx + 1 < len(args):
            load_path = args[idx + 1]
    app = NetLogApp(load_path=load_path)
    app.mainloop()


if __name__ == '__main__':
    main()
