#!/usr/bin/env python3
"""
Cross-platform printer helpers for the Report tab: list installed printers
and send a file to a specific one.

Windows goes straight through the Win32 Print Spooler API (winspool.drv) via
ctypes rather than shelling out to PowerShell or using ShellExecute's
"print"/"printto" shell verbs:

  - Enumeration: launching powershell.exe to run Get-Printer was observed
    taking 15-30+ seconds on a real machine, while the native API call is
    sub-millisecond — it's a direct in-process call, no new process at all.
  - Printing: the "printto" verb depends on the .txt file association's
    handler (normally Notepad) correctly parsing printer/driver/port
    arguments off its command line — in practice that failed with
    ERROR_INVALID_DATA even against a printer that genuinely exists.
    Opening the printer directly and writing the job's bytes to it works
    the same regardless of file associations.

macOS and Linux both ship CUPS, so those go through its `lpstat`/`lp`
command-line tools instead — there's no ctypes equivalent to a system DLL
there, and `lp` already knows how to spool a plain text file correctly.
"""

import re
import subprocess
import sys

SYSTEM_DEFAULT = "(System Default)"

_POSIX = sys.platform == "darwin" or sys.platform.startswith("linux")


def list_printers() -> list:
    """Names of installed/connected printers, or [] on an unsupported platform or failure."""
    if sys.platform == "win32":
        return _win_list_printers()
    if _POSIX:
        return _cups_list_printers()
    return []


def get_default_printer():
    """The OS default printer's name, or None if it can't be determined."""
    if sys.platform == "win32":
        return _win_get_default_printer()
    if _POSIX:
        return _cups_get_default_printer()
    return None


def print_file(path: str, printer: str = None) -> None:
    """
    Print `path` (a plain text file). If `printer` is given (and isn't
    SYSTEM_DEFAULT), targets that printer specifically; otherwise uses
    whichever the OS has set as the default.
    """
    if sys.platform == "win32":
        return _win_print_file(path, printer)
    if _POSIX:
        return _cups_print_file(path, printer)
    raise OSError(f"Printing is not implemented for platform '{sys.platform}'.")


# ── macOS / Linux (CUPS command-line tools) ─────────────────────────────────

# `lpstat -p` prints one line per printer, e.g.:
#   printer Brother_MFC_L2750DW_series is idle.  enabled since Tue 01 Jan 2026...
# This format has been stable across CUPS versions on both macOS and Linux.
_LPSTAT_PRINTER_RE = re.compile(r"^printer\s+(\S+)\s+is\b", re.MULTILINE)
# `lpstat -d` prints either "system default destination: <name>" or
# "no system default destination".
_LPSTAT_DEFAULT_RE = re.compile(r"system default destination:\s*(\S+)")


def _cups_list_printers() -> list:
    try:
        proc = subprocess.run(["lpstat", "-p"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return []  # no CUPS client tools installed, or nothing to report
    return _LPSTAT_PRINTER_RE.findall(proc.stdout)


def _cups_get_default_printer():
    try:
        proc = subprocess.run(["lpstat", "-d"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    m = _LPSTAT_DEFAULT_RE.search(proc.stdout)
    return m.group(1) if m else None


def _cups_print_file(path: str, printer: str = None) -> None:
    cmd = ["lp"]
    if printer and printer != SYSTEM_DEFAULT:
        cmd += ["-d", printer]
    cmd.append(path)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        raise OSError("'lp' was not found — is CUPS (cups-client) installed?") from None
    except subprocess.SubprocessError as exc:
        raise OSError(f"Could not run 'lp': {exc}") from exc
    if proc.returncode != 0:
        raise OSError((proc.stderr or proc.stdout or f"lp exited with status {proc.returncode}").strip())


# ── Windows (Win32 Print Spooler API) ───────────────────────────────────────

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

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


def _win_list_printers() -> list:
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


def _win_get_default_printer():
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


def _win_print_file(path: str, printer: str = None) -> None:
    """
    Print a text file's contents via OpenPrinter/StartDocPrinter/WritePrinter,
    targeting `printer` by name — or the Windows default if `printer` is
    None/SYSTEM_DEFAULT.
    """
    target = printer if printer and printer != SYSTEM_DEFAULT else _win_get_default_printer()
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
