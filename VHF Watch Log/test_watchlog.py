"""Tests for the VHF Watch Log data layer and reports.  Run:  python -m unittest test_watchlog"""
import csv
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

import reports
from watchdb import DEFAULT_PATROL_TYPES, LogError, WatchDB, parse_time, to_iso

UTC = timezone.utc
EST = timezone(timedelta(hours=-5), "EST")
T0 = datetime(2026, 7, 21, 12, 30, tzinfo=UTC)  # 0830 EDT


class Base(unittest.TestCase):
    def setUp(self):
        self.db = WatchDB(":memory:")
        self.v = self.db.save_vessel({"facility_no": "1675", "name": "Sea Breeze", "cell": "555-0101"})
        self.cox = self.db.save_member({"member_no": "1000001", "last_name": "Anchor", "first_name": "Alex"},
                                       {"COXSWAIN": "QUALIFIED", "CREW": "QUALIFIED"})
        self.c1 = self.db.save_member({"member_no": "1000002", "last_name": "Bowline"}, {"CREW": "QUALIFIED"})
        self.c2 = self.db.save_member({"member_no": "1000003", "last_name": "Cleat"}, {"CREW": "TRAINEE"})
        self.wid = self.db.open_watch(watchstander="Gunwale", time_check="ok", when=T0)
        self.pid = self.db.add_patrol(self.wid, self.v, self.cox, [self.c1, self.c2], patrol_order="284083")
        self.addCleanup(self.db.close)


class TestTables(Base):
    def test_seeded_defaults(self):
        self.assertIn("83A", [c["name"] for c in self.db.channels()])
        self.assertTrue(any("Mayport" in c["name"] for c in self.db.contacts()))
        self.assertEqual(self.db.interval_min(), 30)
        self.assertEqual([(t["name"], t["description"]) for t in self.db.patrol_types()], DEFAULT_PATROL_TYPES)

    def test_edit_patrol_types(self):
        self.db.add_patrol(self.wid, self.db.save_vessel({"facility_no": "900"}), mission="Training")
        self.db.set_patrol_types([(" Fireworks ", " safety zone "), ("", "skipped"), ("SAR", ""), ("sar", "dup")])
        self.assertEqual([(t["name"], t["description"]) for t in self.db.patrol_types()],
                         [("Fireworks", "safety zone"), ("SAR", "")])
        # a patrol already logged keeps its mission text
        self.assertIn("Training", [p["mission"] for p in self.db.patrols(self.wid)])

    def test_coxswain_and_crew_views(self):
        self.assertEqual([r["last_name"] for r in self.db.coxswains()], ["Anchor"])
        crew = {r["last_name"]: r["status"] for r in self.db.crew()}
        self.assertEqual(crew, {"Anchor": "QUALIFIED", "Bowline": "QUALIFIED", "Cleat": "TRAINEE"})

    def test_member_number_rules(self):
        with self.assertRaises(LogError):
            self.db.save_member({"member_no": "123", "last_name": "X"})
        with self.assertRaises(LogError):
            self.db.save_member({"member_no": "1000001", "last_name": "Dup"})
        with self.assertRaises(LogError):
            self.db.save_member({"member_no": "1000009", "last_name": "Y"}, {"CREW": "EXPERT"})

    def test_qualification_change_and_removal(self):
        self.db.save_member({}, {"CREW": "QUALIFIED"}, mid=self.c2)
        self.assertEqual(self.db.quals(self.c2), {"CREW": "QUALIFIED"})
        self.db.save_member({}, {"CREW": ""}, mid=self.c2)
        self.assertEqual(self.db.quals(self.c2), {})

    def test_qe_qualification(self):
        self.db.save_member({}, {"QE": "QUALIFIED"}, mid=self.cox)
        self.assertEqual(self.db.quals(self.cox), {"COXSWAIN": "QUALIFIED", "CREW": "QUALIFIED", "QE": "QUALIFIED"})
        self.db.save_member({}, {"QE": ""}, mid=self.cox)
        self.assertNotIn("QE", self.db.quals(self.cox))

    def test_patrol_crew_and_pob(self):
        crew = self.db.patrol_crew(self.pid)
        self.assertEqual([(c["last_name"], c["role"], c["trainee"]) for c in crew],
                         [("Anchor", "COX", 0), ("Bowline", "CREW", 0), ("Cleat", "CREW", 1)])
        self.assertEqual(self.db.patrol(self.pid)["pob"], 3)
        self.assertEqual(self.db.patrol(self.pid)["cell"], "555-0101")

    def test_cell_defaults_to_coxswain_phone(self):
        cox = self.db.save_member({"member_no": "1000020", "last_name": "Davit", "phone": "904-555-0199"},
                                  {"COXSWAIN": "QUALIFIED"})
        v = self.db.save_vessel({"facility_no": "901", "cell": "555-0300"})
        pid = self.db.add_patrol(self.wid, v, cox, [self.c1])
        self.assertEqual(self.db.patrol(pid)["cell"], "904-555-0199")
        # coxswain without a phone: the vessel's cell; a cell typed in always wins
        v2 = self.db.save_vessel({"facility_no": "902", "cell": "555-0301"})
        self.assertEqual(self.db.patrol(self.db.add_patrol(self.wid, v2, self.cox, []))["cell"], "555-0301")
        v3 = self.db.save_vessel({"facility_no": "903"})
        self.assertEqual(self.db.patrol(self.db.add_patrol(self.wid, v3, cox, [], cell="777"))["cell"], "777")

    def test_more_than_three_vessels_and_crew(self):
        for n in range(4):
            v = self.db.save_vessel({"facility_no": f"90{n}", "name": f"Boat {n}"})
            self.db.add_patrol(self.wid, v, self.cox, [self.c1, self.c2])
        self.assertEqual(len(self.db.patrols(self.wid)), 5)


