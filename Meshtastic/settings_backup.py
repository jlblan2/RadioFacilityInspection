#!/usr/bin/env python3
"""
Save and restore a Meshtastic radio's settings (not its firmware).

A firmware update can wipe the radio's settings: an ESP32 "Erase + Flash" always
does, and a UF2 update sometimes does. So the app saves the settings to a JSON
file whenever it connects to a radio and again right before a firmware update,
and can write them back once the new firmware is running.

What a backup holds:
  * owner: long and short name, licensed (ham) flag, unmessagable flag
  * every radio config section (device, position, power, network, display, lora,
    bluetooth, security) and every module config section (MQTT, serial,
    telemetry, canned messages, ...), whatever the connected firmware reports
  * all 8 channel slots, including their names and keys
  * the fixed position, when the radio uses one
  * canned messages and the external-notification ringtone

Restore writes it all back inside one settings transaction, so the radio
reboots once at the end. Sections the new firmware no longer has, or fields it
doesn't know, are skipped rather than failing the restore.

Backups contain the radio's private key, channel keys and any WiFi/MQTT
passwords, so they are kept in the user's own AppData folder, not the app folder.
"""

import json
import os
import re
import threading
import time
from datetime import datetime

from google.protobuf import json_format
from meshtastic.protobuf import admin_pb2, channel_pb2, config_pb2

FORMAT = "meshtastic-gui-settings"
FORMAT_VERSION = 1

# Why a backup was made (part of the file name, and how the restore dialog picks a default).
BEFORE_UPDATE, ON_CONNECT, MANUAL = "before-update", "on-connect", "manual"
KEEP_ON_CONNECT = 20         # older automatic snapshots per radio are pruned; the others are never pruned
WRITE_PAUSE_SECS = 0.3       # between admin messages, so the radio's receive queue keeps up
EXTRA_TIMEOUT_SECS = 6       # canned messages / ringtone each need a round trip to the radio
SKIP_SECTIONS = {"version", "sessionkey", "device_ui"}
IDENTITY_FIELDS = ("private_key", "public_key")


class BackupError(Exception):
    pass


def backup_root() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, "Meshtastic GUI", "settings_backups")


def device_dir(node_id: str) -> str:
    safe = re.sub(r"[^\w.-]", "_", (node_id or "unknown").lstrip("!")) or "unknown"
    return os.path.join(backup_root(), safe)


def _to_dict(msg) -> dict:
    return json_format.MessageToDict(msg, preserving_proto_field_name=True)


def _with_timeout(fn, secs):
    """fn() on a helper thread; None if it hasn't answered in `secs` (the library's
    canned-message and ringtone reads wait forever for a reply that may never come)."""
    box = {}
    t = threading.Thread(target=lambda: box.setdefault("v", fn()), daemon=True)
    t.start()
    t.join(secs)
    return box.get("v")


# ── capture ──────────────────────────────────────────────────────────────────

def capture(interface, device: dict, reason: str, include_extras: bool = True) -> dict:
    """Everything needed to put this radio's settings back, as a JSON-ready dict.
    `device` is the GUI's connection info (long_name, hw_model, firmware, pio_env, node_id)."""
    node = interface.localNode
    if node is None or node.localConfig is None:
        raise BackupError("The radio's settings haven't been read yet — wait for the connection to finish.")
    user = interface.getMyUser() or {}
    data = {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "created": datetime.now().isoformat(timespec="seconds"),
        "reason": reason,
        "device": {k: device.get(k, "") for k in ("node_id", "long_name", "short_name", "hw_model",
                                                   "firmware", "pio_env")},
        "owner": {
            "long_name": user.get("longName", ""),
            "short_name": user.get("shortName", ""),
            "is_licensed": bool(user.get("isLicensed", False)),
            "is_unmessagable": bool(user.get("isUnmessagable", False)),
        },
        "config": {},
        "module_config": {},
        "channels": [],
        "fixed_position": None,
        "canned_messages": None,
        "ringtone": None,
    }
    for field in node.localConfig.DESCRIPTOR.fields:
        if field.name not in SKIP_SECTIONS:
            data["config"][field.name] = _to_dict(getattr(node.localConfig, field.name))
    for field in node.moduleConfig.DESCRIPTOR.fields:
        if field.name not in SKIP_SECTIONS:
            data["module_config"][field.name] = _to_dict(getattr(node.moduleConfig, field.name))
    for ch in node.channels or []:
        data["channels"].append(_to_dict(ch))
    try:
        data["channel_url"] = node.getURL(includeAll=True)
    except Exception:
        pass

    if node.localConfig.position.fixed_position:
        me = (interface.nodesByNum or {}).get(getattr(interface.myInfo, "my_node_num", None)) or {}
        pos = me.get("position") or {}
        if "latitude" in pos and "longitude" in pos:
            data["fixed_position"] = {"latitude": pos["latitude"], "longitude": pos["longitude"],
                                      "altitude": int(pos.get("altitude", 0) or 0)}

    if include_extras:
        try:
            data["canned_messages"] = _with_timeout(node.get_canned_message, EXTRA_TIMEOUT_SECS)
        except Exception:
            pass
        try:
            data["ringtone"] = _with_timeout(node.get_ringtone, EXTRA_TIMEOUT_SECS)
        except Exception:
            pass
    return data


