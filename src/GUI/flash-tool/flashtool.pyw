"""UART DFU flash tool for the OPSUM current-sensing board.

This GUI talks to the dedicated DFU app described in opsum_dfu_spec.md and
implemented in src/Firmware/DFU/src/main.c.

Supported functionality:
- Detect DFU mode using HELLO/HELLO_RSP over binary DFU framing.
- Flash app image with BEGIN/DATA/END according to the live DFU protocol.
- Optional REBOOT command after successful END.
- Request DFU from runtime mode using cleartext ENTERDFU command.
"""

from __future__ import annotations

import queue
import struct
import threading
import time
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import serial
import serial.tools.list_ports as list_ports

# Tool-side UART policy.
NORMAL_UART_BAUD = 115200
DFU_UART_BAUD = 921600
AUTO_PROBE_BAUDS = (DFU_UART_BAUD, NORMAL_UART_BAUD)
MAX_DATA_CHUNK_SAFE = 4096  # DFU frame payload 4104 - 8-byte DATA header


# DFU framing constants.
SYNC0 = 0x44
SYNC1 = 0x46
PROTO_VER = 0x01

# Commands.
CMD_HELLO = 0x01
CMD_HELLO_RSP = 0x02
CMD_BEGIN = 0x10
CMD_BEGIN_RSP = 0x11
CMD_DATA = 0x20
CMD_DATA_RSP = 0x21
CMD_END = 0x30
CMD_END_RSP = 0x31
CMD_STATUS = 0x40
CMD_STATUS_RSP = 0x41
CMD_ABORT = 0x50
CMD_ABORT_RSP = 0x51
CMD_REBOOT = 0x60
CMD_REBOOT_RSP = 0x61

# DFU reboot targets (from DFU firmware).
DFU_TARGET_DFU_APP = 0x00
DFU_TARGET_MAIN_APP = 0x01

# Status/error codes from DFU firmware.
DFU_STATUS_OK = 0x0000
DFU_STATUS_SEQ_MISMATCH = 0x000A

DFU_STATUS_MEANINGS = {
    0x0000: "OK",
    0x0001: "Bad frame CRC",
    0x0002: "Unsupported protocol version",
    0x0003: "Unknown command",
    0x0004: "Invalid state",
    0x0005: "Invalid argument",
    0x0006: "Oversize image",
    0x0007: "Partition error",
    0x0008: "Flash write error",
    0x0009: "Final CRC mismatch",
    0x000A: "Sequence mismatch",
    0x000B: "Offset mismatch",
    0x000C: "Timeout",
    0x000D: "Busy",
    0x000E: "Unsupported target",
    0x000F: "Image validation failed",
    0x0010: "Abort completed",
}

# State values returned by STATUS_RSP.
STATE_NAMES = {
    0: "IDLE",
    1: "READY",
    2: "RECEIVING",
    3: "VERIFYING",
    4: "COMMITTED",
    5: "ERROR",
}


@dataclass
class DfuFrame:
    ver: int
    cmd: int
    seq: int
    payload: bytes


@dataclass
class HelloResponse:
    proto_version: int
    chip_id: int
    flags: int
    fw_version: int
    max_chunk: int
    app_partition_size: int
    storage_partition_size: int


@dataclass
class DataResponse:
    next_offset: int
    running_crc32: int
    status: int


@dataclass
class RuntimeStatus:
    firmware_name: str
    version: str
    target_board_rev: str
    board: str

    @property
    def firmware_string(self) -> str:
        return f"{self.firmware_name},{self.version},{self.target_board_rev},{self.board}"


def crc32_dfu(data: bytes) -> int:
    """CRC32 used by the DFU protocol (init/xor-out as in esp_rom_crc32_le wrapper)."""
    return zlib.crc32(data, 0xFFFFFFFF) ^ 0xFFFFFFFF


def format_dfu_status(status: int) -> str:
    meaning = DFU_STATUS_MEANINGS.get(status, "Unknown status")
    return f"0x{status:04X} ({meaning})"


