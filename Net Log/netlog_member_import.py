#!/usr/bin/env python3
"""
NetLog Member Import — HF Registry Sync
Reads a password-protected Excel HF Registry file and synchronises the
netlog.db members table.

Column mapping (Excel → members table):
  HF International Call Sign → callsign
  Facility: Owner Name + " (" + callsign + ")" → operator_name
  Location  (split on ",")  → city, state
  Unit Name: Sector         → sector  (stripped / mapped to D7 values)
  Latitude  (signed)        → Latitude abs(), Lat_Dir N/S
  Longitude (signed)        → Longitude abs(), Long_Dir E/W

Both "HF operational" and "HF non-operational" sheets are processed.
Operational records take precedence when a callsign appears on both sheets.
Only rows with a non-empty HF International Call Sign are imported.
Non-synthetic DB rows whose callsign is absent from the Excel are deleted.
"""

import io
import os
import sqlite3
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

# ── dependency check ──────────────────────────────────────────────────────────

def _require(pkg, install_name=None):
    try:
        return __import__(pkg)
    except ImportError:
        name = install_name or pkg
        messagebox.showerror(
            "Missing Dependency",
            f"The '{name}' package is required but not installed.\n\n"
            f"Run:  pip install {name}"
        )
        sys.exit(1)

# ── colours / fonts ───────────────────────────────────────────────────────────

HDR_BG   = '#1A3A5C'
HDR_FG   = '#FFFFFF'
BTN_OK   = '#1A6B3C'
BTN_WARN = '#8B0000'
ROW_INS  = '#D4EDDA'   # light green
ROW_UPD  = '#FFF3CD'   # light yellow
ROW_DEL  = '#F8D7DA'   # light red
ROW_NC   = '#F0F0F0'   # grey  (no change)
_FF      = 'Segoe UI'

# ── sector mapping ────────────────────────────────────────────────────────────

_D7_SECTORS = {
    'CHARLESTON':    'CHARLESTON',
    'JACKSONVILLE':  'JACKSONVILLE',
    'MIAMI':         'MIAMI',
    'KEY WEST':      'KEY WEST',
    'SAN JUAN':      'SAN JUAN',
    'ST. PETERSBURG':'ST. PETERSBURG',
    'ST PETERSBURG': 'ST. PETERSBURG',
}

def _map_sector(raw) -> str:
    if not raw:
        return ''
    s = str(raw).upper().strip()
    if s.startswith('SECTOR '):
        s = s[7:]
    return _D7_SECTORS.get(s, 'OTHER DISTRICTS')


# ── Excel reader ──────────────────────────────────────────────────────────────

def _col(headers: list, name: str) -> int:
    """Return column index or -1 if absent."""
    try:
        return headers.index(name)
    except ValueError:
        return -1


