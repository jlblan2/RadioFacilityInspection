#!/usr/bin/env python3
"""
Windows printer helpers for the Report tab: list installed printers and send
a file to a specific one — the shell's plain "print" verb (and os.startfile)
always goes to whatever Windows has set as the default, with no way to target
a different printer, so this uses the "printto" verb instead when one is chosen.

Printer enumeration goes straight through the Win32 Print Spooler API
(winspool.drv) via ctypes rather than shelling out to PowerShell's Get-Printer:
launching powershell.exe to run one cmdlet was observed taking 15-30+ seconds
on a real machine, while the native API call is sub-millisecond — it's a
direct in-process call, no new process involved at all.
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
    Print `path` on Windows. If `printer` is given (and isn't SYSTEM_DEFAULT),
    targets that printer specifically via the shell's "printto" verb;
    otherwise uses "print" (whatever Windows has set as the default).
    """
    if sys.platform != "win32":
        raise OSError("Printing is only implemented for Windows.")
    target = printer if printer and printer != SYSTEM_DEFAULT else None
    verb = "printto" if target else "print"
    params = f'"{target}"' if target else None
    # Last arg 0 = SW_HIDE, so the transient print-handler window doesn't flash on screen.
    result = ctypes.windll.shell32.ShellExecuteW(None, verb, path, params, None, 0)
    if result <= 32:  # per ShellExecute docs: >32 means success
        raise OSError(f"ShellExecute failed (code {result}) — is a printer configured?")