class DfuProtocol:
    def __init__(self, ser: serial.Serial):
        self.ser = ser
        self.seq = 0

    def reset_sequence(self) -> None:
        self.seq = 0

    def _build_frame(self, cmd: int, seq: int, payload: bytes) -> bytes:
        hdr = struct.pack("<2B2BHH", SYNC0, SYNC1, PROTO_VER, cmd, seq, len(payload))
        crc_input = hdr[2:] + payload
        crc = crc32_dfu(crc_input)
        return hdr + payload + struct.pack("<I", crc)

    def _read_exact(self, n: int, timeout_s: float) -> bytes:
        deadline = time.monotonic() + timeout_s
        out = bytearray()
        while len(out) < n:
            if time.monotonic() >= deadline:
                raise TimeoutError("Timed out waiting for serial bytes")
            chunk = self.ser.read(n - len(out))
            if chunk:
                out.extend(chunk)
        return bytes(out)

    def _recv_frame(self, timeout_s: float = 2.0) -> DfuFrame:
        deadline = time.monotonic() + timeout_s

        # Resync on sync bytes.
        while True:
            if time.monotonic() >= deadline:
                raise TimeoutError("Timed out waiting for DFU sync bytes")
            b0 = self.ser.read(1)
            if not b0:
                continue
            if b0[0] != SYNC0:
                continue
            b1 = self.ser.read(1)
            if not b1:
                continue
            if b1[0] == SYNC1:
                break

        hdr = self._read_exact(6, timeout_s)
        ver, cmd, seq, length = struct.unpack("<BBHH", hdr)
        payload = self._read_exact(length, timeout_s) if length else b""
        crc_rx = struct.unpack("<I", self._read_exact(4, timeout_s))[0]

        crc_input = hdr + payload
        crc_calc = crc32_dfu(crc_input)
        if crc_calc != crc_rx:
            raise ValueError(f"Bad frame CRC: got 0x{crc_rx:08X}, expected 0x{crc_calc:08X}")

        return DfuFrame(ver=ver, cmd=cmd, seq=seq, payload=payload)

    def request(self, cmd: int, payload: bytes = b"", timeout_s: float = 2.0) -> DfuFrame:
        req_seq = self.seq
        self.ser.write(self._build_frame(cmd, req_seq, payload))
        self.ser.flush()

        frame = self._recv_frame(timeout_s=timeout_s)
        if frame.seq != req_seq:
            raise ValueError(f"Sequence mismatch in response: got {frame.seq}, expected {req_seq}")

        self.seq = (self.seq + 1) & 0xFFFF
        return frame

    @staticmethod
    def parse_generic_status(frame: DfuFrame) -> int:
        if len(frame.payload) < 4:
            raise ValueError("Generic response payload too short")
        status, _reserved = struct.unpack("<HH", frame.payload[:4])
        return status

    @staticmethod
    def parse_hello(frame: DfuFrame) -> HelloResponse:
        if frame.cmd != CMD_HELLO_RSP:
            raise ValueError(f"Unexpected HELLO response cmd: 0x{frame.cmd:02X}")
        if len(frame.payload) != 20:
            raise ValueError(f"Unexpected HELLO payload size: {len(frame.payload)}")

        proto_version, chip_id, flags, _reserved, fw_version, max_chunk, app_size, storage_size = struct.unpack(
            "<BBBBIIII", frame.payload
        )
        return HelloResponse(
            proto_version=proto_version,
            chip_id=chip_id,
            flags=flags,
            fw_version=fw_version,
            max_chunk=max_chunk,
            app_partition_size=app_size,
            storage_partition_size=storage_size,
        )

    @staticmethod
    def parse_data_rsp(frame: DfuFrame) -> DataResponse:
        if frame.cmd != CMD_DATA_RSP:
            raise ValueError(f"Unexpected DATA response cmd: 0x{frame.cmd:02X}")
        if len(frame.payload) != 12:
            raise ValueError(f"Unexpected DATA_RSP payload size: {len(frame.payload)}")

        next_offset, running_crc32, status, _reserved = struct.unpack("<IIHH", frame.payload)
        return DataResponse(next_offset=next_offset, running_crc32=running_crc32, status=status)

    def sync_hello(self, search_window: int = 1024, frame_timeout_s: float = 0.5) -> HelloResponse:
        """Synchronize HELLO even if local and target sequence counters drifted.

        The DFU firmware keeps an expected sequence counter across commands and does
        not increment it when it returns SEQ_MISMATCH. To recover without a target
        reboot, scan forward from the current local sequence until a valid HELLO_RSP
        payload is found.
        """
        last_err: Optional[Exception] = None
        start_seq = self.seq

        for i in range(max(1, search_window)):
            candidate_seq = (start_seq + i) & 0xFFFF
            try:
                self.ser.reset_input_buffer()
            except Exception:
                pass

            try:
                self.ser.write(self._build_frame(CMD_HELLO, candidate_seq, b""))
                self.ser.flush()
                rsp = self._recv_frame(timeout_s=frame_timeout_s)

                if rsp.seq != candidate_seq or rsp.cmd != CMD_HELLO_RSP:
                    continue

                if len(rsp.payload) == 20:
                    hello = self.parse_hello(rsp)
                    if hello.proto_version != PROTO_VER:
                        raise ValueError(
                            f"Protocol version mismatch (tool={PROTO_VER}, dfu={hello.proto_version})"
                        )
                    self.seq = (candidate_seq + 1) & 0xFFFF
                    return hello

                if len(rsp.payload) == 4:
                    status = self.parse_generic_status(rsp)
                    if status == DFU_STATUS_SEQ_MISMATCH:
                        continue
                    last_err = RuntimeError(f"HELLO failed with status {format_dfu_status(status)}")
                    continue

                last_err = RuntimeError(f"Unexpected HELLO payload size: {len(rsp.payload)}")
            except Exception as exc:
                last_err = exc

        raise RuntimeError(
            f"HELLO synchronization failed after {search_window} sequence attempts"
            + (f": {last_err}" if last_err else "")
        )


class FlashToolApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("OPSUM Device Firmware Update Tool")
        self.root.geometry("820x600")

        self.serial: Optional[serial.Serial] = None
        self.dfu: Optional[DfuProtocol] = None
        self.hello_info: Optional[HelloResponse] = None
        self.runtime_status: Optional[RuntimeStatus] = None
        self.mode = "disconnected"

        self.log_queue: queue.Queue[str] = queue.Queue()
        self.flash_thread: Optional[threading.Thread] = None
        self.flash_abort = threading.Event()
        self.flash_in_progress = False
        self.detect_thread: Optional[threading.Thread] = None
        self.detect_running = False
        self.request_dfu_thread: Optional[threading.Thread] = None
        self.request_dfu_in_progress = False

        self.selected_file = tk.StringVar(value="")
        self.selected_port = tk.StringVar(value="")
        self.active_baud: Optional[int] = None
        self.auto_reboot = tk.BooleanVar(value=True)

        self._build_ui()
        self._refresh_ports()
        self._drain_log_queue()

    def _build_ui(self) -> None:
        container = ttk.Frame(self.root, padding=10)
        container.pack(fill="both", expand=True)

        # Connection row.
        conn = ttk.LabelFrame(container, text="Serial Connection", padding=10)
        conn.pack(fill="x")

        ttk.Label(conn, text="Port:").grid(row=0, column=0, sticky="w")
        self.port_combo = ttk.Combobox(conn, textvariable=self.selected_port, state="readonly", width=20)
        self.port_combo.grid(row=0, column=1, padx=6)

        ttk.Button(conn, text="Refresh", command=self._refresh_ports).grid(row=0, column=2, padx=4)

        self.baud_policy_label = ttk.Label(
            conn,
            text=f"Auto baud: DFU={DFU_UART_BAUD}, normal={NORMAL_UART_BAUD}",
        )
        self.baud_policy_label.grid(row=0, column=3, padx=(16, 0), sticky="w")

        self.active_baud_label = ttk.Label(conn, text="Active: -")
        self.active_baud_label.grid(row=0, column=4, padx=(10, 0), sticky="w")

        self.connect_btn = ttk.Button(conn, text="Connect", command=self._toggle_connection)
        self.connect_btn.grid(row=0, column=5, padx=4)

        # Detection is automatic on connect
        # Mode indicator
        mode_frame = ttk.LabelFrame(container, text="Current Board Status", padding=10)
        mode_frame.pack(fill="x", pady=(10, 0))

        self.mode_label = ttk.Label(mode_frame, text="Disconnected", font=("Segoe UI", 13, "bold"))
        self.mode_label.pack(anchor="w")

        self.mode_detail = ttk.Label(mode_frame, text="Open a serial port and run mode detection.")
        self.mode_detail.pack(anchor="w", pady=(4, 0))

        # Mode control commands
        normal_frame = ttk.LabelFrame(container, text="Mode Control Commands", padding=10)
        normal_frame.pack(fill="x", pady=(10, 0))

        self.mode_command_btn = ttk.Button(
            normal_frame,
            text="Reboot to DFU Mode",
            command=self._run_mode_command,
            state="disabled",
        )
        self.mode_command_btn.pack(anchor="w")
        ttk.Label(
            normal_frame,
            text="Switches between Main Firmware and Device Firmware Update (DFU) mode",
        ).pack(anchor="w", pady=(6, 0))

        # DFU flash controls.
        dfu_frame = ttk.LabelFrame(container, text="DFU Flash", padding=10)
        dfu_frame.pack(fill="x", pady=(10, 0))
        self.dfu_frame = dfu_frame

        file_row = ttk.Frame(dfu_frame)
        file_row.pack(fill="x")
        ttk.Label(file_row, text="Firmware bin:").pack(side="left")
        self.file_entry = ttk.Entry(file_row, textvariable=self.selected_file, state="disabled")
        self.file_entry.pack(side="left", fill="x", expand=True, padx=6)
        self.browse_btn = ttk.Button(file_row, text="Browse", command=self._browse_file, state="disabled")
        self.browse_btn.pack(side="left")

        self.flash_btn = ttk.Button(dfu_frame, text="Flash Main Firmware", command=self._start_flash, state="disabled")
        self.flash_btn.pack(anchor="w", pady=(8, 0))

        self.abort_btn = ttk.Button(dfu_frame, text="Abort Transfer", command=self._abort_flash, state="disabled")
        self.abort_btn.pack(anchor="w", pady=(6, 0))

        self.progress = ttk.Progressbar(dfu_frame, orient="horizontal", mode="determinate", maximum=100)
        self.progress.pack(fill="x", pady=(10, 0))

        self.progress_label = ttk.Label(dfu_frame, text="No transfer in progress.")
        self.progress_label.pack(anchor="w", pady=(4, 0))

        # Log output.
        log_frame = ttk.LabelFrame(container, text="Session Log", padding=10)
        log_frame.pack(fill="both", expand=True, pady=(10, 0))

        self.log_text = tk.Text(log_frame, height=14, wrap="word", state="disabled")
        self.log_text.pack(fill="both", expand=True)

    def _update_dfu_section_state(self) -> None:
        dfu_ready = bool(
            self.mode == "dfu"
            and self.serial is not None
            and self.serial.is_open
            and not self.flash_in_progress
        )

        self.file_entry.configure(state="normal" if dfu_ready else "disabled")
        self.browse_btn.configure(state="normal" if dfu_ready else "disabled")
        self.flash_btn.configure(state="normal" if dfu_ready else "disabled")
        self.abort_btn.configure(state="normal" if self.flash_in_progress else "disabled")

    def _log(self, message: str) -> None:
        self.log_queue.put(f"[{time.strftime('%H:%M:%S')}] {message}")

    def _drain_log_queue(self) -> None:
        while True:
            try:
                line = self.log_queue.get_nowait()
            except queue.Empty:
                break
            self.log_text.configure(state="normal")
            self.log_text.insert("end", line + "\n")
            self.log_text.see("end")
            self.log_text.configure(state="disabled")
        self.root.after(120, self._drain_log_queue)

    def _set_mode(self, mode: str, detail: str) -> None:
        self.mode = mode
        if mode == "dfu":
            self.mode_label.configure(text="DFU Mode", foreground="#0B7A0B")
            self.mode_detail.configure(foreground="#1E1E1E")
        elif mode == "normal":
            self.mode_label.configure(text="Normal Mode", foreground="#0A4E9B")
            self.mode_detail.configure(foreground="#0B7A0B")
        elif mode == "unknown":
            self.mode_label.configure(text="Unknown", foreground="#8A6D00")
            self.mode_detail.configure(foreground="#1E1E1E")
        else:
            self.mode_label.configure(text="Disconnected", foreground="#6B6B6B")
            self.mode_detail.configure(foreground="#1E1E1E")
        self.mode_detail.configure(text=detail)

        self._update_dfu_section_state()
        command_ready = bool(
            mode in ("normal", "dfu")
            and not self.detect_running
            and not self.flash_in_progress
            and not self.request_dfu_in_progress
            and self.serial is not None
            and self.serial.is_open
        )

        if mode == "normal":
            self.mode_command_btn.configure(text="Reboot to DFU Mode")
        elif mode == "dfu":
            self.mode_command_btn.configure(text="Reboot To Main Firmware")
        else:
            self.mode_command_btn.configure(text="Mode Command")

        self.mode_command_btn.configure(state="normal" if command_ready else "disabled")

    def _refresh_ports(self) -> None:
        ports = [p.device for p in list_ports.comports()]
        self.port_combo["values"] = ports
        if ports and self.selected_port.get() not in ports:
            self.selected_port.set(ports[0])

    def _toggle_connection(self) -> None:
        if self.serial and self.serial.is_open:
            self._disconnect_serial()
            return

        port = self.selected_port.get().strip()
        if not port:
            messagebox.showwarning("Port required", "Select a serial port first.")
            return

        try:
            self._reopen_serial(port, NORMAL_UART_BAUD)
            self.connect_btn.configure(text="Disconnect")
            self._set_mode("unknown", "Connected. Detecting mode...")
            self._log(f"Connected to {port} @ {NORMAL_UART_BAUD} baud (normal default)")
            self.detect_mode()
        except Exception as exc:
            self.serial = None
            self.dfu = None
            self.active_baud = None
            self._update_active_baud_label()
            messagebox.showerror("Connection failed", str(exc))

    def _update_active_baud_label(self) -> None:
        text = f"Active: {self.active_baud}" if self.active_baud else "Active: -"
        self.root.after(0, lambda: self.active_baud_label.configure(text=text))

    def _reopen_serial(self, port: str, baud: int) -> None:
        if self.serial:
            try:
                self.serial.close()
            except Exception:
                pass
        self.serial = serial.Serial(port=port, baudrate=baud, timeout=0.15, write_timeout=1.0)
        self.dfu = DfuProtocol(self.serial)
        self.active_baud = baud
        self._update_active_baud_label()

    def _probe_runtime_status(self, timeout_s: float = 1.0) -> Optional[RuntimeStatus]:
        """Probe runtime firmware with STATUS command and parse OK_RUNNING payload."""
        if not self.serial or not self.serial.is_open:
            return None

        try:
            self.serial.reset_input_buffer()
            self.serial.write(b"STATUS\r\n")
            self.serial.flush()
        except Exception:
            return None

        deadline = time.monotonic() + timeout_s
        rx = bytearray()
        marker = b"OK_RUNNING,"

        while time.monotonic() < deadline:
            chunk = self.serial.read(256)
            if chunk:
                rx.extend(chunk)
                # Bound growth while still keeping enough history to find marker.
                if len(rx) > 8192:
                    del rx[:-4096]

                idx = rx.find(marker)
                if idx >= 0:
                    end = rx.find(b"\n", idx)
                    if end >= 0:
                        line_bytes = rx[idx : end + 1]
                        line = line_bytes.decode("utf-8", errors="ignore").strip()
                        parts = [p.strip() for p in line.split(",")]
                        if len(parts) >= 5 and parts[0] == "OK_RUNNING":
                            return RuntimeStatus(
                                firmware_name=parts[1],
                                version=parts[2],
                                target_board_rev=parts[3],
                                board=",".join(parts[4:]).strip(),
                            )

        if not rx:
            return None

        return None

    def _set_mode_threadsafe(self, mode: str, detail: str) -> None:
        self.root.after(0, lambda: self._set_mode(mode, detail))

    @staticmethod
    def _format_runtime_detail(runtime: RuntimeStatus) -> str:
        return (
            f"Detection OK | {runtime.firmware_name} v{runtime.version}, "
            f"Board: {runtime.board}, HW Rev: {runtime.target_board_rev}"
        )

    def _finish_detect_ui(self) -> None:
        def apply() -> None:
            self.detect_running = False
            self.connect_btn.configure(state="normal")
            # Refresh dependent controls after detect state changes.
            self._set_mode(self.mode, self.mode_detail.cget("text"))

        self.root.after(0, apply)

    def _disconnect_serial(self) -> None:
        if self.serial:
            try:
                self.serial.close()
            except Exception:
                pass
        self.serial = None
        self.dfu = None
        self.hello_info = None
        self.runtime_status = None
        self.active_baud = None
        self._update_active_baud_label()
        self.connect_btn.configure(text="Connect")
        self._set_mode("disconnected", "Open a serial port and run mode detection.")
        self._log("Serial port closed")

    def detect_mode(self) -> None:
        if self.detect_running:
            self._log("Detect Mode already running")
            return

        if not self.serial:
            messagebox.showwarning("Not connected", "Open a serial port first.")
            return

        port = self.selected_port.get().strip()
        if not port:
            messagebox.showwarning("Port required", "Select a serial port first.")
            return

        self.detect_running = True
        self.connect_btn.configure(state="disabled")
        self.mode_command_btn.configure(state="disabled")
        self._log("Starting mode detection...")
        self.detect_thread = threading.Thread(target=self._detect_mode_worker, daemon=True)
        self.detect_thread.start()

    def _detect_mode_worker(self) -> None:
        port = self.selected_port.get().strip()
        if not port:
            self._finish_detect_ui()
            return

        # First, prioritize runtime detection at known runtime baud so users don't
        # wait through DFU probing while telemetry is streaming.
        try:
            self._reopen_serial(port, NORMAL_UART_BAUD)
            self._log(f"Probing runtime STATUS @ {NORMAL_UART_BAUD} baud")
            runtime = self._probe_runtime_status(timeout_s=0.8)
            if runtime:
                self.runtime_status = runtime
                detail = self._format_runtime_detail(runtime)
                self._set_mode_threadsafe("normal", detail)
                self._log(f"Normal mode detected: {runtime.firmware_string}")
                self._finish_detect_ui()
                return
        except Exception as runtime_exc:
            self._log(f"Runtime STATUS probe failed: {runtime_exc}")

        # Probe DFU first at high speed, then compatibility speed.
        for baud in dict.fromkeys(AUTO_PROBE_BAUDS):
            try:
                self._reopen_serial(port, int(baud))
            except Exception as open_exc:
                self._log(f"Probe open failed @ {baud}: {open_exc}")
                continue

            self._log(f"Probing DFU HELLO @ {baud} baud")
            assert self.serial is not None
            assert self.dfu is not None

            try:
                self.serial.reset_input_buffer()
                # Keep UI responsive by limiting DFU probe time during detection.
                hello = self.dfu.sync_hello(search_window=12, frame_timeout_s=0.12)
                self.hello_info = hello
                self.runtime_status = None
                detail = (
                    f"DFU protocol v{hello.proto_version}, chip_id=0x{hello.chip_id:02X}, "
                    f"max_chunk={hello.max_chunk}, app_size={hello.app_partition_size} bytes, baud={baud}"
                )
                self._set_mode_threadsafe("dfu", detail)
                self._log(f"DFU HELLO detected successfully @ {baud} baud")
                self._finish_detect_ui()
                return
            except Exception as dfu_exc:
                self._log(f"DFU HELLO probe failed @ {baud}: {dfu_exc}")

        # Keep connection in runtime/normal baud after failed DFU probe cycle.
        try:
            self._reopen_serial(port, NORMAL_UART_BAUD)
        except Exception as reopen_exc:
            self._log(f"Failed to return to normal baud {NORMAL_UART_BAUD}: {reopen_exc}")

        runtime = self._probe_runtime_status()
        if runtime:
            self.runtime_status = runtime
            detail = self._format_runtime_detail(runtime)
            self._set_mode_threadsafe("normal", detail)
            self._log(f"Normal mode detected: {runtime.firmware_string}")
            self._finish_detect_ui()
            return

        self.runtime_status = None

        self._set_mode_threadsafe(
            "unknown",
            f"No valid DFU HELLO or runtime STATUS response at probe bauds. Active baud={self.active_baud}.",
        )
        self._finish_detect_ui()

    def _run_mode_command(self) -> None:
        if self.mode == "normal":
            self._request_dfu()
            return
        if self.mode == "dfu":
            self._request_reboot_to_normal()
            return

        messagebox.showwarning("Wrong mode", "Mode command is available only in Normal or DFU mode.")

    def _request_dfu(self) -> None:
        if self.request_dfu_in_progress:
            self._log("Request DFU already in progress")
            return
        if self.mode != "normal":
            messagebox.showwarning("Wrong mode", "Request DFU is available only in Normal mode.")
            return
        if self.detect_running or self.flash_in_progress:
            self._log("Request DFU blocked while another operation is running")
            return
        if not self.serial or not self.serial.is_open:
            messagebox.showwarning("Not connected", "Open a serial port first.")
            return

        self.request_dfu_in_progress = True
        self.mode_command_btn.configure(state="disabled")
        self._log("Requesting DFU via ENTERDFU...")
        self.request_dfu_thread = threading.Thread(target=self._request_dfu_worker, daemon=True)
        self.request_dfu_thread.start()

    def _request_reboot_to_normal(self) -> None:
        if self.request_dfu_in_progress:
            self._log("Mode command already in progress")
            return
        if self.mode != "dfu":
            messagebox.showwarning("Wrong mode", "Reboot to normal mode is available only in DFU mode.")
            return
        if self.detect_running or self.flash_in_progress:
            self._log("DFU REBOOT blocked while another operation is running")
            return
        if not self.serial or not self.serial.is_open or not self.dfu:
            messagebox.showwarning("Not connected", "Open a serial port first.")
            return

        self.request_dfu_in_progress = True
        self.mode_command_btn.configure(state="disabled")
        self._log("Sending DFU REBOOT to main app (0x01)...")
        self.request_dfu_thread = threading.Thread(
            target=self._request_dfu_reboot_worker,
            daemon=True,
        )
        self.request_dfu_thread.start()

    def _request_dfu_done(self) -> None:
        def apply() -> None:
            self.request_dfu_in_progress = False
            self._set_mode(self.mode, self.mode_detail.cget("text"))

        self.root.after(0, apply)

    def _request_dfu_worker(self) -> None:
        try:
            if not self.serial or not self.serial.is_open:
                raise RuntimeError("Serial port is not open")

            ack_ok = b"OK_REBOOTING_INTO_DFU_MODE"
            ack_err_1 = b"ERR_DFU_PARTITION_NOT_FOUND"
            ack_err_2 = b"ERR_DFU_BOOT_TARGET_SET_FAILED"

            self.serial.reset_input_buffer()
            self.serial.write(b"ENTERDFU\r\n")
            self.serial.flush()

            deadline = time.monotonic() + 1.5
            rx = bytearray()
            got_ok = False

            while time.monotonic() < deadline:
                chunk = self.serial.read(256)
                if not chunk:
                    continue
                rx.extend(chunk)
                if len(rx) > 8192:
                    del rx[:-4096]

                if ack_ok in rx:
                    got_ok = True
                    break
                if ack_err_1 in rx:
                    raise RuntimeError("Runtime firmware reported: DFU partition not found")
                if ack_err_2 in rx:
                    raise RuntimeError("Runtime firmware reported: failed to set DFU boot target")

            if not got_ok:
                raise RuntimeError("No DFU reboot acknowledgement received")

            self._log("Runtime acknowledged DFU reboot request")
            # Give the target a short time window to reboot and swap mode.
            time.sleep(0.8)
            self._log("Re-detecting mode after DFU request...")
            self.root.after(0, self.detect_mode)
        except Exception as exc:
            err_msg = str(exc)
            self._log(f"Request DFU failed: {err_msg}")
            self.root.after(0, lambda msg=err_msg: messagebox.showerror("Request DFU failed", msg))
        finally:
            self._request_dfu_done()

    def _request_dfu_reboot_worker(self) -> None:
        try:
            if not self.serial or not self.serial.is_open or not self.dfu:
                raise RuntimeError("Serial port is not open")

            # Re-sync before sending control command to avoid stale sequence issues.
            self.dfu.sync_hello(search_window=48, frame_timeout_s=0.12)
            rsp = self.dfu.request(CMD_REBOOT, bytes([DFU_TARGET_MAIN_APP]), timeout_s=2.0)
            status = self.dfu.parse_generic_status(rsp)
            if status != DFU_STATUS_OK:
                raise RuntimeError(f"DFU REBOOT failed with status {format_dfu_status(status)}")

            self._log("DFU REBOOT accepted for main app (0x01)")
            # Allow reboot and UART re-enumeration before probing again.
            time.sleep(0.8)
            self._log("Re-detecting mode after DFU REBOOT...")
            self.root.after(0, self.detect_mode)
        except Exception as exc:
            err_msg = str(exc)
            self._log(f"DFU REBOOT failed: {err_msg}")
            self.root.after(0, lambda msg=err_msg: messagebox.showerror("DFU REBOOT failed", msg))
        finally:
            self._request_dfu_done()

    def _browse_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Select OPSUM app binary",
            filetypes=[("Binary files", "*.bin"), ("All files", "*.*")],
        )
        if path:
            self.selected_file.set(path)

    def _start_flash(self) -> None:
        if self.flash_thread and self.flash_thread.is_alive():
            messagebox.showwarning("Busy", "A flash operation is already running.")
            return

        if self.mode != "dfu":
            messagebox.showwarning("Wrong mode", "Target is not in DFU mode.")
            return

        if not self.serial or not self.dfu or not self.hello_info:
            messagebox.showwarning("Not ready", "Connect and run Detect Mode first.")
            return

        file_path = Path(self.selected_file.get().strip())
        if not file_path.is_file():
            messagebox.showwarning("File required", "Select a valid firmware binary file.")
            return

        self.flash_abort.clear()
        self.flash_in_progress = True
        self._update_dfu_section_state()
        self.progress.configure(value=0)
        self.progress_label.configure(text="Preparing transfer...")

        self.flash_thread = threading.Thread(target=self._flash_worker, args=(file_path,), daemon=True)
        self.flash_thread.start()

    def _abort_flash(self) -> None:
        self.flash_abort.set()
        self._log("Abort requested by user")

    def _set_progress(self, percent: float, text: str) -> None:
        def apply() -> None:
            self.progress.configure(value=max(0.0, min(100.0, percent)))
            self.progress_label.configure(text=text)

        self.root.after(0, apply)

    def _flash_done_ui(self, success: bool, message: str) -> None:
        def apply() -> None:
            self.flash_in_progress = False
            self._update_dfu_section_state()
            if success:
                self._disconnect_serial()
                messagebox.showinfo("Flash complete", message)
            else:
                messagebox.showerror("Flash failed", message)

        self.root.after(0, apply)

    def _flash_worker(self, file_path: Path) -> None:
        assert self.dfu is not None
        assert self.hello_info is not None

        try:
            firmware = file_path.read_bytes()
            total_size = len(firmware)
            if total_size == 0:
                raise RuntimeError("Firmware file is empty")

            if total_size > self.hello_info.app_partition_size:
                raise RuntimeError(
                    f"Image too large: {total_size} > app partition {self.hello_info.app_partition_size}"
                )

            if firmware[0] != 0xE9:
                self._log("Warning: image header magic is not 0xE9 (DFU target may reject END)")

            # Must match DFU firmware's full-image CRC convention:
            # init=0xFFFFFFFF, reflected poly, xor_out=0xFFFFFFFF.
            expected_crc = crc32_dfu(firmware)
            max_chunk = max(1, int(self.hello_info.max_chunk))
            chunk_size = min(max_chunk, MAX_DATA_CHUNK_SAFE)
            if max_chunk > MAX_DATA_CHUNK_SAFE:
                self._log(
                    f"DFU max_chunk {max_chunk} exceeds safe DATA payload limit; clamping to {MAX_DATA_CHUNK_SAFE}"
                )

            self._log(
                f"Flashing {file_path.name}: size={total_size}, crc32=0x{expected_crc:08X}, chunk={chunk_size}"
            )

            # Re-sync HELLO without forcing sequence to zero.
            # Flash path can afford a wider resync window than mode detection.
            hello = self.dfu.sync_hello(search_window=128, frame_timeout_s=0.2)
            self._log(
                f"HELLO OK: proto={hello.proto_version}, app_size={hello.app_partition_size}, storage_size={hello.storage_partition_size}"
            )

            begin_payload = struct.pack(
                "<IIIBBHI",
                total_size,
                chunk_size,
                expected_crc,
                0x01,  # target_id = main app
                0x00,  # options
                0x0000,
                0x00000000,  # version_code (optional metadata)
            )
            begin_rsp = self.dfu.request(CMD_BEGIN, begin_payload, timeout_s=4.0)
            if begin_rsp.cmd != CMD_BEGIN_RSP:
                raise RuntimeError(f"Unexpected BEGIN response cmd: 0x{begin_rsp.cmd:02X}")
            begin_status = self.dfu.parse_generic_status(begin_rsp)
            if begin_status != DFU_STATUS_OK:
                raise RuntimeError(f"BEGIN failed with status {format_dfu_status(begin_status)}")

            self._set_progress(0, "BEGIN accepted, streaming DATA...")

            offset = 0
            while offset < total_size:
                if self.flash_abort.is_set():
                    try:
                        abort_rsp = self.dfu.request(CMD_ABORT, b"", timeout_s=2.0)
                        abort_status = self.dfu.parse_generic_status(abort_rsp)
                        self._log(f"ABORT status: {format_dfu_status(abort_status)}")
                    except Exception as abort_exc:
                        self._log(f"ABORT command failed: {abort_exc}")
                    raise RuntimeError("Transfer aborted")

                chunk = firmware[offset : offset + chunk_size]
                data_payload = struct.pack("<IHH", offset, len(chunk), 0) + chunk
                data_rsp_frame = self.dfu.request(CMD_DATA, data_payload, timeout_s=4.0)
                data_rsp = self.dfu.parse_data_rsp(data_rsp_frame)

                if data_rsp.status == DFU_STATUS_SEQ_MISMATCH:
                    raise RuntimeError("DATA failed with sequence mismatch")
                if data_rsp.status != DFU_STATUS_OK:
                    raise RuntimeError(
                        f"DATA failed at offset {offset} with status {format_dfu_status(data_rsp.status)}"
                    )
                if data_rsp.next_offset != offset + len(chunk):
                    raise RuntimeError(
                        f"DATA next_offset mismatch: got {data_rsp.next_offset}, expected {offset + len(chunk)}"
                    )

                offset += len(chunk)
                percent = (offset * 100.0) / total_size
                self._set_progress(percent, f"Transferred {offset}/{total_size} bytes")

            end_payload = struct.pack("<II", total_size, expected_crc)
            end_rsp = self.dfu.request(CMD_END, end_payload, timeout_s=8.0)
            if end_rsp.cmd != CMD_END_RSP:
                raise RuntimeError(f"Unexpected END response cmd: 0x{end_rsp.cmd:02X}")
            end_status = self.dfu.parse_generic_status(end_rsp)
            if end_status != DFU_STATUS_OK:
                raise RuntimeError(f"END failed with status {format_dfu_status(end_status)}")

            self._set_progress(100, "Flash committed successfully")
            self._log("END accepted. APP partition updated and storage erase completed by DFU target.")

            if self.auto_reboot.get():
                try:
                    reboot_rsp = self.dfu.request(CMD_REBOOT, b"", timeout_s=2.0)
                    reboot_status = self.dfu.parse_generic_status(reboot_rsp)
                    self._log(f"REBOOT status: {format_dfu_status(reboot_status)}")
                except Exception as reboot_exc:
                    self._log(f"REBOOT command did not complete cleanly: {reboot_exc}")

            self._flash_done_ui(True, "Firmware flashing completed.")

        except Exception as exc:
            self._log(f"Flash error: {exc}")
            self._set_progress(0, f"Flash failed: {exc}")
            self._flash_done_ui(False, str(exc))


def main() -> None:
    root = tk.Tk()
    app = FlashToolApp(root)
    root.protocol("WM_DELETE_WINDOW", app._disconnect_serial)

    def on_close() -> None:
        app._disconnect_serial()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
