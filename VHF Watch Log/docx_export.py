"""Write the watch log as a Word document (.docx) using only the standard library.

The layout follows the printed log: header block, a block per vessel (COX / CREW with
Trainee marks), the radio log (struck entries shown struck through with initials),
closing/retention note, signature lines and the contact list. Letter size, 0.5 in margins,
"Page X of Y" in the footer.
"""
import zipfile
from xml.sax.saxutils import escape

from watchdb import fmt_local, fmt_z, from_iso, station_lines, tz_abbr


def _gar_text(db, pid):
    """A patrol's GAR as printed: '22', or '22 → 18 (1415Z)' when it changed (same as reports.gar_text)."""
    hist = db.gar_history(pid)
    return hist[0][1] + "".join(f" → {g} ({fmt_z(t)})" for t, g, _ in hist[1:])

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" ' \
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
PAGE_W = 12240 - 2 * 720  # twips between 0.5 in margins


def _x(s):
    return escape("" if s is None else str(s)).replace("\n", " ")


def run(text, bold=False, strike=False, size=None, italic=False, color=None):
    props = ""
    if bold:
        props += "<w:b/>"
    if italic:
        props += "<w:i/>"
    if strike:
        props += "<w:strike/>"
    if color:
        props += f'<w:color w:val="{color}"/>'
    if size:
        props += f'<w:sz w:val="{int(size * 2)}"/><w:szCs w:val="{int(size * 2)}"/>'
    parts = str("" if text is None else text).split("\n")
    out = []
    for i, part in enumerate(parts):
        if i:
            out.append("<w:br/>")
        out.append(f'<w:t xml:space="preserve">{_x(part)}</w:t>')
    return f"<w:r><w:rPr>{props}</w:rPr>{''.join(out)}</w:r>"


def para(*runs, space_after=60, space_before=0, keep_next=False, border_bottom=False):
    ppr = f'<w:spacing w:before="{space_before}" w:after="{space_after}"/>'
    if keep_next:
        ppr += "<w:keepNext/>"
    if border_bottom:
        ppr += '<w:pBdr><w:bottom w:val="single" w:sz="10" w:space="1" w:color="000000"/></w:pBdr>'
    return f"<w:p><w:pPr>{ppr}</w:pPr>{''.join(runs)}</w:p>"


def heading(text):
    return para(run(text, bold=True, size=11), space_before=200, space_after=60, keep_next=True,
                border_bottom=True)


def cell_xml(content, width, shade=None, span=1):
    """content: list of runs (one paragraph) or list of lists of runs (several paragraphs)."""
    if content and isinstance(content[0], list):
        paras = "".join(para(*c, space_after=0) for c in content)
    else:
        paras = para(*(content or [run("")]), space_after=0)
    tcpr = f'<w:tcW w:w="{width}" w:type="dxa"/>'
    if span > 1:
        tcpr += f'<w:gridSpan w:val="{span}"/>'
    if shade:
        tcpr += f'<w:shd w:val="clear" w:color="auto" w:fill="{shade}"/>'
    return f"<w:tc><w:tcPr>{tcpr}</w:tcPr>{paras}</w:tc>"


def table(rows, widths, header_rows=1, repeat_header=True, borders=True):
    """rows: list of rows; each row a list of cells; a cell is a list of runs, or
    (runs, span) for a merged cell."""
    total = sum(widths)
    bd = ("<w:tblBorders>" + "".join(
        f'<w:{s} w:val="single" w:sz="6" w:space="0" w:color="000000"/>'
        for s in ("top", "left", "bottom", "right", "insideH", "insideV")) + "</w:tblBorders>") if borders else ""
    out = [f'<w:tbl><w:tblPr><w:tblW w:w="{total}" w:type="dxa"/><w:tblLayout w:type="fixed"/>{bd}'
           '<w:tblCellMar><w:left w:w="70" w:type="dxa"/><w:right w:w="70" w:type="dxa"/></w:tblCellMar>'
           '</w:tblPr><w:tblGrid>' + "".join(f'<w:gridCol w:w="{w}"/>' for w in widths) + "</w:tblGrid>"]
    for i, row in enumerate(rows):
        trpr = '<w:cantSplit/>'
        if i < header_rows and repeat_header:
            trpr += "<w:tblHeader/>"
        cells, col = [], 0
        for c in row:
            span = 1
            if isinstance(c, tuple):
                c, span = c
            w = sum(widths[col:col + span])
            cells.append(cell_xml(c, w, "E6E6E6" if i < header_rows else None, span))
            col += span
        out.append(f"<w:tr><w:trPr>{trpr}</w:trPr>{''.join(cells)}</w:tr>")
    out.append("</w:tbl>")
    return "".join(out) + para(space_after=40)


def _lbl(label, value, big=False):
    return [[run(label, size=7, color="333333")], [run(value, bold=big, size=11 if big else 9)]]


