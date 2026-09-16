#!/usr/bin/env python3
"""
Meshtastic Control GUI
Tkinter front-end for the `meshtastic` Python module (mesh_client.py wraps it).

Usage:
    python meshtastic_gui.py
"""

import base64
import csv
import os
import queue
import sys
import threading
import time
import tkinter as tk
from datetime import datetime
from tkinter import ttk, messagebox, filedialog

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

from mesh_client import (
    MeshClient, list_serial_ports, scan_ble, node_display_name, strip_ansi,
    BROADCAST_ADDR, ROLE_OPTIONS, REGION_OPTIONS,
    PSK_MODES, describe_psk, encode_psk,
)
import firmware as fw
import printing

FIRMWARE_CACHE_DIR = os.path.join(BASE, "firmware_cache")
REPORT_CACHE_DIR = os.path.join(BASE, "report_cache")

# ── Cross-platform font families ─────────────────────────────────────────────
if sys.platform == "darwin":
    _FF, _FM = "Helvetica", "Menlo"
elif sys.platform.startswith("linux"):
    _FF, _FM = "DejaVu Sans", "DejaVu Sans Mono"
else:
    _FF, _FM = "Arial", "Consolas"

FA = (_FF, 10)
FB = (_FF, 10, "bold")
FT = (_FF, 13, "bold")
FS = (_FF, 9)
FMONO = (_FM, 10)

HDR_BG = "#1F4E79"
HDR_FG = "#FFFFFF"
OK_GRN = "#006400"
ERR_RED = "#CC0000"
DIM_FG = "#666666"

POLL_MS = 200


def ts() -> str:
    return time.strftime("%H:%M:%S")


# Single-cell LiPo discharge curve (voltage -> % charge), descending order.
# Used to estimate a charge percentage when the device reports battery_level=101
# ("powered externally") — that sentinel means the firmware isn't reporting a
# real percentage at all, so voltage is the only signal available.
_LIPO_CURVE = [
    (4.20, 100), (4.15, 95), (4.11, 90), (4.08, 85), (4.02, 80),
    (3.98, 75), (3.95, 70), (3.91, 65), (3.87, 60), (3.85, 55),
    (3.84, 50), (3.82, 45), (3.80, 40), (3.79, 35), (3.77, 30),
    (3.75, 25), (3.73, 20), (3.71, 15), (3.69, 10), (3.61, 5),
    (3.27, 0),
]


def estimate_charge_from_voltage(voltage: float) -> int:
    if voltage >= _LIPO_CURVE[0][0]:
        return 100
    if voltage <= _LIPO_CURVE[-1][0]:
        return 0
    for (v_hi, p_hi), (v_lo, p_lo) in zip(_LIPO_CURVE, _LIPO_CURVE[1:]):
        if v_lo <= voltage <= v_hi:
            frac = (voltage - v_lo) / (v_hi - v_lo)
            return round(p_lo + frac * (p_hi - p_lo))
    return 0


def format_battery(battery_level, voltage) -> str:
    if battery_level is None:
        return "—"
    volt_str = f" ({voltage:.2f}V)" if voltage else ""
    if battery_level > 100:
        if voltage:
            return f"Powered (USB) — ~{estimate_charge_from_voltage(voltage)}% est.{volt_str}"
        return f"Powered (USB){volt_str}"
    return f"{battery_level}%{volt_str}"


