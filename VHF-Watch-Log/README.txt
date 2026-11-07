VHF Watch Log 1.0
=================
Copyright (c) 2026 N. Tesla, Inc. All rights reserved. See LICENSE.

An electronic VHF communications watch log for a Coast Guard Auxiliary radio station
guarding Auxiliary patrols. It replaces the paper "Auxiliary Radio" log: vessels, coxswains
and crew come from a database instead of being written in by hand, and the log keeps to
the Coast Guard's log-keeping rules.


RUNNING IT
----------
Double-click run.bat. It needs Python 3.9 or newer with tkinter ("tcl/tk and IDLE" ticked
when installing Python). No other packages and no internet connection are needed.

The first time it starts it shows a disclaimer that must be accepted.

To try it with made-up boats and people (no real members) without touching your own data:
    run.bat --demo "%TEMP%\watchlog_demo.db"


SETTING UP (once)
-----------------
Data > Vessels          Facility number, name, radio call sign, cell #, owner, type/length,
                        MMSI, facility inspection expiry. Import CSV... accepts columns
                        facility_no, name, call_sign, cell, owner, description, mmsi,
                        inspection_expires.
Data > Members and      Member number (7 digits), name, phone, and each qualification:
       qualifications   Coxswain, Crew, PWC, TCO, each Qualified / Trainee / REYR, or blank if
                        not held. One person can be both coxswain and crew. Import CSV...
                        accepts member_no, last_name, first_name, phone, coxswain, crew, pwc, tco
                        (an AUXDATA export can be reshaped to these columns).
                        The database also has two views, "coxswains" (name, member #, phone)
                        and "crew" (name, member #, status), for anyone reading it directly.
Data > Radio watch      The radio watch standers (table radio_watch_standers): name, member
       standers         number, telephone and TCO date (MM/DD/YYYY). They are the choices for
                        Watchstander when a watch is opened. Import CSV... accepts member_no,
                        last_name, first_name, phone, tco_date.
Data > Radio stations   The Auxiliary radio stations (table radio_stations): call sign, street,
                        city, state, ZIP, latitude/longitude and contact phone. Lat/Long can be
                        typed as on a chart (30 40.09 N, 081 27.67 W), with seconds, or as
                        decimal degrees (30.6682, -81.4612); it is shown as degrees and decimal
                        minutes. Import CSV... accepts call_sign, street, city, state, zip,
                        latitude, longitude, phone.
                        The station chosen for a watch is printed on the completed log (PDF,
                        Word and printout) below the watchstander signature. Its details are
                        copied into the watch when it is opened, so changing a station later
                        does not change logs already kept.
Data > Channels         Channels offered in the log (16, 22A, 83A ... are preloaded).
Data > Contacts         The telephone list printed at the foot of the log (preloaded from the
                        current paper form: Station Mayport, Sector Jacksonville, etc.).
Data > Settings         Station call sign, default channel, ops-normal interval (30 min),
                        warning time, ICS 309 incident name, and whether vessel cell numbers
                        are printed.


A WATCH
-------
1. Open watch...   Radio station and watchstander (picked from the two lists above; the
                   station used last is offered first), channels guarded, time check (the time.gov button opens
                   the official clock; "Checked" records it), radio check, equipment status.
2. Add patrol...   Vessel, patrol order (PO#), GAR score, coxswain, crew (any number; trainees
                   are marked automatically from their status), POB, cell #, float plan / area,
                   ops-normal interval. Any number of vessels.
3. Log entries     Click a vessel on the patrol board, then Underway / Ops normal / Moored to
                   fill in the form, type the position and heading, and press Enter. Leave
                   Time blank for "now", or type a late entry as HHMM (local) or HHMMZ (UTC).
                   Other entry types: Position, Comms, Weather, Phone, Distress, Urgency,
                   Safety, Lost comms, Radio check, Equipment, Watch relief, Other.
                   "Vessel / station" can also be typed freely (e.g. Station Mayport).
4. Patrol board    Shows for each vessel the last contact and when the next ops-normal report
                   is due. Amber = due within 5 minutes, red = overdue. When a boat is overdue
                   a red banner appears and the computer beeps every minute until it reports.
                   Any contact with the boat restarts its clock (M2000.3G ch. 5, CG Boat Communications, 8.a).
5. Mistakes        Select the entry, Strike / correct... (or double-click). The original stays
                   in the log struck through with your initials, and the corrected entry is
                   added next to it. Entries are never erased.
6. Close and sign  Locks the watch permanently (read only) and offers to save the completed
                   log as a PDF or Word file.

SAVING AND PRINTING THE COMPLETED LOG
File > Save watch log as PDF...          (or the Save PDF... button under the log)
File > Save watch log as Word (.docx)... (or the Save Word... button)
Both are laid out like the paper form (header, vessel blocks with COX / CREW / Trainee, the
radio log with struck-through corrections and initials, signature lines, retention date and
contacts), with "Page X of Y" at the foot. You choose the folder; the file is named like
"Watch Log 2026-07-21 0830 Amelia 9.pdf". Saving a watch that is still open is allowed but
the copy says "WATCH STILL OPEN - not signed". Any past watch can be saved from File > Watches.
  - PDF is made by Microsoft Edge (part of Windows 11) or Google Chrome running hidden; it
    takes 10-20 seconds. If neither is installed, LibreOffice is used if present.
  - Word (.docx) is written directly by the program (no Word needed to make it); it opens in
    Word, LibreOffice or Google Docs and can be edited there. Remember that the locked log in
    the database, not an edited copy, is the official record.
File > Print watch log opens the same layout in your web browser to print.
File > ICS 309 makes a FEMA ICS 309 Communications Log of the same watch, for SAR cases,
exercises and events. File > 7030 activity CSV lists each patrol (PO#, facility, coxswain,
crew, POB, underway/moored times, hours) and the watch hours, for the ANSC 7030 report.
File > Watches lists all past watches with their retention date; old ones can be viewed and
reprinted.


LOG-KEEPING RULES BUILT IN
--------------------------
From the Coast Guard Communications Manual, COMDTINST M2000.3G (ch. 8 D.2; ch. 5 "CG Boat Communications") and
the Auxiliary Operations Process Guide Vol III, Telecommunications (16798.33B, 3-F):
  - unit name, call sign, date/time in UTC (local time shown alongside), channel, the
    traffic, equipment status and checks
  - official time check at the start of the watch
  - no erasures; strike-through corrections initialed
  - distress and urgency entries need a synopsis and print in bold
  - ops normal every 30 minutes unless the operational commander sets another interval
  - retention: 1 year, 3 years when distress traffic was logged (shown on each log and in
    File > Watches; nothing is deleted automatically)
  - a closed log is locked read only, which takes the place of a signature on an
    electronic log; the printout still has signature lines


PRIVACY
-------
The database holds member names, member numbers and phone numbers. It is kept outside this
folder, in %APPDATA%\N. Tesla\VHF Watch Log\watchlog.db, so it is never shipped or committed
with the program. Printed logs go to the "prints" folder beside it. Use File > Back up
database... to copy it, and keep copies private. Radio logs can be released under the
Freedom of Information Act; refer any request to the Director and the FOIA coordinator
(AOPG Vol III 3-F.2). Settings can leave vessel cell numbers off the printout.


FILES
-----
watchlog.py        the window (also --db FILE, --demo FILE)
watchdb.py         database and log rules
reports.py         printed log, PDF, ICS 309, 7030 CSV, contact sheet
docx_export.py     Word (.docx) watch log, standard library only
disclaimer.py      first-use disclaimer
test_watchlog.py   tests:  python -m unittest test_watchlog