def load_excel(path: str, password: str) -> dict:
    """
    Return {callsign_upper: record_dict} from both sheets.
    Operational rows win on duplicate callsign.
    """
    msoffcrypto = _require('msoffcrypto')
    openpyxl    = _require('openpyxl')

    with open(path, 'rb') as fh:
        office_file = msoffcrypto.OfficeFile(fh)
        office_file.load_key(password=password)
        buf = io.BytesIO()
        office_file.decrypt(buf)

    buf.seek(0)
    wb = openpyxl.load_workbook(buf, read_only=True, data_only=True)

    records = {}   # callsign → dict, operational wins

    # Process non-operational first so operational can overwrite
    for sheet_order in ['HF non-operational', 'HF operational']:
        if sheet_order not in wb.sheetnames:
            continue
        ws   = wb[sheet_order]
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            continue
        hdrs = list(rows[0])

        hf_col  = _col(hdrs, 'HF International Call Sign')
        own_col = _col(hdrs, 'Facility: Owner Name')
        loc_col = _col(hdrs, 'Location')
        sec_col = _col(hdrs, 'Unit Name: Sector')
        lat_col = _col(hdrs, 'Latitude')
        lon_col = _col(hdrs, 'Longitude')

        if hf_col < 0:
            continue  # sheet has no HF callsign column

        for row in rows[1:]:
            cs_raw = row[hf_col]
            if not cs_raw:
                continue
            cs = str(cs_raw).strip().upper()
            if not cs:
                continue

            owner = str(row[own_col]).strip() if own_col >= 0 and row[own_col] else ''
            name  = f"{owner} ({cs})" if owner else cs

            # Location → city, state
            city = state = ''
            if loc_col >= 0 and row[loc_col]:
                loc_str = str(row[loc_col]).strip()
                if ',' in loc_str:
                    parts = loc_str.split(',', 1)
                    city  = parts[0].strip()
                    state = parts[1].strip()
                else:
                    city = loc_str

            sector = _map_sector(row[sec_col]) if sec_col >= 0 else ''

            lat_raw = row[lat_col] if lat_col >= 0 else None
            lon_raw = row[lon_col] if lon_col >= 0 else None

            lat = lat_dir = None
            if lat_raw is not None:
                try:
                    lv    = float(lat_raw)
                    lat   = round(abs(lv), 4)
                    lat_dir = 'N' if lv >= 0 else 'S'
                except (ValueError, TypeError):
                    pass

            lon = lon_dir = None
            if lon_raw is not None:
                try:
                    lv    = float(lon_raw)
                    lon   = round(abs(lv), 4)
                    lon_dir = 'E' if lv >= 0 else 'W'
                except (ValueError, TypeError):
                    pass

            records[cs] = {
                'callsign':      cs,
                'operator_name': name,
                'city':          city,
                'state':         state,
                'sector':        sector,
                'Latitude':      lat,
                'Lat_Dir':       lat_dir,
                'Longitude':     lon,
                'Long_Dir':      lon_dir,
            }

    wb.close()
    return records


# ── diff / preview ────────────────────────────────────────────────────────────

def build_diff(db_path: str, excel_records: dict) -> list:
    """
    Return list of dicts:
      action  : 'INSERT' | 'UPDATE' | 'DELETE' | 'NO CHANGE'
      + all record fields
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    db_rows = {
        str(r['callsign']).upper(): dict(r)
        for r in conn.execute(
            "SELECT callsign, operator_name, city, state, sector, "
            "Latitude, Lat_Dir, Longitude, Long_Dir, is_synthetic "
            "FROM members"
        ).fetchall()
    }
    conn.close()

    diff = []

    for cs, rec in sorted(excel_records.items()):
        if cs not in db_rows:
            diff.append({'action': 'INSERT', **rec})
        else:
            existing = db_rows[cs]
            changed = any(
                str(rec.get(k) or '') != str(existing.get(k) or '')
                for k in ('operator_name', 'city', 'state', 'sector',
                           'Latitude', 'Lat_Dir', 'Longitude', 'Long_Dir')
            )
            diff.append({'action': 'UPDATE' if changed else 'NO CHANGE', **rec})

    for cs, row in sorted(db_rows.items()):
        if cs not in excel_records and not row.get('is_synthetic'):
            diff.append({'action': 'DELETE',
                         'callsign':      cs,
                         'operator_name': row.get('operator_name', ''),
                         'city':          row.get('city', ''),
                         'state':         row.get('state', ''),
                         'sector':        row.get('sector', '')})

    return diff


def apply_diff(db_path: str, diff: list):
    """Write INSERT / UPDATE / DELETE actions to the database."""
    conn = sqlite3.connect(db_path)
    inserts = updates = deletes = 0
    try:
        for rec in diff:
            action = rec['action']
            cs     = rec['callsign']
            if action == 'INSERT':
                conn.execute(
                    """INSERT INTO members
                       (callsign, operator_name, city, state, sector,
                        Latitude, Lat_Dir, Longitude, Long_Dir)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (cs, rec.get('operator_name'), rec.get('city'),
                     rec.get('state'), rec.get('sector'),
                     rec.get('Latitude'), rec.get('Lat_Dir'),
                     rec.get('Longitude'), rec.get('Long_Dir'))
                )
                inserts += 1
            elif action == 'UPDATE':
                conn.execute(
                    """UPDATE members SET
                       operator_name=?, city=?, state=?, sector=?,
                       Latitude=?, Lat_Dir=?, Longitude=?, Long_Dir=?
                       WHERE UPPER(callsign)=?""",
                    (rec.get('operator_name'), rec.get('city'),
                     rec.get('state'), rec.get('sector'),
                     rec.get('Latitude'), rec.get('Lat_Dir'),
                     rec.get('Longitude'), rec.get('Long_Dir'),
                     cs)
                )
                updates += 1
            elif action == 'DELETE':
                conn.execute(
                    "DELETE FROM members WHERE UPPER(callsign)=? AND is_synthetic=0",
                    (cs,)
                )
                deletes += 1
        conn.commit()
    finally:
        conn.close()
    return inserts, updates, deletes


