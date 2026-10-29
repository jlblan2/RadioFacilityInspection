"""Tests for Best-HF-Freq. Run:  python -m unittest -v test_best_hf"""
import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import hfprop as hp
import planner
import spacewx

NOON_FL = datetime(2026, 6, 21, 17, 30, tzinfo=timezone.utc)      # about 1:30 pm in Florida
MIDNIGHT_FL = datetime(2026, 6, 21, 5, 0, tzinfo=timezone.utc)
TAMPA = (27.95, -82.46)
SAN_JUAN = (18.47, -66.11)


class Geography(unittest.TestCase):
    def test_grid(self):
        lat, lon = hp.grid_to_latlon("FN31pr")
        self.assertAlmostEqual(lat, 41.729, places=2)
        self.assertAlmostEqual(lon, -72.708, places=2)
        self.assertEqual(hp.latlon_to_grid(lat, lon), "FN31pr")
        self.assertEqual(hp.grid_to_latlon("el87"), (27.5, -83.0))
        for bad in ("ZZ00", "EL8", "EL87zz", "12AB"):
            with self.assertRaises(ValueError):
                hp.grid_to_latlon(bad)

    def test_parse_location(self):
        self.assertEqual(hp.parse_location("27.9, -82.5"), (27.9, -82.5))
        self.assertEqual(hp.parse_location("27.9N 82.5W"), (27.9, -82.5))
        self.assertEqual(hp.parse_location("27.9 -82.5"), (27.9, -82.5))
        self.assertEqual(hp.parse_location(" EL87 "), (27.5, -83.0))
        for bad in ("", "95, 10", "abc def ghi"):
            with self.assertRaises(ValueError):
                hp.parse_location(bad)

    def test_distance_and_points(self):
        self.assertAlmostEqual(hp.distance_km(40.71, -74.01, 51.51, -0.13), 5570, delta=15)
        lat, lon = hp.destination(*TAMPA, 0, 150)
        self.assertAlmostEqual(hp.distance_km(*TAMPA, lat, lon), 150, delta=0.5)
        mid = hp.intermediate_point(*TAMPA, *SAN_JUAN, 0.5)
        d = hp.distance_km(*TAMPA, *SAN_JUAN)
        self.assertAlmostEqual(hp.distance_km(*TAMPA, *mid), d / 2, delta=1)

    def test_solar_zenith(self):
        eq = datetime(2026, 3, 20, 12, 7, tzinfo=timezone.utc)
        self.assertLess(hp.solar_zenith(0, 0, eq), 2)
        self.assertGreater(hp.solar_zenith(0, 0, eq.replace(hour=0)), 175)


