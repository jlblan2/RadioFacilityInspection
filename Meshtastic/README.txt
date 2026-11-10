Meshtastic GUI
==============

A Windows program for setting up and watching Meshtastic radios over USB,
Bluetooth or WiFi/Ethernet. It can also update their firmware, and it saves
each radio's settings before an update so they can be put back afterwards.


1. Install Python (one time)
----------------------------
Download Python 3.13 (64-bit) from https://www.python.org/downloads/
(any version from 3.10 to 3.14 works; do NOT use 3.15 or newer yet, because
the Windows Bluetooth packages are not available for it). During setup, tick:
    [x] Add python.exe to PATH
    [x] tcl/tk and IDLE
    [x] py launcher


2. Install the program
----------------------
1. Right-click Meshtastic_GUI_Setup.zip, choose "Extract All...", and open
   the extracted "Meshtastic GUI" folder.
2. Double-click install.bat. It:
     - copies the program to  %LOCALAPPDATA%\Programs\Meshtastic GUI
       (no administrator rights needed),
     - installs the Python packages it needs: meshtastic, truststore and
       esptool (this needs an internet connection, the first time only),
     - puts a "Meshtastic GUI" shortcut on the Desktop and in the Start menu,
     - offers to install the CH340 USB driver (CH341SER.EXE). Answer Y if
       your radio has a CH340 chip and does not show up as a COM port.
   Options (from a command prompt):
     install.bat "D:\Some Folder"    install somewhere else
     install.bat /noshortcuts        no Desktop / Start menu shortcuts
     install.bat /nodriver           do not offer the USB driver

The extracted folder can be deleted after installing.


3. Run it
---------
Use the "Meshtastic GUI" shortcut, or double-click run.bat in the program
folder. run.bat keeps its window open when something goes wrong, so use it if
the shortcut opens nothing: the error message is shown there.

Running without installing also works: extract the zip anywhere and
double-click run.bat. It installs the packages the first time.


4. Updating to a new version
----------------------------
Extract the new zip and run its install.bat. The program files are replaced;
the settings backups are not touched.


5. Uninstall
------------
Run uninstall.bat in %LOCALAPPDATA%\Programs\Meshtastic GUI. It removes the
program, its downloaded firmware and the shortcuts. Python and its packages
stay installed.


Where things are kept
---------------------
Program:           %LOCALAPPDATA%\Programs\Meshtastic GUI
Firmware download: firmware_cache folder in the program folder
Settings backups:  %APPDATA%\Meshtastic GUI\settings_backups
                   (one folder per radio). These files hold the radio's private
                   key, channel keys and WiFi/MQTT passwords: keep them private.
                   Uninstalling does NOT delete them.

The full guide, including how settings are saved and restored around firmware
updates, is "Requirements to run Meshtestic_gui.py in windows.doc".


Troubleshooting
---------------
- "Failed building wheel" during the package install: the Python found is too
  new (3.15 or later). Install Python 3.13 as well, keep "py launcher" ticked,
  and run install.bat again; it picks 3.13 automatically.
- No COM port for a USB radio: open Device Manager. CH340 chip: run
  CH341SER.EXE, click INSTALL, unplug and replug the radio. CP210x chip: get
  the driver from silabs.com.
- Bluetooth radio not listed: pair it first in Windows Settings > Bluetooth
  & devices.
