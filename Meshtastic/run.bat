@echo off
rem Launches the Meshtastic GUI and keeps this window open if anything goes wrong,
rem so the error can be read instead of flashing by. Installs missing packages first.
cd /d "%~dp0"

python --version >nul 2>&1
if errorlevel 1 (
    echo Python was not found. Install Python 3.10 or later from python.org/downloads
    echo and check "Add Python to PATH" and "tcl/tk and IDLE" during setup.
    pause
    exit /b 1
)

python -c "import meshtastic, pubsub, tkinter" >nul 2>&1
if errorlevel 1 (
    echo Installing required packages ^(one-time^)...
    python -m pip install meshtastic truststore esptool
    if errorlevel 1 (
        echo.
        echo Package install failed. If the error mentions tkinter, reinstall Python with
        echo "tcl/tk and IDLE" checked. Otherwise check the internet connection.
        pause
        exit /b 1
    )
)

python meshtastic_gui.py
if errorlevel 1 (
    echo.
    echo The app exited with an error ^(see above^).
    pause
)
