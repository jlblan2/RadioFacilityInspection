@echo off
rem Launches the Meshtastic GUI and keeps this window open if anything goes wrong,
rem so the error can be read instead of flashing by. Installs missing packages first.
cd /d "%~dp0"

rem Which Python to use: the one named in MESHTASTIC_PYTHON if that is set; otherwise the newest
rem supported one the "py" launcher knows about (3.10 - 3.14: the Windows Bluetooth packages this
rem app needs have no prebuilt version for newer Pythons, so a brand-new Python installed alongside
rem must not be picked); otherwise plain "python".
set "PY=%MESHTASTIC_PYTHON%"
if not defined PY (
    for %%V in (3.13 3.14 3.12 3.11 3.10) do (
        if not defined PY (
            call py -%%V -c "import sys" >nul 2>&1
            if not errorlevel 1 set "PY=py -%%V"
        )
    )
)
if not defined PY set "PY=python"

rem "call" so that a .bat/.cmd shim (pyenv-win, for example) returns here instead of ending this script.
call %PY% --version >nul 2>&1
if errorlevel 1 (
    echo Python was not found. Install Python 3.13 ^(64-bit^) from python.org/downloads
    echo and check "Add Python to PATH" and "tcl/tk and IDLE" during setup.
    pause
    exit /b 1
)

rem Prints which Python this is. Exit 3 = older than 3.10 (the app cannot run), 4 = too new or free-threaded.
call %PY% -c "import sys,struct,sysconfig; v=sys.version_info[:2]; bad=v>(3,14) or bool(sysconfig.get_config_var('Py_GIL_DISABLED')); print('Using Python', sys.version.split()[0], str(struct.calcsize('P')*8)+'-bit'); sys.exit(3 if v<(3,10) else 4 if bad else 0)"
set "PYSTATUS=%errorlevel%"
if "%PYSTATUS%"=="3" (
    echo.
    echo This Python is older than 3.10, which the app needs. Install Python 3.13 ^(64-bit^) from
    echo python.org/downloads, keep the "py launcher" option ticked, and run this file again.
    pause
    exit /b 1
)
if "%PYSTATUS%"=="4" (
    echo.
    echo WARNING: this Python is newer than 3.14 ^(or is a free-threaded build^). The Windows Bluetooth
    echo packages the app needs have no prebuilt version for it, so the install below will probably
    echo fail. If it does, install Python 3.13 ^(64-bit^) from python.org/downloads and run this file again.
    echo.
)

call %PY% -c "import tkinter" >nul 2>&1
if errorlevel 1 (
    echo.
    echo tkinter, the window toolkit, is missing from this Python and pip cannot add it.
    echo Reinstall Python and make sure "tcl/tk and IDLE" is ticked.
    pause
    exit /b 1
)

rem The check also needs a feature from recent meshtastic releases, so an old copy gets upgraded.
call %PY% -c "import meshtastic, pubsub; from meshtastic.protobuf import config_pb2; config_pb2.Config.LoRaConfig.FEM_LNA_Mode" >nul 2>&1
if errorlevel 1 (
    echo Installing required packages ^(one-time^)...
    call %PY% -m pip install --upgrade meshtastic truststore esptool
    if errorlevel 1 (
        echo.
        echo Package install failed.
        echo   - "Failed building wheel" for winrt-...: this Python is too new ^(3.15 or later^) or a
        echo     free-threaded build, and the Bluetooth packages have no prebuilt version for it.
        echo     Install Python 3.13 ^(64-bit^) from python.org/downloads, keep the "py launcher" option
        echo     ticked, then run this file again - it picks 3.13 automatically.
        echo   - Anything else: check the internet connection and try again.
        pause
        exit /b 1
    )
    call %PY% -c "import meshtastic, pubsub; from meshtastic.protobuf import config_pb2; config_pb2.Config.LoRaConfig.FEM_LNA_Mode" >nul 2>&1
    if errorlevel 1 (
        echo.
        echo The install finished, but the meshtastic package is still too old for this app:
        call %PY% -m pip show meshtastic 2>nul | findstr /b "Name Version"
        echo This usually means pip could only find an old release for this Python ^(older than 3.10, or
        echo 3.15 and newer^). Install Python 3.13 ^(64-bit^) from python.org/downloads, keep the
        echo "py launcher" option ticked, and run this file again.
        pause
        exit /b 1
    )
)

call %PY% meshtastic_gui.py
if errorlevel 1 (
    echo.
    echo The app exited with an error ^(see above^).
    pause
)
