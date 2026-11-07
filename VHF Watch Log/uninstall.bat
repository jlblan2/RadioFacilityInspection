@echo off
setlocal EnableExtensions
rem ===========================================================================
rem  VHF Watch Log - uninstaller
rem
rem  Removes the program from %LOCALAPPDATA%\Programs\VHF Watch Log (or the folder
rem  given) and the Desktop / Start menu shortcuts.
rem  Your log database (%APPDATA%\N. Tesla\VHF Watch Log) is KEPT: radio logs must
rem  be retained for 1 year, 3 years when distress traffic was logged. Delete that
rem  folder yourself only when the logs are no longer needed.
rem ===========================================================================
set "DEST=%LOCALAPPDATA%\Programs\VHF Watch Log"
if not "%~1"=="" set "DEST=%~f1"

echo.
echo  This removes the VHF Watch Log program from:
echo    %DEST%
echo  and its shortcuts. Your logs and database are kept.
echo.
choice /c YN /m "  Remove the program"
if errorlevel 2 exit /b 0

del /q "%USERPROFILE%\Desktop\VHF Watch Log.lnk" 2>nul
powershell -NoProfile -Command "foreach ($d in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) { Remove-Item -LiteralPath (Join-Path $d 'VHF Watch Log.lnk') -ErrorAction SilentlyContinue }"

if exist "%DEST%\watchlog.py" (
    rem Run the deletion from outside the folder (this file may be inside it).
    cd /d "%TEMP%"
    for %%F in (watchlog.py watchdb.py reports.py docx_export.py disclaimer.py run.bat README.txt LICENSE) do del /q "%DEST%\%%F" 2>nul
    if exist "%DEST%\__pycache__" rmdir /s /q "%DEST%\__pycache__"
    echo  Program removed.
    echo  Database kept in: %APPDATA%\N. Tesla\VHF Watch Log
    echo.
    pause
    rem Finally remove this file and the (now empty) folder.
    (goto) 2>nul & del /q "%DEST%\uninstall.bat" 2>nul & rmdir "%DEST%" 2>nul
) else (
    echo  The program was not found in "%DEST%". Shortcuts removed if they existed.
    pause
)