class TestLogRules(Base):
    def test_no_delete_no_edit(self):
        e = self.db.add_entry(self.wid, "UNDERWAY", time=T0, patrol_id=self.pid, position="Marina")
        with self.assertRaises(Exception):
            with self.db.con:
                self.db.con.execute("DELETE FROM log_entries WHERE id=?", (e,))
        with self.assertRaises(Exception):
            with self.db.con:
                self.db.con.execute("UPDATE log_entries SET position='x' WHERE id=?", (e,))

    def test_correction_keeps_original(self):
        e = self.db.add_entry(self.wid, "OPS NORMAL", time=T0, patrol_id=self.pid, position="Wrong")
        with self.assertRaises(LogError):
            self.db.correct(e, "", position="Right")
        new = self.db.correct(e, "jb", position="Right")
        rows = {r["id"]: r for r in self.db.entries(self.wid)}
        self.assertEqual((rows[e]["struck"], rows[e]["struck_by"], rows[e]["position"]), (1, "JB", "Wrong"))
        self.assertEqual((rows[new]["corrects_id"], rows[new]["position"], rows[new]["time_utc"]),
                         (e, "Right", to_iso(T0)))
        with self.assertRaises(LogError):
            self.db.strike(e, "JB")

    def test_gar_changes_are_tracked(self):
        self.db.update_patrol(self.pid, gar="22")
        a = self.db.add_entry(self.wid, "UNDERWAY", time=T0, patrol_id=self.pid)              # blank: carries 22
        b = self.db.add_entry(self.wid, "OPS NORMAL", time=T0 + timedelta(minutes=30), patrol_id=self.pid,
                              gar="31")                                                       # weather built
        c = self.db.add_entry(self.wid, "OPS NORMAL", time=T0 + timedelta(minutes=60), patrol_id=self.pid)
        other = self.db.add_entry(self.wid, "COMMS", time=T0, station="Station Mayport", gar="99")
        rows = {e["id"]: e for e in self.db.entries(self.wid)}
        self.assertEqual([rows[i]["gar"] for i in (a, b, c, other)], ["22", "31", "31", ""])
        self.assertEqual(self.db.current_gar(self.pid), "31")
        self.assertEqual(self.db.gar_changes(self.wid), {b: ("22", "31")})
        self.assertEqual([g for _, g, _ in self.db.gar_history(self.pid)], ["22", "31"])
        self.assertEqual(self.db.patrol(self.pid)["gar"], "22")   # starting GAR is kept
        # The GAR on an entry can't be edited, only corrected; a struck change no longer counts.
        with self.assertRaises(Exception):
            with self.db.con:
                self.db.con.execute("UPDATE log_entries SET gar='5' WHERE id=?", (b,))
        fixed = self.db.correct(b, "jb", gar="28")
        self.assertEqual(self.db.gar_changes(self.wid), {fixed: ("22", "28"), c: ("28", "31")})
        self.db.strike(c, "jb")
        self.assertEqual(self.db.current_gar(self.pid), "28")
        html = reports.watch_log_html(self.db, self.wid)
        self.assertIn("GAR changed 22 → 28", html)
        self.assertIn("22 → 28 (1300Z)", html)
        self.assertIn("GAR changed 22 → 28", reports.ics309_html(self.db, self.wid))

    def test_distress_needs_synopsis_and_sets_retention(self):
        with self.assertRaises(LogError):
            self.db.add_entry(self.wid, "DISTRESS", station="Unknown vessel")
        self.db.close_watch(self.wid, "GG", when=T0 + timedelta(hours=7))
        self.assertEqual(str(self.db.retain_until(self.wid)), "2027-07-21")

    def test_distress_retention_three_years(self):
        self.db.add_entry(self.wid, "DISTRESS", time=T0, station="Skiff", status="Taking on water, 2 POB")
        self.db.close_watch(self.wid, "GG", when=T0)
        self.assertEqual(str(self.db.retain_until(self.wid)), "2029-07-21")
        self.assertEqual(self.db.past_retention(today=datetime(2029, 7, 22).date())[0]["id"], self.wid)
        self.assertEqual(self.db.past_retention(today=datetime(2028, 1, 1).date()), [])

    def test_locked_watch_is_read_only(self):
        e = self.db.add_entry(self.wid, "UNDERWAY", time=T0, patrol_id=self.pid)
        with self.assertRaises(LogError):
            self.db.close_watch(self.wid, "  ")
        self.db.close_watch(self.wid, "GG")
        for fn in (lambda: self.db.add_entry(self.wid, "COMMS", station="X"),
                   lambda: self.db.strike(e, "GG"),
                   lambda: self.db.update_watch(self.wid, notes="late"),
                   lambda: self.db.update_patrol(self.pid, gar="9"),
                   lambda: self.db.add_patrol(self.wid, self.v)):
            with self.assertRaises(LogError):
                fn()
        self.assertIsNone(self.db.current_watch())

    def test_patrol_with_entries_cannot_be_removed(self):
        self.db.add_entry(self.wid, "UNDERWAY", time=T0, patrol_id=self.pid)
        with self.assertRaises(LogError):
            self.db.remove_patrol(self.pid)
        v = self.db.save_vessel({"facility_no": "77", "name": "Spare"})
        p = self.db.add_patrol(self.wid, v)
        self.db.remove_patrol(p)
        self.assertEqual(len(self.db.patrols(self.wid)), 1)

    def test_entry_needs_station(self):
        with self.assertRaises(LogError):
            self.db.add_entry(self.wid, "COMMS")
        with self.assertRaises(LogError):
            self.db.add_entry(self.wid, "NONSENSE", station="X")


