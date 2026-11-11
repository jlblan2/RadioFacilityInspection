"""Printable outputs for the VHF Watch Log.

All reports are self-contained HTML pages (print them from the browser, or "Save as PDF"),
plus a CSV for the ANSC 7030 activity report. Standard library only.
"""
import csv
import os
import html

from watchdb import APP, fmt_local, fmt_z, from_iso, station_lines, tz_abbr


def _station_block(station):
    """Radio station details printed under the watchstander signature."""
    lines = station_lines(station)
    if not lines:
        return ""
    width = {"Call sign": 17, "Address": 38, "Lat / Long": 29, "Contact phone": 16}
    cells = "".join(f"<td style='width:{width.get(k, 20)}%'><span class='lbl'>{esc(k)}</span>{esc(v)}</td>"
                    for k, v in lines)
    return f"<div class='stn'><div class='stnh'>Radio station</div><table class='hdr'><tr>{cells}</tr></table></div>"

CSS = """
@page { size: letter; margin: 0.5in;
        @bottom-right { content: "Page " counter(page) " of " counter(pages); font: 9pt Arial; } }
body { font: 10pt Arial, Helvetica, sans-serif; color: #000; margin: 0; }
h1 { font-size: 15pt; margin: 0 0 4px; }
h2 { font-size: 11pt; margin: 14px 0 4px; border-bottom: 1.5px solid #000; }
table { border-collapse: collapse; width: 100%; page-break-inside: auto; }
tr { page-break-inside: avoid; }
th, td { border: 1px solid #000; padding: 3px 5px; vertical-align: top; text-align: left; }
th { background: #e6e6e6; font-weight: bold; }
.hdr td { border: 1px solid #000; }
.lbl { font-size: 8pt; color: #333; display: block; }
.big { font-size: 12pt; font-weight: bold; }
.struck td { text-decoration: line-through; color: #555; }
.struck td.init { text-decoration: none; }
.dist td { font-weight: bold; }
.small { font-size: 8.5pt; }
.sig { margin-top: 18px; width: 100%; }
.sig td { border: none; padding-top: 22px; }
.sig span { display: inline-block; border-top: 1px solid #000; min-width: 260px; padding-top: 2px; font-size: 8.5pt; }
.stn { margin-top: 12px; break-inside: avoid; }
.stnh { font-weight: bold; font-size: 9.5pt; margin-bottom: 2px; }
.contacts { columns: 2; font-size: 9pt; margin-top: 4px; }
.contacts div { break-inside: avoid; }
.note { font-size: 8.5pt; color: #333; margin-top: 6px; }
@media screen { body { margin: 20px auto; max-width: 8in; } }
"""


def esc(v):
    return html.escape("" if v is None else str(v))


def _page(title, body):
    return (f"<!doctype html><html><head><meta charset='utf-8'><title>{esc(title)}</title>"
            f"<style>{CSS}</style></head><body>{body}</body></html>")


def _local_date(iso, tz=None):
    dt = from_iso(iso)
    return dt.astimezone(tz).strftime("%m/%d/%Y") if dt else ""


def _tzname(iso, tz=None):
    dt = from_iso(iso)
    return tz_abbr(dt.astimezone(tz)) if dt else "local"


def gar_text(db, pid):
    """A patrol's GAR as printed: '22', or '22 → 18 (1415Z)' when it changed during the patrol."""
    hist = db.gar_history(pid)
    return hist[0][1] + "".join(f" → {g} ({fmt_z(t)})" for t, g, _ in hist[1:])


def _crew_cell(c):
    name = f"{esc(c['last_name'])} ({esc(c['member_no'])})"
    return name + ("<br><span class='small'>(Trainee)</span>" if c["trainee"] else "")