def watch_log_docx(db, wid, path, tz=None):
    w = db.watch(wid)
    if w is None:
        raise ValueError("No such watch")
    opened = from_iso(w["opened_utc"])
    tzn = tz_abbr(opened.astimezone(tz))
    date_local = opened.astimezone(tz).strftime("%m/%d/%Y")
    print_cell = db.setting("print_cell_numbers") == "1"
    body = [para(run("Auxiliary Radio — VHF Communications Watch Log", bold=True, size=15), space_after=80)]

    q = PAGE_W // 4
    closed = (f"{fmt_z(w['closed_utc'])} / {fmt_local(w['closed_utc'], tz)} {tzn}" if w["closed_utc"] else "")
    radio = w["radio_check"] + (f"\n{w['equipment_status']}" if w["equipment_status"] else "")
    body.append(table([
        [_lbl("Station call sign", w["station_call_sign"], True), _lbl("Date (local)", date_local, True),
         _lbl("Channels guarded", w["channels"]), _lbl("Watchstander", w["watchstander"])],
        [_lbl("Watch opened", f"{fmt_z(w['opened_utc'])} / {fmt_local(w['opened_utc'], tz)} {tzn}"),
         _lbl("Watch closed", closed), _lbl("Time check", w["time_check"]),
         _lbl("Radio check / equipment", radio)],
    ], [q] * 4, header_rows=0))

    body.append(heading("Vessels on patrol"))
    patrols = db.patrols(wid)
    if not patrols:
        body.append(para(run("No patrols this watch.", size=9)))
    for i, p in enumerate(patrols, 1):
        heads = [f"Vessel # {i}", "Patrol order", "GAR", "POB"] + (["Cell #"] if print_cell else [])
        vessel = [[run(f"{p['facility_no']} {p['vessel_name']}", bold=True, size=12)]]
        if p["call_sign"]:
            vessel.append([run(p["call_sign"], size=8)])
        vals = [vessel, [run(p["patrol_order"], bold=True, size=12)], [run(_gar_text(db, p["id"]))],
                [run(p["pob"])]]
        if print_cell:
            vals.append([run(p["cell"])])
        widths = ([3200, 2200, 1400, 1400, PAGE_W - 8200] if print_cell else [4000, 2800, 1700, PAGE_W - 8500])
        body.append(table([[[run(h, bold=True)] for h in heads], vals], widths))

        crew = db.patrol_crew(p["id"])
        cox = [c for c in crew if c["role"] == "COX"]
        mates = [c for c in crew if c["role"] == "CREW"]
        n = 1 + len(mates)
        cw = [PAGE_W // n] * n
        cw[-1] = PAGE_W - sum(cw[:-1])

        def who(c, bold=False):
            r = [[run(f"{c['last_name']} ({c['member_no']})", bold=bold)]]
            if c["trainee"]:
                r.append([run("(Trainee)", size=8)])
            return r

        body.append(table([
            [[run("COX", bold=True)]] + [[run("CREW", bold=True)] for _ in mates],
            [who(cox[0], True) if cox else [run("")]] + [who(c) for c in mates],
        ], cw))
        extra = []
        if p["start_utc"]:
            extra.append(f"Underway {fmt_z(p['start_utc'])} / {fmt_local(p['start_utc'], tz)}")
        if p["end_utc"]:
            extra.append(f"Moored {fmt_z(p['end_utc'])} / {fmt_local(p['end_utc'], tz)}")
        extra.append(f"Ops normal every {p['interval_min']} min")
        if p["mission"]:
            extra.append(f"Mission: {p['mission']}")
        if p["float_plan"]:
            extra.append(f"Float plan / area: {p['float_plan']}")
        body.append(para(run("  ·  ".join(extra), size=8), space_after=120))

    body.append(heading("Radio log"))
    entries = db.entries(wid)
    by_id = {e["id"]: e for e in entries}
    changes = db.gar_changes(wid)
    widths = [850, 700, 1250, 550, 1300, 2700, 2900]
    widths.append(PAGE_W - sum(widths))
    rows = [[[run(h, bold=True, size=9)] for h in
             ("Time Z", tzn, "Vessel / station", "Ch", "Type", "Position / heading", "Status / remarks", "Init")]]
    for e in entries:
        st = bool(e["struck"])
        bold = e["entry_type"] in ("DISTRESS", "URGENCY") and not st

        def r(text, **kw):
            return run(text, strike=st, bold=bold, size=9, color="555555" if st else None, **kw)

        pos = [[r(e["position"])]] if e["position"] else []
        if e["heading"]:
            pos.append([r(f"Hdg: {e['heading']}")])
        rem = [[r(e["status"])]] if e["status"] else []
        if e["remarks"]:
            rem.append([r(e["remarks"])])
        if e["id"] in changes:
            rem.append([run(f"GAR changed {changes[e['id']][0] or '-'} → {changes[e['id']][1]}", bold=True, size=9)])
        if e["corrects_id"] and e["corrects_id"] in by_id:
            rem.append([run(f"(corrects entry at {fmt_z(by_id[e['corrects_id']]['time_utc'])})", italic=True, size=8)])
        who = e["station"] + (" (to)" if e["direction"] == "TO" else "")
        rows.append([[r(fmt_z(e["time_utc"]))], [r(fmt_local(e["time_utc"], tz))], [r(who)], [r(e["channel"])],
                     [r(e["entry_type"])], pos or [r("")], rem or [r("")],
                     [run(e["struck_by"], size=9)]])
    if not entries:
        rows.append([([run("No entries.", size=9)], 8)])
    body.append(table(rows, widths))

    if w["notes"]:
        body.append(heading("Notes"))
        body.append(para(run(w["notes"], size=9)))

    half = PAGE_W // 2
    body.append(para(space_before=240))
    body.append(table([[[run("_" * 38)], [run("_" * 38)]],
                       [[run("Watchstander signature", size=8)], [run("Relieved by / reviewed by", size=8)]]],
                      [half, PAGE_W - half], header_rows=0, borders=False))
    lines = station_lines(db.watch_station(wid))
    if lines:
        body.append(para(run("Radio station", bold=True, size=9.5), space_before=160, space_after=40,
                         keep_next=True))
        sw = {"Call sign": 1800, "Address": 4200, "Lat / Long": 2600, "Contact phone": 1700}
        widths = [sw[k] for k, _ in lines]
        widths[-1] += PAGE_W - sum(widths)
        body.append(table([[_lbl(k, v) for k, v in lines]], widths, header_rows=0))
    status = (f"Closed and locked {fmt_z(w['closed_utc'])}; signed: {w['signed_by']}. "
              if w["locked"] else "WATCH STILL OPEN — not signed. ")
    if w["locked"]:
        retain = db.retain_until(wid)
        status += f"Retain until {retain:%d %b %Y} (AOPG Vol III 3-F.3"
        status += "; distress traffic logged — 3 years). " if db.has_distress(wid) else "). "
    status += ("Struck-through entries were corrected by the person whose initials appear. "
               "Times are UTC (Z) with local time alongside.")
    body.append(para(run(status, size=8, color="333333")))

    contacts = db.contacts()
    if contacts:
        body.append(heading("Contacts"))
        half_n = (len(contacts) + 1) // 2

        def c_runs(c):
            rr = [run(c["name"] + " ", bold=True, size=8.5), run(c["phone"], size=8.5)]
            if c["notes"]:
                rr.append(run(f" ({c['notes']})", italic=True, size=8.5))
            return rr

        left, right = contacts[:half_n], contacts[half_n:]
        crow = [[[c_runs(c) for c in left], [c_runs(c) for c in right] or [[run("")]]]]
        body.append(table(crow, [half, PAGE_W - half], header_rows=0, borders=False))

    _write(path, "".join(body), f"Watch Log {date_local} {w['station_call_sign']}")
    return path


def _write(path, body_xml, title):
    document = (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document {W}><w:body>{body_xml}'
        '<w:sectPr><w:footerReference w:type="default" r:id="rIdFooter"/>'
        '<w:pgSz w:w="12240" w:h="15840"/>'
        '<w:pgMar w:top="720" w:right="720" w:bottom="720" w:left="720" w:header="360" w:footer="360" w:gutter="0"/>'
        '</w:sectPr></w:body></w:document>')

    def fld(code):
        return (f'<w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText xml:space="preserve"> {code} </w:instrText></w:r>'
                '<w:r><w:fldChar w:fldCharType="separate"/></w:r><w:r><w:t>1</w:t></w:r><w:r><w:fldChar w:fldCharType="end"/></w:r>')

    footer = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:ftr {W}><w:p><w:pPr><w:jc w:val="right"/>'
              '<w:rPr><w:sz w:val="18"/></w:rPr></w:pPr><w:r><w:t xml:space="preserve">Page </w:t></w:r>'
              f'{fld("PAGE")}<w:r><w:t xml:space="preserve"> of </w:t></w:r>{fld("NUMPAGES")}</w:p></w:ftr>')
    styles = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:styles {W}><w:docDefaults>'
              '<w:rPrDefault><w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial" w:cs="Arial" w:eastAsia="Arial"/>'
              '<w:sz w:val="20"/><w:szCs w:val="20"/><w:lang w:val="en-US"/></w:rPr></w:rPrDefault>'
              '<w:pPrDefault><w:pPr><w:spacing w:after="0" w:line="240" w:lineRule="auto"/></w:pPr></w:pPrDefault>'
              '</w:docDefaults><w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>'
              '</w:styles>')
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
        '<Override PartName="/word/footer1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"/>'
        '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
        '</Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
            '</Relationships>')
    doc_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rIdStyles" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
                '<Relationship Id="rIdFooter" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer" Target="footer1.xml"/>'
                '</Relationships>')
    core = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/">'
            f'<dc:title>{_x(title)}</dc:title><dc:creator>VHF Watch Log</dc:creator></cp:coreProperties>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", document)
        z.writestr("word/_rels/document.xml.rels", doc_rels)
        z.writestr("word/styles.xml", styles)
        z.writestr("word/footer1.xml", footer)
        z.writestr("docProps/core.xml", core)