class ChannelDialog(tk.Toplevel):
    """Modal dialog for adding a new channel or editing an existing one."""

    def __init__(self, parent, index: int, existing, on_save):
        super().__init__(parent)
        self.title(f"{'Edit' if existing else 'Add'} Channel {index}")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self.on_save = on_save
        self.is_new = existing is None

        pad = dict(padx=8, pady=6)
        row = 0

        tk.Label(self, text="Name:", font=FA).grid(row=row, column=0, sticky="w", **pad)
        self.name_entry = tk.Entry(self, font=FA, width=24)
        self.name_entry.grid(row=row, column=1, sticky="w", **pad)
        row += 1

        tk.Label(self, text="Encryption:", font=FA).grid(row=row, column=0, sticky="w", **pad)
        self.psk_mode = ttk.Combobox(self, font=FA, width=12, state="readonly", values=PSK_MODES)
        self.psk_mode.grid(row=row, column=1, sticky="w", **pad)
        self.psk_mode.bind("<<ComboboxSelected>>", self._on_psk_mode_change)
        row += 1

        tk.Label(self, text="Custom PSK (base64):", font=FA).grid(row=row, column=0, sticky="w", **pad)
        self.psk_entry = tk.Entry(self, font=FA, width=34)
        self.psk_entry.grid(row=row, column=1, sticky="w", **pad)
        row += 1

        self.uplink_var = tk.BooleanVar(value=True)
        tk.Checkbutton(self, text="Uplink Enabled", font=FA, variable=self.uplink_var).grid(
            row=row, column=0, columnspan=2, sticky="w", padx=8)
        row += 1
        self.downlink_var = tk.BooleanVar(value=True)
        tk.Checkbutton(self, text="Downlink Enabled", font=FA, variable=self.downlink_var).grid(
            row=row, column=0, columnspan=2, sticky="w", padx=8)
        row += 1
        self.muted_var = tk.BooleanVar(value=False)
        tk.Checkbutton(self, text="Muted", font=FA, variable=self.muted_var).grid(
            row=row, column=0, columnspan=2, sticky="w", padx=8)
        row += 1

        tk.Label(self, text="Position Precision (bits):", font=FA).grid(row=row, column=0, sticky="w", **pad)
        self.precision_entry = tk.Entry(self, font=FA, width=10)
        self.precision_entry.grid(row=row, column=1, sticky="w", **pad)
        row += 1

        if existing:
            self.name_entry.insert(0, existing["name"])
            mode = describe_psk(existing["psk"])
            self.psk_mode.set(mode)
            if mode == "Custom":
                self.psk_entry.insert(0, base64.b64encode(existing["psk"]).decode())
            self.uplink_var.set(existing["uplink_enabled"])
            self.downlink_var.set(existing["downlink_enabled"])
            self.muted_var.set(existing["is_muted"])
            self.precision_entry.insert(0, str(existing["position_precision"]))
        else:
            self.psk_mode.set("Default")
            self.precision_entry.insert(0, "32")
        self._on_psk_mode_change()

        btns = tk.Frame(self)
        btns.grid(row=row, column=0, columnspan=2, pady=10)
        tk.Button(btns, text="Save", font=FB, bg="#2E7D32", fg="white", padx=14,
                   command=self._on_save).pack(side="left", padx=6)
        tk.Button(btns, text="Cancel", font=FA, padx=14, command=self.destroy).pack(side="left", padx=6)

    def _on_psk_mode_change(self, _event=None):
        self.psk_entry.config(state="normal" if self.psk_mode.get() == "Custom" else "disabled")

    def _on_save(self):
        name = self.name_entry.get().strip()
        if self.is_new and not name:
            messagebox.showwarning("Missing Name", "Enter a channel name.", parent=self)
            return
        try:
            psk = encode_psk(self.psk_mode.get(), self.psk_entry.get().strip())
        except ValueError as exc:
            messagebox.showerror("Invalid PSK", str(exc), parent=self)
            return
        try:
            precision = int(self.precision_entry.get().strip() or 0)
        except ValueError:
            messagebox.showerror("Invalid Value", "Position Precision must be numeric.", parent=self)
            return
        self.on_save({
            "name": name,
            "psk": psk,
            "uplink_enabled": self.uplink_var.get(),
            "downlink_enabled": self.downlink_var.get(),
            "position_precision": precision,
            "is_muted": self.muted_var.get(),
        })
        self.destroy()


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Meshtastic Control")
        self.geometry("980x640")
        self.minsize(760, 480)

        self.client = MeshClient()
        self.connected_info = None
        self.node_lookup: dict[str, str] = {}   # display name -> node id, for the destination combobox

        self.fw_queue: "queue.Queue" = queue.Queue()
        self.latest_release = None
        self.fw_platform = None
        self.fw_path = None

        self.ble_queue: "queue.Queue" = queue.Queue()
        self.printer_queue: "queue.Queue" = queue.Queue()

        self._channels_cache: dict = {}

        self._connect_attempt = 0

        self._closing = False

        self._build_header()
        self._build_notebook()
        self._build_statusbar()

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(POLL_MS, self._poll_events)

    # ── layout ───────────────────────────────────────────────────────────
    def _build_header(self):
        hdr = tk.Frame(self, bg=HDR_BG)
        hdr.pack(fill="x")
        tk.Label(hdr, text="Meshtastic Control", font=FT, bg=HDR_BG, fg=HDR_FG,
                  padx=12, pady=8).pack(side="left")
        self.hdr_status = tk.Label(hdr, text="Disconnected", font=FB, bg=HDR_BG, fg="#FF8080", padx=12)
        self.hdr_status.pack(side="right")

    def _build_notebook(self):
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True)

        self.tab_conn = ttk.Frame(nb)
        self.tab_msgs = ttk.Frame(nb)
        self.tab_nodes = ttk.Frame(nb)
        self.tab_settings = ttk.Frame(nb)
        self.tab_firmware = ttk.Frame(nb)
        self.tab_mqtt = ttk.Frame(nb)
        self.tab_report = ttk.Frame(nb)
        self.tab_log = ttk.Frame(nb)
        nb.add(self.tab_conn, text="Connection")
        nb.add(self.tab_msgs, text="Messages")
        nb.add(self.tab_nodes, text="Nodes")
        nb.add(self.tab_settings, text="Settings")
        nb.add(self.tab_firmware, text="Firmware")
        nb.add(self.tab_mqtt, text="MQTT")
        nb.add(self.tab_report, text="Report")
        nb.add(self.tab_log, text="Log")

        self._build_connection_tab()
        self._build_messages_tab()
        self._build_nodes_tab()
        self._build_settings_tab()
        self._build_firmware_tab()
        self._build_mqtt_tab()
        self._build_report_tab()
        self._build_log_tab()

        self.notebook = nb
        nb.bind("<<NotebookTabChanged>>", self._on_tab_changed)

    def _on_tab_changed(self, _event=None):
        if self.notebook.select() == str(self.tab_report):
            self._refresh_report()

    def _build_statusbar(self):
        bar = tk.Frame(self, bd=1, relief="sunken")
        bar.pack(fill="x", side="bottom")
        self.status_var = tk.StringVar(value="Ready")
        tk.Label(bar, textvariable=self.status_var, font=FS, anchor="w").pack(fill="x", padx=6)

    # ── Connection tab ───────────────────────────────────────────────────
    def _build_connection_tab(self):
        f = self.tab_conn
        pad = dict(padx=8, pady=6)

        top = tk.Frame(f)
        top.pack(fill="x", **pad)

        self.conn_kind = tk.StringVar(value="serial")
        for label, val in [("Serial (USB)", "serial"), ("TCP / WiFi", "tcp"), ("Bluetooth LE", "ble")]:
            tk.Radiobutton(top, text=label, variable=self.conn_kind, value=val, font=FA,
                            command=self._refresh_conn_fields).pack(side="left", padx=(0, 16))

        self.conn_fields = tk.Frame(f)
        self.conn_fields.pack(fill="x", **pad)

        # -- serial fields --
        self.serial_frame = tk.Frame(self.conn_fields)
        tk.Label(self.serial_frame, text="Port:", font=FA).grid(row=0, column=0, sticky="w")
        self.serial_port = ttk.Combobox(self.serial_frame, width=40, font=FA, state="readonly")
        self.serial_port.grid(row=0, column=1, padx=6)
        tk.Button(self.serial_frame, text="Refresh", font=FA, command=self._refresh_serial_ports).grid(row=0, column=2)

        # -- tcp fields --
        self.tcp_frame = tk.Frame(self.conn_fields)
        tk.Label(self.tcp_frame, text="Host:", font=FA).grid(row=0, column=0, sticky="w")
        self.tcp_host = tk.Entry(self.tcp_frame, width=30, font=FA)
        self.tcp_host.insert(0, "meshtastic.local")
        self.tcp_host.grid(row=0, column=1, padx=6)
        tk.Label(self.tcp_frame, text="Port:", font=FA).grid(row=0, column=2, sticky="w")
        self.tcp_port = tk.Entry(self.tcp_frame, width=8, font=FA)
        self.tcp_port.insert(0, "4403")
        self.tcp_port.grid(row=0, column=3, padx=6)

        # -- ble fields --
        self.ble_frame = tk.Frame(self.conn_fields)
        tk.Label(self.ble_frame, text="Device:", font=FA).grid(row=0, column=0, sticky="w")
        self.ble_addr = ttk.Combobox(self.ble_frame, width=40, font=FA, state="readonly")
        self.ble_addr.grid(row=0, column=1, padx=6)
        self.btn_ble_scan = tk.Button(self.ble_frame, text="Scan", font=FA, command=self._scan_ble)
        self.btn_ble_scan.grid(row=0, column=2)

        btns = tk.Frame(f)
        btns.pack(fill="x", **pad)
        self.btn_connect = tk.Button(btns, text="Connect", font=FB, bg="#2E7D32", fg="white",
                                       padx=16, command=self._on_connect)
        self.btn_connect.pack(side="left")
        self.btn_disconnect = tk.Button(btns, text="Disconnect", font=FB, bg="#B71C1C", fg="white",
                                          padx=16, command=self._on_disconnect, state="disabled")
        self.btn_disconnect.pack(side="left", padx=8)

        info = tk.LabelFrame(f, text="Device Info", font=FB)
        info.pack(fill="x", **pad)
        self.info_labels = {}
        for i, key in enumerate(["long_name", "short_name", "node_id", "hw_model", "firmware", "battery"]):
            title = {"long_name": "Long Name", "short_name": "Short Name", "node_id": "Node ID",
                      "hw_model": "Hardware", "firmware": "Firmware", "battery": "Battery"}[key]
            tk.Label(info, text=title + ":", font=FA).grid(row=i, column=0, sticky="w", padx=8, pady=2)
            lbl = tk.Label(info, text="—", font=FB)
            lbl.grid(row=i, column=1, sticky="w", padx=8, pady=2)
            self.info_labels[key] = lbl

        self._refresh_serial_ports()
        self._refresh_conn_fields()

    def _refresh_conn_fields(self):
        for w in (self.serial_frame, self.tcp_frame, self.ble_frame):
            w.pack_forget()
        kind = self.conn_kind.get()
        {"serial": self.serial_frame, "tcp": self.tcp_frame, "ble": self.ble_frame}[kind].pack(anchor="w")

    def _refresh_serial_ports(self):
        ports = list_serial_ports()
        values = []
        default_index = 0
        for i, (dev, desc, likely) in enumerate(ports):
            label = f"{dev}  ({desc})"
            if likely:
                label += "  [likely Meshtastic device]"
                default_index = i
            values.append(label)
        self.serial_port["values"] = values
        if values:
            self.serial_port.current(default_index)

    def _scan_ble(self):
        # BLEInterface.scan() blocks for ~10 seconds — running it on the GUI thread
        # freezes the whole window for that long (looks hung/crashed). Run it in a
        # background thread and report the result through ble_queue instead.
        self.btn_ble_scan.config(state="disabled")
        self.status_var.set("Scanning for BLE devices (10s)…")

        def worker():
            try:
                devices = scan_ble()
                self.ble_queue.put(("ble_scan_done", devices))
            except Exception as exc:
                self.ble_queue.put(("ble_scan_error", str(exc)))

        threading.Thread(target=worker, daemon=True, name="ble-scan").start()

    def _handle_ble_event(self, kind: str, payload):
        self.btn_ble_scan.config(state="normal")
        if kind == "ble_scan_done":
            values = [f"{addr}  ({name})" for addr, name in payload]
            self.ble_addr["values"] = values
            if values:
                self.ble_addr.current(0)
            if values:
                self.status_var.set(f"Found {len(values)} BLE device(s)")
            else:
                self.status_var.set(
                    "No Meshtastic BLE devices found. Make sure Bluetooth is enabled on the "
                    "device and it was recently powered on — some boards only advertise BLE "
                    "for a short window after boot to save battery."
                )
        elif kind == "ble_scan_error":
            self.status_var.set("Ready")
            messagebox.showerror("BLE Scan Failed", str(payload))

    # BLE needs far more headroom than serial/TCP: its own scan takes ~10s, GATT
    # negotiation/bonding adds more, and syncing a full NodeDB over BLE's low
    # throughput can legitimately take a long time on a mesh with many nodes —
    # confirmed by hand (a real BLE connect took ~40s to fully complete). A short
    # timeout here doesn't just report a hang, it actively kills good connections
    # right before they finish.
    CONNECT_TIMEOUT_MS = {"serial": 25000, "tcp": 25000, "ble": 120000}

    def _on_connect(self):
        kind = self.conn_kind.get()
        try:
            if kind == "serial":
                sel = self.serial_port.get()
                dev_path = sel.split("  ")[0] if sel else None
                self.client.connect_serial(dev_path)
            elif kind == "tcp":
                host = self.tcp_host.get().strip()
                port = int(self.tcp_port.get().strip() or 4403)
                if not host:
                    messagebox.showwarning("Missing Host", "Enter a hostname or IP address.")
                    return
                self.client.connect_tcp(host, port)
            else:
                sel = self.ble_addr.get()
                addr = sel.split("  ")[0] if sel else None
                self.client.connect_ble(addr)
        except Exception as exc:
            messagebox.showerror("Connection Error", str(exc))
            return
        self.status_var.set("Connecting…")
        self.btn_connect.config(state="disabled")
        self._connect_attempt += 1
        timeout_ms = self.CONNECT_TIMEOUT_MS.get(kind, 25000)
        self.after(timeout_ms, lambda a=self._connect_attempt, t=timeout_ms: self._check_connect_timeout(a, t))

    def _check_connect_timeout(self, attempt: int, timeout_ms: int):
        # A connection attempt can still hang indefinitely past its allotted time
        # with no 'connected' or 'error' event ever arriving. Without this, the
        # Connect button would stay disabled forever with no way to recover
        # short of restarting the app.
        if attempt != self._connect_attempt or self.client.is_connected():
            return
        self.client.disconnect()
        self.status_var.set("Connection timed out")
        messagebox.showerror(
            "Connection Timed Out",
            f"No response after {timeout_ms // 1000}s. The device may be out of range, already "
            "connected elsewhere, not currently advertising (BLE devices often only advertise "
            "briefly after power-on to save battery), or not yet paired in Windows Bluetooth settings."
        )

    def _on_disconnect(self):
        self._connect_attempt += 1  # invalidate any pending connect-timeout check
        self.client.disconnect()

    # ── Messages tab ─────────────────────────────────────────────────────
    def _build_messages_tab(self):
        f = self.tab_msgs
        top = tk.Frame(f)
        top.pack(fill="x", padx=8, pady=8)

        tk.Label(top, text="To:", font=FA).pack(side="left")
        self.msg_dest = ttk.Combobox(top, width=30, font=FA, state="readonly")
        self.msg_dest["values"] = ["Broadcast (all)"]
        self.msg_dest.current(0)
        self.msg_dest.pack(side="left", padx=6)

        tk.Label(top, text="Channel:", font=FA).pack(side="left", padx=(16, 0))
        self.msg_channel = ttk.Spinbox(top, from_=0, to=7, width=4, font=FA)
        self.msg_channel.set(0)
        self.msg_channel.pack(side="left", padx=6)

        self.msg_log = tk.Text(f, font=FMONO, state="disabled", wrap="word")
        self.msg_log.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.msg_log.tag_config("in", foreground="#0B5FA5")
        self.msg_log.tag_config("out", foreground=OK_GRN)
        self.msg_log.tag_config("meta", foreground=DIM_FG)

        bottom = tk.Frame(f)
        bottom.pack(fill="x", padx=8, pady=(0, 8))
        self.msg_entry = tk.Entry(bottom, font=FA)
        self.msg_entry.pack(side="left", fill="x", expand=True)
        self.msg_entry.bind("<Return>", lambda e: self._send_message())
        tk.Button(bottom, text="Send", font=FB, command=self._send_message).pack(side="left", padx=(6, 0))

    def _send_message(self):
        text = self.msg_entry.get().strip()
        if not text:
            return
        if not self.client.is_connected():
            messagebox.showwarning("Not Connected", "Connect to a device first.")
            return
        dest_label = self.msg_dest.get()
        dest_id = self.node_lookup.get(dest_label, BROADCAST_ADDR)
        try:
            channel = int(self.msg_channel.get())
        except ValueError:
            channel = 0
        try:
            self.client.send_text(text, destination_id=dest_id, channel_index=channel)
        except Exception as exc:
            messagebox.showerror("Send Failed", str(exc))
            return
        self._append_msg(f"[{ts()}] → {dest_label} (ch {channel}): {text}\n", "out")
        self.msg_entry.delete(0, "end")

    def _append_msg(self, line: str, tag: str):
        self.msg_log.config(state="normal")
        self.msg_log.insert("end", line, tag)
        self.msg_log.see("end")
        self.msg_log.config(state="disabled")

    # ── Nodes tab ────────────────────────────────────────────────────────
    def _build_nodes_tab(self):
        f = self.tab_nodes
        top = tk.Frame(f)
        top.pack(fill="x", padx=8, pady=(8, 0))
        tk.Button(top, text="Refresh", font=FA, command=self._refresh_nodes).pack(side="left")
        self.node_count_var = tk.StringVar(value="0 nodes")
        tk.Label(top, textvariable=self.node_count_var, font=FS, fg=DIM_FG).pack(side="left", padx=10)

        cols = ("short", "long", "id", "hw", "battery", "snr", "heard")
        headers = {"short": "Short", "long": "Long Name", "id": "Node ID", "hw": "Hardware",
                   "battery": "Batt %", "snr": "SNR", "heard": "Last Heard"}
        self.node_tree = ttk.Treeview(f, columns=cols, show="headings", height=18)
        for c in cols:
            self.node_tree.heading(c, text=headers[c])
            self.node_tree.column(c, width=110, anchor="w")
        self.node_tree.pack(fill="both", expand=True, padx=8, pady=8)
        self.node_tree.bind("<Double-1>", self._node_selected_as_dest)

    def _refresh_nodes(self):
        nodes = self.client.get_nodes()
        for row in self.node_tree.get_children():
            self.node_tree.delete(row)
        self.node_lookup = {"Broadcast (all)": BROADCAST_ADDR}
        for node_id, node in nodes.items():
            self._upsert_node_row(node_id, node)
        self.node_count_var.set(f"{len(nodes)} node(s)")
        self.msg_dest["values"] = list(self.node_lookup.keys())

    def _refresh_battery(self):
        status = self.client.get_battery_status()
        if status is None:
            return
        self.info_labels["battery"].config(text=format_battery(status["battery_level"], status["voltage"]))

    def _upsert_node_row(self, node_id: str, node: dict):
        user = node.get("user", {})
        metrics = node.get("deviceMetrics", {})
        last_heard = node.get("lastHeard")
        heard_str = time.strftime("%H:%M:%S", time.localtime(last_heard)) if last_heard else "—"
        values = (
            user.get("shortName", "—"),
            user.get("longName", "—"),
            user.get("id", node_id),
            user.get("hwModel", "—"),
            metrics.get("batteryLevel", "—"),
            node.get("snr", "—"),
            heard_str,
        )
        if self.node_tree.exists(node_id):
            self.node_tree.item(node_id, values=values)
        else:
            self.node_tree.insert("", "end", iid=node_id, values=values)
        display = node_display_name(node)
        self.node_lookup[display] = user.get("id", node_id)

    def _node_selected_as_dest(self, _event):
        sel = self.node_tree.selection()
        if not sel:
            return
        values = self.node_tree.item(sel[0], "values")
        long_name = values[1]
        if long_name in self.node_lookup:
            self.msg_dest.set(long_name)

    # ── Settings tab ─────────────────────────────────────────────────────
    def _build_settings_tab(self):
        f = self.tab_settings
        form = tk.LabelFrame(f, text="Device Parameters", font=FB)
        form.pack(fill="x", padx=8, pady=8)

        tk.Label(form, text="Long Name:", font=FA).grid(row=0, column=0, sticky="w", padx=8, pady=6)
        self.set_long_name = tk.Entry(form, font=FA, width=30)
        self.set_long_name.grid(row=0, column=1, sticky="w", padx=8)

        tk.Label(form, text="Short Name:", font=FA).grid(row=1, column=0, sticky="w", padx=8, pady=6)
        self.set_short_name = tk.Entry(form, font=FA, width=8)
        self.set_short_name.grid(row=1, column=1, sticky="w", padx=8)

        tk.Label(form, text="Bluetooth PIN:", font=FA).grid(row=2, column=0, sticky="w", padx=8, pady=6)
        self.set_pin = tk.Entry(form, font=FA, width=10)
        self.set_pin.grid(row=2, column=1, sticky="w", padx=8)

        tk.Label(form, text="Device Role:", font=FA).grid(row=3, column=0, sticky="w", padx=8, pady=6)
        self.set_role = ttk.Combobox(form, font=FA, width=20, state="readonly", values=ROLE_OPTIONS)
        self.set_role.grid(row=3, column=1, sticky="w", padx=8)

        tk.Label(form, text="LoRa Region:", font=FA).grid(row=4, column=0, sticky="w", padx=8, pady=6)
        self.set_region = ttk.Combobox(form, font=FA, width=20, state="readonly", values=REGION_OPTIONS)
        self.set_region.grid(row=4, column=1, sticky="w", padx=8)

        btns = tk.Frame(f)
        btns.pack(fill="x", padx=8, pady=(0, 8))
        self.btn_save_settings = tk.Button(btns, text="Save Changes", font=FB, bg="#2E7D32", fg="white",
                                             padx=14, command=self._save_settings, state="disabled")
        self.btn_save_settings.pack(side="left")
        self.btn_discard_settings = tk.Button(btns, text="Discard Changes", font=FB,
                                                padx=14, command=self._load_settings_fields, state="disabled")
        self.btn_discard_settings.pack(side="left", padx=8)

        chans = tk.LabelFrame(f, text="Channels", font=FB)
        chans.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        chan_btns = tk.Frame(chans)
        chan_btns.pack(fill="x", padx=8, pady=(8, 4))
        tk.Button(chan_btns, text="Refresh", font=FA, command=self._refresh_channels).pack(side="left")
        self.btn_add_channel = tk.Button(chan_btns, text="Add", font=FA,
                                           command=self._add_channel, state="disabled")
        self.btn_add_channel.pack(side="left", padx=6)
        self.btn_edit_channel = tk.Button(chan_btns, text="Edit", font=FA,
                                            command=self._edit_channel, state="disabled")
        self.btn_edit_channel.pack(side="left", padx=6)
        self.btn_delete_channel = tk.Button(chan_btns, text="Delete", font=FA, bg="#B71C1C", fg="white",
                                              command=self._delete_channel, state="disabled")
        self.btn_delete_channel.pack(side="left", padx=6)

        cols = ("index", "role", "name", "encryption", "uplink", "downlink", "precision", "muted")
        headers = {"index": "#", "role": "Role", "name": "Name", "encryption": "Encryption",
                   "uplink": "Uplink", "downlink": "Downlink", "precision": "Precision", "muted": "Muted"}
        self.channel_tree = ttk.Treeview(chans, columns=cols, show="headings", height=8)
        for c in cols:
            self.channel_tree.heading(c, text=headers[c])
            self.channel_tree.column(c, width=130 if c == "name" else 80, anchor="w")
        self.channel_tree.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.channel_tree.bind("<<TreeviewSelect>>", self._on_channel_select)

        danger = tk.LabelFrame(f, text="Factory Reset", font=FB, fg=ERR_RED)
        danger.pack(fill="x", padx=8, pady=8)
        tk.Label(danger, text="Wipes device config and NodeDB back to defaults. Firmware is not touched.",
                  font=FS, fg=DIM_FG).pack(anchor="w", padx=8, pady=(6, 0))
        self.full_reset_var = tk.BooleanVar(value=False)
        tk.Checkbutton(danger, text="Full reset (also clears security keys)", font=FS,
                        variable=self.full_reset_var).pack(anchor="w", padx=8)
        tk.Button(danger, text="Factory Reset Device", font=FB, bg="#B71C1C", fg="white",
                   padx=14, command=self._factory_reset).pack(anchor="w", padx=8, pady=8)

    def _load_settings_fields(self):
        if not self.client.is_connected():
            return
        try:
            s = self.client.get_device_settings()
        except Exception as exc:
            messagebox.showerror("Settings", f"Could not read device settings: {exc}")
            return
        self.set_long_name.delete(0, "end"); self.set_long_name.insert(0, s["long_name"])
        self.set_short_name.delete(0, "end"); self.set_short_name.insert(0, s["short_name"])
        self.set_pin.delete(0, "end"); self.set_pin.insert(0, str(s["fixed_pin"]))
        self.set_role.set(s["role"])
        self.set_region.set(s["region"])
        self.btn_save_settings.config(state="normal")
        self.btn_discard_settings.config(state="normal")

    def _save_settings(self):
        if not self.client.is_connected():
            return
        try:
            fixed_pin = int(self.set_pin.get().strip())
        except ValueError:
            messagebox.showwarning("Invalid PIN", "Bluetooth PIN must be numeric.")
            return
        role = self.set_role.get()
        region = self.set_region.get()
        if not role or not region:
            messagebox.showwarning("Missing Selection", "Choose a Device Role and LoRa Region.")
            return
        if not messagebox.askyesno("Confirm Save",
                                    "Write these settings to the device? It may reboot to apply them."):
            return
        self.btn_save_settings.config(state="disabled")
        self.status_var.set("Saving settings…")
        self.client.save_device_settings(
            long_name=self.set_long_name.get().strip(),
            short_name=self.set_short_name.get().strip(),
            fixed_pin=fixed_pin,
            role=role,
            region=region,
        )

    def _factory_reset(self):
        full = self.full_reset_var.get()
        warning = ("This erases ALL device configuration and the NodeDB" +
                   (" and security keys" if full else "") +
                   ". This cannot be undone. Continue?")
        if not messagebox.askyesno("Confirm Factory Reset", warning, icon="warning"):
            return
        self.client.factory_reset(full=full)
        self.status_var.set("Factory reset requested…")

    # ── Channels (within the Settings tab) ──────────────────────────────
    def _refresh_channels(self):
        if not self.client.is_connected():
            return
        try:
            channels = self.client.get_channels()
        except Exception as exc:
            messagebox.showerror("Channels", f"Could not read channels: {exc}")
            return
        self._channels_cache = {c["index"]: c for c in channels}
        for row in self.channel_tree.get_children():
            self.channel_tree.delete(row)
        for c in channels:
            self.channel_tree.insert("", "end", iid=str(c["index"]), values=(
                c["index"], c["role"], c["name"] or "—", describe_psk(c["psk"]),
                "Yes" if c["uplink_enabled"] else "No",
                "Yes" if c["downlink_enabled"] else "No",
                c["position_precision"], "Yes" if c["is_muted"] else "No",
            ))
        self.btn_add_channel.config(state="normal")
        self._on_channel_select()

    def _on_channel_select(self, _event=None):
        sel = self.channel_tree.selection()
        if not sel:
            self.btn_edit_channel.config(state="disabled")
            self.btn_delete_channel.config(state="disabled")
            return
        role = self._channels_cache.get(int(sel[0]), {}).get("role")
        self.btn_edit_channel.config(state="normal" if role in ("PRIMARY", "SECONDARY") else "disabled")
        self.btn_delete_channel.config(state="normal" if role == "SECONDARY" else "disabled")

    def _add_channel(self):
        free = next((c["index"] for c in self._channels_cache.values() if c["role"] == "DISABLED"), None)
        if free is None:
            messagebox.showwarning("Channels", "All 8 channel slots are already in use.")
            return
        ChannelDialog(self, free, None, lambda data: self._do_save_channel(free, data, role="SECONDARY"))

    def _edit_channel(self):
        sel = self.channel_tree.selection()
        if not sel:
            return
        index = int(sel[0])
        existing = self._channels_cache.get(index)
        if not existing:
            return
        ChannelDialog(self, index, existing, lambda data: self._do_save_channel(index, data, role=None))

    def _do_save_channel(self, index: int, data: dict, role):
        if not messagebox.askyesno("Confirm Save",
                                    "Write this channel to the device? It may reboot to apply it."):
            return
        self.client.save_channel(
            index, data["name"], data["psk"], data["uplink_enabled"], data["downlink_enabled"],
            data["position_precision"], data["is_muted"], role=role,
        )
        self.status_var.set("Saving channel…")

    def _delete_channel(self):
        sel = self.channel_tree.selection()
        if not sel:
            return
        index = int(sel[0])
        name = self._channels_cache.get(index, {}).get("name") or f"slot {index}"
        if not messagebox.askyesno("Confirm Delete", f"Delete channel '{name}'? This cannot be undone.",
                                    icon="warning"):
            return
        try:
            self.client.delete_channel(index)
        except Exception as exc:
            messagebox.showerror("Delete Failed", str(exc))
            return
        self.status_var.set("Deleting channel…")

    # ── Firmware tab ─────────────────────────────────────────────────────
    def _build_firmware_tab(self):
        f = self.tab_firmware
        info = tk.LabelFrame(f, text="Current Device", font=FB)
        info.pack(fill="x", padx=8, pady=8)
        self.fw_current_var = tk.StringVar(value="Not connected")
        tk.Label(info, textvariable=self.fw_current_var, font=FA, justify="left").pack(anchor="w", padx=8, pady=6)

        manual = tk.LabelFrame(f, text="Board (for boards that can't stay connected in bootloader mode)", font=FB)
        manual.pack(fill="x", padx=8, pady=(0, 8))
        tk.Label(manual,
                  text="Some boards (e.g. SenseCAP T1000-E) drop the connection entirely once they're in\n"
                       "bootloader/DFU mode. Pick the board here to check for and download firmware\n"
                       "without needing to be connected.",
                  font=FS, fg=DIM_FG, justify="left").pack(anchor="w", padx=8, pady=(6, 0))
        manual_row = tk.Frame(manual)
        manual_row.pack(fill="x", padx=8, pady=8)
        tk.Label(manual_row, text="Board:", font=FA).pack(side="left")
        self.fw_board_manual = ttk.Combobox(manual_row, font=FA, width=40, state="readonly")
        self.fw_board_manual.pack(side="left", padx=6)
        self.fw_board_manual.bind("<<ComboboxSelected>>", lambda e: self.btn_check_updates.config(state="normal"))
        tk.Button(manual_row, text="Load Board List", font=FA, command=self._load_board_list).pack(side="left")

        check = tk.Frame(f)
        check.pack(fill="x", padx=8)
        self.btn_check_updates = tk.Button(check, text="Check for Updates", font=FB,
                                             command=self._check_for_updates, state="disabled")
        self.btn_check_updates.pack(side="left")
        self.fw_latest_var = tk.StringVar(value="")
        tk.Label(check, textvariable=self.fw_latest_var, font=FA, fg=DIM_FG).pack(side="left", padx=10)

        dl = tk.Frame(f)
        dl.pack(fill="x", padx=8, pady=8)
        self.btn_download_fw = tk.Button(dl, text="Download Firmware", font=FB,
                                           command=self._download_firmware, state="disabled")
        self.btn_download_fw.pack(side="left")
        self.fw_download_var = tk.StringVar(value="")
        tk.Label(dl, textvariable=self.fw_download_var, font=FA, fg=DIM_FG).pack(side="left", padx=10)

        # -- UF2 flashing (nRF52 / RP2040 / RP2350) --
        self.uf2_frame = tk.LabelFrame(f, text="Flash via UF2 Bootloader", font=FB)
        tk.Label(self.uf2_frame,
                  text="Double-tap the device's reset button to enter bootloader mode\n"
                       "(it will appear as a USB drive), then detect and flash it.",
                  font=FS, fg=DIM_FG, justify="left").pack(anchor="w", padx=8, pady=(6, 0))
        row = tk.Frame(self.uf2_frame)
        row.pack(fill="x", padx=8, pady=8)
        tk.Button(row, text="Detect Bootloader Drive", font=FA, command=self._detect_uf2_drive).pack(side="left")
        self.uf2_drive = ttk.Combobox(row, font=FA, width=10, state="readonly")
        self.uf2_drive.pack(side="left", padx=8)
        self.btn_flash_uf2 = tk.Button(row, text="Flash", font=FB, bg="#2E7D32", fg="white",
                                         command=self._flash_uf2, state="disabled")
        self.btn_flash_uf2.pack(side="left")

        # -- esptool flashing (ESP32 family) --
        self.esp_frame = tk.LabelFrame(f, text="Flash via esptool (ESP32)", font=FB)
        tk.Label(self.esp_frame, text="Device must be connected over USB serial.",
                  font=FS, fg=DIM_FG, justify="left").pack(anchor="w", padx=8, pady=(6, 0))
        row2 = tk.Frame(self.esp_frame)
        row2.pack(fill="x", padx=8, pady=8)
        self.btn_flash_esp = tk.Button(row2, text="Erase + Flash", font=FB, bg="#2E7D32", fg="white",
                                         command=self._flash_esptool, state="disabled")
        self.btn_flash_esp.pack(side="left")
        tk.Button(row2, text="Erase Flash Only", font=FA, bg="#B71C1C", fg="white",
                   command=self._erase_esptool).pack(side="left", padx=8)

        self.fw_status_var = tk.StringVar(value="")
        tk.Label(f, textvariable=self.fw_status_var, font=FA, fg=DIM_FG, wraplength=900,
                  justify="left").pack(anchor="w", padx=8, pady=8)

    def _refresh_firmware_tab_for_platform(self):
        for w in (self.uf2_frame, self.esp_frame):
            w.pack_forget()
        if self.fw_platform in fw.UF2_PLATFORMS:
            self.uf2_frame.pack(fill="x", padx=8, pady=8)
        elif self.fw_platform in fw.ESPTOOL_PLATFORMS:
            self.esp_frame.pack(fill="x", padx=8, pady=8)

    def _current_board_id(self):
        """The board id to check/download firmware for: the connected device's
        pio_env if one is connected, otherwise whatever was picked manually
        (needed for boards like the SenseCAP T1000-E, which can't stay
        connected once they're in bootloader/DFU mode)."""
        if self.connected_info and self.connected_info.get("pio_env"):
            return self.connected_info["pio_env"]
        return self.fw_board_manual.get() or None

    def _load_board_list(self):
        self.fw_status_var.set("Loading board list…")

        def worker():
            try:
                release = fw.get_latest_release()
                boards = sorted({t["board"] for t in release["targets"] if t.get("board")})
                self.fw_queue.put(("fw_board_list", boards))
            except Exception as exc:
                self.fw_queue.put(("fw_error", f"Could not load board list: {exc}"))

        threading.Thread(target=worker, daemon=True, name="fw-board-list").start()

    def _check_for_updates(self):
        pio_env = self._current_board_id()
        if not pio_env:
            messagebox.showwarning("Firmware", "Select a board first — connect to the device, "
                                                "or pick one manually from the list above.")
            return
        self.btn_check_updates.config(state="disabled")
        self.fw_latest_var.set("Checking…")

        def worker():
            try:
                release = fw.get_latest_release()
                platform = fw.find_platform_for_board(release, pio_env)
                if platform is None:
                    self.fw_queue.put(("fw_error", f"No firmware target found for board '{pio_env}'."))
                    return
                self.fw_queue.put(("fw_release", (release, platform)))
            except Exception as exc:
                self.fw_queue.put(("fw_error", f"Update check failed: {exc}"))

        threading.Thread(target=worker, daemon=True, name="fw-check").start()

    def _download_firmware(self):
        if self.latest_release is None or self.fw_platform is None:
            return
        pio_env = self._current_board_id()
        release = self.latest_release
        zip_name = fw.platform_zip_asset(release, self.fw_platform)
        if zip_name is None:
            messagebox.showerror("Firmware", f"No firmware archive found for platform '{self.fw_platform}'.")
            return
        zip_url = release["assets"][zip_name]
        self.btn_download_fw.config(state="disabled")
        self.fw_download_var.set("Downloading…")

        def worker():
            try:
                path = fw.extract_firmware_file(zip_url, pio_env, release["version"], FIRMWARE_CACHE_DIR, self.fw_platform)
                self.fw_queue.put(("fw_downloaded", path))
            except Exception as exc:
                self.fw_queue.put(("fw_error", f"Download failed: {exc}"))

        threading.Thread(target=worker, daemon=True, name="fw-download").start()

    def _detect_uf2_drive(self):
        try:
            drives = fw.find_uf2_drives()
        except Exception as exc:
            messagebox.showerror("Drive Detection Failed", str(exc))
            self.fw_status_var.set(f"Drive detection error: {exc}")
            return

        if drives:
            self.uf2_drive["values"] = drives
            self.uf2_drive.current(0)
            self.btn_flash_uf2.config(state="normal")
            self.fw_status_var.set(f"Found bootloader drive: {drives[0]}")
            return

        # No drive has the INFO_UF2.TXT marker yet — fall back to listing every
        # removable drive so the user can pick the right one manually (some
        # bootloaders are slow to write the marker, or omit it).
        try:
            candidates = fw.list_removable_drives()
        except Exception as exc:
            messagebox.showerror("Drive Detection Failed", str(exc))
            self.fw_status_var.set(f"Drive detection error: {exc}")
            return

        if candidates:
            self.uf2_drive["values"] = [f"{root}  ({label})" for root, label in candidates]
            self.uf2_drive.current(0)
            self.btn_flash_uf2.config(state="normal")
            self.fw_status_var.set(
                "No confirmed UF2 bootloader drive found, but removable drives are listed below — "
                "verify it's the right one (check its label/contents in File Explorer) before flashing."
            )
        else:
            self.btn_flash_uf2.config(state="disabled")
            self.fw_status_var.set(
                "No bootloader drive or removable drive found at all. Double-tap reset on the "
                "device to enter bootloader mode, confirm it shows up in File Explorer, then try again."
            )

    def _flash_uf2(self):
        sel = self.uf2_drive.get()
        drive = sel.split("  ")[0] if sel else None
        if not drive or not self.fw_path:
            return
        if not messagebox.askyesno("Confirm Flash", f"Copy {os.path.basename(self.fw_path)} to {drive}?"):
            return
        try:
            dest = fw.flash_uf2(self.fw_path, drive)
        except Exception as exc:
            messagebox.showerror("Flash Failed", str(exc))
            return
        self.fw_status_var.set(f"Flashed: copied to {dest}. Device will reboot automatically.")
        messagebox.showinfo("Flash Complete", "Firmware copied. The device will reboot on its own.")

    def _flash_esptool(self):
        self._run_esptool_action(erase_first=True)

    def _erase_esptool(self):
        self._run_esptool_action(erase_first=True, write=False)

    def _run_esptool_action(self, erase_first: bool, write: bool = True):
        if not fw.esptool_available():
            messagebox.showerror(
                "esptool Not Installed",
                "Install it yourself first, then retry:\n\n    pip install esptool"
            )
            return
        if write and not self.fw_path:
            messagebox.showwarning("Firmware", "Download a firmware file first.")
            return
        port = self._current_serial_port()
        if not port:
            messagebox.showwarning("Firmware", "Select the device's serial port on the Connection tab first.")
            return
        action = "erase and flash" if write else "erase"
        if not messagebox.askyesno("Confirm", f"This will {action} the device over {port}. Continue?", icon="warning"):
            return
        if self.client.is_connected():
            messagebox.showwarning("Disconnect First", "Disconnect from the device before flashing.")
            return

        self.fw_status_var.set(f"Running esptool ({action})…")

        def worker():
            try:
                if erase_first:
                    fw.esptool_erase_flash(port)
                if write:
                    fw.esptool_write_flash(port, self.fw_path)
                self.fw_queue.put(("fw_flashed", port))
            except fw.FirmwareError as exc:
                self.fw_queue.put(("fw_error", f"esptool failed:\n{exc}"))
            except Exception as exc:
                self.fw_queue.put(("fw_error", f"esptool failed: {exc}"))

        threading.Thread(target=worker, daemon=True, name="fw-esptool").start()

    def _current_serial_port(self):
        sel = self.serial_port.get()
        return sel.split("  ")[0] if sel else None

    # ── MQTT tab ─────────────────────────────────────────────────────────
    def _build_mqtt_tab(self):
        f = self.tab_mqtt

        top = tk.LabelFrame(f, text="MQTT Module", font=FB)
        top.pack(fill="x", padx=8, pady=8)
        self.mqtt_enabled = tk.BooleanVar(value=False)
        tk.Checkbutton(top, text="Enabled", font=FB, variable=self.mqtt_enabled).grid(
            row=0, column=0, sticky="w", padx=8, pady=6)

        conn = tk.LabelFrame(f, text="Server", font=FB)
        conn.pack(fill="x", padx=8, pady=(0, 8))

        tk.Label(conn, text="Address:", font=FA).grid(row=0, column=0, sticky="w", padx=8, pady=6)
        self.mqtt_address = tk.Entry(conn, font=FA, width=32)
        self.mqtt_address.grid(row=0, column=1, sticky="w", padx=8)

        tk.Label(conn, text="Username:", font=FA).grid(row=1, column=0, sticky="w", padx=8, pady=6)
        self.mqtt_username = tk.Entry(conn, font=FA, width=32)
        self.mqtt_username.grid(row=1, column=1, sticky="w", padx=8)

        tk.Label(conn, text="Password:", font=FA).grid(row=2, column=0, sticky="w", padx=8, pady=6)
        self.mqtt_password = tk.Entry(conn, font=FA, width=32, show="*")
        self.mqtt_password.grid(row=2, column=1, sticky="w", padx=8)
        self.mqtt_show_password = tk.BooleanVar(value=False)
        tk.Checkbutton(conn, text="Show", font=FS, variable=self.mqtt_show_password,
                        command=self._toggle_mqtt_password_visibility).grid(row=2, column=2, sticky="w")

        tk.Label(conn, text="Root Topic:", font=FA).grid(row=3, column=0, sticky="w", padx=8, pady=6)
        self.mqtt_root = tk.Entry(conn, font=FA, width=32)
        self.mqtt_root.grid(row=3, column=1, sticky="w", padx=8)

        opts = tk.LabelFrame(f, text="Options", font=FB)
        opts.pack(fill="x", padx=8, pady=(0, 8))
        self.mqtt_encryption = tk.BooleanVar(value=False)
        self.mqtt_json = tk.BooleanVar(value=False)
        self.mqtt_tls = tk.BooleanVar(value=False)
        self.mqtt_proxy = tk.BooleanVar(value=False)
        tk.Checkbutton(opts, text="Encryption Enabled", font=FA, variable=self.mqtt_encryption).grid(
            row=0, column=0, sticky="w", padx=8, pady=4)
        tk.Checkbutton(opts, text="JSON Enabled", font=FA, variable=self.mqtt_json).grid(
            row=0, column=1, sticky="w", padx=8, pady=4)
        tk.Checkbutton(opts, text="TLS Enabled", font=FA, variable=self.mqtt_tls).grid(
            row=1, column=0, sticky="w", padx=8, pady=4)
        tk.Checkbutton(opts, text="Proxy to Client Enabled", font=FA, variable=self.mqtt_proxy).grid(
            row=1, column=1, sticky="w", padx=8, pady=4)

        mapf = tk.LabelFrame(f, text="Map Reporting", font=FB)
        mapf.pack(fill="x", padx=8, pady=(0, 8))
        self.mqtt_map_enabled = tk.BooleanVar(value=False)
        tk.Checkbutton(mapf, text="Map Reporting Enabled", font=FA, variable=self.mqtt_map_enabled).grid(
            row=0, column=0, columnspan=2, sticky="w", padx=8, pady=4)
        self.mqtt_map_report_location = tk.BooleanVar(value=False)
        tk.Checkbutton(mapf, text="Should Report Location", font=FA,
                        variable=self.mqtt_map_report_location).grid(
            row=1, column=0, columnspan=2, sticky="w", padx=8, pady=4)
        tk.Label(mapf, text="Publish Interval (secs):", font=FA).grid(
            row=2, column=0, sticky="w", padx=8, pady=4)
        self.mqtt_map_interval = tk.Entry(mapf, font=FA, width=10)
        self.mqtt_map_interval.grid(row=2, column=1, sticky="w", padx=8)
        tk.Label(mapf, text="Position Precision (bits):", font=FA).grid(
            row=3, column=0, sticky="w", padx=8, pady=4)
        self.mqtt_map_precision = tk.Entry(mapf, font=FA, width=10)
        self.mqtt_map_precision.grid(row=3, column=1, sticky="w", padx=8)

        btns = tk.Frame(f)
        btns.pack(fill="x", padx=8, pady=(0, 8))
        self.btn_save_mqtt = tk.Button(btns, text="Save Changes", font=FB, bg="#2E7D32", fg="white",
                                         padx=14, command=self._save_mqtt_settings, state="disabled")
        self.btn_save_mqtt.pack(side="left")
        self.btn_discard_mqtt = tk.Button(btns, text="Discard Changes", font=FB,
                                            padx=14, command=self._load_mqtt_fields, state="disabled")
        self.btn_discard_mqtt.pack(side="left", padx=8)

    def _toggle_mqtt_password_visibility(self):
        self.mqtt_password.config(show="" if self.mqtt_show_password.get() else "*")

    def _load_mqtt_fields(self):
        if not self.client.is_connected():
            return
        try:
            s = self.client.get_mqtt_settings()
        except Exception as exc:
            messagebox.showerror("MQTT", f"Could not read MQTT settings: {exc}")
            return
        self.mqtt_enabled.set(s["enabled"])
        self.mqtt_address.delete(0, "end"); self.mqtt_address.insert(0, s["address"])
        self.mqtt_username.delete(0, "end"); self.mqtt_username.insert(0, s["username"])
        self.mqtt_password.delete(0, "end"); self.mqtt_password.insert(0, s["password"])
        self.mqtt_root.delete(0, "end"); self.mqtt_root.insert(0, s["root"])
        self.mqtt_encryption.set(s["encryption_enabled"])
        self.mqtt_json.set(s["json_enabled"])
        self.mqtt_tls.set(s["tls_enabled"])
        self.mqtt_proxy.set(s["proxy_to_client_enabled"])
        self.mqtt_map_enabled.set(s["map_reporting_enabled"])
        self.mqtt_map_report_location.set(s["map_should_report_location"])
        self.mqtt_map_interval.delete(0, "end"); self.mqtt_map_interval.insert(0, str(s["map_publish_interval_secs"]))
        self.mqtt_map_precision.delete(0, "end"); self.mqtt_map_precision.insert(0, str(s["map_position_precision"]))
        self.btn_save_mqtt.config(state="normal")
        self.btn_discard_mqtt.config(state="normal")

    def _save_mqtt_settings(self):
        if not self.client.is_connected():
            return
        try:
            interval = int(self.mqtt_map_interval.get().strip() or 0)
            precision = int(self.mqtt_map_precision.get().strip() or 0)
        except ValueError:
            messagebox.showwarning("Invalid Value", "Publish Interval and Position Precision must be numeric.")
            return
        if not messagebox.askyesno("Confirm Save",
                                    "Write these MQTT settings to the device? It may reboot to apply them."):
            return
        settings = {
            "enabled": self.mqtt_enabled.get(),
            "address": self.mqtt_address.get().strip(),
            "username": self.mqtt_username.get().strip(),
            "password": self.mqtt_password.get(),
            "encryption_enabled": self.mqtt_encryption.get(),
            "json_enabled": self.mqtt_json.get(),
            "tls_enabled": self.mqtt_tls.get(),
            "root": self.mqtt_root.get().strip(),
            "proxy_to_client_enabled": self.mqtt_proxy.get(),
            "map_reporting_enabled": self.mqtt_map_enabled.get(),
            "map_should_report_location": self.mqtt_map_report_location.get(),
            "map_publish_interval_secs": interval,
            "map_position_precision": precision,
        }
        self.btn_save_mqtt.config(state="disabled")
        self.status_var.set("Saving MQTT settings…")
        self.client.save_mqtt_settings(settings)

    # ── Report tab ───────────────────────────────────────────────────────
    def _build_report_tab(self):
        f = self.tab_report

        top = tk.Frame(f)
        top.pack(fill="x", padx=8, pady=8)
        tk.Button(top, text="Refresh", font=FA, command=self._refresh_report).pack(side="left")
        tk.Button(top, text="Print…", font=FB, bg="#2E7D32", fg="white", padx=14,
                   command=self._print_report).pack(side="left", padx=8)
        tk.Button(top, text="Save as CSV…", font=FB, padx=14,
                   command=self._export_report_csv).pack(side="left", padx=(0, 16))

        tk.Label(top, text="Printer:", font=FA).pack(side="left")
        self.printer_choice = ttk.Combobox(top, font=FA, width=28, state="readonly")
        self.printer_choice.pack(side="left", padx=6)
        self.btn_refresh_printers = tk.Button(top, text="Refresh Printers", font=FA,
                                                command=self._refresh_printers)
        self.btn_refresh_printers.pack(side="left")
        self._refresh_printers()

        self.report_text = tk.Text(f, font=FMONO, state="disabled", wrap="none")
        self.report_text.pack(fill="both", expand=True, padx=8, pady=(0, 8))

    def _build_report_rows(self):
        """[(section, field, value), ...] pulled live from the Connection,
        Settings, and MQTT tabs — the single source for both the on-screen
        preview and the Print/CSV exports, so they can never drift apart."""
        rows = []
        info = self.connected_info or {}

        rows.append(("Connection", "Status", "Connected" if self.client.is_connected() else "Disconnected"))
        rows.append(("Connection", "Long Name", info.get("long_name", "—")))
        rows.append(("Connection", "Short Name", info.get("short_name", "—")))
        rows.append(("Connection", "Node ID", info.get("node_id", "—")))
        rows.append(("Connection", "Hardware", info.get("hw_model", "—")))
        rows.append(("Connection", "Firmware", info.get("firmware", "—")))
        rows.append(("Connection", "Board", info.get("pio_env", "—")))
        rows.append(("Connection", "Battery", self.info_labels["battery"].cget("text")))

        rows.append(("Settings", "Long Name", self.set_long_name.get()))
        rows.append(("Settings", "Short Name", self.set_short_name.get()))
        rows.append(("Settings", "Bluetooth PIN", self.set_pin.get()))
        rows.append(("Settings", "Device Role", self.set_role.get()))
        rows.append(("Settings", "LoRa Region", self.set_region.get()))
        for idx, ch in sorted(self._channels_cache.items()):
            prefix = f"Channel {idx}"
            rows.append(("Settings", f"{prefix} Role", ch["role"]))
            rows.append(("Settings", f"{prefix} Name", ch["name"] or "—"))
            rows.append(("Settings", f"{prefix} Encryption", describe_psk(ch["psk"])))
            rows.append(("Settings", f"{prefix} Uplink", "Yes" if ch["uplink_enabled"] else "No"))
            rows.append(("Settings", f"{prefix} Downlink", "Yes" if ch["downlink_enabled"] else "No"))
            rows.append(("Settings", f"{prefix} Position Precision", ch["position_precision"]))
            rows.append(("Settings", f"{prefix} Muted", "Yes" if ch["is_muted"] else "No"))

        rows.append(("MQTT", "Enabled", "Yes" if self.mqtt_enabled.get() else "No"))
        rows.append(("MQTT", "Address", self.mqtt_address.get()))
        rows.append(("MQTT", "Username", self.mqtt_username.get()))
        # Redacted by default — this report may end up printed or saved to a shared
        # file, and the plaintext credential isn't needed to see the config shape.
        rows.append(("MQTT", "Password", "•••••• (set)" if self.mqtt_password.get() else "—"))
        rows.append(("MQTT", "Root Topic", self.mqtt_root.get()))
        rows.append(("MQTT", "Encryption Enabled", "Yes" if self.mqtt_encryption.get() else "No"))
        rows.append(("MQTT", "JSON Enabled", "Yes" if self.mqtt_json.get() else "No"))
        rows.append(("MQTT", "TLS Enabled", "Yes" if self.mqtt_tls.get() else "No"))
        rows.append(("MQTT", "Proxy to Client Enabled", "Yes" if self.mqtt_proxy.get() else "No"))
        rows.append(("MQTT", "Map Reporting Enabled", "Yes" if self.mqtt_map_enabled.get() else "No"))
        rows.append(("MQTT", "Map Should Report Location", "Yes" if self.mqtt_map_report_location.get() else "No"))
        rows.append(("MQTT", "Map Publish Interval (secs)", self.mqtt_map_interval.get()))
        rows.append(("MQTT", "Map Position Precision", self.mqtt_map_precision.get()))
        return rows

    def _report_as_text(self, rows) -> str:
        lines = [f"Meshtastic Device Report — {datetime.now():%Y-%m-%d %H:%M:%S}", ""]
        section = None
        for sect, field, value in rows:
            if sect != section:
                section = sect
                lines.append(f"[{section}]")
            lines.append(f"  {field}: {value}")
        lines.append("")
        return "\n".join(lines)

    def _refresh_printers(self):
        # Listing printers shells out to PowerShell, which can take upward of
        # 15+ seconds to spin up on a loaded machine — never run that on the
        # GUI thread, or the whole app freezes for that long on every refresh
        # (including the first one, at startup).
        self.btn_refresh_printers.config(state="disabled")
        self.printer_choice.set("Loading…")

        def worker():
            try:
                names = printing.list_printers()
                default = printing.get_default_printer()
                self.printer_queue.put(("printers_loaded", (names, default)))
            except Exception as exc:
                self.printer_queue.put(("printers_error", str(exc)))

        threading.Thread(target=worker, daemon=True, name="printer-list").start()

    def _handle_printer_event(self, kind: str, payload):
        self.btn_refresh_printers.config(state="normal")
        if kind == "printers_loaded":
            names, default = payload
            self.printer_choice["values"] = [printing.SYSTEM_DEFAULT] + names
            if default and default in names:
                self.printer_choice.set(default)
            else:
                self.printer_choice.current(0)
        elif kind == "printers_error":
            self.printer_choice["values"] = [printing.SYSTEM_DEFAULT]
            self.printer_choice.current(0)
            self._append_log(f"Could not list printers: {payload}")

    def _refresh_report(self):
        text = self._report_as_text(self._build_report_rows())
        self.report_text.config(state="normal")
        self.report_text.delete("1.0", "end")
        self.report_text.insert("1.0", text)
        self.report_text.config(state="disabled")

    def _print_report(self):
        self._refresh_report()
        text = self.report_text.get("1.0", "end")
        printer = self.printer_choice.get()
        try:
            os.makedirs(REPORT_CACHE_DIR, exist_ok=True)
            path = os.path.join(REPORT_CACHE_DIR, f"report_{datetime.now():%Y%m%d_%H%M%S}.txt")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
            if sys.platform == "win32":
                printing.print_file(path, printer)
                label = printer if printer and printer != printing.SYSTEM_DEFAULT else "default printer"
                self.status_var.set(f"Sent to {label}: {path}")
            else:
                messagebox.showinfo(
                    "Printing Not Supported",
                    f"Direct printing is only wired up for Windows. The report was saved to:\n{path}"
                )
        except OSError as exc:
            messagebox.showerror(
                "Print Failed",
                f"Could not print to '{printer}':\n{exc}"
            )

    def _export_report_csv(self):
        self._refresh_report()
        default_name = f"meshtastic_report_{datetime.now():%Y%m%d_%H%M%S}.csv"
        path = filedialog.asksaveasfilename(
            title="Save Report As", initialdir=BASE, initialfile=default_name,
            defaultextension=".csv", filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8") as fh:
                writer = csv.writer(fh)
                writer.writerow(["Section", "Field", "Value"])
                writer.writerows(self._build_report_rows())
        except OSError as exc:
            messagebox.showerror("Save Failed", str(exc))
            return
        self.status_var.set(f"Report saved: {path}")
        messagebox.showinfo("Saved", f"Report saved to:\n{path}")

    # ── Log tab ──────────────────────────────────────────────────────────
    MAX_LOG_LINES = 1000

    def _build_log_tab(self):
        f = self.tab_log
        self.log_text = tk.Text(f, font=FMONO, state="disabled", wrap="word", bg="#111111", fg="#DDDDDD")
        self.log_text.pack(fill="both", expand=True, padx=8, pady=8)
        self._last_log_line = None
        self._last_log_repeats = 0

    def _append_log(self, line: str):
        # Firmware can emit the same warning hundreds of times per second (e.g. radio-busy
        # chatter). Collapse consecutive repeats instead of inserting each one into the
        # Text widget, or the GUI grinds to a halt processing its own log tab.
        line = strip_ansi(line).strip()
        if line == self._last_log_line:
            self._last_log_repeats += 1
            self.log_text.config(state="normal")
            self.log_text.delete("end-2l", "end-1l")
            self.log_text.insert("end", f"[{ts()}] {line}  (x{self._last_log_repeats})\n")
            self.log_text.config(state="disabled")
            return

        self._last_log_line = line
        self._last_log_repeats = 1
        self.log_text.config(state="normal")
        self.log_text.insert("end", f"[{ts()}] {line}\n")
        num_lines = int(self.log_text.index("end-1c").split(".")[0])
        if num_lines > self.MAX_LOG_LINES:
            self.log_text.delete("1.0", f"{num_lines - self.MAX_LOG_LINES}.0")
        self.log_text.see("end")
        self.log_text.config(state="disabled")

    # ── event pump ───────────────────────────────────────────────────────
    MAX_EVENTS_PER_TICK = 200

    def _poll_events(self):
        # Checked before *every* dequeue, not just on entry: a messagebox popup
        # inside _handle_event() pumps a nested Tk event loop, so a close
        # request can be processed mid-loop — without this, the loop would
        # resume afterward and touch widgets destroy() already tore down.
        if self._closing:
            return
        drained = 0
        for kind, payload in self.client.poll():
            self._handle_event(kind, payload)
            drained += 1
            if self._closing or drained >= self.MAX_EVENTS_PER_TICK:
                break
        if self._closing:
            return
        while True:
            try:
                kind, payload = self.fw_queue.get_nowait()
            except queue.Empty:
                break
            self._handle_fw_event(kind, payload)
            if self._closing:
                return
        while True:
            try:
                kind, payload = self.ble_queue.get_nowait()
            except queue.Empty:
                break
            self._handle_ble_event(kind, payload)
            if self._closing:
                return
        while True:
            try:
                kind, payload = self.printer_queue.get_nowait()
            except queue.Empty:
                break
            self._handle_printer_event(kind, payload)
            if self._closing:
                return
        # If the queue is still backed up, come back quickly instead of waiting a full
        # tick — keeps the UI responsive while draining a burst without starving it.
        delay = 10 if drained >= self.MAX_EVENTS_PER_TICK else POLL_MS
        self.after(delay, self._poll_events)

    def _handle_fw_event(self, kind: str, payload):
        if kind == "fw_release":
            release, platform = payload
            self.latest_release = release
            self.fw_platform = platform
            self.btn_check_updates.config(state="normal")
            current = (self.connected_info or {}).get("firmware", "?")
            latest = release["version"].lstrip("v")
            newer = latest not in current
            self.fw_latest_var.set(f"Latest: {latest}  ({'update available' if newer else 'up to date'})")
            self.btn_download_fw.config(state="normal")
            self._refresh_firmware_tab_for_platform()
            self._append_log(f"Firmware check: latest={latest}, platform={platform}")

        elif kind == "fw_downloaded":
            self.fw_path = payload
            self.btn_download_fw.config(state="normal")
            self.fw_download_var.set(f"Ready: {os.path.basename(payload)}")
            if self.fw_platform in fw.ESPTOOL_PLATFORMS:
                self.btn_flash_esp.config(state="normal")
            self._append_log(f"Firmware downloaded: {payload}")

        elif kind == "fw_flashed":
            self.fw_status_var.set(f"Flash completed via {payload}.")
            messagebox.showinfo("Flash Complete", "esptool finished successfully.")

        elif kind == "fw_board_list":
            self.fw_board_manual["values"] = payload
            self.fw_status_var.set(f"Loaded {len(payload)} board(s). Select one, then Check for Updates.")

        elif kind == "fw_error":
            self.btn_check_updates.config(state="normal" if self._current_board_id() else "disabled")
            self.btn_download_fw.config(state="normal" if self.latest_release else "disabled")
            self.fw_status_var.set(str(payload))
            self._append_log(f"Firmware error: {payload}")
            messagebox.showerror("Firmware", str(payload))

    def _handle_event(self, kind: str, payload):
        if kind == "connected":
            self.connected_info = payload
            self.hdr_status.config(text=f"Connected — {payload['long_name']}", fg="#A5FFA5")
            self.status_var.set("Connected")
            self.btn_connect.config(state="disabled")
            self.btn_disconnect.config(state="normal")
            for key, lbl in self.info_labels.items():
                lbl.config(text=str(payload.get(key, "—")))
            self._append_log(f"Connected: {payload}")
            self.after(500, self._refresh_nodes)
            self.after(500, self._load_settings_fields)
            self.after(500, self._refresh_battery)
            self.after(500, self._load_mqtt_fields)
            self.after(500, self._refresh_channels)
            self.fw_current_var.set(
                f"{payload['long_name']}  |  {payload['hw_model']}  |  "
                f"firmware {payload['firmware']}  |  board {payload.get('pio_env', '?')}"
            )
            self.btn_check_updates.config(state="normal")
            self.latest_release = None
            self.fw_platform = None
            self.fw_path = None
            self.fw_latest_var.set("")
            self.fw_download_var.set("")
            self.btn_download_fw.config(state="disabled")
            self.btn_flash_esp.config(state="disabled")
            self.uf2_frame.pack_forget()
            self.esp_frame.pack_forget()

        elif kind == "disconnected":
            self._connect_attempt += 1  # invalidate any pending connect-timeout check
            self.connected_info = None
            self.hdr_status.config(text="Disconnected", fg="#FF8080")
            self.status_var.set("Disconnected")
            self.btn_connect.config(state="normal")
            self.btn_disconnect.config(state="disabled")
            for lbl in self.info_labels.values():
                lbl.config(text="—")
            self._append_log("Disconnected")
            self.fw_current_var.set("Not connected")
            self.btn_check_updates.config(state="normal" if self.fw_board_manual.get() else "disabled")
            self.btn_save_settings.config(state="disabled")
            self.btn_discard_settings.config(state="disabled")
            self.btn_save_mqtt.config(state="disabled")
            self.btn_discard_mqtt.config(state="disabled")
            self._channels_cache = {}
            for row in self.channel_tree.get_children():
                self.channel_tree.delete(row)
            self.btn_add_channel.config(state="disabled")
            self.btn_edit_channel.config(state="disabled")
            self.btn_delete_channel.config(state="disabled")

        elif kind == "error":
            self.status_var.set(f"Error: {payload}")
            self._append_log(f"ERROR: {payload}")
            self.btn_connect.config(state="normal")
            self.btn_save_settings.config(state="normal" if self.client.is_connected() else "disabled")
            self.btn_save_mqtt.config(state="normal" if self.client.is_connected() else "disabled")
            messagebox.showerror("Meshtastic Error", str(payload))

        elif kind == "settings_saved":
            self.status_var.set("Settings saved")
            self.btn_save_settings.config(state="normal")
            self._append_log("Device settings saved")
            messagebox.showinfo("Settings", "Settings saved to device.")

        elif kind == "mqtt_saved":
            self.status_var.set("MQTT settings saved")
            self.btn_save_mqtt.config(state="normal")
            self._append_log("MQTT settings saved")
            messagebox.showinfo("MQTT", "MQTT settings saved to device.")

        elif kind == "channel_saved":
            self.status_var.set(f"Channel {payload} saved")
            self._append_log(f"Channel {payload} saved")
            self._refresh_channels()

        elif kind == "channel_deleted":
            self.status_var.set(f"Channel {payload} deleted")
            self._append_log(f"Channel {payload} deleted")
            self._refresh_channels()

        elif kind == "factory_reset_done":
            self.status_var.set("Factory reset complete — device will reboot")
            self._append_log("Factory reset complete")

        elif kind == "node_updated":
            node_id = (payload.get("user") or {}).get("id") or str(payload.get("num"))
            self._upsert_node_row(node_id, payload)
            self.node_count_var.set(f"{len(self.node_tree.get_children())} node(s)")
            self.msg_dest["values"] = list(self.node_lookup.keys())

        elif kind == "text":
            sender = self.node_lookup_reverse(payload["from_id"])
            self._append_msg(f"[{ts()}] {sender} (ch {payload['channel']}): {payload['text']}\n", "in")

        elif kind == "packet":
            portnum = payload.get("decoded", {}).get("portnum", "?")
            self._append_log(f"Packet from {payload.get('fromId', '?')}: {portnum}")

        elif kind == "telemetry":
            node_id = payload["node_id"]
            battery_text = format_battery(payload["battery_level"], payload["voltage"])
            if self.connected_info and node_id == self.connected_info.get("node_id"):
                self.info_labels["battery"].config(text=battery_text)
            if self.node_tree.exists(node_id):
                self.node_tree.set(node_id, "battery", payload["battery_level"] if payload["battery_level"] is not None else "—")

        elif kind == "log":
            self._append_log(payload)

    def node_lookup_reverse(self, node_id: str) -> str:
        for name, nid in self.node_lookup.items():
            if nid == node_id:
                return name
        return node_id

    # ── shutdown ─────────────────────────────────────────────────────────
    def _on_close(self):
        # Background threads/pubsub can still enqueue events right up to (or after)
        # destroy() — this stops _poll_events from acting on them and touching
        # already-destroyed widgets (a TclError otherwise).
        self._closing = True
        try:
            self.client.disconnect()
        except Exception:
            pass
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
