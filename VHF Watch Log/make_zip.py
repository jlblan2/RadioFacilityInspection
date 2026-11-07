"""Build VHF_Watch_Log_Setup.zip for other watch standers:  python make_zip.py

The zip holds one folder, "VHF Watch Log", with the program, instructions and the
install/uninstall files. It never includes a database (member names and phone numbers
live only in %APPDATA% on each computer) or the developer test file.
"""
import os
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
FILES = ["install.bat", "uninstall.bat", "run.bat", "README.txt", "LICENSE",
         "watchlog.py", "watchdb.py", "reports.py", "docx_export.py", "disclaimer.py"]
ZIP_NAME = "VHF_Watch_Log_Setup.zip"


def build(out_dir=HERE):
    out = os.path.join(out_dir, ZIP_NAME)
    tmp = out + ".tmp"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for name in FILES:
            src = os.path.join(HERE, name)
            data = open(src, "rb").read()
            if name.endswith(".bat"):  # Windows batch files need CRLF line endings
                data = data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
            z.writestr(zipfile.ZipInfo.from_file(src, "VHF Watch Log/" + name), data,
                       compress_type=zipfile.ZIP_DEFLATED)
    os.replace(tmp, out)
    return out


if __name__ == "__main__":
    path = build(*sys.argv[1:2])
    with zipfile.ZipFile(path) as z:
        for i in z.infolist():
            print(f"{i.file_size:>9,}  {i.filename}")
    print(f"\n{path}  ({os.path.getsize(path):,} bytes)")