def watch_log_html(db, wid, tz=None):
    """The radio log for one watch, laid out like the paper form it replaces."""
    w = db.watch(wid)
    if w is None:
        raise ValueError("No such watch")
    patrols = db.patrols(wid)
    entries = db.entries(wid)
    print_cell = db.setting("print_cell_numbers") == "1"
    tzn = _tzname(w["opened_utc"], tz)
    out = [f"<h1>Auxiliary Radio — VHF Communications Watch Log</h1>"]
    out.append(
        "<table class='hdr'><tr>"
        f"<td><span class='lbl'>Station call sign</span><span class='big'>{esc(w['station_call_sign'])}</span></td>"
        f"<td><span class='lbl'>Date (local)</span><span class='big'>{_local_date(w['opened_utc'], tz)}</span></td>"
        f"<td><span class='lbl'>Channels guarded</span>{esc(w['channels'])}</td>"
        f"<td><span class='lbl'>Watchstander</span>{esc(w['watchstander'])}</td></tr><tr>"
        f"<td><span class='lbl'>Watch opened</span>{fmt_z(w['opened_utc'])} / {fmt_local(w['opened_utc'], tz)} {esc(tzn)}</td>"
        f"<td><span class='lbl'>Watch closed</span>{fmt_z(w['closed_utc'])}"
        f"{' / ' + fmt_local(w['closed_utc'], tz) + ' ' + esc(tzn) if w['closed_utc'] else ''}</td>"
        f"<td><span class='lbl'>Time check</span>{esc(w['time_check'])}</td>"
        f"<td><span class='lbl'>Radio check / equipment</span>{esc(w['radio_check'])}"
        f"{'<br>' + esc(w['equipment_status']) if w['equipment_status'] else ''}</td></tr></table>")

    out.append("<h2>Vessels on patrol</h2>")
    if not patrols:
        out.append("<p class='small'>No patrols this watch.</p>")
    for i, p in enumerate(patrols, 1):
        crew = db.patrol_crew(p["id"])
        cox = [c for c in crew if c["role"] == "COX"]
        mates = [c for c in crew if c["role"] == "CREW"]
        ncols = max(4, 1 + len(mates))
        width = f"{100 / ncols:.1f}%"
        row1 = [
            f"<th style='width:{width}'>Vessel # {i}</th><th>Patrol order</th><th>GAR</th><th>POB</th>",
        ]
        if print_cell:
            row1.append("<th>Cell #</th>")
        out.append("<table style='margin-bottom:6px'>")
        out.append("<tr>" + "".join(row1) + "</tr>")
        vals = (f"<td class='big'>{esc(p['facility_no'])} {esc(p['vessel_name'])}"
                f"{'<br><span class=small>' + esc(p['call_sign']) + '</span>' if p['call_sign'] else ''}</td>"
                f"<td class='big'>{esc(p['patrol_order'])}</td><td>{esc(gar_text(db, p['id']))}</td>"
                f"<td>{esc(p['pob'])}</td>")
        if print_cell:
            vals += f"<td>{esc(p['cell'])}</td>"
        out.append(f"<tr>{vals}</tr></table>")
        out.append("<table style='margin-bottom:6px'><tr><th>COX</th>"
                   + "".join("<th>CREW</th>" for _ in mates) + "</tr><tr>"
                   + (f"<td><b>{_crew_cell(cox[0])}</b></td>" if cox else "<td></td>")
                   + "".join(f"<td>{_crew_cell(c)}</td>" for c in mates) + "</tr></table>")
        extra = []
        if p["start_utc"]:
            extra.append(f"Underway {fmt_z(p['start_utc'])} / {fmt_local(p['start_utc'], tz)}")
        if p["end_utc"]:
            extra.append(f"Moored {fmt_z(p['end_utc'])} / {fmt_local(p['end_utc'], tz)}")
        extra.append(f"Ops normal every {p['interval_min']} min")
        if p["mission"]:
            extra.append(f"Mission: {esc(p['mission'])}")
        if p["float_plan"]:
            extra.append(f"Float plan / area: {esc(p['float_plan'])}")
        out.append(f"<div class='small' style='margin-bottom:10px'>{' &nbsp;·&nbsp; '.join(extra)}</div>")

    out.append("<h2>Radio log</h2>")
    out.append(f"<table><tr><th style='width:9%'>Time Z</th><th style='width:8%'>{esc(tzn)}</th>"
               "<th style='width:10%'>Vessel / station</th><th style='width:6%'>Ch</th>"
               "<th style='width:12%'>Type</th><th>Position / heading</th><th>Status / remarks</th>"
               "<th style='width:7%'>Init</th></tr>")
    by_id = {e["id"]: e for e in entries}
    changes = db.gar_changes(wid)
    for e in entries:
        cls = "struck" if e["struck"] else ("dist" if e["entry_type"] in ("DISTRESS", "URGENCY") else "")
        pos = esc(e["position"])
        if e["heading"]:
            pos += f"{'<br>' if pos else ''}Hdg: {esc(e['heading'])}"
        rem = esc(e["status"])
        if e["remarks"]:
            rem += f"{'<br>' if rem else ''}{esc(e['remarks'])}"
        if e["id"] in changes:
            old, new = changes[e["id"]]
            rem += f"{'<br>' if rem else ''}<b>GAR changed {esc(old or '-')} → {esc(new)}</b>"
        if e["corrects_id"] and e["corrects_id"] in by_id:
            rem += f"{'<br>' if rem else ''}<i class='small'>(corrects entry at {fmt_z(by_id[e['corrects_id']]['time_utc'])})</i>"
        who = esc(e["station"]) + (" (to)" if e["direction"] == "TO" else "")
        out.append(f"<tr class='{cls}'><td>{fmt_z(e['time_utc'])}</td><td>{fmt_local(e['time_utc'], tz)}</td>"
                   f"<td>{who}</td><td>{esc(e['channel'])}</td><td>{esc(e['entry_type'])}</td>"
                   f"<td>{pos}</td><td>{rem}</td><td class='init'>{esc(e['struck_by'])}</td></tr>")
    if not entries:
        out.append("<tr><td colspan='8'>No entries.</td></tr>")
    out.append("</table>")
    if w["notes"]:
        out.append(f"<h2>Notes</h2><p>{esc(w['notes'])}</p>")

    status = (f"Closed and locked {fmt_z(w['closed_utc'])}; signed: {esc(w['signed_by'])}"
              if w["locked"] else "WATCH STILL OPEN — not signed")
    retain = db.retain_until(wid) if w["locked"] else None
    out.append("<table class='sig'><tr><td><span>Watchstander signature</span></td>"
               "<td><span>Relieved by / reviewed by</span></td></tr></table>")
    out.append(_station_block(db.watch_station(wid)))
    out.append(f"<div class='note'>{status}. "
               + (f"Retain until {retain:%d %b %Y} (AOPG Vol III 3-F.3"
                  f"{'; distress traffic logged — 3 years' if db.has_distress(wid) else ''}). " if retain else "")
               + "Struck-through entries were corrected by the person whose initials appear. "
               "Times are UTC (Z) with local time alongside.</div>")
    out.append(_contacts_block(db))
    return _page(f"Watch Log {_local_date(w['opened_utc'], tz)} {w['station_call_sign']}", "".join(out))


