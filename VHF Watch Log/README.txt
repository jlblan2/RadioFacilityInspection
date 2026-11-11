VHF Watch Log 1.0
=================
Copyright (c) 2026 N. Tesla, Inc. All rights reserved. See LICENSE.

An electronic VHF communications watch log for a Coast Guard Auxiliary radio station
guarding Auxiliary patrols. It replaces the paper "Auxiliary Radio" log: vessels, coxswains
and crew come from a database instead of being written in by hand, and the log keeps to
the Coast Guard's log-keeping rules.

It is a stand-alone program for VHF communications. It is separate from the HF Net Log
program and does not use its files or database.


INSTALLING
----------
1. Python 3.9 or newer is needed (free, from https://www.python.org/downloads/). When
   installing Python tick "Add python.exe to PATH" and "tcl/tk and IDLE". No other packages
   and no internet connection are needed by the program.
2. If you received VHF_Watch_Log_Setup.zip: right-click it, choose "Extract All...", then
   open the extracted "VHF Watch Log" folder. (Running install.bat from inside the zip
   without extracting does not work.) Otherwise copy this whole "VHF Watch Log" folder to
   the computer, or open it from a USB stick or network drive.
   Double-click install.bat.
   It checks Python, copies the program to
       %LOCALAPPDATA%\Programs\VHF Watch Log
   (your own Programs folder, so administrator rights are not needed), and puts a
   "VHF Watch Log" shortcut on the Desktop and in the Start menu.
   To install somewhere else:   install.bat "D:\Radio\VHF Watch Log"
3. Start it from the shortcut. The first time it shows a disclaimer that must be accepted.

Updating: run install.bat from the new version; it replaces the program files only.
Removing: run uninstall.bat in the installed folder. It removes the program and shortcuts
but KEEPS your logs (radio logs must be kept 1 year, 3 years with distress traffic).

Your logs are never in the program folder. They are in one database file:
    %APPDATA%\N. Tesla\VHF Watch Log\watchlog.db
Installing, updating or uninstalling never touches it. Use File > Back up database... to
copy it, for example before moving to a new computer (put the copy in that same place on
the new computer, or open it with run.bat --db "path\to\copy.db").


RUNNING IT WITHOUT INSTALLING
-----------------------------
Double-click run.bat in this folder.

To try it with made-up boats and people (no real members) without touching your own data:
    run.bat --demo "%TEMP%\watchlog_demo.db"


SETTING UP (once)
-----------------
Data > Vessels          Facility number, name, radio call sign, cell #, owner, type/length,
                        MMSI, facility inspection expiry. Import CSV... accepts columns
                        facility_no, name, call_sign, cell, owner, description, mmsi,
                        inspection_expires.
Data > Members and      Member number (7 digits), name, phone, and each qualification:
       qualifications   Coxswain, Crew, PWC, TCO, QE (Qualification Examiner), each
                        Qualified / Trainee / REYR, or blank if not held. One person can be both
                        coxswain and crew. "Show: QEs" lists the Qualification Examiners.
                        Import CSV... accepts member_no, last_name, first_name, phone, coxswain,
                        crew, pwc, tco, qe (an AUXDATA export can be reshaped to these columns).
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
Data > Patrol types     The Mission choices on a patrol (table patrol_types): Safety patrol,
                        Regatta / marine event patrol, Training, SAR, ATON verification and
                        Chart updating are preloaded. Add, rename, remove or reorder them;
                        patrols already logged keep the mission they were given.
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
                   are marked automatically from their status), POB, cell # (filled in with
                   the coxswain's phone, or the boat's cell if the coxswain has none), mission (from
                   Data > Patrol types, or type another), float plan / area, ops-normal
                   interval. Any number of vessels.
3. Log entries     Click a vessel on the patrol board, then Underway / Ops normal / Moored to
                   fill in the form, type the position and heading, and press Enter. Leave
                   Time blank for "now", or type a late entry as HHMM (local) or HHMMZ (UTC).
                   Other entry types: Position, Comms, Weather, Phone, Distress, Urgency,
                   Safety, Lost comms, Radio check, Equipment, Watch relief, Other.
                   "Vessel / station" can also be typed freely (e.g. Station Mayport).
                   From / to: who started the call. "To" (the default) = you, the radio
                   watch stander, called the vessel, as for routine checks (underway, ops
                   normal, moored, position). "From" = the vessel called you; picking
                   Distress, Urgency or Safety switches to From, since with an issue the
                   vessel initiates the call. Change it whenever the call went the other
                   way. The log shows "(to)" after the station's name for "To" entries, and
                   the ICS 309 puts the names in its From and To columns.
                   GAR: filled in with the vessel's latest GAR score. If the GAR changes
                   (weather, crew fatigue...), type the new score before logging; the app
                   asks you to confirm, and the change shows in the GAR column ("22 → 31"),
                   on the patrol board, and on the printed log ("GAR changed 22 → 31" on
                   that entry, and the GAR history under Vessels on patrol). The GAR on the
                   patrol itself stays the score at the start. A wrong GAR is fixed like any
                   entry: Strike / correct.
4. Patrol board    Lists each vessel by its radio call sign (from Data > Vessels; the facility
                   number is shown until a call sign is entered) and its coxswain with phone
                   number, with the last contact and when the next ops-normal report
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
install.bat        installs the program and makes the shortcuts
uninstall.bat      removes the program (keeps your logs)
run.bat            starts the program from whatever folder it is in
watchlog.py        the window (also --db FILE, --demo FILE)
watchdb.py         database and log rules
reports.py         printed log, PDF, ICS 309, 7030 CSV, contact sheet
docx_export.py     Word (.docx) watch log, standard library only
disclaimer.py      first-use disclaimer
test_watchlog.py   tests:  python -m unittest test_watchlog
make_zip.py        builds VHF_Watch_Log_Setup.zip to send to other watch standers