def _comparable(data: dict) -> str:
    return json.dumps({k: v for k, v in data.items() if k not in ("created", "reason", "device")},
                      sort_keys=True)


def save(data: dict) -> str:
    """Write the backup under the radio's folder; returns the path. An automatic
    on-connect snapshot identical to the newest backup isn't written again (its
    path is returned instead), and old on-connect snapshots are pruned."""
    folder = device_dir(data["device"].get("node_id"))
    os.makedirs(folder, exist_ok=True)
    reason = data.get("reason", MANUAL)
    if reason == ON_CONNECT:
        newest = list_backups(data["device"].get("node_id"))
        if newest:
            try:
                if _comparable(load(newest[0]["path"])) == _comparable(data):
                    return newest[0]["path"]
            except BackupError:
                pass
    short = re.sub(r"[^\w-]", "_", data["owner"].get("short_name") or "radio")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(folder, f"settings_{short}_{stamp}_{reason}.json")
    n = 1
    while os.path.exists(path):
        n += 1
        path = os.path.join(folder, f"settings_{short}_{stamp}_{reason}_{n}.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    os.replace(tmp, path)
    if reason == ON_CONNECT:
        old = [b for b in list_backups(data["device"].get("node_id")) if b["reason"] == ON_CONNECT]
        for b in old[KEEP_ON_CONNECT:]:
            try:
                os.remove(b["path"])
            except OSError:
                pass
    return path


def load(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        raise BackupError(f"Can't read {os.path.basename(path)}: {exc}") from None
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise BackupError(f"{os.path.basename(path)} isn't a settings backup made by this program.")
    if data.get("format_version", 0) > FORMAT_VERSION:
        raise BackupError("This backup was made by a newer version of this program.")
    return data


def list_backups(node_id: str = None) -> list:
    """Backups newest first: [{'path','created','reason','node_id','long_name'}]. With
    node_id None, every radio's backups."""
    folders = [device_dir(node_id)] if node_id else (
        [os.path.join(backup_root(), d) for d in os.listdir(backup_root())] if os.path.isdir(backup_root()) else [])
    found = []
    for folder in folders:
        if not os.path.isdir(folder):
            continue
        for name in os.listdir(folder):
            if not (name.startswith("settings_") and name.endswith(".json")):
                continue
            path = os.path.join(folder, name)
            try:
                data = load(path)
            except BackupError:
                continue
            found.append({"path": path, "created": data.get("created", ""), "reason": data.get("reason", ""),
                          "node_id": data["device"].get("node_id", ""),
                          "long_name": data["owner"].get("long_name") or data["device"].get("long_name", ""),
                          "unconfigured": is_unconfigured(data)})
    found.sort(key=lambda b: b["created"], reverse=True)
    return found


def best_backup(node_id: str = None):
    """The backup a restore should offer first: the newest one made before a
    firmware update, else the newest made of a configured radio."""
    backups = [b for b in list_backups(node_id) if not b["unconfigured"]]
    for b in backups:
        if b["reason"] == BEFORE_UPDATE:
            return b
    return backups[0] if backups else None


def is_unconfigured(data: dict) -> bool:
    """True for a radio fresh from a firmware update or factory reset: its LoRa
    region is still UNSET, so it can't transmit at all."""
    return (data.get("config", {}).get("lora", {}).get("region", "UNSET")) in ("UNSET", 0)


def radio_unconfigured(interface) -> bool:
    lc = interface.localNode.localConfig if interface and interface.localNode else None
    return lc is not None and lc.lora.region == config_pb2.Config.LoRaConfig.RegionCode.UNSET


def describe(data: dict) -> str:
    """A few lines for a confirmation dialog."""
    d, o, c = data.get("device", {}), data.get("owner", {}), data.get("config", {})
    region = c.get("lora", {}).get("region", "UNSET")
    preset = c.get("lora", {}).get("modem_preset", "LONG_FAST")
    role = c.get("device", {}).get("role", "CLIENT")
    chans = [ch.get("settings", {}).get("name") or "(unnamed)"
             for ch in data.get("channels", []) if ch.get("role", "DISABLED") != "DISABLED"]
    mqtt = "on" if data.get("module_config", {}).get("mqtt", {}).get("enabled") else "off"
    when = data.get("created", "?").replace("T", " ")
    return (f"Radio: {o.get('long_name') or '?'} ({o.get('short_name') or '?'})  {d.get('node_id', '')}\n"
            f"Saved: {when}  ({data.get('reason', '?')}), firmware {d.get('firmware') or '?'}\n"
            f"Role {role}, region {region}, preset {preset}, MQTT {mqtt}\n"
            f"Channels: {', '.join(chans) if chans else 'none'}")


# ── restore ──────────────────────────────────────────────────────────────────

def _send_section(node, kind: str, name: str, values: dict, skipped: list) -> bool:
    """Write one config section from its saved dict. kind is 'config' or 'module_config'.
    Built directly as an admin message (the library's writeConfig() exits the whole
    program for section names it doesn't know, and lags behind new firmware sections)."""
    admin = admin_pb2.AdminMessage()
    holder = admin.set_config if kind == "config" else admin.set_module_config
    if name not in holder.DESCRIPTOR.fields_by_name:
        skipped.append(f"{name} (this program's Meshtastic library doesn't know it)")
        return False
    local = node.localConfig if kind == "config" else node.moduleConfig
    if name not in local.DESCRIPTOR.fields_by_name:
        skipped.append(f"{name} (not in the new firmware)")
        return False
    msg = getattr(holder, name)
    json_format.ParseDict(values, msg, ignore_unknown_fields=True)
    getattr(local, name).CopyFrom(msg)    # keep the library's copy in step with the radio
    node._sendAdmin(admin)
    time.sleep(WRITE_PAUSE_SECS)
    return True


def restore(interface, data: dict, include_identity: bool = True, on_step=None) -> dict:
    """Write a backup to the connected radio. include_identity=False leaves the radio's
    own private/public key alone (for copying settings onto a different radio).
    Returns {'written': [...], 'skipped': [...]}; the radio reboots afterwards."""
    node = interface.localNode
    if node is None or node.localConfig is None:
        raise BackupError("The radio's settings haven't been read yet — wait for the connection to finish.")
    step = on_step or (lambda text: None)
    written, skipped = [], []

    node.beginSettingsTransaction()
    time.sleep(WRITE_PAUSE_SECS)

    owner = data.get("owner") or {}
    if (owner.get("long_name") or "").strip() and (owner.get("short_name") or "").strip():
        step("owner name")
        node.setOwner(long_name=owner["long_name"], short_name=owner["short_name"],
                      is_licensed=bool(owner.get("is_licensed")),
                      is_unmessagable=bool(owner.get("is_unmessagable")))
        written.append("owner name")
        time.sleep(WRITE_PAUSE_SECS)

    for name, values in (data.get("config") or {}).items():
        if name in SKIP_SECTIONS:
            continue
        values = dict(values)
        if name == "security" and not include_identity:
            # Keep this radio's own key pair; take everything else from the backup.
            current = _to_dict(node.localConfig.security)
            for f in IDENTITY_FIELDS:
                values.pop(f, None)
                if f in current:
                    values[f] = current[f]
        step(f"{name} settings")
        if _send_section(node, "config", name, values, skipped):
            written.append(name)

    for name, values in (data.get("module_config") or {}).items():
        if name in SKIP_SECTIONS:
            continue
        step(f"{name} module")
        if _send_section(node, "module_config", name, values, skipped):
            written.append(f"{name} module")

    saved_channels = data.get("channels") or []
    for i, saved in enumerate(saved_channels):
        if node.channels is None or i >= len(node.channels):
            skipped.append(f"channel {i} (the radio has no slot for it)")
            continue
        step(f"channel {i}")
        ch = channel_pb2.Channel()
        json_format.ParseDict(saved, ch, ignore_unknown_fields=True)
        ch.index = i
        node.channels[i].CopyFrom(ch)
        node.writeChannel(i)
        time.sleep(WRITE_PAUSE_SECS)
    if saved_channels:
        written.append(f"{sum(1 for c in saved_channels if c.get('role', 'DISABLED') != 'DISABLED')} channel(s)")

    fixed = data.get("fixed_position")
    if fixed:
        step("fixed position")
        node.setFixedPosition(float(fixed["latitude"]), float(fixed["longitude"]), int(fixed.get("altitude", 0)))
        written.append("fixed position")
        time.sleep(WRITE_PAUSE_SECS)

    if data.get("canned_messages"):
        step("canned messages")
        try:
            node.set_canned_message(data["canned_messages"])
            written.append("canned messages")
        except (Exception, SystemExit) as exc:   # the library exits on messages over 200 characters
            skipped.append(f"canned messages ({exc})")
        time.sleep(WRITE_PAUSE_SECS)
    if data.get("ringtone"):
        step("ringtone")
        try:
            node.set_ringtone(data["ringtone"])
            written.append("ringtone")
        except (Exception, SystemExit) as exc:
            skipped.append(f"ringtone ({exc})")
        time.sleep(WRITE_PAUSE_SECS)

    step("saving on the radio")
    node.commitSettingsTransaction()
    return {"written": written, "skipped": skipped}
