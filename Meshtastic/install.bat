@echo off
setlocal EnableExtensions
rem ===========================================================================
rem  Meshtastic GUI - installer
rem
rem  Copies the program to your own Programs folder (no administrator rights
rem  needed), installs the Python packages it needs, and puts "Meshtastic GUI"
rem  shortcuts on the Desktop and Start menu.
rem
rem    install.bat                        install to %LOCALAPPDATA%\Programs\Meshtastic GUI
rem    install.bat "D:\Some Folder"       install somewhere else
rem    install.bat ... /noshortcuts       do not make shortcuts
rem    install.bat ... /nodriver          do not offer the CH340 USB driver
rem
rem  Radio settings backups are NOT in the program folder; they are kept in
rem  %APPDATA%\Meshtastic GUI\settings_backups and are never overwritten by
rem  installing, reinstalling or updating.
rem ===========================================================================
cd /d "%~dp0"
set "SRC=%~dp0"
if "%SRC:~-1%"=="\" set "SRC=%SRC:~0,-1%"

set "DEST=%LOCALAPPDATA%\Programs\Meshtastic GUI"
set "SHORTCUTS=1"
set "DRIVER=1"
:args
if "%~1"=="" goto args_done
if /i "%~1"=="/noshortcuts" (
    set "SHORTCUTS=0"
) else if /i "%~1"=="/nodriver" (
    set "DRIVER=0"
) else (
    set "DEST=%~f1"
)
shift
goto args
:args_done

echo.
echo  Meshtastic GUI - installation
echo  =============================
echo.

rem Opened straight from inside the zip? Then the other files are not here.
if not exist "%SRC%\meshtastic_gui.py" (
    echo  The program files are not next to install.bat.
    echo  If you received a zip file: right-click it, choose "Extract All...", open the
    echo  extracted "Meshtastic GUI" folder and run install.bat from there.
    echo.
    pause
    exit /b 1
)

rem ---- 1. Copy the program -----------------------------------------------------
if /i "%SRC%"=="%DEST%" (
    echo  Already running from the install folder; nothing to copy.
) else (
    echo  Installing to: %DEST%
    if not exist "%DEST%" mkdir "%DEST%"
    if errorlevel 1 goto copy_failed
    for %%F in (meshtastic_gui.py mesh_client.py firmware.py netdiscovery.py printing.py settings_backup.py disclaimer.py run.bat uninstall.bat README.txt LICENSE CH341SER.EXE "Requirements to run Meshtestic_gui.py in windows.doc") do (
        if exist "%SRC%\%%~F" (
            copy /y "%SRC%\%%~F" "%DEST%\%%~F" >nul
            if errorlevel 1 goto copy_failed
        )
    )
    if exist "%DEST%\__pycache__" rmdir /s /q "%DEST%\__pycache__"
)

rem ---- 2. Python and the packages ---------------------------------------------
rem run.bat /setup picks a suitable Python (3.10 - 3.14), checks tkinter and installs
rem meshtastic, truststore and esptool when they are missing. It leaves PY set.
echo.
call "%DEST%\run.bat" /setup
if errorlevel 1 (
    echo.
    echo  Installation stopped: fix the problem above, then run install.bat again.
    pause
    exit /b 1
)
cd /d "%SRC%"
set "PYEXE="
for /f "usebackq delims=" %%P in (`%PY% -c "import sys; print(sys.executable)"`) do set "PYEXE=%%P"
if not defined PYEXE (
    echo  Could not find where Python is installed.
    pause
    exit /b 1
)

rem ---- 3. Check that it loads ----------------------------------------------------
call %PY% -c "import sys; sys.path.insert(0, r'%DEST%'); import mesh_client, firmware, settings_backup" >nul 2>&1
if errorlevel 1 (
    echo  The program files were copied but do not load. Run "%DEST%\run.bat" to see the error.
    pause
    exit /b 1
)
echo  Program and packages are ready.

rem ---- 4. Shortcuts ---------------------------------------------------------------
if "%SHORTCUTS%"=="0" goto driver
rem Use pythonw.exe (no black console window) when it is next to python.exe.
set "TARGET=%PYEXE%"
for %%D in ("%PYEXE%") do if exist "%%~dpDpythonw.exe" set "TARGET=%%~dpDpythonw.exe"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$s = New-Object -ComObject WScript.Shell; $q = [char]34;" ^
  "$dirs = if ($env:LNKDIR) { @($env:LNKDIR) } else { @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs')) };" ^
  "foreach ($d in $dirs) { $l = $s.CreateShortcut((Join-Path $d 'Meshtastic GUI.lnk'));" ^
  "  $l.TargetPath = $env:TARGET; $l.Arguments = $q + (Join-Path $env:DEST 'meshtastic_gui.py') + $q;" ^
  "  $l.WorkingDirectory = $env:DEST; $l.Description = 'Meshtastic radio control';" ^
  "  $l.IconLocation = $env:TARGET + ',0'; $l.Save() }"
if errorlevel 1 (
    echo  Could not make the shortcuts. Start the program with "%DEST%\run.bat".
) else (
    echo  Shortcuts made on the Desktop and in the Start menu: "Meshtastic GUI"
)

rem ---- 5. Optional USB driver ------------------------------------------------------
:driver
if "%DRIVER%"=="0" goto done
if not exist "%DEST%\CH341SER.EXE" goto done
echo.
echo  Radios with a CH340 USB chip need the CH341 driver before Windows gives them a
echo  COM port. Other radios ^(CP210x, nRF52, ESP32-S3 native USB^) do not need it.
choice /c YN /n /m "  Install the CH340 driver now? It asks for administrator permission. [Y/N] "
if errorlevel 2 goto done
start "" /wait "%DEST%\CH341SER.EXE"
echo  In the driver window click INSTALL, then unplug and replug the radio.

:done
echo.
echo  Installed. Start it from the "Meshtastic GUI" shortcut, or with:
echo    "%DEST%\run.bat"
echo  Radio settings backups are kept in:
echo    %APPDATA%\Meshtastic GUI\settings_backups
echo  Instructions: "%DEST%\README.txt"
echo.
if "%SHORTCUTS%"=="1" pause
exit /b 0

:copy_failed
echo.
echo  Could not copy the program to "%DEST%".
echo  Close the Meshtastic GUI if it is running, and try again.
pause
exit /b 1