def _contacts_block(db):
    rows = db.contacts()
    if not rows:
        return ""
    items = "".join(f"<div><b>{esc(c['name'])}</b> {esc(c['phone'])}"
                    f"{' <i>(' + esc(c['notes']) + ')</i>' if c['notes'] else ''}</div>" for c in rows)
    return f"<h2>Contacts</h2><div class='contacts'>{items}</div>"


def contact_sheet_html(db):
    rows = "".join(f"<tr><td>{esc(c['name'])}</td><td>{esc(c['phone'])}</td><td>{esc(c['notes'])}</td></tr>"
                   for c in db.contacts())
    chans = "".join(f"<tr><td>{esc(c['name'])}</td><td>{esc(c['purpose'])}</td></tr>" for c in db.channels())
    body = (f"<h1>{esc(db.setting('station_call_sign'))} — Contacts and channels</h1>"
            f"<h2>Telephone</h2><table><tr><th>Name</th><th>Phone</th><th>Notes</th></tr>{rows}</table>"
            f"<h2>VHF channels</h2><table><tr><th>Channel</th><th>Use</th></tr>{chans}</table>")
    return _page("Contact Sheet", body)


def ics309_html(db, wid, incident=None, tz=None):
    """FEMA ICS 309 Communications Log built from the watch."""
    w = db.watch(wid)
    entries = [e for e in db.entries(wid) if not e["struck"]]
    station = w["station_call_sign"]
    incident = incident or db.setting("incident_name")
    start = from_iso(w["opened_utc"])
    end = from_iso(w["closed_utc"]) if w["closed_utc"] else None

    def dt(d):
        return f"{d.astimezone(tz):%m/%d/%Y %H%M} ({d:%H%MZ})" if d else ""

    rows = []
    changes = db.gar_changes(wid)
    for e in entries:
        frm, to = (e["station"], station) if e["direction"] == "FROM" else (station, e["station"])
        msg = e["entry_type"]
        gar = f"GAR changed {changes[e['id']][0] or '-'} → {changes[e['id']][1]}" if e["id"] in changes else ""
        for part in (e["position"], ("Hdg " + e["heading"]) if e["heading"] else "", e["status"], e["remarks"], gar):
            if part:
                msg += " — " + part
        rows.append(f"<tr><td>{fmt_local(e['time_utc'], tz)}<br><span class='small'>{fmt_z(e['time_utc'])}</span></td>"
                    f"<td>{esc(frm)}</td><td></td><td>{esc(to)}</td><td></td><td>{esc(msg)}</td></tr>")
    body = (
        "<h1>COMMUNICATIONS LOG (ICS 309)</h1>"
        "<table class='hdr'><tr>"
        f"<td><span class='lbl'>1. Incident Name</span>{esc(incident)}</td>"
        f"<td colspan='2'><span class='lbl'>2. Operational Period</span>From: {dt(start)}<br>To: {dt(end)}</td></tr><tr>"
        f"<td><span class='lbl'>3. Radio Net Name or Position/Tactical Call</span>{esc(station)} — Ch {esc(w['channels'])}</td>"
        f"<td colspan='2'><span class='lbl'>4. Radio Operator (Name, Call Sign)</span>{esc(w['watchstander'])}, {esc(station)}</td>"
        "</tr></table>"
        "<h2>5. Communications Log</h2>"
        "<table><tr><th rowspan='2' style='width:9%'>Time</th><th colspan='2'>From</th><th colspan='2'>To</th>"
        "<th rowspan='2'>Message</th></tr><tr><th style='width:12%'>Call Sign/ID</th><th style='width:6%'>Msg #</th>"
        "<th style='width:12%'>Call Sign/ID</th><th style='width:6%'>Msg #</th></tr>"
        + ("".join(rows) or "<tr><td colspan='6'>No entries.</td></tr>") + "</table>"
        "<table class='hdr' style='margin-top:10px'><tr>"
        f"<td><span class='lbl'>6. Prepared by (Name, Call Sign)</span>{esc(w['watchstander'])}, {esc(station)}</td>"
        f"<td><span class='lbl'>7. Date &amp; Time Prepared</span>{dt(from_iso(w['closed_utc']) if w['closed_utc'] else None)}</td>"
        "</tr></table>"
        f"<div class='note'>ICS 309 — generated by {APP}. Struck-through entries are omitted; "
        "see the watch log for the complete record.</div>")
    return _page(f"ICS 309 {incident}", body)