class Ionosphere(unittest.TestCase):
    def test_fof2_day_night_and_sunspots(self):
        day = hp.model_fof2(*TAMPA, NOON_FL, 100)
        night = hp.model_fof2(*TAMPA, MIDNIGHT_FL, 100)
        self.assertGreater(day, night * 1.5)
        self.assertGreater(hp.model_fof2(*TAMPA, NOON_FL, 150), day)
        self.assertLess(hp.model_fof2(*TAMPA, NOON_FL, 100, kp=8), day)

    def test_nvis_muf_is_near_fof2_and_long_paths_are_higher(self):
        io = hp.Ionosphere(80, now=NOON_FL)
        lat, lon = hp.destination(*TAMPA, 0, 100)
        nvis = hp.analyze_path(io, *TAMPA, lat, lon, NOON_FL)
        mid = hp.intermediate_point(*TAMPA, lat, lon, 0.5)
        f = hp.model_fof2(*mid, NOON_FL, 80)
        self.assertAlmostEqual(nvis.f2_muf / f, 1.03, delta=0.05)
        far = hp.analyze_path(io, *TAMPA, *SAN_JUAN, NOON_FL)
        self.assertGreater(far.muf, 2.5 * f)
        self.assertEqual(far.hops, 1)
        self.assertGreater(hp.analyze_path(io, *TAMPA, 51.5, -0.1, NOON_FL).hops, 1)

    def test_absorption(self):
        io = hp.Ionosphere(100, now=NOON_FL)
        day = hp.analyze_path(io, *TAMPA, *SAN_JUAN, NOON_FL)
        night = hp.analyze_path(io, *TAMPA, *SAN_JUAN, MIDNIGHT_FL)
        self.assertGreater(day.loss_db(3.5), day.loss_db(14))
        self.assertGreater(day.loss_db(3.5), night.loss_db(3.5) + 10)
        flare = hp.analyze_path(hp.Ionosphere(100, xray_flux=2e-4, now=NOON_FL), *TAMPA, *SAN_JUAN, NOON_FL)
        self.assertGreater(flare.loss_db(7), day.loss_db(7) + 10)
        self.assertLess(day.luf(hp.station_margin("SSB voice")), day.fot)

    def test_reliability(self):
        io = hp.Ionosphere(80, now=NOON_FL)
        p = hp.analyze_path(io, *TAMPA, *SAN_JUAN, NOON_FL)
        m = hp.station_margin("SSB voice")
        self.assertLess(p.reliability(p.muf * 2, m), 0.01)
        self.assertGreater(p.reliability(p.fot * 0.9, m), 0.8)
        weak = hp.station_margin("SSB voice", watts=5)
        self.assertLess(p.reliability(4.0, weak), p.reliability(4.0, m))
        self.assertGreater(hp.station_margin("FT8 / weak signal"), hp.station_margin("SSB voice"))

    def test_ionosonde_correction(self):
        lat, lon = hp.destination(*TAMPA, 0, 100)
        mid = hp.intermediate_point(*TAMPA, lat, lon, 0.5)
        model = hp.model_fof2(*mid, NOON_FL, 80)
        near = hp.Ionosonde("near", mid[0], mid[1], model * 1.5, NOON_FL, 280)
        io = hp.Ionosphere(80, ionosondes=[near], now=NOON_FL)
        f, hm, d = io.fof2(*mid, NOON_FL)
        self.assertAlmostEqual(f / model, 1.5, delta=0.01)
        self.assertEqual(hm, 280)
        self.assertLess(io.sigma(d, NOON_FL), hp.Ionosphere(80, now=NOON_FL).sigma(None, NOON_FL))
        # The correction fades in the forecast.
        later = NOON_FL + timedelta(hours=12)
        f12, _, _ = io.fof2(*mid, later)
        self.assertLess(f12 / hp.model_fof2(*mid, later, 80), 1.1)
        far = hp.Ionosonde("far", 60.0, 20.0, 20.0, NOON_FL)
        f2, _, _ = hp.Ionosphere(80, ionosondes=[far], now=NOON_FL).fof2(*mid, NOON_FL)
        self.assertAlmostEqual(f2, model, places=6)

    def test_rank_prefers_higher_frequency_within_tie_band(self):
        io = hp.Ionosphere(80, now=MIDNIGHT_FL)
        p = hp.analyze_path(io, *TAMPA, *SAN_JUAN, MIDNIGHT_FL)
        chans = [hp.Channel(str(f), f) for f in (2.0, 3.0, 4.0, 5.0, 7.0, 10.0, 14.0, 21.0)]
        r = hp.rank_channels([p], chans, hp.station_margin("SSB voice"))
        top = max(x.reliability for x in r)
        self.assertGreaterEqual(r[0].reliability, top - hp.TIE_BAND)
        band = [x for x in r if x.reliability >= top - hp.TIE_BAND]
        self.assertEqual(r[0].channel.mhz, max(x.channel.mhz for x in band))
        self.assertLess(r[0].channel.mhz, p.muf)
        self.assertEqual(hp.rating(0.9), "Excellent")
        self.assertEqual(hp.rating(0.05), "Closed")
        self.assertEqual(hp.xray_class(2.5e-6), "C2.5")
        self.assertEqual(hp.xray_class(3e-9), "A0.3")


def make_netlog_db(path):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE frequencies (ID INTEGER, Frequency TEXT)")
    con.executemany("INSERT INTO frequencies VALUES (?, ?)", [
        (1, "NONE ASSIGNED"), (2, "X4A:   4.1000 MHz"), (3, "X7B:   7.2000 MHz"), (4, "BAD: 45 MHz")])
    con.execute("CREATE TABLE members (callsign TEXT, state TEXT, is_synthetic INTEGER, Latitude REAL, "
                "Lat_Dir TEXT, Longitude REAL, Long_Dir TEXT)")
    con.executemany("INSERT INTO members VALUES (?, ?, ?, ?, ?, ?, ?)", [
        ("TEST1", "FL", 0, 30.4, "N", 84.3, "W"),
        ("TEST2", "PR", 0, 18.4, "N", 66.1, "W"),
        ("TEST3", "GA", 0, None, None, None, None),
        ("NOTHING HEARD", None, 1, None, None, None, None),
    ])
    con.commit()
    con.close()


def fake_conditions():
    return spacewx.Conditions(sfi=120, ssn=80, kp=2, a_index=7, xray_flux=1e-7,
                              fetched=NOON_FL, ionosondes=[])


