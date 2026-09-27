#!/usr/bin/env python3
"""
Thin wrapper around the `meshtastic` Python module.

Owns the interface connection (Serial / TCP / BLE), subscribes to the
library's pubsub topics, and forwards everything as plain events onto a
thread-safe queue.Queue so a GUI's main thread can poll it safely — the
meshtastic library invokes pubsub callbacks from its own background
threads, never from the caller's thread.
"""

import base64
import ipaddress
import queue
import re
import socket
import threading
import time
import traceback

from pubsub import pub

import meshtastic
import meshtastic.serial_interface
import meshtastic.tcp_interface
import meshtastic.ble_interface
import meshtastic.util
from meshtastic.protobuf import channel_pb2, config_pb2
from meshtastic.util import genPSK256

BROADCAST_ADDR = "^all"

TCP_CONNECT_TIMEOUT_SECS = 8


class _BoundedTCPInterface(meshtastic.tcp_interface.TCPInterface):
    """
    TCPInterface whose connect gives up after TCP_CONNECT_TIMEOUT_SECS. The stock
    class calls socket.create_connection() with no timeout, so an unreachable
    host (say, a direct Ethernet cable where neither end got an IP) just hangs
    for the OS default before failing — long enough that the GUI's own watchdog
    fires first and the real reason (unreachable vs. refused) is lost.
    """

    def myConnect(self) -> None:
        self.socket = socket.create_connection(
            (self.hostname, self.portNumber), timeout=TCP_CONNECT_TIMEOUT_SECS
        )
        self.socket.settimeout(None)  # the reader thread relies on blocking recv()


ROLE_OPTIONS = list(config_pb2.Config.DeviceConfig.Role.keys())
REGION_OPTIONS = list(config_pb2.Config.LoRaConfig.RegionCode.keys())

CHANNEL_ROLE_NAMES = list(channel_pb2.Channel.Role.keys())  # DISABLED, PRIMARY, SECONDARY
PSK_MODES = ["None", "Default", "Random", "Custom"]

_PSK_NONE = bytes([0])
_PSK_DEFAULT = bytes([1])


def describe_psk(psk: bytes) -> str:
    """Which PSK_MODES bucket an existing channel's raw PSK bytes fall into."""
    if not psk or psk == _PSK_NONE:
        return "None"
    if psk == _PSK_DEFAULT:
        return "Default"
    return "Custom"


def encode_psk(mode: str, custom_b64: str = "") -> bytes:
    """Inverse of describe_psk(), for writing a channel's PSK from the UI."""
    if mode == "None":
        return _PSK_NONE
    if mode == "Default":
        return _PSK_DEFAULT
    if mode == "Random":
        return genPSK256()
    if mode == "Custom":
        try:
            return base64.b64decode(custom_b64, validate=True)
        except Exception as exc:
            raise ValueError(f"Invalid base64 PSK: {exc}") from exc
    raise ValueError(f"Unknown PSK mode: {mode}")

ADDRESS_MODES = list(config_pb2.Config.NetworkConfig.AddressMode.keys())  # DHCP, STATIC


def ipv4_to_int(text: str) -> int:
    """
    Dotted quad -> the fixed32 the device stores. Firmware hands this field
    straight to Arduino's IPAddress(uint32_t), which keeps the FIRST octet in
    the LOW byte — so 192.168.1.10 is 0x0A01A8C0, not the network-order value
    Python's struct/socket helpers would give without the "<".
    """
    return int.from_bytes(ipaddress.IPv4Address(text.strip()).packed, "little")


def int_to_ipv4(value: int) -> str:
    """Inverse of ipv4_to_int(); 0 (unset) comes back as an empty string."""
    if not value:
        return ""
    return str(ipaddress.IPv4Address(value.to_bytes(4, "little")))


