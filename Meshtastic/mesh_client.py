#!/usr/bin/env python3
"""
Thin wrapper around the `meshtastic` Python module.

Owns the interface connection (Serial / TCP / BLE), subscribes to the
library's pubsub topics, and forwards everything as plain events onto a
thread-safe queue.Queue so a GUI's main thread can poll it safely — the
meshtastic library invokes pubsub callbacks from its own background
threads, never from the caller's thread.
"""

import queue
import re
import threading
import traceback

from pubsub import pub

import meshtastic
import meshtastic.serial_interface
import meshtastic.tcp_interface
import meshtastic.ble_interface
import meshtastic.util
from meshtastic.protobuf import config_pb2

BROADCAST_ADDR = "^all"

ROLE_OPTIONS = list(config_pb2.Config.DeviceConfig.Role.keys())
REGION_OPTIONS = list(config_pb2.Config.LoRaConfig.RegionCode.keys())

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def list_serial_ports():
    """
    Return [(device, description, likely), ...] for connected USB/serial devices.
    `likely` is True for devices whose USB VID matches a known Meshtastic board
    (same whitelist the meshtastic CLI itself uses for auto-detection) — most
    machines have other USB-serial gadgets attached that are not the radio.
    """
    import serial.tools.list_ports as lp
    likely_devices = set(meshtastic.util.findPorts(True))
    return [(p.device, p.description, p.device in likely_devices) for p in lp.comports()]


def scan_ble():
    """Return [(address, name), ...] for nearby BLE devices advertising Meshtastic."""
    devices = meshtastic.ble_interface.BLEInterface.scan()
    return [(d.address, d.name or "(unknown)") for d in devices]


def node_display_name(node: dict) -> str:
    user = (node or {}).get("user") or {}
    return user.get("longName") or user.get("shortName") or user.get("id") or "?"