class Planner(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "netlog.db")
        make_netlog_db(self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def test_channel_lines(self):
        ch = planner.parse_channel_lines("Net primary, 5.3305\nNight 3.996 MHz\n7153.5 kHz  # alt\n\n14300")
        self.assertEqual([(c.name, round(c.mhz, 4)) for c in ch],
                         [("Net primary", 5.3305), ("Night", 3.996), ("7.1535 MHz", 7.1535), ("14.3 MHz", 14.3)])
        for bad in ("Primary", "50 MHz"):
            with self.assertRaises(ValueError):
                planner.parse_channel_lines(bad)

    def test_netlog(self):
        self.assertEqual([(c.name, c.mhz) for c in planner.netlog_channels(self.db)],
                         [("X4A", 4.1), ("X7B", 7.2)])
        m = planner.netlog_members(self.db)
        self.assertEqual([(x.callsign, x.lat, x.lon) for x in m],
                         [("TEST1", 30.4, -84.3), ("TEST2", 18.4, -66.1)])

    def _settings(self, **kw):
        s = dict(planner.DEFAULTS, location="EL87", netlog_db=self.db)
        s.update(kw)
        return s

    def test_build_plan_modes(self):
        members = planner.netlog_members(self.db)
        c = fake_conditions()
        nvis = planner.build_plan(self._settings(), c, now=NOON_FL)
        self.assertEqual(len(nvis.hourly), 24)
        self.assertEqual(len(nvis.now.ranking), len(planner.AMATEUR_BANDS))
        self.assertEqual(nvis.hourly[0].when, NOON_FL.replace(minute=0) + timedelta(hours=1))

        st = planner.build_plan(self._settings(path_mode="station", target="test2", channel_set="netlog"),
                                c, members, now=NOON_FL)
        self.assertEqual(st.now.targets[0].label, "TEST2")
        self.assertEqual({x.channel.name for x in st.now.ranking}, {"X4A", "X7B"})

        net = planner.build_plan(self._settings(path_mode="net", net_max_km=3000), c, members, now=NOON_FL)
        self.assertEqual(len(net.now.paths), 2)
        self.assertLessEqual(net.now.best.covered, 2)
        with self.assertRaises(ValueError):
            planner.build_plan(self._settings(path_mode="net", net_max_km=100), c, members, now=NOON_FL)
        with self.assertRaises(ValueError):
            planner.build_plan(self._settings(channel_set="custom", custom_channels=""), c, now=NOON_FL)


class SpaceWeather(unittest.TestCase):
    def test_parse_ionosondes(self):
        now = datetime(2026, 10, 29, 22, 0, tzinfo=timezone.utc)
        raw = [
            {"fof2": 6.1, "cs": 90, "hmf2": 260, "time": "2026-10-29T21:45:00",
             "station": {"latitude": "30.4", "longitude": "262.3", "name": "Austin"}},
            {"fof2": 6.1, "cs": 90, "time": "2026-10-29T18:00:00",            # stale
             "station": {"latitude": "30.5", "longitude": "273.5", "name": "Old"}},
            {"fof2": 6.1, "cs": 10, "time": "2026-10-29T21:50:00",            # low confidence
             "station": {"latitude": "30.5", "longitude": "273.5", "name": "Low"}},
            {"fof2": None, "cs": 90, "time": "2026-10-29T21:50:00",
             "station": {"latitude": "30.5", "longitude": "273.5", "name": "Empty"}},
            {"garbage": True},
        ]
        out = spacewx.parse_ionosondes(raw, now)
        self.assertEqual([s.name for s in out], ["Austin"])
        self.assertAlmostEqual(out[0].lon, -97.7, places=3)
        self.assertEqual(out[0].hmf2, 260)

    def test_get_conditions_uses_cache_when_offline(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"APPDATA": tmp}):
            cache = os.path.join(spacewx.data_dir(), "cache")
            os.makedirs(cache)
            samples = {
                "sfi": [{"flux": 150.0}],
                "kp": [{"Kp": 5.33, "a_running": 48}],
                "xray": [{"energy": "0.05-0.4nm", "flux": 1e-6}, {"energy": "0.1-0.8nm", "flux": 3.2e-5}],
                "scales": {"0": {"R": {"Scale": "2"}, "S": {"Scale": None}, "G": {"Scale": "1"}}},
                "essn": {"24h": [{"ssn": 101.5, "sfi": 150}]},
                "stations": [],
            }
            for name, data in samples.items():
                with open(os.path.join(cache, f"{name}.json"), "w") as f:
                    json.dump(data, f)
            with mock.patch("urllib.request.urlopen", side_effect=OSError("offline")):
                c = spacewx.get_conditions()
            self.assertEqual((c.sfi, c.kp, c.a_index, c.ssn), (150.0, 5.33, 48, 101.5))
            self.assertEqual(c.xray_class, "M3.2")
            self.assertEqual(c.scales, {"R": "2", "S": "0", "G": "1"})
            self.assertEqual(len(c.errors), 6)
            self.assertTrue(all("cached" in e for e in c.errors))

            with mock.patch("urllib.request.urlopen", side_effect=OSError("offline")), \
                    mock.patch.dict(os.environ, {"APPDATA": os.path.join(tmp, "empty")}):
                c = spacewx.get_conditions()
            self.assertIsNone(c.sfi)
            self.assertEqual(c.model_ssn, 50.0)


if __name__ == "__main__":
    unittest.main()