def parse_subnet_mask(text: str) -> ipaddress.IPv4Network:
    """
    Accepts a dotted mask (255.255.255.0), a CIDR prefix (/24 or 24), and
    returns the 0.0.0.0/N network object. Raises ValueError for anything that
    isn't a contiguous mask — e.g. 255.0.255.0, which no router would honor.
    """
    text = text.strip().lstrip("/")
    if not text:
        raise ValueError("Enter a subnet mask (e.g. 255.255.255.0).")
    try:
        if "." in text:
            net = ipaddress.IPv4Network(f"0.0.0.0/{text}")
            # ipaddress also accepts wildcard masks (0.0.0.255) as a /24 — reject those.
            if str(net.netmask) != text:
                raise ValueError
        else:
            net = ipaddress.IPv4Network(f"0.0.0.0/{int(text)}")
    except ValueError:
        raise ValueError(f"'{text}' isn't a valid subnet mask (use e.g. 255.255.255.0 or /24).") from None
    return net


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


def _release_interface(iface) -> None:
    """
    Close an interface and — whatever happens along the way — its OS-level handle.

    SerialInterface.close() begins with stream.flush(), which raises on a port
    that has vanished (the unit rebooting) and aborts everything after it: the
    reader thread keeps running and the COM handle stays open, so Windows
    refuses every later open of that port with "Access is denied" until this
    process exits. Closing the handle ourselves afterwards makes that impossible.
    Safe to call more than once, and on an interface that only half connected.
    """
    if iface is None:
        return
    try:
        iface.close()
    except Exception:
        pass
    try:
        iface._wantExit = True  # lets a reader thread that close() never reached exit
    except Exception:
        pass
    for attr in ("stream", "socket"):
        handle = getattr(iface, attr, None)
        if handle is not None:
            try:
                handle.close()
            except Exception:
                pass
            try:
                setattr(iface, attr, None)
            except Exception:
                pass


class _AttemptCancelled(Exception):
    """Raised inside a connect factory when the attempt was cancelled before it opened anything."""


def _is_port_open_failure(exc: Exception) -> bool:
    """pyserial couldn't open the COM port (busy, or not re-enumerated yet)."""
    return "could not open port" in str(exc) or isinstance(exc, PermissionError)


