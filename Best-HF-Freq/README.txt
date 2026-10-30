Best-HF-Freq 1.0
================
Copyright (c) 2026 N. Tesla, Inc. All rights reserved. See LICENSE.

Picks the HF frequency most likely to work right now, from live solar, geomagnetic and
ionosonde data plus atmospherics (static from thunderstorms), and shows how that changes
hour by hour over the next 24 hours.


RUNNING IT
----------
Double-click run.bat. It needs Python 3.9 or newer with tkinter ("tcl/tk and IDLE" ticked
when installing Python). No other packages are needed. An internet connection is needed for
live data; without one the app uses the last data it downloaded and says so.

The first time it starts it shows a disclaimer that must be accepted.

Command line report (no window):
    python best_hf_freq.py --cli --from EL87 --nvis 150
    python best_hf_freq.py --cli --from EL87 --to FK68 --mode CW --watts 50
    python best_hf_freq.py --cli --from EL87 --net --channels netlog
Settings not given on the command line come from the window's saved settings.


USING THE WINDOW
----------------
My station   Location as a grid square (EL87, EL87qk) or "lat, lon" (27.95, -82.46 or
             27.95N 82.46W). Mode, power and antenna gain set how much loss the signal can
             take: FT8 and CW get through where SSB does not.

Path         NVIS / local: everyone within the radius (typical net or SAR coverage).
             To station: one far station, by grid, lat/lon, or a NetLog callsign.
             Whole net: every NetLog member with a position within the distance limit; the
             best channel is the one with the highest average reliability, and the table shows
             how many members it covers (50% or better).

Channels     Amateur bands, the NetLog channel list (read from netlog.db, the "frequencies"
             table), or your own list (Edit...). The NetLog database is found automatically
             next to this folder in "..\Net Log\netlog.db", or choose it with
             "NetLog database...". The app only reads it, never changes it.

Tabs         Best now        every channel ranked, with its reliability and rating
             Next 24 hours   reliability by hour (UTC and local); the outlined cell is the best
                             channel for that hour - useful for planning net times and alternates
             Stations        in station or net mode, how each station does on the best channel,
                             and which channel would suit it best by itself

Data refreshes every 15 minutes, or press F5 / Refresh. Help > How it works explains the model.


DATA SOURCES
------------
NOAA Space Weather Prediction Center (services.swpc.noaa.gov): 10.7 cm solar flux, planetary
K index, GOES X-ray flux, NOAA R/S/G space-weather scales.
KC2G (prop.kc2g.com): effective sunspot number and latest GIRO / NOAA ionosonde readings.
Open-Meteo (open-meteo.com): where thunderstorms are now and over the next 24 hours, on a grid
around the path, for atmospheric static. Weather data by Open-Meteo.com (CC BY 4.0).


LIMITS
------
This is a planning aid, not a substitute for listening, ALE soundings or official forecasts.
- The ionosphere model is built for mid-latitudes. Near the geomagnetic equator and in the
  auroral zones it is rougher; polar-cap absorption (S-scale events) is not modeled.
- Ground wave is not modeled: paths under 30 km are treated as 30 km NVIS.
- Sporadic-E openings and the grey-line enhancement are not predicted.
- Forecast hours assume no solar flares.
- Thunderstorm areas come from a weather model (Open-Meteo), not from lightning detection,
  sampled on a grid about 200 km apart, so a single small storm can be missed. Static from
  storms is assumed loud within about 250 km and noticeable within 800 km (more at night).


FILES
-----
best_hf_freq.py   the window and the command line
hfprop.py         propagation model (MUF, LUF, absorption, reliability)
spacewx.py        downloads and caches the live data
planner.py        channels, NetLog stations, and the 24-hour plan
disclaimer.py     first-use disclaimer
test_best_hf.py   tests:  python -m unittest -v test_best_hf

Settings, the data cache and the disclaimer acceptance are kept in
%APPDATA%\N. Tesla\Best-HF-Freq.
