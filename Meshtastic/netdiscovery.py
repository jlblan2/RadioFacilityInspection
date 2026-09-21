#!/usr/bin/env python3
"""
Find Meshtastic devices reachable over IP (Ethernet or WiFi) from this PC.

Meshtastic nodes with a network interface expose their API on TCP port 4403.
There's no reliable name to type — "meshtastic.local" relies on mDNS, which
Windows frequently can't resolve, and a wired node's DHCP address is whatever
the router handed out — so this looks for the port instead, three ways:

  1. Sweep the /24 around each of this PC's routable IPv4 addresses.
  2. Ask mDNS for `_meshtastic._tcp` and collect whoever answers.
  3. Probe every host already in the ARP table — the only way to see a
     device on a direct cable, where the PC's own address is link-local
     (169.254.x.x) and there's no subnet worth sweeping.

Every candidate is confirmed by an actual TCP connect to the API port.
"""

import ctypes
import ipaddress
import re
import socket
import struct
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from ctypes import wintypes

MESHTASTIC_TCP_PORT = 4403
PROBE_TIMEOUT_SECS = 0.7
MDNS_WAIT_SECS = 2.0
_MDNS_GROUP = ("224.0.0.251", 5353)


# ── local interface enumeration (Windows, no third-party deps) ───────────────

class _IP_ADDR_STRING(ctypes.Structure):
    pass


_IP_ADDR_STRING._fields_ = [
    ("Next", ctypes.POINTER(_IP_ADDR_STRING)),
    ("IpAddress", ctypes.c_char * 16),
    ("IpMask", ctypes.c_char * 16),
    ("Context", wintypes.DWORD),
]


class _IP_ADAPTER_INFO(ctypes.Structure):
    pass


# Only the leading fields are declared — everything after IpAddressList is
# never read, and the API owns the buffer's real size and layout.
_IP_ADAPTER_INFO._fields_ = [
    ("Next", ctypes.POINTER(_IP_ADAPTER_INFO)),
    ("ComboIndex", wintypes.DWORD),
    ("AdapterName", ctypes.c_char * 260),
    ("Description", ctypes.c_char * 132),
    ("AddressLength", wintypes.UINT),
    ("Address", ctypes.c_ubyte * 8),
    ("Index", wintypes.DWORD),
    ("Type", wintypes.UINT),
    ("DhcpEnabled", wintypes.UINT),
    ("CurrentIpAddress", ctypes.c_void_p),
    ("IpAddressList", _IP_ADDR_STRING),
]


def local_ipv4_networks() -> list:
    """[(ip, prefix_len), ...] for this PC's configured IPv4 addresses."""
    if sys.platform != "win32":
        return _local_ipv4_fallback()
    try:
        iphlpapi = ctypes.WinDLL("iphlpapi.dll")
        size = wintypes.ULONG(0)
        iphlpapi.GetAdaptersInfo(None, ctypes.byref(size))  # ask for required size
        buf = ctypes.create_string_buffer(size.value)
        if iphlpapi.GetAdaptersInfo(buf, ctypes.byref(size)) != 0:
            return _local_ipv4_fallback()
        results = []
        adapter = ctypes.cast(buf, ctypes.POINTER(_IP_ADAPTER_INFO))
        while adapter:
            node = adapter.contents.IpAddressList
            entry = ctypes.pointer(node)
            while entry:
                ip = entry.contents.IpAddress.decode("ascii", "ignore")
                mask = entry.contents.IpMask.decode("ascii", "ignore")
                if ip and ip != "0.0.0.0" and not ip.startswith("127."):
                    try:
                        prefix = ipaddress.IPv4Network(f"0.0.0.0/{mask}").prefixlen
                    except ValueError:
                        prefix = 24
                    results.append((ip, prefix))
                entry = entry.contents.Next
            adapter = adapter.contents.Next
        return results or _local_ipv4_fallback()
    except Exception:
        return _local_ipv4_fallback()


def _local_ipv4_fallback() -> list:
    found = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.add(info[4][0])
    except OSError:
        pass
    return [(ip, 24) for ip in sorted(found) if not ip.startswith("127.")]


# ── candidate sources ────────────────────────────────────────────────────────

def _sweep_candidates(networks: list) -> set:
    """Every host in the /24 around each routable local address."""
    hosts = set()
    for ip, prefix in networks:
        addr = ipaddress.IPv4Address(ip)
        # Link-local (169.254/16) means "no DHCP answered" — nothing to sweep there.
        # A /31-/32 is a point-to-point or VPN address — nobody else lives on it,
        # and sweeping would just spray probes into the tunnel.
        if addr.is_link_local or prefix >= 31:
            continue
        net = ipaddress.IPv4Network(f"{ip}/24", strict=False)
        hosts.update(str(h) for h in net.hosts() if str(h) != ip)
    return hosts


def _mdns_candidates(networks: list) -> set:
    """Hosts that answer an mDNS PTR query for _meshtastic._tcp, per interface."""
    qname = b"".join(bytes([len(p)]) + p.encode() for p in ("_meshtastic", "_tcp", "local")) + b"\x00"
    # Class 0x8001 = IN with the "unicast response" bit, so answers come straight back to us.
    query = struct.pack("!6H", 0, 0, 1, 0, 0, 0) + qname + struct.pack("!2H", 12, 0x8001)
    responders = set()
    socks = []
    for ip, _ in networks:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.bind((ip, 0))
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(ip))
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
            s.settimeout(0.2)
            s.sendto(query, _MDNS_GROUP)
            socks.append(s)
        except OSError:
            continue
    deadline = time.time() + MDNS_WAIT_SECS
    while time.time() < deadline and socks:
        for s in socks:
            try:
                _, (src, _port) = s.recvfrom(4096)
                responders.add(src)
            except OSError:
                pass
    for s in socks:
        s.close()
    return responders


_ARP_LINE = re.compile(
    r"^\s*(\d{1,3}(?:\.\d{1,3}){3})\s+[0-9a-fA-F]{2}(?:-[0-9a-fA-F]{2}){5}\s+dynamic", re.M
)


def _arp_candidates() -> set:
    """Unicast hosts already in this PC's ARP cache, across all interfaces."""
    try:
        out = subprocess.run(["arp", "-a"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return set()
    hosts = set()
    for m in _ARP_LINE.finditer(out):
        addr = ipaddress.IPv4Address(m.group(1))
        if not addr.is_multicast and not m.group(1).endswith(".255"):
            hosts.add(m.group(1))
    return hosts


# ── probing ──────────────────────────────────────────────────────────────────

def _port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=PROBE_TIMEOUT_SECS):
            return True
    except OSError:
        return False


def discover_tcp_devices(port: int = MESHTASTIC_TCP_PORT) -> list:
    """
    IP addresses currently accepting connections on the Meshtastic API port.
    Blocks for a few seconds — call it from a background thread.
    """
    networks = local_ipv4_networks()
    candidates = _sweep_candidates(networks) | _arp_candidates() | _mdns_candidates(networks)
    own = {ip for ip, _ in networks}
    candidates -= own

    with ThreadPoolExecutor(max_workers=96) as pool:
        ordered = sorted(candidates, key=lambda h: tuple(int(p) for p in h.split(".")))
        hits = [h for h, ok in zip(ordered, pool.map(lambda h: _port_open(h, port), ordered)) if ok]
    return hits