def _describe_connect_error(kind: str, exc: Exception) -> str:
    """
    Turn a raw connection exception into something actionable. BLE in
    particular fails with an opaque bleak/WinRT error when Windows hasn't
    bonded with the device yet — the fix lives in Windows Bluetooth settings,
    not in this app, so say so instead of just dumping the exception text.
    """
    text = str(exc)
    if kind == "ble" and ("Insufficient Authentication" in text or "Access Denied" in text):
        return (
            "Connection failed: Windows has not paired/bonded with this BLE device yet "
            "(GATT error: insufficient authentication).\n\n"
            "Fix: open Windows Settings → Bluetooth & devices → Add device → Bluetooth, "
            "select the Meshtastic device, and enter its Bluetooth PIN when prompted "
            "(set on the Settings tab — default is a 6-digit fixed PIN). "
            "Once Windows shows it as \"Paired\", reconnect from here."
        )
    if kind == "ble" and "Error writing BLE" in text:
        return (
            "Connection failed: the BLE link connected but writing to the device failed.\n\n"
            "This has been seen even when Windows already shows the device as \"Paired\" — "
            "the pairing bond can go stale (e.g. after the device reboots or its Bluetooth "
            "stack resets) while Windows still thinks it's valid.\n\n"
            "Fix: in Windows Settings → Bluetooth & devices, remove/forget this device, "
            "then pair it again from scratch and reconnect from here. Also confirm nothing "
            "else (the Meshtastic phone app, another PC) is already connected to it — a BLE "
            "peripheral only accepts one active connection at a time."
        )
    if kind == "serial" and "could not open port" in text:
        if "Access is denied" in text or "PermissionError" in text:
            return (
                f"Connection failed: {text}\n\n"
                "Something already has that COM port open. If the unit just rebooted, Windows can "
                "take a few seconds to hand the port back — wait a moment and try again. Otherwise "
                "close anything else using it (Arduino IDE/serial monitor, the meshtastic CLI, "
                "another copy of this app)."
            )
        return (
            f"Connection failed: {text}\n\n"
            "That port doesn't exist right now — the unit is unplugged or still rebooting. Wait for "
            "it to reappear, click Refresh next to the port list, and connect again."
        )
    if kind == "tcp":
        if isinstance(exc, socket.gaierror):
            return (
                "Connection failed: that host name couldn't be resolved.\n\n"
                "\"meshtastic.local\"-style names rely on mDNS, which Windows often can't "
                "resolve. Use the device's IP address instead — click Discover to find "
                "devices on this PC's networks."
            )
        if isinstance(exc, ConnectionRefusedError):
            return (
                "Connection failed: the host answered, but nothing is listening on that port.\n\n"
                "Check that Ethernet (or WiFi) is enabled in the device's network config, that "
                "the port is 4403, and that no other client (the Meshtastic phone app, another "
                "PC) is already connected — the device accepts only one TCP client at a time."
            )
        if isinstance(exc, (TimeoutError, socket.timeout)) or "10060" in text or "timed out" in text:
            return (
                f"Connection failed: no response from the device within {TCP_CONNECT_TIMEOUT_SECS}s "
                "— it isn't reachable from this PC.\n\n"
                "Wrong IP, or the device and PC aren't on the same network. On a direct "
                "Ethernet cable with no router there's no DHCP server, so the PC falls back to "
                "a 169.254.x.x address and a DHCP-mode device gets no address at all — give "
                "both ends static IPs in the same subnet, or connect them through the router/switch."
            )
    return f"Connection failed: {exc}"


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
      'network_saved'     -> None
      'channel_saved'     -> int (channel index)
      'channel_deleted'   -> int (channel index)
      'factory_reset_done' -> None
      'telemetry'    -> {'node_id','battery_level','voltage'}
    """

    def __init__(self):
        self.interface = None
        self.kind = None
        self.events: "queue.Queue" = queue.Queue()
        self._subscribed = False
        # Connect-attempt bookkeeping: each _connect() gets a number, and bumping
        # self._attempt (disconnect(), or a newer connect) cancels any attempt
        # still in flight. _pending is the interface that attempt is currently
        # connecting, so cancelling can actually close its port.
        self._lock = threading.Lock()
        self._attempt = 0
        self._pending = None
        self._pending_attempt = None
        self._releasing: list = []
        # Which channel index each node was last heard transmitting on. The
        # NodeDB itself carries no such field (confirmed against real
        # hardware — a node isn't inherently "on" one channel), but every
        # received packet is, so this is built up live as traffic arrives.
        self.node_channels: dict = {}

    # ── connection management ────────────────────────────────────────────
    def connect_serial(self, dev_path: str | None):
        def factory(register):
            # SerialInterface(...) normally opens the port and runs the whole handshake
            # inside its constructor, so an attempt that is abandoned mid-handshake (the
            # GUI's watchdog gives up while a rebooting unit is still slow to answer) is
            # unreachable — it keeps the COM port open and every later Connect fails
            # with "Access is denied" until the app is restarted. Building it in two
            # steps lets us hold a reference while it connects, so it can be cancelled.
            iface = meshtastic.serial_interface.SerialInterface(devPath=dev_path or None, connectNow=False)
            if not hasattr(iface, "_rxThread"):  # the library found no port and bailed out early
                raise RuntimeError("No serial Meshtastic device detected — pick a port and try again.")
            register(iface)
            iface.connect()
            iface.waitForConfig()
            return iface

        # A unit that just rebooted takes a few seconds to hand its COM port back
        # (missing, or "Access is denied"), so failures to *open* the port retry.
        self._connect("serial", factory, retries=4, retry_if=_is_port_open_failure)

    def connect_tcp(self, hostname: str, port: int = 4403):
        self._connect("tcp", lambda register: _BoundedTCPInterface(hostname=hostname, portNumber=port))

    def connect_ble(self, address: str | None):
        # BLE on Windows has repeatedly proven flaky in practice: a connect attempt
        # (or the GATT write right after) fails once, then an immediate retry with
        # the exact same address succeeds — seen firsthand multiple times against
        # real hardware. Serial/TCP don't show this pattern, so only BLE retries.
        self._connect(
            "ble", lambda register: meshtastic.ble_interface.BLEInterface(address=address), retries=2
        )

    def _connect(self, kind: str, factory, retries: int = 0, retry_if=None):
        """
        Run `factory(register)` on a background thread. `register(iface)` lets a
        factory hand over an interface it is still connecting, so a cancelled or
        failed attempt can close it. `retry_if(exc)` narrows which failures are
        retried (default: all).
        """
        if self.interface is not None:
            raise RuntimeError("Already connected — disconnect first")
        self._ensure_subscribed()
        with self._lock:
            self._attempt += 1  # supersedes (cancels) any attempt still in flight
            attempt = self._attempt

        def register(iface):
            with self._lock:
                if attempt != self._attempt:
                    raise _AttemptCancelled()  # cancelled before it opened anything
                self._pending, self._pending_attempt = iface, attempt

        def release_pending():
            with self._lock:
                iface = self._pending if self._pending_attempt == attempt else None
                if iface is not None:
                    self._pending = self._pending_attempt = None
            _release_interface(iface)

        def cancelled() -> bool:
            return attempt != self._attempt

        def worker():
            # A Connect right after a Disconnect/timeout must not race the previous
            # attempt's port still being closed.
            with self._lock:
                closing = list(self._releasing)
            for t in closing:
                t.join(5)

            tries = 0
            while True:
                try:
                    iface = factory(register)
                except _AttemptCancelled:
                    return
                except Exception as exc:  # noqa: BLE001 - surface any failure to the GUI
                    release_pending()  # a half-connected interface must not keep its port open
                    if cancelled():
                        return  # the caller already gave up (e.g. GUI timeout) — stay quiet
                    if tries < retries and (retry_if is None or retry_if(exc)):
                        tries += 1
                        self.events.put((
                            "log", f"{kind} connect attempt failed ({exc}); retrying ({tries}/{retries})…",
                        ))
                        time.sleep(2)
                        if cancelled():
                            return
                        continue
                    self.events.put(("error", _describe_connect_error(kind, exc)))
                    self.events.put(("disconnected", None))
                    return

                with self._lock:
                    current = attempt == self._attempt
                    if current:
                        self._pending = self._pending_attempt = None
                        self.interface = iface
                        self.kind = kind
                if not current:
                    # Finished connecting *after* the caller gave up on it. Adopting it
                    # would leave the GUI showing "disconnected" over a live link, and
                    # the next Connect failing with "Already connected".
                    _release_interface(iface)
                return

        threading.Thread(target=worker, daemon=True, name="mesh-connect").start()

    def _release_async(self, *ifaces, wait: float = 0.0):
        """Close interfaces off the caller's thread (close() can take a second or
        block); optionally wait a bounded time. connect() waits for any that are
        still closing, so a fast reconnect never races the old port's release."""
        ifaces = [i for i in ifaces if i is not None]
        if not ifaces:
            return

        def run():
            for i in ifaces:
                _release_interface(i)

        t = threading.Thread(target=run, daemon=True, name="mesh-release")
        with self._lock:
            self._releasing = [x for x in self._releasing if x.is_alive()] + [t]
        t.start()
        if wait:
            t.join(wait)

    def disconnect(self):
        with self._lock:
            self._attempt += 1  # cancels an attempt that is still connecting
            pending, self._pending, self._pending_attempt = self._pending, None, None
            iface, self.interface = self.interface, None
            self.kind = None
        self.node_channels = {}  # don't carry a previous device's channel history into the next session
        self._release_async(iface, pending, wait=3.0)
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
            "wait_bluetooth_secs": lc.power.wait_bluetooth_secs,
        }

    def save_device_settings(self, long_name: str, short_name: str, fixed_pin: int, role: str, region: str,
                              wait_bluetooth_secs: int):
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
                node.localConfig.power.wait_bluetooth_secs = int(wait_bluetooth_secs)
                node.writeConfig("power")
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

    # ── network (IPv4 address) settings ─────────────────────────────────
    def get_network_settings(self) -> dict:
        """The device's current IPv4 addressing config, read from the
        already-synced local config (populated during connect)."""
        if self.interface is None:
            raise RuntimeError("Not connected")
        n = self.interface.localNode.localConfig.network
        return {
            "address_mode": config_pb2.Config.NetworkConfig.AddressMode.Name(n.address_mode),
            "ip": int_to_ipv4(n.ipv4_config.ip),
            "subnet": int_to_ipv4(n.ipv4_config.subnet),
            "gateway": int_to_ipv4(n.ipv4_config.gateway),
            "dns": int_to_ipv4(n.ipv4_config.dns),
            "eth_enabled": n.eth_enabled,
            "wifi_enabled": n.wifi_enabled,
        }

    @staticmethod
    def validate_network_settings(address_mode: str, ip: str = "", subnet: str = "",
                                   gateway: str = "", dns: str = "") -> dict:
        """
        Check a requested IPv4 assignment and return the device-ready values
        (fixed32 ints). Raises ValueError with a user-readable message. DHCP has
        nothing to validate.
        """
        values = {"ip": 0, "subnet": 0, "gateway": 0, "dns": 0}
        if address_mode != "STATIC":
            return values

        def v4(text: str, label: str) -> ipaddress.IPv4Address:
            try:
                return ipaddress.IPv4Address(text.strip())
            except ipaddress.AddressValueError as exc:
                raise ValueError(f"{label}: {exc}") from None

        ip_addr = v4(ip, "IP Address")
        try:
            net = ipaddress.IPv4Network(f"{ip_addr}/{parse_subnet_mask(subnet).prefixlen}", strict=False)
        except ValueError as exc:
            raise ValueError(f"Subnet Mask: {exc}") from None
        if ip_addr.is_multicast or ip_addr.is_unspecified or ip_addr.is_loopback:
            raise ValueError(f"IP Address: {ip_addr} can't be assigned to a device.")
        if ip_addr in (net.network_address, net.broadcast_address) and net.prefixlen < 31:
            raise ValueError(f"IP Address: {ip_addr} is the network/broadcast address of {net} — pick a host address.")
        values["ip"] = ipv4_to_int(str(ip_addr))
        values["subnet"] = ipv4_to_int(str(net.netmask))
        for key, text, label in (("gateway", gateway, "Gateway"), ("dns", dns, "DNS")):
            if text.strip():
                addr = v4(text, label)
                if key == "gateway" and addr not in net:
                    raise ValueError(f"Gateway: {addr} isn't inside {net} — it must be on the device's own subnet.")
                values[key] = ipv4_to_int(str(addr))
        return values

    def save_network_settings(self, address_mode: str, ip: str = "", subnet: str = "",
                               gateway: str = "", dns: str = ""):
        """
        Assign the device's IPv4 addressing (DHCP or a static address/mask).
        Serial only: the change makes the device drop and re-acquire its
        network address, which would sever a TCP session mid-write. Validation
        errors raise immediately; the write itself runs on a background thread
        and reports 'network_saved' or 'error'.
        """
        if self.interface is None:
            raise RuntimeError("Not connected")
        if self.kind != "serial":
            raise RuntimeError("Assigning an IP address requires a Serial (USB) connection.")

        static = address_mode == "STATIC"
        values = self.validate_network_settings(address_mode, ip, subnet, gateway, dns)
        node = self.interface.localNode

        def worker():
            try:
                node.beginSettingsTransaction()
                n = node.localConfig.network
                n.address_mode = config_pb2.Config.NetworkConfig.AddressMode.Value(address_mode)
                if static:  # in DHCP mode the device ignores these; leave any stored static values alone
                    n.ipv4_config.ip = values["ip"]
                    n.ipv4_config.subnet = values["subnet"]
                    n.ipv4_config.gateway = values["gateway"]
                    n.ipv4_config.dns = values["dns"]
                node.writeConfig("network")
                node.commitSettingsTransaction()
                self.events.put(("network_saved", None))
            except Exception as exc:
                self.events.put(("error", f"Save network settings failed: {exc}"))

        threading.Thread(target=worker, daemon=True, name="mesh-save-network").start()

    # ── channels ─────────────────────────────────────────────────────────
    def get_channels(self) -> list:
        """All 8 channel slots (some may be DISABLED/empty), read from the
        device's already-synced channel list (populated during connect)."""
        if self.interface is None:
            raise RuntimeError("Not connected")
        result = []
        for ch in self.interface.localNode.channels:
            s = ch.settings
            result.append({
                "index": ch.index,
                "role": channel_pb2.Channel.Role.Name(ch.role),
                "name": s.name,
                "psk": bytes(s.psk),
                "uplink_enabled": s.uplink_enabled,
                "downlink_enabled": s.downlink_enabled,
                "position_precision": s.module_settings.position_precision,
                "is_muted": s.module_settings.is_muted,
            })
        return result

    def save_channel(self, index: int, name: str, psk: bytes, uplink_enabled: bool,
                      downlink_enabled: bool, position_precision: int, is_muted: bool,
                      role: str = None):
        """
        Write one channel slot to the device. Pass role="SECONDARY" only when
        turning a DISABLED slot into a new channel — leave it None to edit an
        existing PRIMARY/SECONDARY channel's settings without touching its role.
        Runs on a background thread; reports 'channel_saved' or 'error'.
        """
        if self.interface is None:
            raise RuntimeError("Not connected")
        node = self.interface.localNode

        def worker():
            try:
                ch = node.channels[index]
                if role is not None:
                    ch.role = channel_pb2.Channel.Role.Value(role)
                ch.settings.name = name
                ch.settings.psk = psk
                ch.settings.uplink_enabled = uplink_enabled
                ch.settings.downlink_enabled = downlink_enabled
                ch.settings.module_settings.position_precision = position_precision
                ch.settings.module_settings.is_muted = is_muted
                node.writeChannel(index)
                self.events.put(("channel_saved", index))
            except Exception as exc:
                self.events.put(("error", f"Save channel failed: {exc}"))

        threading.Thread(target=worker, daemon=True, name="mesh-save-channel").start()

    def delete_channel(self, index: int):
        """
        Delete a SECONDARY channel (shifts higher slots down). The underlying
        library hard-exits the whole process if asked to delete a non-SECONDARY
        channel, so that's checked here first and raised as a normal error instead.
        """
        if self.interface is None:
            raise RuntimeError("Not connected")
        node = self.interface.localNode
        role = channel_pb2.Channel.Role.Name(node.channels[index].role)
        if role != "SECONDARY":
            raise RuntimeError(f"Only SECONDARY channels can be deleted (slot {index} is {role})")

        def worker():
            try:
                node.deleteChannel(index)
                self.events.put(("channel_deleted", index))
            except Exception as exc:
                self.events.put(("error", f"Delete channel failed: {exc}"))

        threading.Thread(target=worker, daemon=True, name="mesh-delete-channel").start()

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
        with self._lock:
            if interface is not self.interface:
                return
            self.interface = None
            self.kind = None
        self.node_channels = {}
        # The unit rebooted or was unplugged. Dropping the reference isn't enough:
        # make sure its port handle is actually closed so reconnecting works.
        self._release_async(interface)
        self.events.put(("disconnected", None))

    def _on_node_updated(self, node, interface):
        if interface is not self.interface:
            return
        self.events.put(("node_updated", node))

    def _track_node_channel(self, packet: dict):
        # Channel 0 is protobuf's zero-value, so MessageToDict() drops the "channel"
        # key entirely for anything received on the primary channel -- the same
        # reason _on_receive_text below defaults it to 0 rather than treating a
        # missing key as "unknown". Without this default, every packet on the
        # primary channel (most real traffic) was silently discarded here.
        from_id = packet.get("fromId")
        channel = packet.get("channel", 0)
        if from_id:
            self.node_channels[from_id] = channel

    def _on_receive_text(self, packet, interface):
        if interface is not self.interface:
            return
        self._track_node_channel(packet)
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
        self._track_node_channel(packet)
        portnum = packet.get("decoded", {}).get("portnum")
        if portnum == "TEXT_MESSAGE_APP":
            return  # already handled by _on_receive_text
        self.events.put(("packet", packet))

    def get_node_channels(self) -> dict:
        """{node_id: channel_index} for every node heard on this connection so
        far, from the channel field of its received packets (the NodeDB has
        no per-node channel field of its own)."""
        return dict(self.node_channels)

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