class TestSchedule(Base):
    def state(self, minutes):
        return self.db.schedule(self.wid, now=T0 + timedelta(minutes=minutes))[0]

    def test_planned_then_underway_then_due(self):
        self.assertEqual(self.state(0)["state"], "PLANNED")
        self.db.add_entry(self.wid, "UNDERWAY", time=T0, patrol_id=self.pid)
        self.assertEqual(self.state(10)["state"], "OK")
        self.assertEqual(self.state(10)["due"], T0 + timedelta(minutes=30))
        self.assertEqual(self.state(26)["state"], "DUE SOON")
        s = self.state(34)
        self.assertEqual((s["state"], s["overdue_min"]), ("OVERDUE", 4))

    def test_any_contact_restarts_clock_but_struck_does_not(self):
        self.db.add_entry(self.wid, "UNDERWAY", time=T0, patrol_id=self.pid)
        e = self.db.add_entry(self.wid, "COMMS", time=T0 + timedelta(minutes=20), patrol_id=self.pid)
        self.assertEqual(self.state(35)["due"], T0 + timedelta(minutes=50))
        self.db.strike(e, "GG")
        self.assertEqual(self.state(35)["state"], "OVERDUE")

    def test_equipment_entry_is_not_contact(self):
        self.db.add_entry(self.wid, "UNDERWAY", time=T0, patrol_id=self.pid)
        self.db.add_entry(self.wid, "EQUIPMENT", time=T0 + timedelta(minutes=20), patrol_id=self.pid)
        self.assertEqual(self.state(31)["state"], "OVERDUE")

    def test_custom_interval_and_moored(self):
        self.db.update_patrol(self.pid, interval_min=15)
        self.db.add_entry(self.wid, "UNDERWAY", time=T0, patrol_id=self.pid)
        self.assertEqual(self.state(16)["state"], "OVERDUE")
        self.db.add_entry(self.wid, "MOORED", time=T0 + timedelta(minutes=17), patrol_id=self.pid)
        s = self.state(60)
        self.assertEqual((s["state"], s["due"]), ("MOORED", None))
        summ = self.db.patrol_summary(self.wid)[0]
        self.assertEqual((summ["hours"], summ["coxswain"]), (0.3, "Anchor (1000001)"))
        self.assertIn("Cleat (1000003) T", summ["crew"])


