========================================================================
USCG Auxiliary District 7 — HF Contingency Net Log
Python Application — Installation and Setup Guide
========================================================================

CONTENTS OF THIS PACKAGE
-------------------------
  netlog.py               Data layer (logic, database, calculations)
  netlog_gui.py           Graphical user interface
  netlog_db_schema.sql    Members/reference database schema (reference)
  net_archives_db_schema.sql  Archive database schema (reference)
  netlog_db_schema.pdf    Members database diagram (reference)
  net_archives_db_schema.pdf  Archive database diagram (reference)
  readme.txt              This file


OVERVIEW
--------
The Net Log application is a Python/tkinter program that replaces the
original Excel-based NetLog.xlsm workbook.  It stores member data and
net session archives in two SQLite databases that are created
automatically in the same folder as the program on the first run:

  netlog.db           Members, reference data, email list
  net_archives.db     Completed session archives


STEP 1 — INSTALL PYTHON
------------------------
1. Open your web browser and go to:
       https://www.python.org/downloads/windows/

2. Click "Download Python 3.x.x" (the large yellow button for the
   latest stable release).

3. Run the downloaded installer (python-3.x.x-amd64.exe).

   IMPORTANT: On the first screen of the installer, check BOTH of
   these boxes before clicking Install Now:
     [x] Add Python to PATH
     [x] Install launcher for all users

4. Click "Install Now" and let the installer complete.

5. Verify the installation — open a Command Prompt (press Windows+R,
   type cmd, press Enter) and run:
       python --version
   You should see something like: Python 3.13.x


STEP 2 — INSTALL REQUIRED PACKAGES
------------------------------------
The application requires one third-party library:

  openpyxl   — reads Excel workbooks for distance/bearing look-up tables

An optional second library enables loading a password-protected
NetLog.xlsm directly; if it is not installed the application still
runs fully and uses great-circle calculations instead:

  msoffcrypto-tool   — decrypts password-protected .xlsm files

To install both, open a Command Prompt and run:

    pip install openpyxl msoffcrypto-tool

If you only need the core functionality:

    pip install openpyxl

To confirm the installation:

    pip show openpyxl


STEP 3 — PLACE THE PROGRAM FILES
----------------------------------
Copy all files from this package into a single folder on your PC.
A suggested location is:

    C:\Users\<YourName>\Documents\NetLog\

The program will create the two SQLite databases (netlog.db and
net_archives.db) in that same folder the first time it runs.

If you have an existing NetLog.xlsm file you may place it in the
same folder.  The program will decrypt and read the DISTANCE and
BEARING worksheets from it automatically.


STEP 4 — RUN THE APPLICATION
------------------------------
Option A — Double-click (requires file association, see Step 5):
  Double-click netlog_gui.py

Option B — Command Prompt (always works):
  1. Open a Command Prompt.
  2. Navigate to the folder:
         cd "C:\Users\<YourName>\Documents\NetLog"
  3. Run:
         python netlog_gui.py

Option C — Right-click:
  Right-click netlog_gui.py → Open with → Python


STEP 5 — SET THE DEFAULT FILE ASSOCIATION FOR .PY FILES (WINDOWS 10/11)
-------------------------------------------------------------------------
This lets you double-click any .py file to run it directly with Python.

Method 1 — Using Windows Settings (recommended):
  1. Right-click netlog_gui.py in File Explorer.
  2. Select "Open with" → "Choose another app".
  3. Scroll down and click "More apps" if Python is not listed.
  4. Click "Look for another app on this PC".
  5. Navigate to the Python launcher:
         C:\Windows\py.exe
     (or, if you used "Install for all users":
         C:\Program Files\Python3x\python.exe)
  6. Check "Always use this app to open .py files".
  7. Click OK.

Method 2 — Using the Command Prompt (run as Administrator):
  1. Press Windows+S, type "cmd", right-click "Command Prompt",
     choose "Run as administrator".
  2. Run the following two commands:

         assoc .py=Python.File
         ftype Python.File="C:\Windows\py.exe" "%1" %*

     If Python was installed for the current user only, replace the
     path with the full path to python.exe, for example:
         C:\Users\<YourName>\AppData\Local\Programs\Python\Python313\python.exe

Method 3 — Using the Python installer:
  Re-run the Python installer, click "Modify", and ensure
  "Associate files with Python" is checked under Optional Features.

NOTE: Even with a file association set, Windows may open a terminal
(black) window behind the GUI.  To suppress it, you can rename or
copy python.exe to pythonw.exe and associate .py with pythonw.exe,
or create a small batch file (run.bat) with:

    @echo off
    pythonw netlog_gui.py


STEP 6 — CREATING A DESKTOP SHORTCUT (OPTIONAL)
------------------------------------------------
1. Right-click the Desktop → New → Shortcut.
2. In the location box enter:
       pythonw "C:\Users\<YourName>\Documents\NetLog\netlog_gui.py"
   (Use the full path to your folder.)
3. Click Next, give it a name such as "Net Log", click Finish.
4. Right-click the new shortcut → Properties → Change Icon if desired.


TROUBLESHOOTING
---------------
"python is not recognized as an internal or external command"
  → Python was not added to PATH during installation.
    Re-run the installer, choose "Modify", and tick "Add Python to
    environment variables".

"No module named 'openpyxl'"
  → Run:  pip install openpyxl

"No module named 'tkinter'"
  → tkinter is included with the standard Windows Python installer.
    Re-run the installer and ensure "tcl/tk and IDLE" is checked.

"PermissionError" when writing databases
  → Move the program files to a folder under your Documents or
    Desktop where you have write permission (not Program Files).

The application still runs if NetLog.xlsm is absent or if openpyxl /
msoffcrypto-tool are not installed — it will use the great-circle
formula to compute distance and bearing from coordinates stored in
the member database instead of the Excel look-up tables.


PACKAGE VERSIONS TESTED
------------------------
  Python        3.14.x
  openpyxl      3.1.5
  msoffcrypto-tool  5.4.2


========================================================================
USCG Auxiliary District 7 — Southeast Region
Net Control Station (NCS) Support Tool
========================================================================
