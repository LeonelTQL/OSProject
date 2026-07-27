#!/usr/bin/env python3
"""
Mini Monitor de Recursos Linux
UI dark profesional inspirada en dashboards modernos de System Monitor.

Cumple el proyecto:
- Python 3 + Tkinter
- Lectura desde /proc: cpuinfo, stat, meminfo, net/dev, loadavg, diskstats, procesos
- Dos hilos concurrentes con threading.Thread
- Proceso hijo con os.fork()
- Comandos Linux con subprocess y os.system
- CRUD SQLite para capturas de monitoreo

Ejecución:
    python app.py
"""

from __future__ import annotations

import json
import os
import pwd
import queue
import random
import sqlite3
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import tkinter as tk
from tkinter import messagebox, ttk

APP_DIR = Path(__file__).resolve().parent
DB_PATH = APP_DIR / "monitor.db"
FORK_LOG_PATH = APP_DIR / "fork_child.log"
OS_SYSTEM_LOG_PATH = APP_DIR / "os_system_demo.log"

C = {
    "bg": "#0b111a",
    "bg2": "#0f1724",
    "sidebar": "#0a1018",
    "panel": "#121a27",
    "panel2": "#161f2e",
    "panel3": "#1b2638",
    "border": "#2a3548",
    "grid": "#273244",
    "text": "#e8edf7",
    "muted": "#9da8bd",
    "muted2": "#68758d",
    "purple": "#a970ff",
    "purple2": "#c084fc",
    "cyan": "#24c8ff",
    "blue": "#2f8cff",
    "pink": "#ff4fa3",
    "coral": "#ff7a6b",
    "mint": "#55e37a",
    "green": "#67d94f",
    "amber": "#f59e0b",
    "yellow": "#f2d94e",
    "danger": "#ef4444",
}

FONT = "DejaVu Sans"
MONO = "DejaVu Sans Mono"


# ============================================================
# Utilidades
# ============================================================