class TestParseTime(unittest.TestCase):
    now = datetime(2026, 7, 21, 15, 0, tzinfo=UTC)

    def test_forms(self):
        self.assertEqual(parse_time("", now=self.now), self.now)
        self.assertEqual(parse_time("1432Z", now=self.now), datetime(2026, 7, 21, 14, 32, tzinfo=UTC))
        self.assertEqual(parse_time("09:45", now=self.now, tz=EST), datetime(2026, 7, 21, 14, 45, tzinfo=UTC))

    def test_late_entry_across_midnight(self):
        now = datetime(2026, 7, 22, 0, 5, tzinfo=UTC)
        self.assertEqual(parse_time("2355Z", now=now), datetime(2026, 7, 21, 23, 55, tzinfo=UTC))

    def test_bad(self):
        for bad in ("2460", "12345", "noon", "9:7"):
            with self.assertRaises(ValueError):
                parse_time(bad, now=self.now)


class TestReportsAndImport(Base):
    def test_watch_log_html(self):
        self.db.add_entry(self.wid, "UNDERWAY", time=T0, patrol_id=self.pid, position="Amelia Island Marina",
                          heading="North")
        e = self.db.add_entry(self.wid, "OPS NORMAL", time=T0 + timedelta(minutes=30), patrol_id=self.pid,
                              position="Wrong & <bad>")
        self.db.correct(e, "GG", position="St Marys Inlet")
        self.db.add_entry(self.wid, "URGENCY", time=T0, station="Skiff", status="Disabled, 2 POB")
        self.db.close_watch(self.wid, "GG", when=T0 + timedelta(hours=7))
        h = reports.watch_log_html(self.db, self.wid, tz=EST)
        for s in ("1675 Sea Breeze", "284083", "Anchor (1000001)", "(Trainee)", "1230Z", "0730",
                  "class='struck'", "Wrong &amp; &lt;bad&gt;", "St Marys Inlet", "Retain until 21 Jul 2029",
                  "555-0101", "Station Mayport"):
            self.assertIn(s, h)
        self.db2 = None

    def test_cell_numbers_can_be_left_off(self):
        self.db.set_setting("print_cell_numbers", "0")
        self.assertNotIn("555-0101", reports.watch_log_html(self.db, self.wid))

    def test_ics309_and_csv(self):
        self.db.add_entry(self.wid, "UNDERWAY", time=T0, patrol_id=self.pid, position="Marina")
        self.db.add_entry(self.wid, "COMMS", time=T0, patrol_id=self.pid, direction="TO", remarks="Wx update")
        h = reports.ics309_html(self.db, self.wid, tz=EST)
        self.assertIn("ICS 309", h)
        self.assertIn("UNDERWAY — Marina", h)
        self.assertIn("<td>Amelia 9</td><td></td><td>1675</td>", h)  # outbound row: station -> vessel
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "a.csv")
            reports.activity_csv(self.db, [self.wid], p)
            with open(p, newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
        self.assertEqual([r["record"] for r in rows], ["WATCH", "PATROL"])
        self.assertEqual(rows[1]["patrol_order"], "284083")

    def test_csv_import(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "m.csv")
            with open(p, "w", newline="", encoding="utf-8") as f:
                f.write("Member No,Last Name,First Name,Phone,Coxswain,Crew,QE\n"
                        "1000002,Bowline,Blake,555,Qualified,Qualified,Trainee\n"
                        "1000010,Newman,Nora,,,Trainee\n")
            self.assertEqual(self.db.import_members_csv(p), (1, 1))
            self.assertEqual(self.db.quals(self.c1), {"COXSWAIN": "QUALIFIED", "CREW": "QUALIFIED", "QE": "TRAINEE"})
            self.assertEqual(self.db.member_by_no("1000002")["first_name"], "Blake")
            vp = os.path.join(d, "v.csv")
            with open(vp, "w", newline="", encoding="utf-8") as f:
                f.write("facility_no,name,call_sign\n1675,Sea Breeze II,CGAUX 1675\n4000,New Boat,\n")
            self.assertEqual(self.db.import_vessels_csv(vp), (1, 1))
            self.assertEqual(self.db.vessel(self.v)["name"], "Sea Breeze II")

    def test_docx_export(self):
        import zipfile
        from xml.dom import minidom
        e = self.db.add_entry(self.wid, "UNDERWAY", time=T0, patrol_id=self.pid, position="Marina & <dock>")
        self.db.correct(e, "GG", position="Amelia Island Marina")
        self.db.add_entry(self.wid, "DISTRESS", time=T0, station="Skiff", status="Taking on water")
        self.db.close_watch(self.wid, "GG", when=T0 + timedelta(hours=7))
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "log.docx")
            reports.save_watch_log(self.db, self.wid, p, tz=EST)
            with zipfile.ZipFile(p) as z:
                self.assertIsNone(z.testzip())
                for name in z.namelist():
                    if name.endswith(".xml") or name.endswith(".rels"):
                        minidom.parseString(z.read(name))  # well-formed
                doc = z.read("word/document.xml").decode("utf-8")
        for s in ("1675 Sea Breeze", "284083", "Anchor (1000001)", "(Trainee)", "<w:strike/>", "GG",
                  "Marina &amp; &lt;dock&gt;", "Retain until 21 Jul 2029", "Station Mayport", "0730"):
            self.assertIn(s, doc)

    def test_save_rejects_other_formats(self):
        with self.assertRaises(ValueError):
            reports.save_watch_log(self.db, self.wid, "x.txt")

    @unittest.skipUnless(reports._find_browser() or reports._find_soffice(), "no PDF converter here")
    def test_pdf_export(self):
        self.db.add_entry(self.wid, "UNDERWAY", time=T0, patrol_id=self.pid, position="Marina")
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "log.pdf")
            reports.save_watch_log(self.db, self.wid, p)
            with open(p, "rb") as f:
                self.assertEqual(f.read(5), b"%PDF-")

    def test_backup_and_reopen(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "b.db")
            self.db.backup(p)
            db2 = WatchDB(p)
            self.assertEqual(len(db2.patrols(self.wid)), 1)
            db2.close()


