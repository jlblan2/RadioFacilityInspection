"""Build Meshtastic_GUI_Setup.zip:  python make_zip.py [output folder]

The zip holds one folder, "Meshtastic GUI", with the program, the install / run /
uninstall files, the instructions and the CH340 USB driver. It never includes
settings backups (private keys and passwords, kept in %APPDATA%), downloaded
firmware, reports, or local files such as pw.txt.
"""
import os
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
FILES = ["install.bat", "uninstall.bat", "run.bat", "README.txt", "LICENSE",
         "meshtastic_gui.py", "mesh_client.py", "firmware.py", "netdiscovery.py",
         "printing.py", "settings_backup.py", "disclaimer.py",
         "CH341SER.EXE", "Requirements to run Meshtestic_gui.py in windows.doc"]
ZIP_NAME = "Meshtastic_GUI_Setup.zip"
FOLDER = "Meshtastic GUI/"


def build(out_dir=HERE):
    out = os.path.join(out_dir, ZIP_NAME)
    tmp = out + ".tmp"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for name in FILES:
            src = os.path.join(HERE, name)
            data = open(src, "rb").read()
            if name.endswith((".bat", ".txt")):  # Windows batch / Notepad files: CRLF
                data = data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
            z.writestr(zipfile.ZipInfo.from_file(src, FOLDER + name), data,
                       compress_type=zipfile.ZIP_DEFLATED)
    os.replace(tmp, out)
    return out


if __name__ == "__main__":
    path = build(*sys.argv[1:2])
    with zipfile.ZipFile(path) as z:
        for i in z.infolist():
            print(f"{i.file_size:>9,}  {i.filename}")
    print(f"\n{path}  ({os.path.getsize(path):,} bytes)")