# ── GUI ───────────────────────────────────────────────────────────────────────

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('NetLog Member Import — HF Registry Sync')
        self.resizable(True, True)
        self.minsize(900, 620)
        self._diff = []
        self._excel_records = {}
        self._build()
        self._auto_fill_db()

    # ── layout ────────────────────────────────────────────────────────────────

    def _build(self):
        # ── header ────────────────────────────────────────────────────────────
        hdr = tk.Frame(self, bg=HDR_BG)
        hdr.pack(fill='x')
        tk.Label(hdr, text='NetLog Member Import', font=(_FF, 14, 'bold'),
                 bg=HDR_BG, fg=HDR_FG, pady=8).pack(side='left', padx=12)
        tk.Label(hdr, text='HF Registry → netlog.db sync',
                 font=(_FF, 9), bg=HDR_BG, fg='#AAC8E8').pack(side='left')

        # ── input panel ───────────────────────────────────────────────────────
        inp = tk.LabelFrame(self, text='Source & Destination',
                            font=(_FF, 9, 'bold'), padx=8, pady=6)
        inp.pack(fill='x', padx=10, pady=(8, 0))

        self._build_file_row(inp, 0, 'Excel File:',
                             self._browse_excel, '_v_excel')
        self._build_pw_row(inp, 1)
        self._build_file_row(inp, 2, 'Database (netlog.db):',
                             self._browse_db, '_v_db')

        # ── action buttons ────────────────────────────────────────────────────
        bf = tk.Frame(self, pady=6)
        bf.pack(fill='x', padx=10)

        self._btn_preview = tk.Button(
            bf, text='  Load & Preview  ', font=(_FF, 10, 'bold'),
            bg=HDR_BG, fg='white', relief='flat', padx=10, pady=5,
            command=self._do_preview)
        self._btn_preview.pack(side='left', padx=(0, 8))

        self._btn_apply = tk.Button(
            bf, text='  Apply Import  ', font=(_FF, 10, 'bold'),
            bg=BTN_OK, fg='white', relief='flat', padx=10, pady=5,
            state='disabled', command=self._do_apply)
        self._btn_apply.pack(side='left', padx=(0, 8))

        tk.Button(bf, text='Clear', font=(_FF, 9),
                  padx=6, pady=4, command=self._do_clear).pack(side='left')

        # ── summary labels ────────────────────────────────────────────────────
        sf = tk.Frame(self)
        sf.pack(fill='x', padx=10, pady=(0, 2))
        self._lbl_summary = tk.Label(sf, text='', font=(_FF, 9),
                                     fg='#333', anchor='w')
        self._lbl_summary.pack(side='left')

        # ── preview treeview ─────────────────────────────────────────────────
        pf = tk.LabelFrame(self, text='Preview', font=(_FF, 9, 'bold'),
                           padx=4, pady=4)
        pf.pack(fill='both', expand=True, padx=10, pady=(0, 4))
        pf.rowconfigure(0, weight=1)
        pf.columnconfigure(0, weight=1)

        cols = ('action', 'callsign', 'name', 'city', 'state',
                'sector', 'lat', 'lon')
        hdgs = ('Action', 'Callsign', 'Name', 'City', 'ST',
                'Sector', 'Lat', 'Lon')
        widths = (80, 90, 260, 130, 36, 140, 75, 75)

        self.tree = ttk.Treeview(pf, columns=cols, show='headings',
                                 selectmode='browse')
        for c, h, w in zip(cols, hdgs, widths):
            self.tree.heading(c, text=h)
            self.tree.column(c, width=w, stretch=False, minwidth=30)

        vsb = ttk.Scrollbar(pf, orient='vertical',   command=self.tree.yview)
        hsb = ttk.Scrollbar(pf, orient='horizontal', command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self.tree.grid(row=0, column=0, sticky='nsew')
        vsb.grid(row=0, column=1, sticky='ns')
        hsb.grid(row=1, column=0, sticky='ew')

        for tag, bg in [('INSERT',    ROW_INS),
                        ('UPDATE',    ROW_UPD),
                        ('DELETE',    ROW_DEL),
                        ('NO CHANGE', ROW_NC)]:
            self.tree.tag_configure(tag, background=bg)

        # ── status bar ────────────────────────────────────────────────────────
        self._status = tk.StringVar(value='Ready.')
        tk.Label(self, textvariable=self._status, anchor='w',
                 font=(_FF, 8), fg='#555', relief='sunken',
                 padx=6).pack(fill='x', side='bottom')

    def _build_file_row(self, parent, row, label, cmd, attr):
        tk.Label(parent, text=label, font=(_FF, 9),
                 width=22, anchor='e').grid(row=row, column=0,
                                            sticky='e', pady=3, padx=(0, 4))
        var = tk.StringVar()
        setattr(self, attr, var)
        ent = tk.Entry(parent, textvariable=var, font=(_FF, 9), width=60)
        ent.grid(row=row, column=1, sticky='ew', pady=3)
        tk.Button(parent, text='Browse…', font=(_FF, 8),
                  command=cmd).grid(row=row, column=2, padx=4)
        parent.columnconfigure(1, weight=1)

    def _build_pw_row(self, parent, row):
        tk.Label(parent, text='Password:', font=(_FF, 9),
                 width=22, anchor='e').grid(row=row, column=0,
                                            sticky='e', pady=3, padx=(0, 4))
        self._v_pw = tk.StringVar()
        tk.Entry(parent, textvariable=self._v_pw, show='*',
                 font=(_FF, 9), width=30).grid(row=row, column=1,
                                               sticky='w', pady=3)

    # ── helpers ───────────────────────────────────────────────────────────────

    def _auto_fill_db(self):
        """Pre-fill the DB path to netlog.db in the same folder as this script."""
        here = os.path.dirname(os.path.abspath(__file__))
        candidate = os.path.join(here, 'netlog.db')
        self._v_db.set(candidate)

    def _browse_excel(self):
        p = filedialog.askopenfilename(
            title='Select HF Registry Excel file',
            filetypes=[('Excel files', '*.xlsx *.xlsm *.xls'),
                       ('All files', '*.*')])
        if p:
            self._v_excel.set(p)

    def _browse_db(self):
        p = filedialog.askopenfilename(
            title='Select netlog.db',
            filetypes=[('SQLite databases', '*.db'), ('All files', '*.*')])
        if p:
            self._v_db.set(p)

    def _set_status(self, msg):
        self._status.set(msg)
        self.update_idletasks()

    # ── actions ───────────────────────────────────────────────────────────────

    def _do_preview(self):
        xlsx  = self._v_excel.get().strip()
        pw    = self._v_pw.get()
        dbp   = self._v_db.get().strip()

        if not xlsx or not os.path.isfile(xlsx):
            messagebox.showerror('Error', 'Please select a valid Excel file.')
            return
        if not dbp or not os.path.isfile(dbp):
            messagebox.showerror('Error', 'Please select a valid netlog.db file.')
            return

        self._set_status('Loading Excel file…')
        try:
            excel_recs = load_excel(xlsx, pw)
        except Exception as exc:
            messagebox.showerror('Load Error',
                                 f'Failed to open Excel file:\n{exc}')
            self._set_status('Load failed.')
            return

        self._set_status('Building diff…')
        try:
            diff = build_diff(dbp, excel_recs)
        except Exception as exc:
            messagebox.showerror('Database Error',
                                 f'Failed to read database:\n{exc}')
            self._set_status('Preview failed.')
            return

        self._excel_records = excel_recs
        self._diff = diff
        self._populate_tree(diff)
        self._btn_apply.config(state='normal')
        self._set_status(
            f'Preview loaded — {len(excel_recs)} Excel records processed.')

    def _populate_tree(self, diff: list):
        for item in self.tree.get_children():
            self.tree.delete(item)

        counts = {'INSERT': 0, 'UPDATE': 0, 'DELETE': 0, 'NO CHANGE': 0}
        for rec in diff:
            action = rec['action']
            counts[action] = counts.get(action, 0) + 1

            lat_s = ''
            if rec.get('Latitude') is not None:
                lat_s = f"{rec['Latitude']:.4f} {rec.get('Lat_Dir','')}"
            lon_s = ''
            if rec.get('Longitude') is not None:
                lon_s = f"{rec['Longitude']:.4f} {rec.get('Long_Dir','')}"

            self.tree.insert('', 'end', tags=(action,), values=(
                action,
                rec.get('callsign', ''),
                rec.get('operator_name', ''),
                rec.get('city', ''),
                rec.get('state', ''),
                rec.get('sector', ''),
                lat_s,
                lon_s,
            ))

        total = sum(counts.values())
        self._lbl_summary.config(
            text=(f"Total: {total}   "
                  f"INSERT: {counts['INSERT']}   "
                  f"UPDATE: {counts['UPDATE']}   "
                  f"DELETE: {counts['DELETE']}   "
                  f"No Change: {counts['NO CHANGE']}")
        )

    def _do_apply(self):
        if not self._diff:
            return

        counts = {a: sum(1 for r in self._diff if r['action'] == a)
                  for a in ('INSERT', 'UPDATE', 'DELETE')}
        msg = (f"Apply the following changes to netlog.db?\n\n"
               f"  INSERT:  {counts['INSERT']} new members\n"
               f"  UPDATE:  {counts['UPDATE']} existing members\n"
               f"  DELETE:  {counts['DELETE']} members not in Excel\n\n"
               f"Deleted members cannot be recovered unless you have a backup.")
        if not messagebox.askyesno('Confirm Import', msg, icon='warning'):
            return

        dbp = self._v_db.get().strip()
        self._set_status('Applying changes…')
        try:
            ins, upd, dlt = apply_diff(dbp, self._diff)
        except Exception as exc:
            messagebox.showerror('Database Error',
                                 f'Import failed:\n{exc}')
            self._set_status('Import failed.')
            return

        messagebox.showinfo(
            'Import Complete',
            f'Import finished successfully.\n\n'
            f'  Inserted: {ins}\n'
            f'  Updated:  {upd}\n'
            f'  Deleted:  {dlt}'
        )
        self._set_status(
            f'Done — inserted {ins}, updated {upd}, deleted {dlt}.')
        self._btn_apply.config(state='disabled')
        # Refresh preview to show all as NO CHANGE
        self._do_preview()

    def _do_clear(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        self._diff = []
        self._excel_records = {}
        self._btn_apply.config(state='disabled')
        self._lbl_summary.config(text='')
        self._set_status('Ready.')


# ── entry point ───────────────────────────────────────────────────────────────

if __name__ == '__main__':
    app = App()
    app.mainloop()
