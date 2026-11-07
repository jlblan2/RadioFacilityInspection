@echo off
setlocal EnableExtensions
rem ===========================================================================
rem  VHF Watch Log - installer
rem
rem  Copies the program to your own Programs folder (no administrator rights
rem  needed) and puts "VHF Watch Log" shortcuts on the Desktop and Start menu.
rem
rem    install.bat                        install to %LOCALAPPDATA%\Programs\VHF Watch Log
rem    install.bat "D:\Some Folder"       install somewhere else
rem    install.bat ... /noshortcuts       do not make shortcuts
rem
rem  Your log database is NOT in the program folder; it is kept in
rem  %APPDATA%\N. Tesla\VHF Watch Log\watchlog.db and is never overwritten by
rem  installing, reinstalling or updating.
rem ===========================================================================
cd /d "%~dp0"
set "SRC=%~dp0"
if "%SRC:~-1%"=="\" set "SRC=%SRC:~0,-1%"

set "DEST=%LOCALAPPDATA%\Programs\VHF Watch Log"
set "SHORTCUTS=1"
:args
if "%~1"=="" goto args_done
if /i "%~1"=="/noshortcuts" (set "SHORTCUTS=0") else (set "DEST=%~f1")
shift
goto args
:args_done

echo.
echo  VHF Watch Log - installation
echo  ============================
echo.

rem Opened straight from inside the zip? Then the other files are not here.
if not exist "%SRC%\watchlog.py" (
    echo  The program files are not next to install.bat.
    echo  If you received a zip file: right-click it, choose "Extract All...", open the
    echo  extracted "VHF Watch Log" folder and run install.bat from there.
    echo.
    pause
    exit /b 1
)

rem ---- 1. Python 3.9+ with tkinter --------------------------------------------
set "PY=%WATCHLOG_PYTHON%"
if not defined PY (
    call py -3 -c "import sys" >nul 2>&1
    if not errorlevel 1 set "PY=py -3"
)
if not defined PY set "PY=python"
call %PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 3)" >nul 2>&1
if errorlevel 1 (
    echo  Python 3.9 or newer was not found.
    echo.
    echo  Install Python from https://www.python.org/downloads/ ^(64-bit^), and during setup tick
    echo    [x] Add python.exe to PATH
    echo    [x] tcl/tk and IDLE
    echo  then run install.bat again.
    echo.
    pause
    exit /b 1
)
call %PY% -c "import tkinter, sqlite3" >nul 2>&1
if errorlevel 1 (
    echo  This Python is missing tkinter ^(the window toolkit^).
    echo  Run the Python installer again, choose Modify, tick "tcl/tk and IDLE", then run install.bat again.
    echo.
    pause
    exit /b 1
)
set "PYEXE="
for /f "usebackq delims=" %%P in (`%PY% -c "import sys; print(sys.executable)"`) do set "PYEXE=%%P"
for /f "usebackq delims=" %%V in (`%PY% -c "import sys; print('.'.join(map(str, sys.version_info[:3])))"`) do set "PYVER=%%V"
echo  Python %PYVER% found: %PYEXE%

rem ---- 2. Copy the program -----------------------------------------------------
if /i "%SRC%"=="%DEST%" (
    echo  Already running from the install folder; nothing to copy.
) else (
    echo  Installing to: %DEST%
    if not exist "%DEST%" mkdir "%DEST%"
    if errorlevel 1 goto copy_failed
    for %%F in (watchlog.py watchdb.py reports.py docx_export.py disclaimer.py run.bat uninstall.bat README.txt LICENSE) do (
        copy /y "%SRC%\%%F" "%DEST%\%%F" >nul
        if errorlevel 1 goto copy_failed
    )
    if exist "%DEST%\__pycache__" rmdir /s /q "%DEST%\__pycache__"
)

rem ---- 3. Check that it starts ---------------------------------------------------
call %PY% -c "import sys; sys.path.insert(0, r'%DEST%'); import watchdb, reports, docx_export" >nul 2>&1
if errorlevel 1 (
    echo  The program files were copied but do not load. Run "%DEST%\run.bat" to see the error.
    pause
    exit /b 1
)

rem ---- 4. Shortcuts ---------------------------------------------------------------
if "%SHORTCUTS%"=="0" goto done
rem Use pythonw.exe (no black console window) when it is next to python.exe.
set "TARGET=%PYEXE%"
for %%D in ("%PYEXE%") do if exist "%%~dpDpythonw.exe" set "TARGET=%%~dpDpythonw.exe"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$s = New-Object -ComObject WScript.Shell; $q = [char]34;" ^
  "$dirs = if ($env:LNKDIR) { @($env:LNKDIR) } else { @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs')) };" ^
  "foreach ($d in $dirs) { $l = $s.CreateShortcut((Join-Path $d 'VHF Watch Log.lnk'));" ^
  "  $l.TargetPath = $env:TARGET; $l.Arguments = $q + (Join-Path $env:DEST 'watchlog.py') + $q;" ^
  "  $l.WorkingDirectory = $env:DEST; $l.Description = 'USCG Auxiliary VHF communications watch log';" ^
  "  $l.IconLocation = $env:TARGET + ',0'; $l.Save() }"
if errorlevel 1 (
    echo  Could not make the shortcuts. Start the program with "%DEST%\run.bat".
) else (
    echo  Shortcuts made on the Desktop and in the Start menu: "VHF Watch Log"
)

:done
echo.
echo  Installed. Your log database is kept in:
echo    %APPDATA%\N. Tesla\VHF Watch Log\watchlog.db
echo  Instructions: "%DEST%\README.txt"
echo.
echo  To try the program with made-up boats and people first:
echo    "%DEST%\run.bat" --demo "%%TEMP%%\watchlog_demo.db"
echo.
if "%SHORTCUTS%"=="1" pause
exit /b 0

:copy_failed
echo.
echo  Could not copy the program to "%DEST%".
echo  Close the VHF Watch Log if it is running, and try again.
pause
exit /b 1
