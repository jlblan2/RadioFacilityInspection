#!/usr/bin/env python3
"""
Firmware update helpers for the Meshtastic Control GUI.

Checks the latest release on GitHub, downloads just the single file needed
for the connected device's chip (without pulling the whole 20-450MB
per-platform archive), and flashes it via whichever mechanism that chip
family uses:

  - nRF52840 / RP2040 / RP2350 boards: UF2 bootloader drag-and-drop
    (device exposes a mass-storage drive in bootloader mode; copying the
    .uf2 file onto it triggers the flash and an automatic reboot).
  - ESP32 / ESP32-C3 / ESP32-C6 / ESP32-S3 boards: esptool, writing the
    ".factory.bin" merged image (bootloader + partitions + app) at offset
    0x0 — the same file and offset the official web flasher uses.
"""

import ctypes
import io
import json
import os
import shutil
import string
import subprocess
import sys
import urllib.request
import zipfile

try:
    # Use the OS certificate store (Windows/macOS/Linux) instead of the
    # bundled certifi CA list, so HTTPS still works on machines where
    # security software does TLS inspection with a root cert that's trusted
    # by the OS but isn't in certifi's bundle.
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

GITHUB_LATEST_RELEASE = "https://api.github.com/repos/meshtastic/firmware/releases/latest"
USER_AGENT = "meshtastic-gui/1.0"

UF2_PLATFORMS = {"nrf52840", "rp2040", "rp2350"}
ESPTOOL_PLATFORMS = {"esp32", "esp32c3", "esp32c6", "esp32s3"}
ESP32_FLASH_OFFSET = "0x0"


class FirmwareError(Exception):
    pass


def _get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def get_latest_release():
    """Return {'version': tag, 'assets': {name: url}, 'targets': [{'board','platform'}]}."""
    data = _get_json(GITHUB_LATEST_RELEASE)
    version = data["tag_name"]
    assets = {a["name"]: a["browser_download_url"] for a in data["assets"]}
    manifest_name = next((n for n in assets if n.startswith("firmware-") and n.endswith(".json")), None)
    targets = []
    if manifest_name:
        targets = _get_json(assets[manifest_name]).get("targets", [])
    return {"version": version, "assets": assets, "targets": targets}


def find_platform_for_board(release: dict, pio_env: str):
    """Look up the chip platform (e.g. 'nrf52840') for a device's pioEnv/board id."""
    for t in release["targets"]:
        if t.get("board") == pio_env:
            return t.get("platform")
    return None


def platform_zip_asset(release: dict, platform: str):
    name = next((n for n in release["assets"] if n == f"firmware-{platform}-{release['version'].lstrip('v')}.zip"), None)
    if name is None:
        # fall back to a looser match in case naming shifts slightly between releases
        name = next((n for n in release["assets"] if n.startswith(f"firmware-{platform}-") and n.endswith(".zip")), None)
    return name


