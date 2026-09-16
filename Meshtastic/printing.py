#!/usr/bin/env python3
"""
Windows printer helpers for the Report tab: list installed printers and send
a file to a specific one.

Both operations go straight through the Win32 Print Spooler API
(winspool.drv) via ctypes rather than shelling out to PowerShell or using
ShellExecute's "print"/"printto" shell verbs:

  - Enumeration: launching powershell.exe to run Get-Printer was observed
    taking 15-30+ seconds on a real machine, while the native API call is
    sub-millisecond — it's a direct in-process call, no new process at all.
  - Printing: the "printto" verb depends on the .txt file association's
    handler (normally Notepad) correctly parsing printer/driver/port
    arguments off its command line — in practice that failed with
    ERROR_INVALID_DATA even against a printer that genuinely exists.
    Opening the printer directly and writing the job's bytes to it works
    the same regardless of file associations.
"""

import ctypes
import sys
from ctypes import wintypes

SYSTEM_DEFAULT = "(System Default)"

PRINTER_ENUM_LOCAL = 0x00000002
PRINTER_ENUM_CONNECTIONS = 0x00000004


class _PRINTER_INFO_4(ctypes.Structure):
    _fields_ = [
        ("pPrinterName", wintypes.LPWSTR),
        ("pServerName", wintypes.LPWSTR),
        ("Attributes", wintypes.DWORD),
    ]


class _DOC_INFO_1(ctypes.Structure):
    _fields_ = [
        ("pDocName", wintypes.LPWSTR),
        ("pOutputFile", wintypes.LPWSTR),
        ("pDatatype", wintypes.LPWSTR),
    ]


def list_printers() -> list:
    """Names of installed/connected printers, or [] on non-Windows or failure."""
    if sys.platform != "win32":
        return []
    try:
        winspool = ctypes.WinDLL("winspool.drv")
        flags = PRINTER_ENUM_LOCAL | PRINTER_ENUM_CONNECTIONS
        needed = wintypes.DWORD(0)
        returned = wintypes.DWORD(0)
        # First call with no buffer just asks how many bytes we'll need.
        winspool.EnumPrintersW(flags, None, 4, None, 0, ctypes.byref(needed), ctypes.byref(returned))
        buf = ctypes.create_string_buffer(needed.value)
        ok = winspool.EnumPrintersW(flags, None, 4, buf, needed.value,
                                      ctypes.byref(needed), ctypes.byref(returned))
        if not ok:
            return []
        infos = ctypes.cast(buf, ctypes.POINTER(_PRINTER_INFO_4 * returned.value)).contents
        return [info.pPrinterName for info in infos if info.pPrinterName]
    except Exception:
        return []


def get_default_printer():
    """The Windows default printer's name, or None if it can't be determined."""
    if sys.platform != "win32":
        return None
    try:
        winspool = ctypes.WinDLL("winspool.drv")
        size = wintypes.DWORD(0)
        winspool.GetDefaultPrinterW(None, ctypes.byref(size))
        if size.value == 0:
            return None
        buf = ctypes.create_unicode_buffer(size.value)
        if not winspool.GetDefaultPrinterW(buf, ctypes.byref(size)):
            return None
        return buf.value or None
    except Exception:
        return None


def print_file(path: str, printer: str = None) -> None:
    """
    Print a text file's contents on Windows via OpenPrinter/StartDocPrinter/
    WritePrinter, targeting `printer` by name — or the Windows default if
    `printer` is None/SYSTEM_DEFAULT.
    """
    if sys.platform != "win32":
        raise OSError("Printing is only implemented for Windows.")

    target = printer if printer and printer != SYSTEM_DEFAULT else get_default_printer()
    if not target:
        raise OSError("No printer selected and no Windows default printer is set.")

    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    # "RAW" bytes go straight through to the printer/port untouched — this is
    # the same technique `copy file.txt \\server\printer` has relied on for
    # decades, and it's near-universally supported. "TEXT" looked like the
    # more proper choice, but many drivers don't actually implement it (it
    # failed outright — ERROR_INVALID_DATATYPE — even against Microsoft's own
    # "Print to PDF" driver), so this avoids relying on optional support.
    # Plain ASCII/CRLF text has no escape sequences a printer language would
    # misinterpret, so it prints as literal characters either way.
    normalized = text.replace("\r\n", "\n").replace("\n", "\r\n")
    data = normalized.encode("mbcs", errors="replace")

    winspool = ctypes.WinDLL("winspool.drv", use_last_error=True)
    h_printer = wintypes.HANDLE()
    if not winspool.OpenPrinterW(target, ctypes.byref(h_printer), None):
        raise OSError(f"Could not open printer '{target}' (Windows error {ctypes.get_last_error()})")
    try:
        doc_info = _DOC_INFO_1(pDocName="Meshtastic Report", pOutputFile=None, pDatatype="RAW")
        job_id = winspool.StartDocPrinterW(h_printer, 1, ctypes.byref(doc_info))
        if not job_id:
            raise OSError(f"StartDocPrinter failed (Windows error {ctypes.get_last_error()})")
        try:
            if not winspool.StartPagePrinter(h_printer):
                raise OSError(f"StartPagePrinter failed (Windows error {ctypes.get_last_error()})")
            try:
                written = wintypes.DWORD(0)
                buf = ctypes.create_string_buffer(data, len(data))
                if not winspool.WritePrinter(h_printer, buf, len(data), ctypes.byref(written)):
                    raise OSError(f"WritePrinter failed (Windows error {ctypes.get_last_error()})")
            finally:
                winspool.EndPagePrinter(h_printer)
        finally:
            winspool.EndDocPrinter(h_printer)
    finally:
        winspool.ClosePrinter(h_printer)
