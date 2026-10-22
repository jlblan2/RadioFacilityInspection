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

call %PY% -c "import sys,struct,sysconfig; ok=(3,10)<=sys.version_info[:2]<=(3,14) and not sysconfig.get_config_var('Py_GIL_DISABLED'); print('Using Python', sys.version.split()[0], str(struct.calcsize('P')*8)+'-bit'); sys.exit(0 if ok else 3)"
set "PYSTATUS=%errorlevel%"
if "%PYSTATUS%"=="3" (
    echo.
    echo WARNING: this Python is outside the 3.10 - 3.14 range ^(or is a free-threaded build^). The Windows
    echo Bluetooth packages the app needs have no prebuilt version for it, so the install below will
    echo probably fail. If it does, install Python 3.13 ^(64-bit^) from python.org/downloads and run this
    echo file again.
    echo.
)

rem Also checks for a feature the app needs, so an older meshtastic that is already installed gets upgraded.
call %PY% -c "import meshtastic, pubsub, tkinter; from meshtastic.protobuf import config_pb2; config_pb2.Config.LoRaConfig.FEM_LNA_Mode" >nul 2>&1
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
        echo   - tkinter in the error: reinstall Python with "tcl/tk and IDLE" checked.
        echo   - Anything else: check the internet connection and try again.
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
