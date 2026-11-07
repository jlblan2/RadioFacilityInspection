@echo off
rem Launches VHF Watch Log and keeps this window open if anything goes wrong,
rem so the error can be read instead of flashing by. No extra packages are needed.
cd /d "%~dp0"

rem Which Python to use: the one named in WATCHLOG_PYTHON if that is set; otherwise the newest
rem one the "py" launcher knows about; otherwise plain "python".
set "PY=%WATCHLOG_PYTHON%"
if not defined PY (
    call py -3 -c "import sys" >nul 2>&1
    if not errorlevel 1 set "PY=py -3"
)
if not defined PY set "PY=python"

rem "call" so that a .bat/.cmd shim (pyenv-win, for example) returns here instead of ending this script.
call %PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 3)" >nul 2>&1
if errorlevel 1 (
    echo Python 3.9 or newer was not found. Install Python 3.13 ^(64-bit^) from python.org/downloads
    echo and check "Add Python to PATH" and "tcl/tk and IDLE" during setup.
    pause
    exit /b 1
)

call %PY% -c "import tkinter" >nul 2>&1
if errorlevel 1 (
    echo.
    echo tkinter, the window toolkit, is missing from this Python and pip cannot add it.
    echo Reinstall Python and make sure "tcl/tk and IDLE" is ticked.
    pause
    exit /b 1
)

call %PY% watchlog.py %*
if errorlevel 1 (
    echo.
    echo The app exited with an error ^(see above^).
    pause
)