class MeshClient:
    """
    Manages one meshtastic interface at a time and reports state changes /
    incoming packets as events: (kind, payload) tuples pulled via `poll()`.

    Event kinds:
      'connected'    -> {'long_name','short_name','hw_model','firmware','node_id'}
      'disconnected' -> None
      'error'        -> str
      'log'          -> str
      'node_updated' -> dict (raw meshtastic node record)
      'text'         -> {'from','from_id','to_id','channel','text','rx_time'}
      'packet'       -> dict (raw packet, for anything not handled above)
      'settings_saved'    -> None
      'mqtt_saved'        -> None
      'factory_reset_done' -> None
      'telemetry'    -> {'node_id','battery_level','voltage'}
    """

    def __init__(self):
        self.interface = None
        self.kind = None
        self.events: "queue.Queue" = queue.Queue()
        self._subscribed = False

    # ── connection management ────────────────────────────────────────────
    def connect_serial(self, dev_path: str | None):
        self._connect("serial", lambda: meshtastic.serial_interface.SerialInterface(devPath=dev_path or None))

    def connect_tcp(self, hostname: str, port: int = 4403):
        self._connect("tcp", lambda: meshtastic.tcp_interface.TCPInterface(hostname=hostname, portNumber=port))

    def connect_ble(self, address: str | None):
        self._connect("ble", lambda: meshtastic.ble_interface.BLEInterface(address=address))

    def _connect(self, kind: str, factory):
        if self.interface is not None:
            raise RuntimeError("Already connected — disconnect first")
        self._ensure_subscribed()

        def worker():
            try:
                iface = factory()
                self.interface = iface
                self.kind = kind
            except Exception as exc:  # noqa: BLE001 - surface any failure to the GUI
                self.events.put(("error", f"Connection failed: {exc}"))
                self.events.put(("disconnected", None))

        threading.Thread(target=worker, daemon=True, name="mesh-connect").start()

    def disconnect(self):
        iface, self.interface = self.interface, None
        self.kind = None
        if iface is not None:
            try:
                iface.close()
            except Exception:
                pass
        self.events.put(("disconnected", None))

    def is_connected(self) -> bool:
        return self.interface is not None

    # ── outgoing ─────────────────────────────────────────────────────────
    def send_text(self, text: str, destination_id: str = BROADCAST_ADDR, channel_index: int = 0):
        if self.interface is None:
            raise RuntimeError("Not connected")
        self.interface.sendText(text, destinationId=destination_id, channelIndex=channel_index)

    def get_nodes(self) -> dict:
        if self.interface is None or self.interface.nodes is None:
            return {}
        return dict(self.interface.nodes)

    def get_my_node_id(self):
        if self.interface is None or self.interface.myInfo is None:
            return None
        return getattr(self.interface.myInfo, "my_node_num", None)

    def get_battery_status(self):
        """{'battery_level','voltage'} for the connected device, read from the
        already-synced NodeDB — or None if not known yet (fresh telemetry
        events keep this current after connect via the 'telemetry' event)."""
        if self.interface is None:
            return None
        try:
            my_id = (self.interface.getMyUser() or {}).get("id")
        except Exception:
            return None
        node = self.get_nodes().get(my_id)
        if not node:
            return None
        metrics = node.get("deviceMetrics") or {}
        if "batteryLevel" not in metrics:
            return None
        return {"battery_level": metrics.get("batteryLevel"), "voltage": metrics.get("voltage")}

    # ── device settings ──────────────────────────────────────────────────
    def get_device_settings(self) -> dict:
        """Snapshot of the settings the Settings tab edits, read straight from
        the device's already-synced local config (populated during connect)."""
        if self.interface is None:
            raise RuntimeError("Not connected")
        node = self.interface.localNode
        owner = self.interface.getMyUser() or {}
        lc = node.localConfig
        return {
            "long_name": owner.get("longName", ""),
            "short_name": owner.get("shortName", ""),
            "fixed_pin": lc.bluetooth.fixed_pin,
            "role": config_pb2.Config.DeviceConfig.Role.Name(lc.device.role),
            "region": config_pb2.Config.LoRaConfig.RegionCode.Name(lc.lora.region),
        }

    def save_device_settings(self, long_name: str, short_name: str, fixed_pin: int, role: str, region: str):
        """
        Push a settings change to the device. Runs on a background thread —
        each admin round-trip can take a couple of seconds — and reports
        'settings_saved' or 'error' back through the event queue.
        """
        if self.interface is None:
            raise RuntimeError("Not connected")
        node = self.interface.localNode

        def worker():
            try:
                node.beginSettingsTransaction()
                if long_name or short_name:
                    node.setOwner(long_name=long_name or None, short_name=short_name or None)
                node.localConfig.bluetooth.fixed_pin = int(fixed_pin)
                node.writeConfig("bluetooth")
                node.localConfig.device.role = config_pb2.Config.DeviceConfig.Role.Value(role)
                node.writeConfig("device")
                node.localConfig.lora.region = config_pb2.Config.LoRaConfig.RegionCode.Value(region)
                node.writeConfig("lora")
                node.commitSettingsTransaction()
                self.events.put(("settings_saved", None))
            except Exception as exc:
                self.events.put(("error", f"Save settings failed: {exc}"))

        threading.Thread(target=worker, daemon=True, name="mesh-save-settings").start()

    # ── MQTT module settings ─────────────────────────────────────────────
    def get_mqtt_settings(self) -> dict:
        """Snapshot of the MQTT module config, read straight from the
        device's already-synced module config (populated during connect)."""
        if self.interface is None:
            raise RuntimeError("Not connected")
        m = self.interface.localNode.moduleConfig.mqtt
        return {
            "enabled": m.enabled,
            "address": m.address,
            "username": m.username,
            "password": m.password,
            "encryption_enabled": m.encryption_enabled,
            "json_enabled": m.json_enabled,
            "tls_enabled": m.tls_enabled,
            "root": m.root,
            "proxy_to_client_enabled": m.proxy_to_client_enabled,
            "map_reporting_enabled": m.map_reporting_enabled,
            "map_publish_interval_secs": m.map_report_settings.publish_interval_secs,
            "map_position_precision": m.map_report_settings.position_precision,
            "map_should_report_location": m.map_report_settings.should_report_location,
        }

    def save_mqtt_settings(self, settings: dict):
        """
        Push the MQTT module config to the device. Runs on a background
        thread and reports 'mqtt_saved' or 'error' back through the event
        queue, same pattern as save_device_settings.
        """
        if self.interface is None:
            raise RuntimeError("Not connected")
        node = self.interface.localNode

        def worker():
            try:
                node.beginSettingsTransaction()
                m = node.moduleConfig.mqtt
                m.enabled = bool(settings["enabled"])
                m.address = settings["address"]
                m.username = settings["username"]
                m.password = settings["password"]
                m.encryption_enabled = bool(settings["encryption_enabled"])
                m.json_enabled = bool(settings["json_enabled"])
                m.tls_enabled = bool(settings["tls_enabled"])
                m.root = settings["root"]
                m.proxy_to_client_enabled = bool(settings["proxy_to_client_enabled"])
                m.map_reporting_enabled = bool(settings["map_reporting_enabled"])
                m.map_report_settings.publish_interval_secs = int(settings["map_publish_interval_secs"])
                m.map_report_settings.position_precision = int(settings["map_position_precision"])
                m.map_report_settings.should_report_location = bool(settings["map_should_report_location"])
                node.writeConfig("mqtt")
                node.commitSettingsTransaction()
                self.events.put(("mqtt_saved", None))
            except Exception as exc:
                self.events.put(("error", f"Save MQTT settings failed: {exc}"))

        threading.Thread(target=worker, daemon=True, name="mesh-save-mqtt").start()

    def factory_reset(self, full: bool = False):
        """Wipe device config/NodeDB back to defaults. Does NOT touch firmware."""
        if self.interface is None:
            raise RuntimeError("Not connected")
        node = self.interface.localNode

        def worker():
            try:
                node.factoryReset(full=full)
                self.events.put(("factory_reset_done", None))
            except Exception as exc:
                self.events.put(("error", f"Factory reset failed: {exc}"))

        threading.Thread(target=worker, daemon=True, name="mesh-factory-reset").start()

    # ── pubsub plumbing ──────────────────────────────────────────────────
    def _ensure_subscribed(self):
        if self._subscribed:
            return
        pub.subscribe(self._on_connection_established, "meshtastic.connection.established")
        pub.subscribe(self._on_connection_lost, "meshtastic.connection.lost")
        pub.subscribe(self._on_node_updated, "meshtastic.node.updated")
        pub.subscribe(self._on_receive_text, "meshtastic.receive.text")
        pub.subscribe(self._on_telemetry, "meshtastic.receive.telemetry")
        pub.subscribe(self._on_receive, "meshtastic.receive")
        pub.subscribe(self._on_log_line, "meshtastic.log.line")
        self._subscribed = True

    def _on_connection_established(self, interface):
        if interface is not self.interface:
            return
        try:
            info = interface.getMyUser() or {}
            metadata = interface.metadata
            my_info = interface.myInfo
            payload = {
                "long_name": info.get("longName", "?"),
                "short_name": info.get("shortName", "?"),
                "hw_model": info.get("hwModel", "?"),
                "firmware": getattr(metadata, "firmware_version", "?") if metadata else "?",
                "node_id": info.get("id", "?"),
                "pio_env": getattr(my_info, "pio_env", "") if my_info else "",
            }
        except Exception:
            payload = {"long_name": "?", "short_name": "?", "hw_model": "?", "firmware": "?",
                       "node_id": "?", "pio_env": ""}
        self.events.put(("connected", payload))

    def _on_connection_lost(self, interface):
        if interface is not self.interface:
            return
        self.interface = None
        self.kind = None
        self.events.put(("disconnected", None))

    def _on_node_updated(self, node, interface):
        if interface is not self.interface:
            return
        self.events.put(("node_updated", node))

    def _on_receive_text(self, packet, interface):
        if interface is not self.interface:
            return
        decoded = packet.get("decoded", {})
        self.events.put(("text", {
            "from_id": packet.get("fromId", "?"),
            "to_id": packet.get("toId", "?"),
            "channel": packet.get("channel", 0),
            "text": decoded.get("text", ""),
            "rx_time": packet.get("rxTime"),
        }))

    def _on_telemetry(self, packet, interface):
        if interface is not self.interface:
            return
        metrics = packet.get("decoded", {}).get("telemetry", {}).get("deviceMetrics")
        if not metrics:
            return
        self.events.put(("telemetry", {
            "node_id": packet.get("fromId", "?"),
            "battery_level": metrics.get("batteryLevel"),
            "voltage": metrics.get("voltage"),
        }))

    def _on_receive(self, packet, interface):
        if interface is not self.interface:
            return
        portnum = packet.get("decoded", {}).get("portnum")
        if portnum == "TEXT_MESSAGE_APP":
            return  # already handled by _on_receive_text
        self.events.put(("packet", packet))

    def _on_log_line(self, line, interface=None):
        if interface is not None and interface is not self.interface:
            return
        self.events.put(("log", line))

    # ── event pump ───────────────────────────────────────────────────────
    def poll(self):
        """Yield all events currently queued, without blocking."""
        while True:
            try:
                yield self.events.get_nowait()
            except queue.Empty:
                return