def activity_csv(db, wids, path):
    """One row per patrol for the ANSC 7030 Activity Report – Mission, plus one per watch
    for the watchstander's own time."""
    cols = ["record", "date_local", "station", "facility_no", "vessel", "patrol_order", "gar", "pob",
            "coxswain", "crew", "start_utc", "end_utc", "hours", "mission", "watchstander"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=cols)
        wr.writeheader()
        for wid in wids:
            w = db.watch(wid)
            date = _local_date(w["opened_utc"])
            o, c = from_iso(w["opened_utc"]), from_iso(w["closed_utc"])
            wr.writerow({"record": "WATCH", "date_local": date, "station": w["station_call_sign"],
                         "start_utc": w["opened_utc"], "end_utc": w["closed_utc"] or "",
                         "hours": round((c - o).total_seconds() / 3600, 1) if c else "",
                         "watchstander": w["watchstander"]})
            for r in db.patrol_summary(wid):
                wr.writerow({"record": "PATROL", "date_local": date, "station": w["station_call_sign"],
                             "facility_no": r["facility_no"], "vessel": r["vessel"],
                             "patrol_order": r["patrol_order"], "gar": r["gar"], "pob": r["pob"],
                             "coxswain": r["coxswain"], "crew": r["crew"], "start_utc": r["underway_utc"],
                             "end_utc": r["moored_utc"], "hours": "" if r["hours"] is None else r["hours"],
                             "mission": r["mission"], "watchstander": w["watchstander"]})


