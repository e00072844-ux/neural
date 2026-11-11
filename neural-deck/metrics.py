"""Lectura de métricas del sistema para los paneles de info del wallpaper."""
from __future__ import annotations

import os
import platform
import socket
import time

import psutil

# Estado entre lecturas: la red se mide como delta de bytes por segundo y la
# lista de procesos es cara, asi que se refresca cada PROCESS_EVERY segundos.
_last_net: tuple[float, int, int] | None = None
_last_procs: list[dict] = []
_last_procs_at = 0.0
PROCESS_EVERY = 3.0


def _net_rates() -> tuple[float | None, float | None]:
    global _last_net
    try:
        io = psutil.net_io_counters()
    except Exception:
        return None, None
    now = time.monotonic()
    prev = _last_net
    _last_net = (now, io.bytes_sent, io.bytes_recv)
    if prev is None:
        return 0.0, 0.0
    dt = max(now - prev[0], 1e-3)
    up = max(0, io.bytes_sent - prev[1]) / dt
    down = max(0, io.bytes_recv - prev[2]) / dt
    return up, down


def _top_processes(limit: int = 6) -> list[dict]:
    global _last_procs, _last_procs_at
    now = time.monotonic()
    if now - _last_procs_at < PROCESS_EVERY and _last_procs:
        return _last_procs
    _last_procs_at = now
    procs = []
    ncpu = psutil.cpu_count() or 1
    # process_iter cachea los objetos Process, asi cpu_percent compara contra
    # la lectura anterior (la primera vez devuelve 0 para todos).
    for p in psutil.process_iter(["name", "cpu_percent", "memory_percent"]):
        info = p.info
        cpu = info.get("cpu_percent")
        if cpu is None:
            continue
        procs.append(
            {
                "name": info.get("name") or "?",
                "cpu": round(cpu / ncpu, 1),
                "mem": round(info.get("memory_percent") or 0.0, 1),
            }
        )
    procs.sort(key=lambda p: (p["cpu"], p["mem"]), reverse=True)
    _last_procs = procs[:limit]
    return _last_procs


def read_metrics() -> dict:
    metrics: dict = {
        "battery_percent": None,
        "battery_charging": None,
        "battery_secs_left": None,
        "disk_used_percent": None,
        "disk_used_gb": None,
        "disk_total_gb": None,
        "cpu_percent": None,
        "cpu_cores": [],
        "cpu_freq_mhz": None,
        "ram_used_percent": None,
        "ram_used_gb": None,
        "ram_total_gb": None,
        "swap_used_percent": None,
        "temp_c": None,
        "net_up_bps": None,
        "net_down_bps": None,
        "uptime_s": None,
        "load_avg": None,
        "processes": [],
        "hostname": socket.gethostname(),
        "user": os.environ.get("USER", ""),
        "kernel": platform.release(),
    }

    try:
        metrics["cpu_percent"] = round(psutil.cpu_percent(interval=None), 1)
        metrics["cpu_cores"] = [
            round(c, 1) for c in psutil.cpu_percent(interval=None, percpu=True)
        ]
    except Exception:
        pass

    try:
        freq = psutil.cpu_freq()
        if freq:
            metrics["cpu_freq_mhz"] = round(freq.current)
    except Exception:
        pass

    try:
        vm = psutil.virtual_memory()
        metrics["ram_used_percent"] = round(vm.percent, 1)
        metrics["ram_used_gb"] = round((vm.total - vm.available) / 1024**3, 1)
        metrics["ram_total_gb"] = round(vm.total / 1024**3, 1)
        metrics["swap_used_percent"] = round(psutil.swap_memory().percent, 1)
    except Exception:
        pass

    try:
        du = psutil.disk_usage("/")
        metrics["disk_used_percent"] = round(du.percent, 1)
        metrics["disk_used_gb"] = round(du.used / 1024**3)
        metrics["disk_total_gb"] = round(du.total / 1024**3)
    except Exception:
        pass

    try:
        batt = psutil.sensors_battery()
        if batt is not None:
            metrics["battery_percent"] = round(batt.percent, 1)
            metrics["battery_charging"] = bool(batt.power_plugged)
            if batt.secsleft not in (
                psutil.POWER_TIME_UNLIMITED,
                psutil.POWER_TIME_UNKNOWN,
            ) and batt.secsleft > 0:
                metrics["battery_secs_left"] = int(batt.secsleft)
    except Exception:
        pass

    try:
        temps = psutil.sensors_temperatures()
        # Preferir el sensor de la CPU (k10temp en AMD, coretemp en Intel).
        for key in ("k10temp", "coretemp", "zenpower", "cpu_thermal"):
            if temps.get(key):
                metrics["temp_c"] = round(temps[key][0].current, 1)
                break
        else:
            for _label, entries in temps.items():
                if entries:
                    metrics["temp_c"] = round(entries[0].current, 1)
                    break
    except Exception:
        pass

    up, down = _net_rates()
    metrics["net_up_bps"] = up
    metrics["net_down_bps"] = down

    try:
        metrics["uptime_s"] = int(time.time() - psutil.boot_time())
    except Exception:
        pass

    try:
        metrics["load_avg"] = [round(x, 2) for x in os.getloadavg()]
    except Exception:
        pass

    try:
        metrics["processes"] = _top_processes()
    except Exception:
        pass

    return metrics
