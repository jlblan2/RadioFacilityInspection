@echo off
setlocal EnableExtensions
rem ===========================================================================
rem  Meshtastic GUI - uninstaller
rem
rem  Removes the program from %LOCALAPPDATA%\Programs\Meshtastic GUI (or the folder
rem  given), its downloaded-firmware and report caches, and the Desktop / Start
rem  menu shortcuts.
rem  Radio settings backups (%APPDATA%\Meshtastic GUI\settings_backups) are KEPT.
rem  They hold private keys and passwords: delete that folder yourself when the
rem  backups are no longer needed. Python and its packages are left installed.
rem ===========================================================================
set "DEST=%LOCALAPPDATA%\Programs\Meshtastic GUI"
if not "%~1"=="" set "DEST=%~f1"

echo.
echo  This removes the Meshtastic GUI program from:
echo    %DEST%
echo  and its shortcuts. Your radio settings backups are kept.
echo.
choice /c YN /m "  Remove the program"
if errorlevel 2 exit /b 0

powershell -NoProfile -Command "foreach ($d in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) { Remove-Item -LiteralPath (Join-Path $d 'Meshtastic GUI.lnk') -ErrorAction SilentlyContinue }"

if exist "%DEST%\meshtastic_gui.py" (
    rem Run the deletion from outside the folder (this file may be inside it).
    cd /d "%TEMP%"
    for %%F in (meshtastic_gui.py mesh_client.py firmware.py netdiscovery.py printing.py settings_backup.py disclaimer.py run.bat README.txt LICENSE CH341SER.EXE "Requirements to run Meshtestic_gui.py in windows.doc") do del /q "%DEST%\%%~F" 2>nul
    for %%D in (__pycache__ firmware_cache report_cache) do if exist "%DEST%\%%D" rmdir /s /q "%DEST%\%%D"
    echo  Program removed.
    echo  Settings backups kept in: %APPDATA%\Meshtastic GUI\settings_backups
    echo.
    pause
    rem Finally remove this file and the (now empty) folder.
    (goto) 2>nul & del /q "%DEST%\uninstall.bat" 2>nul & rmdir "%DEST%" 2>nul
) else (
    echo  The program was not found in "%DEST%". Shortcuts removed if they existed.
    pause
)