# ---------------------------------------------------------------- PDF

def _find_browser():
    import shutil
    cands = []
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA")):
        if base:
            cands += [os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"),
                      os.path.join(base, "Google", "Chrome", "Application", "chrome.exe")]
    cands += [shutil.which(n) for n in ("msedge", "chrome", "google-chrome", "chromium")]
    return next((c for c in cands if c and os.path.exists(c)), None)


def _find_soffice():
    import shutil
    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
        if base:
            p = os.path.join(base, "LibreOffice", "program", "soffice.exe")
            if os.path.exists(p):
                return p
    return shutil.which("soffice")


def _pdf_complete(path):
    try:
        with open(path, "rb") as f:
            if f.read(5) != b"%PDF-":
                return False
            f.seek(max(0, os.path.getsize(path) - 64))
            return b"%%EOF" in f.read()
    except OSError:
        return False


def html_to_pdf(html_text, pdf_path, docx_fallback=None, timeout=90):
    """Save a report as PDF.

    Uses Microsoft Edge (built into Windows) or Chrome in headless mode, which prints the
    HTML exactly as the browser would. If neither is found, and docx_fallback is a function
    that writes the same report as .docx, LibreOffice converts that instead.
    Raises RuntimeError if no converter is available or the conversion fails.
    """
    import subprocess
    import tempfile
    pdf_path = os.path.abspath(pdf_path)
    if os.path.exists(pdf_path):
        os.remove(pdf_path)
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    browser = _find_browser()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        if browser:
            src = os.path.join(tmp, "report.html")
            with open(src, "w", encoding="utf-8") as f:
                f.write(html_text)
            url = "file:///" + src.replace("\\", "/")
            cmd = [browser, "--headless=new", "--disable-gpu", "--no-pdf-header-footer", "--no-first-run",
                   "--no-default-browser-check", "--disable-extensions", "--disable-sync",
                   "--disable-background-networking", "--disable-component-update",
                   "--disable-features=msEdgeSidebarV2,EdgeCollections,msSmartScreenProtection",
                   f"--user-data-dir={os.path.join(tmp, 'profile')}", f"--print-to-pdf={pdf_path}", url]
            # The browser can linger for half a minute after writing the PDF, so watch for the
            # finished file (size steady, ends with %%EOF) and close the browser then.
            import time
            try:
                proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                        creationflags=flags)
            except OSError:
                proc = None
            if proc is not None:
                deadline, last = time.monotonic() + timeout, -1
                while time.monotonic() < deadline:
                    done = proc.poll() is not None
                    size = os.path.getsize(pdf_path) if os.path.exists(pdf_path) else -1
                    if size > 0 and size == last and _pdf_complete(pdf_path):
                        break
                    if done and size <= 0:
                        break
                    last = size
                    time.sleep(0.25)
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(10)
                time.sleep(0.3)  # let the profile folder be released before cleanup
            if os.path.exists(pdf_path) and _pdf_complete(pdf_path):
                return pdf_path
        soffice = _find_soffice()
        if soffice and docx_fallback:
            src = os.path.join(tmp, "report.docx")
            docx_fallback(src)
            try:
                subprocess.run([soffice, "--headless", "--convert-to", "pdf", "--outdir", tmp, src],
                               timeout=timeout, capture_output=True, creationflags=flags)
            except (OSError, subprocess.TimeoutExpired):
                pass
            out = os.path.join(tmp, "report.pdf")
            if os.path.exists(out):
                import shutil
                shutil.move(out, pdf_path)
                return pdf_path
    raise RuntimeError("Could not make a PDF: Microsoft Edge, Chrome or LibreOffice is needed. "
                       "Use Print watch log and choose \"Save as PDF\" in the browser instead.")


def save_watch_log(db, wid, path, tz=None):
    """Save the watch log as .pdf or .docx, chosen by the file extension."""
    import docx_export
    ext = os.path.splitext(path)[1].lower()
    if ext == ".docx":
        return docx_export.watch_log_docx(db, wid, path, tz)
    if ext == ".pdf":
        return html_to_pdf(watch_log_html(db, wid, tz), path,
                           docx_fallback=lambda p: docx_export.watch_log_docx(db, wid, p, tz))
    raise ValueError("Save as .pdf or .docx")