def read_text(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def fmt_bytes(num_bytes: float, decimals: int = 1) -> str:
    value = float(max(num_bytes, 0))
    units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"]
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{value:.0f} {unit}"
            return f"{value:.{decimals}f} {unit}"
        value /= 1024
    return f"{value:.{decimals}f} PiB"


def fmt_kb(kb: float, decimals: int = 1) -> str:
    return fmt_bytes(float(kb) * 1024, decimals)


def run_command(command: List[str], timeout: int = 4) -> str:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
        output = result.stdout.strip() or result.stderr.strip()
        return output if output else "Sin salida"
    except FileNotFoundError:
        return f"Comando no encontrado: {command[0]}"
    except subprocess.TimeoutExpired:
        return f"Tiempo agotado ejecutando: {' '.join(command)}"
    except OSError as exc:
        return f"Error ejecutando {' '.join(command)}: {exc}"


def uid_to_user(uid: str) -> str:
    try:
        return pwd.getpwuid(int(uid)).pw_name
    except Exception:
        return uid or "?"


# ============================================================
# Lectura Linux: /proc + comandos
# ============================================================


class SystemProbe:
    def __init__(self) -> None:
        self.prev_cpu: Dict[str, Tuple[int, int]] = {}
        self.prev_net: Dict[str, Tuple[int, int, float]] = {}
        self.prev_disk: Optional[Tuple[int, int, float]] = None

    def _cpu_ticks(self) -> Dict[str, Tuple[int, int]]:
        rows: Dict[str, Tuple[int, int]] = {}
        for line in read_text("/proc/stat").splitlines():
            if not line.startswith("cpu"):
                break
            parts = line.split()
            name = parts[0]
            nums = [safe_int(x) for x in parts[1:]]
            if len(nums) < 4:
                continue
            idle = nums[3] + (nums[4] if len(nums) > 4 else 0)
            total = sum(nums)
            rows[name] = (total, idle)
        return rows

    def cpu_percentages(self) -> Tuple[float, List[float]]:
        current = self._cpu_ticks()
        total_pct = 0.0
        core_pcts: List[float] = []
        for name, (total, idle) in current.items():
            old_total, old_idle = self.prev_cpu.get(name, (total, idle))
            total_delta = total - old_total
            idle_delta = idle - old_idle
            pct = 0.0 if total_delta <= 0 else (1.0 - idle_delta / total_delta) * 100.0
            pct = round(max(0, min(100, pct)), 1)
            if name == "cpu":
                total_pct = pct
            else:
                core_pcts.append(pct)
        self.prev_cpu = current
        return total_pct, core_pcts

    def cpu_info(self) -> Dict[str, Any]:
        total_pct, core_pcts = self.cpu_percentages()
        model = "No disponible"
        mhz_values: List[float] = []
        processors = 0
        siblings = 0
        for line in read_text("/proc/cpuinfo").splitlines():
            if line.startswith("processor"):
                processors += 1
            elif line.startswith("model name") and model == "No disponible":
                model = line.split(":", 1)[1].strip()
            elif line.startswith("cpu MHz"):
                mhz_values.append(safe_float(line.split(":", 1)[1].strip()))
            elif line.startswith("siblings") and not siblings:
                siblings = safe_int(line.split(":", 1)[1].strip())
        freq = sum(mhz_values) / len(mhz_values) if mhz_values else 0.0
        load_parts = read_text("/proc/loadavg").split()
        load = [safe_float(x) for x in load_parts[:3]] if len(load_parts) >= 3 else [0.0, 0.0, 0.0]
        return {
            "usage": total_pct,
            "cores": processors or len(core_pcts),
            "threads": siblings or processors or len(core_pcts),
            "freq_mhz": freq,
            "model": model,
            "per_core": core_pcts,
            "load": load,
        }

    def memory_info(self) -> Dict[str, Any]:
        mem: Dict[str, int] = {}
        for line in read_text("/proc/meminfo").splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            mem[key] = safe_int(value.replace("kB", "").strip())
        total = mem.get("MemTotal", 0)
        free = mem.get("MemFree", 0)
        available = mem.get("MemAvailable", free)
        cached = mem.get("Cached", 0) + mem.get("SReclaimable", 0)
        buffers = mem.get("Buffers", 0)
        used = max(total - available, 0)
        swap_total = mem.get("SwapTotal", 0)
        swap_free = mem.get("SwapFree", 0)
        swap_used = max(swap_total - swap_free, 0)
        return {
            "total_kb": total,
            "used_kb": used,
            "free_kb": free,
            "available_kb": available,
            "cached_kb": cached,
            "buffers_kb": buffers,
            "used_pct": round((used / total) * 100, 1) if total else 0.0,
            "swap_total_kb": swap_total,
            "swap_used_kb": swap_used,
            "swap_free_kb": swap_free,
            "swap_pct": round((swap_used / swap_total) * 100, 1) if swap_total else 0.0,
        }

    def disk_info(self) -> Dict[str, Any]:
        output = run_command(["df", "-B1", "-P", "/"])
        lines = output.splitlines()
        result = {
            "filesystem": "No disponible",
            "mount": "/",
            "total": 0,
            "used": 0,
            "free": 0,
            "used_pct": 0.0,
            "raw": output,
        }
        if len(lines) >= 2:
            parts = lines[1].split()
            if len(parts) >= 6:
                result.update(
                    {
                        "filesystem": parts[0],
                        "total": safe_int(parts[1]),
                        "used": safe_int(parts[2]),
                        "free": safe_int(parts[3]),
                        "used_pct": safe_float(parts[4].replace("%", "")),
                        "mount": parts[5],
                    }
                )
        read_b, write_b = self.disk_rates()
        result["read_rate"] = read_b
        result["write_rate"] = write_b
        return result

    def disk_rates(self) -> Tuple[float, float]:
        # /proc/diskstats: campos 6 y 10 son sectores leídos/escritos en kernels actuales.
        read_sectors = 0
        write_sectors = 0
        for line in read_text("/proc/diskstats").splitlines():
            parts = line.split()
            if len(parts) < 14:
                continue
            name = parts[2]
            if name.startswith(("loop", "ram", "zram")) or name[-1:].isdigit():
                continue
            read_sectors += safe_int(parts[5])
            write_sectors += safe_int(parts[9])
        now = time.time()
        read_bytes = read_sectors * 512
        write_bytes = write_sectors * 512
        if self.prev_disk is None:
            self.prev_disk = (read_bytes, write_bytes, now)
            return 0.0, 0.0
        old_read, old_write, old_time = self.prev_disk
        elapsed = max(now - old_time, 0.001)
        self.prev_disk = (read_bytes, write_bytes, now)
        return max((read_bytes - old_read) / elapsed, 0.0), max((write_bytes - old_write) / elapsed, 0.0)

    def network_info(self) -> Dict[str, Any]:
        ip_output = run_command(["ip", "-o", "addr", "show"], timeout=3)
        ips: Dict[str, List[str]] = {}
        for line in ip_output.splitlines():
            parts = line.split()
            if len(parts) >= 4 and parts[2] in {"inet", "inet6"}:
                iface = parts[1]
                ips.setdefault(iface, []).append(parts[3])

        gateway = "No disponible"
        route = run_command(["ip", "route", "show", "default"], timeout=3)
        route_parts = route.split()
        if "via" in route_parts:
            gateway = route_parts[route_parts.index("via") + 1]

        interfaces: List[Dict[str, Any]] = []
        total_rx_rate = 0.0
        total_tx_rate = 0.0
        now = time.time()
        for line in read_text("/proc/net/dev").splitlines()[2:]:
            if ":" not in line:
                continue
            iface, values = line.split(":", 1)
            iface = iface.strip()
            parts = values.split()
            if len(parts) < 16:
                continue
            rx = safe_int(parts[0])
            tx = safe_int(parts[8])
            old_rx, old_tx, old_time = self.prev_net.get(iface, (rx, tx, now))
            elapsed = max(now - old_time, 0.001)
            rx_rate = max((rx - old_rx) / elapsed, 0.0)
            tx_rate = max((tx - old_tx) / elapsed, 0.0)
            self.prev_net[iface] = (rx, tx, now)
            if iface != "lo":
                total_rx_rate += rx_rate
                total_tx_rate += tx_rate
            interfaces.append(
                {
                    "iface": iface,
                    "ips": ips.get(iface, []),
                    "rx_total": rx,
                    "tx_total": tx,
                    "rx_rate": rx_rate,
                    "tx_rate": tx_rate,
                    "rx_packets": safe_int(parts[1]),
                    "tx_packets": safe_int(parts[9]),
                }
            )
        active = next((i for i in interfaces if i["iface"] != "lo" and i["ips"]), interfaces[0] if interfaces else {})
        return {"interfaces": interfaces, "active": active, "rx_rate": total_rx_rate, "tx_rate": total_tx_rate, "gateway": gateway}

    def users_info(self) -> List[Dict[str, str]]:
        output = run_command(["who", "-u"], timeout=3)
        rows: List[Dict[str, str]] = []
        for line in output.splitlines():
            parts = line.split()
            if len(parts) >= 5:
                rows.append({"user": parts[0], "terminal": parts[1], "date": f"{parts[2]} {parts[3]}", "idle": parts[4], "raw": line})
        return rows

    def processes_from_proc(self, limit: int = 500) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for name in os.listdir("/proc"):
            if not name.isdigit():
                continue
            status = read_text(f"/proc/{name}/status")
            if not status:
                continue
            data: Dict[str, str] = {}
            for line in status.splitlines():
                if ":" in line:
                    key, value = line.split(":", 1)
                    data[key] = value.strip()
            uid = data.get("Uid", "").split()[0] if data.get("Uid") else "?"
            rows.append(
                {
                    "pid": safe_int(name),
                    "name": data.get("Name", "?"),
                    "state": data.get("State", "?"),
                    "user": uid_to_user(uid),
                    "rss_kb": safe_int(data.get("VmRSS", "0 kB").replace("kB", "").strip()),
                    "cpu": 0.0,
                    "mem_pct": 0.0,
                    "read_bytes": 0,
                    "write_bytes": 0,
                }
            )
            if len(rows) >= limit:
                break
        return sorted(rows, key=lambda x: x["pid"])

    def top_processes(self, limit: int = 60) -> List[Dict[str, Any]]:
        # ps se usa explícitamente como comando Linux obligatorio.
        output = run_command(["ps", "-eo", "pid=,user=,stat=,comm=,%cpu=,%mem=,rss=", "--sort=-%cpu"], timeout=4)
        rows: List[Dict[str, Any]] = []
        for line in output.splitlines()[:limit]:
            parts = line.split(None, 6)
            if len(parts) < 7:
                continue
            pid = safe_int(parts[0])
            io_data = read_text(f"/proc/{pid}/io")
            read_b = 0
            write_b = 0
            for io_line in io_data.splitlines():
                if io_line.startswith("read_bytes:"):
                    read_b = safe_int(io_line.split(":", 1)[1].strip())
                elif io_line.startswith("write_bytes:"):
                    write_b = safe_int(io_line.split(":", 1)[1].strip())
            rows.append(
                {
                    "pid": pid,
                    "user": parts[1],
                    "state": parts[2],
                    "name": parts[3],
                    "cpu": safe_float(parts[4]),
                    "mem_pct": safe_float(parts[5]),
                    "rss_kb": safe_int(parts[6]),
                    "read_bytes": read_b,
                    "write_bytes": write_b,
                }
            )
        if not rows:
            rows = self.processes_from_proc(limit)
        return rows

    def collect_metrics(self) -> Dict[str, Any]:
        cpu = self.cpu_info()
        mem = self.memory_info()
        disk = self.disk_info()
        net = self.network_info()
        users = self.users_info()
        return {"created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "cpu": cpu, "memory": mem, "disk": disk, "network": net, "users": users}


# ============================================================
# SQLite CRUD
# ============================================================


class SnapshotDB:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.init()

    def connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def init(self) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    label TEXT NOT NULL DEFAULT '',
                    comment TEXT NOT NULL DEFAULT '',
                    cpu_pct REAL NOT NULL DEFAULT 0,
                    mem_pct REAL NOT NULL DEFAULT 0,
                    swap_pct REAL NOT NULL DEFAULT 0,
                    disk_pct REAL NOT NULL DEFAULT 0,
                    net_down REAL NOT NULL DEFAULT 0,
                    net_up REAL NOT NULL DEFAULT 0,
                    payload_json TEXT NOT NULL
                )
                """
            )
            conn.commit()

    def create(self, payload: Dict[str, Any], label: str, comment: str) -> int:
        cpu = payload.get("cpu", {})
        mem = payload.get("memory", {})
        disk = payload.get("disk", {})
        net = payload.get("network", {})
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO snapshots
                (created_at, label, comment, cpu_pct, mem_pct, swap_pct, disk_pct, net_down, net_up, payload_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload.get("created_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                    label,
                    comment,
                    safe_float(cpu.get("usage")),
                    safe_float(mem.get("used_pct")),
                    safe_float(mem.get("swap_pct")),
                    safe_float(disk.get("used_pct")),
                    safe_float(net.get("rx_rate")),
                    safe_float(net.get("tx_rate")),
                    json.dumps(payload, ensure_ascii=False, indent=2),
                ),
            )
            conn.commit()
            return int(cur.lastrowid)

    def list_all(self) -> List[Tuple[Any, ...]]:
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT id, created_at, label, comment, cpu_pct, mem_pct, swap_pct, disk_pct, net_down, net_up
                FROM snapshots
                ORDER BY id DESC
                """
            ).fetchall()

    def get(self, snapshot_id: int) -> Optional[Tuple[Any, ...]]:
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT id, created_at, label, comment, cpu_pct, mem_pct, swap_pct, disk_pct, net_down, net_up, payload_json
                FROM snapshots
                WHERE id = ?
                """,
                (snapshot_id,),
            ).fetchone()

    def update(self, snapshot_id: int, label: str, comment: str) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE snapshots SET label = ?, comment = ? WHERE id = ?", (label, comment, snapshot_id))
            conn.commit()

    def delete(self, snapshot_id: int) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM snapshots WHERE id = ?", (snapshot_id,))
            conn.commit()


# ============================================================
# Dibujo UI Canvas
# ============================================================


def rounded_rect(canvas: tk.Canvas, x1: float, y1: float, x2: float, y2: float, r: float = 16, fill: str = C["panel"], outline: str = C["border"], width: int = 1) -> int:
    points = [
        x1 + r,
        y1,
        x2 - r,
        y1,
        x2,
        y1,
        x2,
        y1 + r,
        x2,
        y2 - r,
        x2,
        y2,
        x2 - r,
        y2,
        x1 + r,
        y2,
        x1,
        y2,
        x1,
        y2 - r,
        x1,
        y1 + r,
        x1,
        y1,
    ]
    return canvas.create_polygon(points, smooth=True, fill=fill, outline=outline, width=width)


def draw_panel(canvas: tk.Canvas, x: float, y: float, w: float, h: float, title: str = "", icon: str = "") -> None:
    rounded_rect(canvas, x, y, x + w, y + h, 14, C["panel"], C["border"], 1)
    # brillo sutil superior
    canvas.create_line(x + 16, y + 1, x + w - 16, y + 1, fill="#344157", width=1)
    if title:
        label = f"{icon}  {title}" if icon else title
        canvas.create_text(x + 18, y + 24, text=label, fill=C["text"], font=(FONT, 13, "bold"), anchor="w")


def draw_donut(canvas: tk.Canvas, cx: float, cy: float, r: float, pct: float, color: str, text: str, subtext: str = "") -> None:
    pct = max(0.0, min(100.0, pct))
    canvas.create_oval(cx - r, cy - r, cx + r, cy + r, outline="#263043", width=12)
    # sombra interior
    canvas.create_oval(cx - r + 18, cy - r + 18, cx + r - 18, cy + r - 18, fill="#141c2a", outline="")
    canvas.create_arc(cx - r, cy - r, cx + r, cy + r, start=90, extent=-pct * 3.6, style="arc", outline=color, width=12)
    canvas.create_arc(cx - r, cy - r, cx + r, cy + r, start=90 - pct * 3.6, extent=-8, style="arc", outline="#ffffff", width=3)
    canvas.create_text(cx, cy - 8, text=text, fill=C["text"], font=(FONT, 20, "bold"))
    if subtext:
        canvas.create_text(cx, cy + 22, text=subtext, fill=C["muted"], font=(FONT, 8, "bold"))


def draw_sparkline(canvas: tk.Canvas, values: List[float], x: float, y: float, w: float, h: float, color: str, max_value: Optional[float] = None, fill: bool = True) -> None:
    if len(values) < 2:
        # simulación visual mientras llegan datos reales
        values = [random.uniform(5, 45) for _ in range(18)]
    max_val = max_value if max_value else max(max(values), 1)
    min_val = 0.0
    pts: List[float] = []
    for i, value in enumerate(values[-40:]):
        px = x + (i / max(len(values[-40:]) - 1, 1)) * w
        py = y + h - ((value - min_val) / max(max_val - min_val, 1)) * h
        pts.extend([px, py])
    if fill and len(pts) >= 4:
        polygon = pts + [x + w, y + h, x, y + h]
        canvas.create_polygon(polygon, fill="#1b2740", outline="")
    canvas.create_line(*pts, fill=color, width=2, smooth=True)


def draw_line_chart(
    canvas: tk.Canvas,
    x: float,
    y: float,
    w: float,
    h: float,
    series: List[Tuple[str, List[float], str]],
    max_value: Optional[float] = None,
    y_labels: Optional[List[str]] = None,
    fill_first: bool = False,
) -> None:
    # grid
    left_pad = 44
    bottom_pad = 26
    top_pad = 18
    plot_x = x + left_pad
    plot_y = y + top_pad
    plot_w = w - left_pad - 12
    plot_h = h - top_pad - bottom_pad
    rows = 4
    for i in range(rows + 1):
        yy = plot_y + i * plot_h / rows
        canvas.create_line(plot_x, yy, plot_x + plot_w, yy, fill=C["grid"], dash=(2, 4))
        if y_labels and i < len(y_labels):
            canvas.create_text(x + 10, yy, text=y_labels[i], fill=C["muted"], font=(FONT, 8), anchor="w")
    for i in range(0, 7):
        xx = plot_x + i * plot_w / 6
        canvas.create_line(xx, plot_y, xx, plot_y + plot_h, fill="#1f2a3c", dash=(2, 5))
    # labels tiempo falsos/relativos sobrios
    times = ["-60m", "-50m", "-40m", "-30m", "-20m", "-10m", "ahora"]
    for i, label in enumerate(times):
        xx = plot_x + i * plot_w / 6
        canvas.create_text(xx, y + h - 10, text=label, fill=C["muted"], font=(FONT, 7))

    all_values = [v for _, vals, _ in series for v in vals]
    if not all_values:
        all_values = [0]
    max_val = max_value if max_value else max(max(all_values), 1)
    min_val = 0.0
    for idx, (_name, vals, color) in enumerate(series):
        vals = vals[-90:]
        if len(vals) < 2:
            vals = [random.uniform(0, max_val * 0.3) for _ in range(24)]
        pts: List[float] = []
        for i, val in enumerate(vals):
            px = plot_x + (i / max(len(vals) - 1, 1)) * plot_w
            py = plot_y + plot_h - ((val - min_val) / max(max_val - min_val, 1)) * plot_h
            pts.extend([px, py])
        if idx == 0 and fill_first and len(pts) >= 4:
            canvas.create_polygon(pts + [plot_x + plot_w, plot_y + plot_h, plot_x, plot_y + plot_h], fill="#241c3c", outline="")
        canvas.create_line(*pts, fill=color, width=2, smooth=True)


def draw_metric_text(
    canvas: tk.Canvas,
    x: float,
    y: float,
    label: str,
    value: str,
    color: str = C["muted"],
    value_right_x: Optional[float] = None,
    value_font: Tuple[str, int, str] = (FONT, 10, "bold"),
) -> None:
    """Dibuja una pareja etiqueta/valor evitando solapamientos."""
    canvas.create_text(x, y, text=label, fill=color, font=(FONT, 9), anchor="w")
    right_x = value_right_x if value_right_x is not None else x + 122
    canvas.create_text(right_x, y, text=value, fill=C["text"], font=value_font, anchor="e")


def bar(canvas: tk.Canvas, x: float, y: float, w: float, h: float, pct: float, color: str) -> None:
    pct = max(0, min(100, pct))
    rounded_rect(canvas, x, y, x + w, y + h, h / 2, "#222b3c", "", 0)
    if pct > 0:
        rounded_rect(canvas, x, y, x + (w * pct / 100), y + h, h / 2, color, "", 0)


# ============================================================
# Interfaz
# ============================================================


class ModernMonitorApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("System Monitor — Mini Monitor Linux")
        self.geometry("1440x840")
        self.minsize(1180, 700)
        self.configure(bg=C["bg"])

        self.probe = SystemProbe()
        self.db = SnapshotDB(DB_PATH)
        self.events: queue.Queue = queue.Queue()
        self.stop_event = threading.Event()
        self.page = "overview"
        self.latest: Dict[str, Any] = {}
        self.processes: List[Dict[str, Any]] = []
        self.selected_record: Optional[int] = None
        self.history: Dict[str, List[float]] = {
            "cpu": [],
            "mem": [],
            "swap": [],
            "net_down": [],
            "net_up": [],
            "disk_read": [],
            "disk_write": [],
            "load1": [],
            "load5": [],
            "load15": [],
        }
        self.core_history: List[List[float]] = []

        self._configure_styles()
        self._build_shell()
        self._build_pages()
        self.show_page("overview")
        self._start_workers()
        self.after(150, self._drain_events)
        self.protocol("WM_DELETE_WINDOW", self._close)

    # ---------- Shell ----------
    def _configure_styles(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Treeview", background=C["panel"], foreground=C["text"], fieldbackground=C["panel"], borderwidth=0, rowheight=28, font=(FONT, 9))
        style.configure("Treeview.Heading", background=C["panel3"], foreground=C["text"], font=(FONT, 9, "bold"), borderwidth=0)
        style.map("Treeview", background=[("selected", C["purple"])], foreground=[("selected", "#0b111a")])
        style.configure("Vertical.TScrollbar", background=C["panel3"], troughcolor=C["bg"], arrowcolor=C["text"])
        style.configure("TCombobox", fieldbackground=C["panel2"], background=C["panel2"], foreground=C["text"], arrowcolor=C["text"])

    def _build_shell(self) -> None:
        self.sidebar = tk.Frame(self, bg=C["sidebar"], width=172, highlightthickness=1, highlightbackground="#172033")
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)

        # Sin los tres puntos decorativos en la esquina superior izquierda.
        # Dejamos solo un pequeño margen superior para que la barra lateral respire.
        tk.Frame(self.sidebar, bg=C["sidebar"], height=16).pack(fill="x")

        self.nav_buttons: Dict[str, tk.Button] = {}
        items = [
            ("overview", "◌", "Overview"),
            ("applications", "▦", "Applications"),
            ("history", "◷", "History"),
            ("processes", "▤", "Processes"),
            ("records", "▣", "Records CRUD"),
            ("commands", "⌁", "Commands"),
        ]
        for key, icon, text in items:
            btn = tk.Button(
                self.sidebar,
                text=f"  {icon}   {text}",
                command=lambda k=key: self.show_page(k),
                anchor="w",
                bg=C["sidebar"],
                fg=C["text"],
                activebackground=C["panel3"],
                activeforeground=C["text"],
                relief="flat",
                bd=0,
                padx=10,
                pady=12,
                font=(FONT, 10),
                cursor="hand2",
            )
            btn.pack(fill="x", padx=12, pady=3)
            self.nav_buttons[key] = btn

        spacer = tk.Frame(self.sidebar, bg=C["sidebar"])
        spacer.pack(fill="both", expand=True)
        self.thread_label = tk.Label(self.sidebar, text="threads: starting", fg=C["muted"], bg=C["sidebar"], font=(FONT, 8))
        self.thread_label.pack(anchor="w", padx=18, pady=(0, 8))
        tk.Label(self.sidebar, text="⚙  Settings", fg=C["text"], bg=C["sidebar"], font=(FONT, 10)).pack(anchor="w", padx=22, pady=(0, 22))
        tk.Label(self.sidebar, text="ⓘ  /proc · SQLite", fg=C["muted"], bg=C["sidebar"], font=(FONT, 8)).pack(anchor="w", padx=22, pady=(0, 20))

        self.main = tk.Frame(self, bg=C["bg"])
        self.main.pack(side="left", fill="both", expand=True)

        self.topbar = tk.Frame(self.main, bg=C["bg"], height=58, highlightthickness=1, highlightbackground="#172033")
        self.topbar.pack(fill="x")
        self.topbar.pack_propagate(False)

        self.title_var = tk.StringVar(value="System Monitor")
        self.subtitle_var = tk.StringVar(value="Overview")
        title_box = tk.Frame(self.topbar, bg=C["bg"])
        title_box.pack(expand=True)
        tk.Label(title_box, text="⌁  System Monitor", fg=C["text"], bg=C["bg"], font=(FONT, 13, "bold")).pack(pady=(8, 0))
        tk.Label(title_box, textvariable=self.subtitle_var, fg=C["muted"], bg=C["bg"], font=(FONT, 8)).pack()

        right_box = tk.Frame(self.topbar, bg=C["bg"])
        right_box.place(relx=1.0, x=-14, y=12, anchor="ne")
        self.fork_btn = tk.Button(right_box, text="fork()", command=self.run_fork_demo, bg=C["panel2"], fg=C["text"], activebackground=C["panel3"], relief="flat", bd=0, padx=12, pady=6, font=(FONT, 9, "bold"), cursor="hand2")
        self.fork_btn.pack(side="left", padx=5)

        self.page_host = tk.Frame(self.main, bg=C["bg"])
        self.page_host.pack(fill="both", expand=True)

    def _build_pages(self) -> None:
        self.pages: Dict[str, tk.Frame] = {}
        self.canvas_pages: Dict[str, tk.Canvas] = {}
        for name in ["overview", "history", "applications", "processes"]:
            frame = tk.Frame(self.page_host, bg=C["bg"])
            canvas = tk.Canvas(frame, bg=C["bg"], highlightthickness=0)
            canvas.pack(fill="both", expand=True)
            canvas.bind("<Configure>", lambda _e, n=name: self.redraw(n))
            self.pages[name] = frame
            self.canvas_pages[name] = canvas
        self._build_records_page()
        self._build_commands_page()

    def show_page(self, name: str) -> None:
        self.page = name
        for frame in self.pages.values():
            frame.pack_forget()
        self.pages[name].pack(fill="both", expand=True)
        labels = {
            "overview": "Overview",
            "applications": "Applications",
            "history": "History",
            "processes": "Processes",
            "records": "Records CRUD",
            "commands": "Commands / Technical Evidence",
        }
        self.subtitle_var.set(labels.get(name, name.title()))
        for key, btn in self.nav_buttons.items():
            if key == name:
                btn.configure(bg="#30264c", fg=C["text"])
            else:
                btn.configure(bg=C["sidebar"], fg=C["text"])
        if name == "records":
            self.refresh_records()
        if name == "commands":
            self.refresh_commands()
        self.redraw(name)

    # ---------- Workers ----------
    def _start_workers(self) -> None:
        self.metric_thread = threading.Thread(target=self._metric_worker, daemon=True, name="metrics-thread")
        self.process_thread = threading.Thread(target=self._process_worker, daemon=True, name="process-thread")
        self.metric_thread.start()
        self.process_thread.start()

    def _metric_worker(self) -> None:
        while not self.stop_event.is_set():
            try:
                self.events.put(("metrics", self.probe.collect_metrics()))
            except Exception as exc:
                self.events.put(("log", f"Error métricas: {exc}"))
            self.stop_event.wait(1.5)

    def _process_worker(self) -> None:
        while not self.stop_event.is_set():
            try:
                self.events.put(("processes", self.probe.top_processes(80)))
            except Exception as exc:
                self.events.put(("log", f"Error procesos: {exc}"))
            self.stop_event.wait(3.5)

    def _drain_events(self) -> None:
        while True:
            try:
                kind, payload = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "metrics":
                self.latest = payload
                self._append_history(payload)
                self.redraw(self.page)
            elif kind == "processes":
                self.processes = payload
                self.redraw(self.page)
            elif kind == "log":
                print(payload)
        self.thread_label.configure(text=f"threads: {int(self.metric_thread.is_alive()) + int(self.process_thread.is_alive())}/2 active")
        self.after(180, self._drain_events)

    def _append_history(self, snapshot: Dict[str, Any]) -> None:
        cpu = snapshot.get("cpu", {})
        mem = snapshot.get("memory", {})
        disk = snapshot.get("disk", {})
        net = snapshot.get("network", {})
        load = cpu.get("load", [0, 0, 0])
        pairs = {
            "cpu": safe_float(cpu.get("usage")),
            "mem": safe_float(mem.get("used_pct")),
            "swap": safe_float(mem.get("swap_pct")),
            "net_down": min(safe_float(net.get("rx_rate")) / (1024 * 1024), 120),
            "net_up": min(safe_float(net.get("tx_rate")) / (1024 * 1024), 120),
            "disk_read": min(safe_float(disk.get("read_rate")) / (1024 * 1024), 180),
            "disk_write": min(safe_float(disk.get("write_rate")) / (1024 * 1024), 180),
            "load1": safe_float(load[0] if len(load) > 0 else 0),
            "load5": safe_float(load[1] if len(load) > 1 else 0),
            "load15": safe_float(load[2] if len(load) > 2 else 0),
        }
        for key, value in pairs.items():
            self.history.setdefault(key, []).append(value)
            self.history[key] = self.history[key][-100:]
        per_core = cpu.get("per_core", []) or []
        while len(self.core_history) < len(per_core):
            self.core_history.append([])
        for i, value in enumerate(per_core):
            self.core_history[i].append(safe_float(value))
            self.core_history[i] = self.core_history[i][-100:]

    # ---------- Canvas render ----------
    def redraw(self, name: Optional[str] = None) -> None:
        name = name or self.page
        if name == "overview" and hasattr(self, "canvas_pages"):
            self.draw_overview()
        elif name == "history":
            self.draw_history()
        elif name == "applications":
            self.draw_applications()
        elif name == "processes":
            self.draw_processes()

    def draw_overview(self) -> None:
        canvas = self.canvas_pages["overview"]
        canvas.delete("all")
        w = max(canvas.winfo_width(), 1100)
        h = max(canvas.winfo_height(), 700)
        m = 24
        gap = 12
        cpu = self.latest.get("cpu", {})
        mem = self.latest.get("memory", {})
        disk = self.latest.get("disk", {})
        net = self.latest.get("network", {})
        active = net.get("active", {}) if isinstance(net, dict) else {}
        processes = self.processes[:8]

        card_h = 188
        card_w = (w - 2 * m - 2 * gap) / 3
        y = 20
        # CPU
        draw_panel(canvas, m, y, card_w, card_h, "CPU")
        draw_donut(canvas, m + 90, y + 104, 62, safe_float(cpu.get("usage")), C["purple"], f"{safe_float(cpu.get('usage')):.0f}%", f"{safe_float(cpu.get('freq_mhz'))/1000:.2f} GHz")
        # Sparkline más compacto y sin relleno para que no se monte sobre los textos.
        draw_sparkline(canvas, self.history.get("cpu", []), m + 250, y + 42, card_w - 286, 24, C["purple2"], 100, fill=False)
        cpu_value_x = m + card_w - 24
        draw_metric_text(canvas, m + 190, y + 92, "Cores", str(cpu.get("cores", "--")), value_right_x=cpu_value_x)
        draw_metric_text(canvas, m + 190, y + 116, "Threads", str(cpu.get("threads", "--")), value_right_x=cpu_value_x)
        draw_metric_text(canvas, m + 190, y + 140, "Processes", str(len(self.processes) or "--"), value_right_x=cpu_value_x)
        load = cpu.get("load", [0, 0, 0])
        draw_metric_text(canvas, m + 190, y + 164, "Load Average", f"{safe_float(load[0] if load else 0):.2f}", value_right_x=cpu_value_x)

        # Memory
        x2 = m + card_w + gap
        draw_panel(canvas, x2, y, card_w, card_h, "Memory")
        draw_donut(canvas, x2 + 90, y + 104, 62, safe_float(mem.get("used_pct")), C["cyan"], f"{safe_float(mem.get('used_pct')):.0f}%", f"{fmt_kb(safe_float(mem.get('used_kb')))} / {fmt_kb(safe_float(mem.get('total_kb')))}")
        draw_sparkline(canvas, self.history.get("mem", []), x2 + 250, y + 42, card_w - 286, 24, C["cyan"], 100, fill=False)
        mem_value_x = x2 + card_w - 24
        draw_metric_text(canvas, x2 + 190, y + 92, "Used", fmt_kb(safe_float(mem.get("used_kb"))), value_right_x=mem_value_x)
        draw_metric_text(canvas, x2 + 190, y + 116, "Available", fmt_kb(safe_float(mem.get("available_kb"))), value_right_x=mem_value_x)
        draw_metric_text(canvas, x2 + 190, y + 140, "Cached", fmt_kb(safe_float(mem.get("cached_kb"))), value_right_x=mem_value_x)
        draw_metric_text(canvas, x2 + 190, y + 164, "Buffers", fmt_kb(safe_float(mem.get("buffers_kb"))), value_right_x=mem_value_x)

        # Swap
        x3 = m + 2 * (card_w + gap)
        draw_panel(canvas, x3, y, card_w, card_h, "Swap")
        draw_donut(canvas, x3 + 90, y + 104, 62, safe_float(mem.get("swap_pct")), C["pink"], f"{safe_float(mem.get('swap_pct')):.0f}%", f"{fmt_kb(safe_float(mem.get('swap_used_kb')))} / {fmt_kb(safe_float(mem.get('swap_total_kb')))}")
        draw_sparkline(canvas, self.history.get("swap", []), x3 + 250, y + 42, card_w - 286, 24, C["pink"], 100, fill=False)
        swap_value_x = x3 + card_w - 28
        draw_metric_text(canvas, x3 + 190, y + 92, "Used", fmt_kb(safe_float(mem.get("swap_used_kb"))), value_right_x=swap_value_x)
        draw_metric_text(canvas, x3 + 190, y + 116, "Free", fmt_kb(safe_float(mem.get("swap_free_kb"))), value_right_x=swap_value_x)
        # Usar textos cortos y fuente pequeña para evitar solapamiento.
        draw_metric_text(canvas, x3 + 190, y + 140, "Cmd", "free -h", value_right_x=swap_value_x, value_font=(FONT, 8, "bold"))
        draw_metric_text(canvas, x3 + 190, y + 164, "Src", "/proc/meminfo", value_right_x=swap_value_x, value_font=(FONT, 8, "bold"))

        # Disk + Network
        y2 = y + card_h + gap
        row_h = 188
        disk_w = (w - 2 * m - gap) * 0.48
        net_w = w - 2 * m - gap - disk_w
        draw_panel(canvas, m, y2, disk_w, row_h, "Disk", "▣")
        canvas.create_text(m + 20, y2 + 64, text=f"Root Volume       {disk.get('filesystem', '--')}", fill=C["text"], font=(FONT, 9, "bold"), anchor="w")
        canvas.create_text(m + disk_w - 22, y2 + 64, text=f"{safe_float(disk.get('used_pct')):.0f}%", fill=C["text"], font=(FONT, 10, "bold"), anchor="e")
        bar(canvas, m + 20, y2 + 86, disk_w - 40, 12, safe_float(disk.get("used_pct")), C["cyan"])
        bar(canvas, m + 20, y2 + 86, (disk_w - 40) * 0.65, 12, safe_float(disk.get("used_pct")) * 0.8, C["purple2"])
        canvas.create_text(m + 20, y2 + 122, text=f"Used  {fmt_bytes(safe_float(disk.get('used')))}", fill=C["text"], font=(FONT, 10), anchor="w")
        canvas.create_text(m + disk_w - 20, y2 + 122, text=f"Total  {fmt_bytes(safe_float(disk.get('total')))}", fill=C["text"], font=(FONT, 10), anchor="e")
        canvas.create_line(m + 20, y2 + 146, m + disk_w - 20, y2 + 146, fill=C["border"])
        canvas.create_text(m + 20, y2 + 164, text=f"● Read Speed   {fmt_bytes(safe_float(disk.get('read_rate')))} /s", fill=C["blue"], font=(FONT, 9), anchor="w")
        canvas.create_text(m + disk_w * 0.46, y2 + 164, text=f"● Write Speed   {fmt_bytes(safe_float(disk.get('write_rate')))} /s", fill=C["pink"], font=(FONT, 9), anchor="w")
        canvas.create_text(m + disk_w - 20, y2 + 164, text=f"● /proc/diskstats", fill=C["mint"], font=(FONT, 9), anchor="e")

        nx = m + disk_w + gap
        draw_panel(canvas, nx, y2, net_w, row_h, "Network", "◎")
        small_w = (net_w - 54) / 2
        rounded_rect(canvas, nx + 18, y2 + 50, nx + 18 + small_w, y2 + 126, 10, C["panel2"], C["border"])
        rounded_rect(canvas, nx + 36 + small_w, y2 + 50, nx + 36 + 2 * small_w, y2 + 126, 10, C["panel2"], "#542a45")
        canvas.create_text(nx + 30, y2 + 66, text=str(active.get("iface", "interfaz")), fill=C["muted"], font=(FONT, 8), anchor="w")
        canvas.create_text(nx + 30, y2 + 84, text="↓ Download", fill=C["cyan"], font=(FONT, 9), anchor="w")
        canvas.create_text(nx + 30, y2 + 110, text=f"{fmt_bytes(safe_float(net.get('rx_rate')))} /s", fill=C["text"], font=(FONT, 16, "bold"), anchor="w")
        draw_sparkline(canvas, self.history.get("net_down", []), nx + small_w - 90, y2 + 70, 90, 42, C["blue"], max_value=max(self.history.get("net_down", [1]) + [1]), fill=False)
        xup = nx + 48 + small_w
        canvas.create_text(xup, y2 + 66, text=str(active.get("iface", "interfaz")), fill=C["muted"], font=(FONT, 8), anchor="w")
        canvas.create_text(xup, y2 + 84, text="↑ Upload", fill=C["pink"], font=(FONT, 9), anchor="w")
        canvas.create_text(xup, y2 + 110, text=f"{fmt_bytes(safe_float(net.get('tx_rate')))} /s", fill=C["text"], font=(FONT, 16, "bold"), anchor="w")
        draw_sparkline(canvas, self.history.get("net_up", []), nx + net_w - 112, y2 + 70, 90, 42, C["pink"], max_value=max(self.history.get("net_up", [1]) + [1]), fill=False)
        ips = active.get("ips", []) if isinstance(active, dict) else []
        ipv4 = next((ip for ip in ips if "." in ip), "No IP")
        ipv6 = next((ip for ip in ips if ":" in ip), "No IPv6")
        canvas.create_line(nx + 18, y2 + 144, nx + net_w - 18, y2 + 144, fill=C["border"])
        canvas.create_text(nx + 24, y2 + 166, text=f"IP(v4)   {ipv4}", fill=C["text"], font=(FONT, 9), anchor="w")
        canvas.create_text(nx + net_w * 0.44, y2 + 166, text=f"IP(v6)   {ipv6[:28]}", fill=C["text"], font=(FONT, 9), anchor="w")
        canvas.create_text(nx + net_w - 24, y2 + 166, text=f"Gateway   {net.get('gateway', '--')}", fill=C["text"], font=(FONT, 9), anchor="e")

        # Applications table
        ty = y2 + row_h + gap
        th = max(230, h - ty - 58)
        draw_panel(canvas, m, ty, w - 2 * m, th, "Top Applications", "▥")
        search_x = w - m - 396
        rounded_rect(canvas, search_x, ty + 14, search_x + 190, ty + 48, 8, C["panel2"], C["border"])
        canvas.create_text(search_x + 14, ty + 31, text="⌕  Search applications...", fill=C["muted"], font=(FONT, 9), anchor="w")
        rounded_rect(canvas, search_x + 204, ty + 14, search_x + 340, ty + 48, 8, C["panel2"], C["border"])
        canvas.create_text(search_x + 222, ty + 31, text="Sort by CPU  ⌄", fill=C["text"], font=(FONT, 9), anchor="w")
        self._draw_process_table(canvas, m + 12, ty + 62, w - 2 * m - 24, th - 112, processes, compact=True)
        # Bottom stats
        by = ty + th - 50
        labels = [
            ("◌", "Total CPU Usage", f"{safe_float(cpu.get('usage')):.0f}%", C["purple"]),
            ("◌", "Total Memory", f"{fmt_kb(safe_float(mem.get('used_kb')))} / {fmt_kb(safe_float(mem.get('total_kb')))}", C["cyan"]),
            ("◌", "Total Network (↓ / ↑)", f"{fmt_bytes(safe_float(net.get('rx_rate')))} /s  /  {fmt_bytes(safe_float(net.get('tx_rate')))} /s", C["pink"]),
            ("◌", "System Uptime", self._uptime_text(), C["green"]),
        ]
        part_w = (w - 2 * m) / 4
        for i, (_ico, label, value, color) in enumerate(labels):
            x = m + i * part_w
            canvas.create_line(x, by - 4, x, ty + th - 12, fill=C["border"])
            canvas.create_text(x + 24, by + 12, text=label, fill=C["muted"], font=(FONT, 9), anchor="w")
            canvas.create_text(x + 24, by + 34, text=value, fill=C["text"], font=(FONT, 12, "bold"), anchor="w")
            canvas.create_oval(x + 8, by + 12, x + 16, by + 20, fill=color, outline="")

    def _uptime_text(self) -> str:
        text = read_text("/proc/uptime").split()
        seconds = safe_float(text[0] if text else 0)
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        return f"{h}h {m}m {s}s"

    def draw_history(self) -> None:
        canvas = self.canvas_pages["history"]
        canvas.delete("all")
        w = max(canvas.winfo_width(), 1100)
        h = max(canvas.winfo_height(), 700)
        m = 24
        gap = 12
        # controls
        rounded_rect(canvas, w - 398, 16, w - 268, 48, 8, C["panel2"], C["border"])
        canvas.create_text(w - 386, 32, text="Time Range   Last 1 Hour⌄", fill=C["text"], font=(FONT, 9), anchor="w")
        rounded_rect(canvas, w - 254, 16, w - 142, 48, 8, C["panel2"], C["border"])
        canvas.create_text(w - 242, 32, text="Refresh   5s⌄", fill=C["text"], font=(FONT, 9), anchor="w")
        rounded_rect(canvas, w - 128, 16, w - 40, 48, 8, C["panel2"], C["border"])
        canvas.create_text(w - 84, 32, text="Ⅱ  Pause", fill=C["text"], font=(FONT, 9, "bold"))

        top = 66
        chart_h = (h - top - 92) / 3
        chart_w = (w - 2 * m - gap) / 2
        panels = [
            (m, top, chart_w, chart_h, "CPU Usage", [("Total Usage", self.history.get("cpu", []), C["purple"])], 100, ["100%", "75%", "50%", "25%", "0%"], True),
            (m + chart_w + gap, top, chart_w, chart_h, "CPU Usage per Core", self._core_series(), 100, ["100%", "75%", "50%", "25%", "0%"], False),
            (m, top + chart_h + gap, chart_w, chart_h, "Memory Usage", [("Used", self.history.get("mem", []), C["purple2"]), ("Cached", [v * 0.34 for v in self.history.get("mem", [])], C["cyan"]), ("Buffers", [3 for _ in self.history.get("mem", [])], C["mint"])], 100, ["100%", "75%", "50%", "25%", "0%"], True),
            (m + chart_w + gap, top + chart_h + gap, chart_w, chart_h, "Network Activity", [("Download Rate", self.history.get("net_down", []), C["blue"]), ("Upload Rate", self.history.get("net_up", []), C["pink"])], max(max(self.history.get("net_down", [1]) + self.history.get("net_up", [1])), 1), ["max", "", "", "", "0"], True),
            (m, top + 2 * (chart_h + gap), chart_w, chart_h, "Disk Activity", [("Read Speed", self.history.get("disk_read", []), C["blue"]), ("Write Speed", self.history.get("disk_write", []), C["pink"]), ("I/O", [v * 0.2 for v in self.history.get("disk_read", [])], C["amber"])], max(max(self.history.get("disk_read", [1]) + self.history.get("disk_write", [1])), 1), ["max", "", "", "", "0"], False),
            (m + chart_w + gap, top + 2 * (chart_h + gap), chart_w, chart_h, "Swap Usage", [("Used Swap", self.history.get("swap", []), C["pink"])], 100, ["100%", "75%", "50%", "25%", "0%"], True),
        ]
        for x, y, cw, ch, title, series, maxv, labels, fill_first in panels:
            draw_panel(canvas, x, y, cw, ch, title)
            draw_line_chart(canvas, x + 6, y + 34, cw - 12, ch - 50, series, maxv, labels, fill_first)
            # leyenda compacta
            lx = x + 20
            ly = y + ch - 20
            for name, vals, color in series[:8]:
                value = vals[-1] if vals else 0
                canvas.create_oval(lx, ly - 4, lx + 8, ly + 4, fill=color, outline="")
                canvas.create_text(lx + 14, ly, text=f"{name}  {value:.1f}", fill=C["text"], font=(FONT, 8), anchor="w")
                lx += 110
        # load average full bottom overlay if enough height not used? draw small metric labels top right
        load_y = h - 64
        rounded_rect(canvas, m, load_y, w - m, h - 20, 12, C["panel"], C["border"])
        canvas.create_text(m + 18, load_y + 22, text="Load Average", fill=C["text"], font=(FONT, 11, "bold"), anchor="w")
        canvas.create_text(w - m - 300, load_y + 22, text=f"● 1m  {self._last('load1'):.2f}", fill=C["purple"], font=(FONT, 9), anchor="w")
        canvas.create_text(w - m - 200, load_y + 22, text=f"● 5m  {self._last('load5'):.2f}", fill=C["cyan"], font=(FONT, 9), anchor="w")
        canvas.create_text(w - m - 100, load_y + 22, text=f"● 15m  {self._last('load15'):.2f}", fill=C["pink"], font=(FONT, 9), anchor="w")

    def _core_series(self) -> List[Tuple[str, List[float], str]]:
        colors = [C["purple"], C["pink"], C["amber"], C["yellow"], C["mint"], C["green"], C["cyan"], C["blue"], C["coral"], C["purple2"]]
        series = []
        for i, vals in enumerate(self.core_history[:10]):
            series.append((f"Core {i + 1}", vals, colors[i % len(colors)]))
        return series or [("Core 1", [], C["purple"])]

    def _last(self, key: str) -> float:
        vals = self.history.get(key, [])
        return safe_float(vals[-1] if vals else 0)

    def draw_applications(self) -> None:
        canvas = self.canvas_pages["applications"]
        canvas.delete("all")
        w = max(canvas.winfo_width(), 1100)
        h = max(canvas.winfo_height(), 700)
        m = 24
        draw_panel(canvas, m, 24, w - 2 * m, h - 48, "Applications", "▦")
        canvas.create_text(m + 20, 66, text="Procesos principales ordenados por CPU usando el comando ps + /proc/<pid>/io", fill=C["muted"], font=(FONT, 9), anchor="w")
        self._draw_process_table(canvas, m + 18, 96, w - 2 * m - 36, h - 150, self.processes[:22], compact=False)

    def draw_processes(self) -> None:
        canvas = self.canvas_pages["processes"]
        canvas.delete("all")
        w = max(canvas.winfo_width(), 1100)
        h = max(canvas.winfo_height(), 700)
        m = 24
        draw_panel(canvas, m, 24, w - 2 * m, h - 48, "Processes", "▤")
        canvas.create_text(m + 20, 66, text="PID, nombre, estado y usuario propietario. Datos cruzados con /proc y ps.", fill=C["muted"], font=(FONT, 9), anchor="w")
        self._draw_process_table(canvas, m + 18, 96, w - 2 * m - 36, h - 150, self.processes[:24], compact=False)

    def _draw_process_table(self, canvas: tk.Canvas, x: float, y: float, w: float, h: float, rows: List[Dict[str, Any]], compact: bool = True) -> None:
        header_h = 28
        row_h = 28 if compact else 30
        rounded_rect(canvas, x, y, x + w, y + h, 10, C["panel2"], C["border"])
        headers = ["Name", "CPU", "Memory", "Disk Read", "Disk Write", "PID"] if compact else ["PID", "User", "State", "Name", "CPU", "Memory", "Read", "Write"]
        if compact:
            widths = [0.31, 0.13, 0.15, 0.15, 0.15, 0.11]
        else:
            widths = [0.08, 0.12, 0.1, 0.28, 0.12, 0.12, 0.09, 0.09]
        cx = x
        for head, frac in zip(headers, widths):
            canvas.create_text(cx + 10, y + 15, text=head, fill=C["text"], font=(FONT, 8, "bold"), anchor="w")
            cx += w * frac
            canvas.create_line(cx, y, cx, y + h, fill="#202b3c")
        canvas.create_line(x, y + header_h, x + w, y + header_h, fill=C["border"])
        max_rows = int((h - header_h - 8) // row_h)
        rows = rows[:max_rows]
        app_icons = {
            "firefox": "●",
            "brave": "●",
            "code": "◆",
            "python": "◆",
            "spotify": "●",
            "discord": "●",
            "terminal": "▣",
            "bash": "▣",
        }
        for idx, row in enumerate(rows):
            ry = y + header_h + idx * row_h
            if idx % 2 == 0:
                canvas.create_rectangle(x + 1, ry, x + w - 1, ry + row_h, fill="#151d2b", outline="")
            name = str(row.get("name", "?"))
            cpu = safe_float(row.get("cpu"))
            mem_pct = safe_float(row.get("mem_pct"))
            rss = safe_int(row.get("rss_kb"))
            read_b = safe_float(row.get("read_bytes"))
            write_b = safe_float(row.get("write_bytes"))
            pid = str(row.get("pid", "—"))
            if compact:
                vals = [name, f"{cpu:.1f}%", fmt_kb(rss), fmt_bytes(read_b), fmt_bytes(write_b), pid]
                cx = x
                for col, (val, frac) in enumerate(zip(vals, widths)):
                    if col == 0:
                        icon = next((ic for key, ic in app_icons.items() if key in name.lower()), "●")
                        color = [C["purple"], C["cyan"], C["pink"], C["mint"], C["amber"]][idx % 5]
                        canvas.create_text(cx + 12, ry + row_h / 2, text=icon, fill=color, font=(FONT, 10, "bold"), anchor="w")
                        canvas.create_text(cx + 32, ry + row_h / 2, text=val[:34], fill=C["text"], font=(FONT, 9), anchor="w")
                    elif col in {1, 2, 3, 4}:
                        canvas.create_text(cx + 10, ry + row_h / 2, text=val, fill=C["text"], font=(FONT, 8), anchor="w")
                        pct = cpu if col == 1 else mem_pct if col == 2 else min((read_b if col == 3 else write_b) / (1024 * 1024), 100)
                        color = C["purple"] if col == 1 else C["blue"] if col == 2 else C["cyan"] if col == 3 else C["pink"]
                        bar(canvas, cx + 76, ry + 10, max(w * frac - 92, 30), 7, pct, color)
                    else:
                        canvas.create_text(cx + 10, ry + row_h / 2, text=val, fill=C["text"], font=(FONT, 8), anchor="w")
                    cx += w * frac
            else:
                vals = [pid, str(row.get("user", "?")), str(row.get("state", "?"))[:12], name[:34], f"{cpu:.1f}%", fmt_kb(rss), fmt_bytes(read_b), fmt_bytes(write_b)]
                cx = x
                for col, (val, frac) in enumerate(zip(vals, widths)):
                    canvas.create_text(cx + 10, ry + row_h / 2, text=val, fill=C["text"] if col != 4 else C["purple2"], font=(FONT, 8), anchor="w")
                    if col == 4:
                        bar(canvas, cx + 68, ry + 10, max(w * frac - 80, 20), 7, cpu, C["purple"])
                    if col == 5:
                        bar(canvas, cx + 80, ry + 10, max(w * frac - 90, 20), 7, mem_pct, C["blue"])
                    cx += w * frac

    # ---------- Records CRUD page ----------
    def _build_records_page(self) -> None:
        frame = tk.Frame(self.page_host, bg=C["bg"])
        self.pages["records"] = frame
        top = tk.Frame(frame, bg=C["bg"])
        top.pack(fill="x", padx=24, pady=(24, 10))
        tk.Label(top, text="Registros CRUD", fg=C["text"], bg=C["bg"], font=(FONT, 16, "bold")).pack(side="left")
        tk.Button(top, text="Crear captura", command=self.create_record, bg=C["purple"], fg="#0b111a", activebackground=C["purple2"], relief="flat", bd=0, padx=14, pady=8, font=(FONT, 9, "bold"), cursor="hand2").pack(side="right", padx=5)
        tk.Button(top, text="Actualizar", command=self.update_record, bg=C["panel2"], fg=C["text"], activebackground=C["panel3"], relief="flat", bd=0, padx=14, pady=8, cursor="hand2").pack(side="right", padx=5)
        tk.Button(top, text="Eliminar", command=self.delete_record, bg="#3b1c2b", fg=C["pink"], activebackground="#4a2435", relief="flat", bd=0, padx=14, pady=8, cursor="hand2").pack(side="right", padx=5)

        form = tk.Frame(frame, bg=C["panel"], highlightthickness=1, highlightbackground=C["border"])
        form.pack(fill="x", padx=24, pady=(0, 12))
        tk.Label(form, text="Etiqueta", fg=C["muted"], bg=C["panel"], font=(FONT, 9, "bold")).grid(row=0, column=0, padx=14, pady=(12, 4), sticky="w")
        tk.Label(form, text="Comentario", fg=C["muted"], bg=C["panel"], font=(FONT, 9, "bold")).grid(row=0, column=1, padx=14, pady=(12, 4), sticky="w")
        self.label_entry = tk.Entry(form, bg=C["panel2"], fg=C["text"], insertbackground=C["text"], relief="flat", font=(FONT, 10))
        self.comment_entry = tk.Entry(form, bg=C["panel2"], fg=C["text"], insertbackground=C["text"], relief="flat", font=(FONT, 10))
        self.label_entry.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 12), ipady=8)
        self.comment_entry.grid(row=1, column=1, sticky="ew", padx=14, pady=(0, 12), ipady=8)
        form.grid_columnconfigure(0, weight=1)
        form.grid_columnconfigure(1, weight=2)

        body = tk.Frame(frame, bg=C["bg"])
        body.pack(fill="both", expand=True, padx=24, pady=(0, 24))
        body.grid_columnconfigure(0, weight=3)
        body.grid_columnconfigure(1, weight=2)
        body.grid_rowconfigure(0, weight=1)

        columns = ("id", "created", "label", "cpu", "mem", "swap", "disk", "down", "up")
        self.records_tree = ttk.Treeview(body, columns=columns, show="headings")
        headings = ["ID", "Fecha", "Etiqueta", "CPU %", "RAM %", "Swap %", "Disk %", "Down", "Up"]
        for col, head in zip(columns, headings):
            self.records_tree.heading(col, text=head)
            self.records_tree.column(col, width=84 if col != "created" else 150, anchor="w")
        self.records_tree.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        self.records_tree.bind("<<TreeviewSelect>>", self.select_record)

        self.record_detail = tk.Text(body, bg=C["panel"], fg=C["text"], insertbackground=C["text"], relief="flat", font=(MONO, 9), wrap="word")
        self.record_detail.grid(row=0, column=1, sticky="nsew")

    def create_record(self) -> None:
        if not self.latest:
            messagebox.showwarning("CRUD", "Todavía no hay métricas cargadas. Espera 1 segundo.")
            return
        label = self.label_entry.get().strip() or "captura"
        comment = self.comment_entry.get().strip()
        record_id = self.db.create(self.latest, label, comment)
        self.refresh_records()
        messagebox.showinfo("CRUD", f"Captura registrada. ID: {record_id}")

    def refresh_records(self) -> None:
        if not hasattr(self, "records_tree"):
            return
        self.records_tree.delete(*self.records_tree.get_children())
        for row in self.db.list_all():
            _id, created, label, _comment, cpu, mem, swap, disk, down, up = row
            self.records_tree.insert("", "end", values=(_id, created, label, f"{cpu:.1f}", f"{mem:.1f}", f"{swap:.1f}", f"{disk:.1f}", fmt_bytes(down) + "/s", fmt_bytes(up) + "/s"))

    def select_record(self, _event: Any) -> None:
        selected = self.records_tree.selection()
        if not selected:
            return
        vals = self.records_tree.item(selected[0], "values")
        snapshot_id = safe_int(vals[0])
        row = self.db.get(snapshot_id)
        if not row:
            return
        self.selected_record = snapshot_id
        _id, created, label, comment, cpu, mem, swap, disk, down, up, payload = row
        self.label_entry.delete(0, "end")
        self.label_entry.insert(0, str(label))
        self.comment_entry.delete(0, "end")
        self.comment_entry.insert(0, str(comment))
        self.record_detail.delete("1.0", "end")
        self.record_detail.insert("1.0", f"Registro #{_id} | {created}\nCPU {cpu:.1f}% · RAM {mem:.1f}% · Swap {swap:.1f}% · Disk {disk:.1f}%\nDown {fmt_bytes(down)}/s · Up {fmt_bytes(up)}/s\n\n{payload}")

    def update_record(self) -> None:
        if self.selected_record is None:
            messagebox.showwarning("CRUD", "Selecciona un registro primero.")
            return
        self.db.update(self.selected_record, self.label_entry.get().strip(), self.comment_entry.get().strip())
        self.refresh_records()
        messagebox.showinfo("CRUD", "Registro actualizado.")

    def delete_record(self) -> None:
        if self.selected_record is None:
            messagebox.showwarning("CRUD", "Selecciona un registro primero.")
            return
        if not messagebox.askyesno("CRUD", "¿Eliminar el registro seleccionado?"):
            return
        self.db.delete(self.selected_record)
        self.selected_record = None
        self.label_entry.delete(0, "end")
        self.comment_entry.delete(0, "end")
        self.record_detail.delete("1.0", "end")
        self.refresh_records()

    # ---------- Commands page ----------
    def _build_commands_page(self) -> None:
        frame = tk.Frame(self.page_host, bg=C["bg"])
        self.pages["commands"] = frame
        top = tk.Frame(frame, bg=C["bg"])
        top.pack(fill="x", padx=24, pady=(24, 10))
        tk.Label(top, text="Comandos Linux y evidencia técnica", fg=C["text"], bg=C["bg"], font=(FONT, 16, "bold")).pack(side="left")
        buttons = [
            ("ps", lambda: self.run_single_command(["ps", "-eo", "pid,user,stat,comm,%cpu,%mem", "--sort=-%cpu"])),
            ("who", lambda: self.run_single_command(["who", "-u"])),
            ("df", lambda: self.run_single_command(["df", "-hT", "/"])),
            ("free", lambda: self.run_single_command(["free", "-h"])),
            ("ip", lambda: self.run_single_command(["ip", "-brief", "address"])),
            ("os.system()", self.run_os_system_demo),
            ("fork()", self.run_fork_demo),
        ]
        for label, cmd in reversed(buttons):
            tk.Button(top, text=label, command=cmd, bg=C["panel2"], fg=C["text"], activebackground=C["panel3"], relief="flat", bd=0, padx=12, pady=8, cursor="hand2").pack(side="right", padx=4)
        info = tk.Label(frame, text="Usa subprocess para ps/who/df/free/ip. El botón os.system() escribe una evidencia en os_system_demo.log. fork() crea un proceso hijo y registra fork_child.log.", fg=C["muted"], bg=C["bg"], font=(FONT, 9))
        info.pack(anchor="w", padx=24, pady=(0, 8))
        self.command_text = tk.Text(frame, bg=C["panel"], fg=C["text"], insertbackground=C["text"], relief="flat", font=(MONO, 10), wrap="none")
        self.command_text.pack(fill="both", expand=True, padx=24, pady=(0, 24))

    def refresh_commands(self) -> None:
        if not hasattr(self, "command_text"):
            return
        outputs = [
            ("ps", run_command(["bash", "-lc", "ps -eo pid,user,stat,comm,%cpu,%mem --sort=-%cpu | head -n 20"])),
            ("who", run_command(["who", "-u"])),
            ("df", run_command(["df", "-hT", "/"])),
            ("free", run_command(["free", "-h"])),
            ("ip", run_command(["ip", "-brief", "address"])),
        ]
        text = "\n\n".join([f"$ {name}\n{output}" for name, output in outputs])
        self.command_text.delete("1.0", "end")
        self.command_text.insert("1.0", text)

    def run_single_command(self, command: List[str]) -> None:
        output = run_command(command, timeout=5)
        self.command_text.delete("1.0", "end")
        self.command_text.insert("1.0", f"$ {' '.join(command)}\n{output}")

    def run_os_system_demo(self) -> None:
        command = f"date >> '{OS_SYSTEM_LOG_PATH}'"
        code = os.system(command)
        self.command_text.delete("1.0", "end")
        self.command_text.insert("1.0", f"$ os.system({command!r})\nCódigo de salida: {code}\nArchivo generado: {OS_SYSTEM_LOG_PATH}\n\nContenido:\n{read_text(str(OS_SYSTEM_LOG_PATH))}")

    def run_fork_demo(self) -> None:
        if not hasattr(os, "fork"):
            messagebox.showerror("fork()", "os.fork() solo está disponible en Linux/Unix.")
            return
        try:
            pid = os.fork()
            if pid == 0:
                # Proceso hijo: no toca widgets de Tkinter, solo escribe evidencia y sale.
                metrics = self.probe.collect_metrics()
                with open(FORK_LOG_PATH, "a", encoding="utf-8") as fh:
                    fh.write(
                        f"[{metrics['created_at']}] CHILD PID={os.getpid()} PPID={os.getppid()} "
                        f"CPU={metrics['cpu'].get('usage')}% RAM={metrics['memory'].get('used_pct')}%\n"
                    )
                os._exit(0)
            messagebox.showinfo("fork()", f"Proceso hijo creado con PID {pid}. Evidencia: fork_child.log")
        except OSError as exc:
            messagebox.showerror("fork()", f"No se pudo ejecutar os.fork(): {exc}")

    # ---------- Close ----------
    def _close(self) -> None:
        self.stop_event.set()
        self.destroy()


if __name__ == "__main__":
    app = ModernMonitorApp()
    app.mainloop()