class _RemoteZipFile(io.RawIOBase):
    """
    Read-only file-like object that fetches a remote zip's bytes via HTTP Range
    requests on demand, so `zipfile` can list/extract a single member without
    downloading the whole archive.
    """

    def __init__(self, url: str):
        self.url = url
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT}, method="HEAD")
        with urllib.request.urlopen(req, timeout=20) as r:
            self.size = int(r.headers["Content-Length"])
        self.pos = 0

    def readable(self):
        return True

    def seekable(self):
        return True

    def seek(self, offset, whence=0):
        if whence == 0:
            self.pos = offset
        elif whence == 1:
            self.pos += offset
        else:
            self.pos = self.size + offset
        return self.pos

    def tell(self):
        return self.pos

    def readinto(self, b):
        length = len(b)
        end = min(self.pos + length, self.size) - 1
        if end < self.pos:
            return 0
        req = urllib.request.Request(
            self.url, headers={"User-Agent": USER_AGENT, "Range": f"bytes={self.pos}-{end}"}
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read()
        b[: len(data)] = data
        self.pos += len(data)
        return len(data)


def extract_firmware_file(zip_url: str, board: str, version: str, dest_dir: str, platform: str) -> str:
    """
    Download just the single firmware file matching `board` out of the
    chip-family zip at `zip_url`, and save it under dest_dir. Returns the
    local file path.
    """
    version = version.lstrip("v")
    rz = _RemoteZipFile(zip_url)
    zf = zipfile.ZipFile(rz)
    candidates = [n for n in zf.namelist() if board in n and version in n]
    if not candidates:
        raise FirmwareError(f"No firmware file found for board '{board}' in {zip_url}")

    if platform in ESPTOOL_PLATFORMS:
        wanted = next((n for n in candidates if n.endswith(".factory.bin")), None)
    else:
        wanted = next((n for n in candidates if n.endswith(".uf2")), None)
    if wanted is None:
        raise FirmwareError(f"Expected firmware image for '{board}' not found among: {candidates}")

    os.makedirs(dest_dir, exist_ok=True)
    dest_path = os.path.join(dest_dir, os.path.basename(wanted))
    with zf.open(wanted) as src, open(dest_path, "wb") as dst:
        shutil.copyfileobj(src, dst)
    return dest_path


# ── UF2 (nRF52840 / RP2040 / RP2350) flashing ───────────────────────────────

DRIVE_REMOVABLE = 2


def _mounted_drive_roots():
    """All currently mounted drive letters, as 'X:\\' roots."""
    if sys.platform != "win32":
        return []
    bitmask = ctypes.windll.kernel32.GetLogicalDrives()
    return [f"{letter}:\\" for i, letter in enumerate(string.ascii_uppercase) if bitmask & (1 << i)]


def find_uf2_drives():
    """Return drive roots (e.g. 'D:\\\\') currently mounted in UF2 bootloader
    mode — they expose an INFO_UF2.TXT marker file at their root."""
    drives = []
    for root in _mounted_drive_roots():
        try:
            if os.path.isfile(os.path.join(root, "INFO_UF2.TXT")):
                drives.append(root)
        except OSError:
            # A drive letter can be mounted but not ready (empty card reader slot,
            # a stale network mapping, etc.) — skip it instead of aborting the
            # whole scan, or a single unrelated bad drive hides the real one.
            continue
    return drives


def list_removable_drives():
    """
    Fallback for when find_uf2_drives() finds nothing: every currently mounted
    removable drive, whether or not it has the INFO_UF2.TXT marker. Some board
    bootloaders are slow to write that marker, or use a variant that omits it,
    so this lets the user pick the drive manually.
    """
    if sys.platform != "win32":
        return []
    results = []
    for root in _mounted_drive_roots():
        try:
            if ctypes.windll.kernel32.GetDriveTypeW(root) != DRIVE_REMOVABLE:
                continue
            label_buf = ctypes.create_unicode_buffer(261)
            ctypes.windll.kernel32.GetVolumeInformationW(root, label_buf, 260, None, None, None, None, 0)
            label = label_buf.value or "(no label)"
        except OSError:
            label = "(unreadable)"
        results.append((root, label))
    return results


def flash_uf2(firmware_path: str, drive_root: str) -> str:
    dest = os.path.join(drive_root, os.path.basename(firmware_path))
    shutil.copyfile(firmware_path, dest)
    return dest


# ── esptool (ESP32 family) flashing ─────────────────────────────────────────

def esptool_available() -> bool:
    try:
        import esptool  # noqa: F401
        return True
    except ImportError:
        return False


def esptool_erase_flash(port: str) -> str:
    return _run_esptool(["--port", port, "erase_flash"])


def esptool_write_flash(port: str, firmware_path: str, offset: str = ESP32_FLASH_OFFSET) -> str:
    return _run_esptool(["--port", port, "write_flash", offset, firmware_path])


def _run_esptool(args) -> str:
    cmd = [sys.executable, "-m", "esptool"] + args
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    output = proc.stdout + "\n" + proc.stderr
    if proc.returncode != 0:
        raise FirmwareError(output)
    return output