class TestWatchstandersAndStations(unittest.TestCase):
    def setUp(self):
        self.db = WatchDB(":memory:")
        self.addCleanup(self.db.close)
        self.sid = self.db.save_station({"call_sign": "Amelia 9", "street": "100 Example Way",
                                         "city": "Fernandina Beach", "state": "fl", "zip": "32034",
                                         "latitude": "30 40.09 N", "longitude": "081° 27.67' W",
                                         "phone": "(904) 555-0190"})
        self.rid = self.db.save_watchstander({"member_no": "1000007", "last_name": "Gunwale", "first_name": "Gray",
                                              "phone": "555-0107", "tco_date": "3/15/2024"})

    def test_tables(self):
        s = self.db.station(self.sid)
        self.assertEqual((s["state"], s["latitude"], s["longitude"]), ("FL", 30.668167, -81.461167))
        self.assertEqual(self.db.watchstander(self.rid)["tco_date"], "2024-03-15")
        with self.assertRaises(LogError):
            self.db.save_watchstander({"member_no": "1000007", "last_name": "Dup"})
        with self.assertRaises(LogError):
            self.db.save_station({"call_sign": "Amelia 9"})
        with self.assertRaises(LogError):
            self.db.save_watchstander({"member_no": "1000008", "last_name": "X", "tco_date": "someday"})

    def test_coordinates(self):
        from watchdb import fmt_coord, parse_coord
        self.assertEqual(parse_coord("-81.4612", "lon"), -81.4612)
        self.assertEqual(parse_coord("30 40 5.4 N", "lat"), 30.668167)
        self.assertEqual(fmt_coord(-81.461167, "lon"), "081°27.67'W")
        self.assertEqual(parse_coord(fmt_coord(30.668167, "lat"), "lat"), 30.668167)
        for bad, kind in (("95", "lat"), ("30 N", "lon"), ("abc", "lat")):
            with self.assertRaises(LogError):
                parse_coord(bad, kind)

    def test_watch_uses_station_snapshot(self):
        wid = self.db.open_watch(rws_id=self.rid, station_id=self.sid, when=T0)
        w = self.db.watch(wid)
        self.assertEqual((w["watchstander"], w["station_call_sign"]), ("Gunwale, Gray (1000007)", "Amelia 9"))
        # Later changes to the station do not alter a watch already opened.
        self.db.save_station({"street": "200 New St"}, self.sid)
        self.assertEqual(self.db.watch_station(wid)["street"], "100 Example Way")
        self.db.close_watch(wid, "GG", when=T0)
        h = reports.watch_log_html(self.db, wid)
        sig = h.index("Watchstander signature")
        for s in ("Radio station", "100 Example Way, Fernandina Beach, FL 32034", "30°40.09&#x27;N", "081°27.67&#x27;W",
                  "(904) 555-0190"):
            self.assertGreater(h.index(s), sig)
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "x.docx")
            reports.save_watch_log(self.db, wid, p)
            import zipfile
            doc = zipfile.ZipFile(p).read("word/document.xml").decode("utf-8")
        self.assertGreater(doc.index("100 Example Way"), doc.index("Watchstander signature"))

    def test_watch_without_station_prints_no_block(self):
        wid = self.db.open_watch(watchstander="Someone", station_call_sign="Other Radio")
        self.assertNotIn("Radio station</div>", reports.watch_log_html(self.db, wid))

    def test_csv_imports(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "w.csv")
            with open(p, "w", newline="", encoding="utf-8") as f:
                f.write("Member No,Last Name,First Name,Phone,TCO Date\n1000007,Gunwale,Gray,555,01/02/2020\n"
                        "1000011,Hawser,Harper,556,2021-05-05\n")
            self.assertEqual(self.db.import_watchstanders_csv(p), (1, 1))
            self.assertEqual(self.db.watchstander(self.rid)["tco_date"], "2020-01-02")
            p = os.path.join(d, "s.csv")
            with open(p, "w", newline="", encoding="utf-8") as f:
                f.write("call_sign,city,latitude,longitude\nAmelia 9,Yulee,30.6,-81.5\nAmelia 10,Yulee,,\n")
            self.assertEqual(self.db.import_stations_csv(p), (1, 1))
            self.assertEqual(self.db.station(self.sid)["city"], "Yulee")

    def test_old_database_is_upgraded(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "old.db")
            db = WatchDB(p)
            wid = db.open_watch(watchstander="X", when=T0)
            db.add_entry(wid, "COMMS", time=T0, station="Y")
            db.close()
            con = sqlite3.connect(p)  # make it look like a version-1 database
            con.executescript("DROP TABLE radio_watch_standers; DROP TABLE radio_stations;"
                              "ALTER TABLE watches DROP COLUMN station_info; ALTER TABLE watches DROP COLUMN station_id;"
                              "ALTER TABLE watches DROP COLUMN rws_id; UPDATE meta SET value='1';")
            con.close()
            db = WatchDB(p)
            self.assertEqual(len(db.entries(wid)), 1)
            self.assertIsNone(db.watch_station(wid))
            db.save_station({"call_sign": "Z"})
            db.close()

    def test_v2_database_gets_qe(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "v2.db")
            db = WatchDB(p)
            mid = db.save_member({"member_no": "1000001", "last_name": "Anchor"},
                                 {"COXSWAIN": "QUALIFIED", "CREW": "TRAINEE"})
            db.close()
            con = sqlite3.connect(p)  # recreate the version-2 table without QE
            con.executescript(
                "DROP VIEW coxswains; DROP VIEW crew; ALTER TABLE qualifications RENAME TO q;"
                "CREATE TABLE qualifications(member_id INTEGER NOT NULL REFERENCES members(id),"
                " qual TEXT NOT NULL CHECK (qual IN ('COXSWAIN','CREW','PWC','TCO')),"
                " status TEXT NOT NULL CHECK (status IN ('QUALIFIED','TRAINEE','REYR')),"
                " as_of TEXT NOT NULL DEFAULT '', PRIMARY KEY(member_id, qual));"
                "INSERT INTO qualifications SELECT * FROM q; DROP TABLE q; UPDATE meta SET value='2';")
            con.close()
            db = WatchDB(p)
            self.assertEqual(db.quals(mid), {"COXSWAIN": "QUALIFIED", "CREW": "TRAINEE"})
            db.save_member({}, {"QE": "QUALIFIED"}, mid=mid)
            self.assertEqual(db.quals(mid)["QE"], "QUALIFIED")
            self.assertEqual(db.con.execute("SELECT COUNT(*) FROM coxswains").fetchone()[0], 1)
            self.assertEqual(db.con.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0], "5")
            db.close()

    def test_v3_database_gets_patrol_types(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "v3.db")
            db = WatchDB(p)
            v = db.save_vessel({"facility_no": "1675"})
            wid = db.open_watch(watchstander="X", when=T0)
            db.add_patrol(wid, v, mission="Fireworks safety zone")   # typed in, not from the list
            db.add_patrol(wid, db.save_vessel({"facility_no": "1676"}), mission="SAR")
            db.close()
            con = sqlite3.connect(p)  # a version-3 database had no patrol_types table
            con.executescript("DROP TABLE patrol_types; UPDATE meta SET value='3';")
            con.close()
            db = WatchDB(p)
            names = [t["name"] for t in db.patrol_types()]
            self.assertEqual(names, [n for n, _ in DEFAULT_PATROL_TYPES] + ["Fireworks safety zone"])
            self.assertEqual(db.con.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0], "5")
            db.set_patrol_types([("SAR", "")])
            db.close()
            db = WatchDB(p)  # reopening an up-to-date database does not re-add removed types
            self.assertEqual([t["name"] for t in db.patrol_types()], ["SAR"])
            db.close()

    def test_v4_database_gets_entry_gar(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "v4.db")
            db = WatchDB(p)
            v = db.save_vessel({"facility_no": "1675"})
            wid = db.open_watch(watchstander="X", when=T0)
            pid = db.add_patrol(wid, v, gar="20")
            old = db.add_entry(wid, "UNDERWAY", time=T0, patrol_id=pid)
            db.close()
            con = sqlite3.connect(p)  # a version-4 log_entries had no gar column
            con.executescript("DROP TRIGGER log_no_edit; ALTER TABLE log_entries DROP COLUMN gar;"
                              "CREATE TRIGGER log_no_edit BEFORE UPDATE OF watch_id, remarks ON log_entries"
                              " BEGIN SELECT RAISE(ABORT, 'x'); END; UPDATE meta SET value='4';")
            con.close()
            db = WatchDB(p)
            self.assertEqual(db.entry(old)["gar"], "")          # old entries: no GAR recorded
            self.assertEqual(db.current_gar(pid), "20")         # falls back to the patrol's GAR
            new = db.add_entry(wid, "OPS NORMAL", time=T0 + timedelta(minutes=30), patrol_id=pid, gar="25")
            self.assertEqual(db.gar_changes(wid), {new: ("20", "25")})
            with self.assertRaises(Exception):                  # the recreated trigger protects gar
                with db.con:
                    db.con.execute("UPDATE log_entries SET gar='1' WHERE id=?", (new,))
            with self.assertRaises(Exception):                  # ... and still the other columns
                with db.con:
                    db.con.execute("UPDATE log_entries SET position='x' WHERE id=?", (new,))
            self.assertEqual(db.con.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0], "5")
            db.close()


if __name__ == "__main__":
    unittest.main()
