"""NEURAL//PULSO: lectura del sistema (CPU, memoria, GPU, batería, discos, red y procesos). Sin GTK."""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import threading
import time
from pathlib import Path

import psutil

def _buscar_bateria() -> Path:
    """Primera batería del sistema (BAT0, BAT1, CMB0…); en un PC de escritorio no hay ninguna."""
    for d in sorted(Path("/sys/class/power_supply").glob("*")):
        try:
            if (d / "type").read_text().strip() == "Battery":
                return d
        except OSError:
            pass
    return Path("/sys/class/power_supply/BAT0")


BAT = _buscar_bateria()
PROTEGIDOS = {"wayfire", "systemd", "systemd-logind", "dbus-daemon", "dbus-broker", "Xwayland", "greetd", "pipewire",
              "wireplumber", "NetworkManager", "bluetoothd", "udisksd", "polkitd", "neural-deck", "neural-dock", "neural-bar"}
IFACES_NO = ("lo", "docker", "virbr", "veth", "neuralvm")
RE_USER = re.compile(r'users:\(\("([^"]+)",pid=(\d+)')
RE_RX = re.compile(r"bytes_received:(\d+)")
RE_TX = re.compile(r"bytes_acked:(\d+)")
RE_LINEA = re.compile(r"^\S+\s+\S+\s+(\S+)\s+(\S+)")


def _leer_int(p: Path) -> int | None:
    try:
        return int(p.read_text())
    except (OSError, ValueError):
        return None


def bateria() -> dict | None:
    b = psutil.sensors_battery()
    if b is None:
        return None
    lleno, diseno, ciclos = (_leer_int(BAT / n) for n in ("energy_full", "energy_full_design", "cycle_count"))
    uw = _leer_int(BAT / "power_now") or 0
    try:
        estado = (BAT / "status").read_text().strip()
    except OSError:
        estado = ""
    return {"pct": b.percent, "enchufada": bool(b.power_plugged), "estado": estado, "w": uw / 1e6,
            "seg": b.secsleft if isinstance(b.secsleft, int) and b.secsleft > 0 else None,
            "salud": 100 * lleno / diseno if lleno and diseno else None, "ciclos": ciclos}


def temperaturas() -> dict:
    t = psutil.sensors_temperatures()
    def uno(chip, i=0):
        v = t.get(chip) or []
        return v[i].current if len(v) > i and v[i].current else None
    def primero(*chips):
        for c in chips:
            v = uno(c)
            if v is not None:
                return v
        return None
    wifi = next((c for c in t if c.endswith("_phy0") or c.startswith("iwlwifi")), None)
    ventiladores = [f for lista in psutil.sensors_fans().values() for f in lista if f.current]
    return {"cpu": primero("k10temp", "coretemp", "zenpower", "cpu_thermal"),
            "gpu": primero("amdgpu", "nouveau", "i915", "xe"), "nvme": uno("nvme"),
            "wifi": uno(wifi) if wifi else None,
            "ventilador": ventiladores[0].current if ventiladores else None}


class GPU:
    """amdgpu_top en modo continuo: un hilo se queda con la última muestra."""

    def __init__(self):
        self.ultima: dict | None = None
        self.vivo = True
        threading.Thread(target=self._hilo, daemon=True).start()

    def _hilo(self):
        while self.vivo:
            try:
                p = subprocess.Popen(["setpriv", "--pdeathsig", "TERM", "amdgpu_top", "-J", "-s", "1500"],
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
                for linea in p.stdout:
                    try:
                        self.ultima = self.extraer(json.loads(linea))
                    except (ValueError, KeyError, TypeError, IndexError):
                        pass
                p.wait()
            except OSError:
                return
            time.sleep(3)

    @staticmethod
    def extraer(d: dict) -> dict:
        x = d["devices"][0]
        v = x["VRAM"]
        return {"nombre": x["Info"].get("DeviceName", "GPU"), "gfx": x["gpu_activity"]["GFX"]["value"] or 0,
                "media": x["gpu_activity"]["MediaEngine"]["value"] or 0, "vram": v["Total VRAM Usage"]["value"],
                "vram_t": v["Total VRAM"]["value"], "gtt": v["Total GTT Usage"]["value"], "gtt_t": v["Total GTT"]["value"]}


def _interfaz_util(n: str) -> bool:
    return not n.startswith(IFACES_NO)


class Muestreo:
    def __init__(self):
        self.t = time.monotonic()
        self.disco = psutil.disk_io_counters()
        self.red = psutil.net_io_counters(pernic=True)
        self.socks: dict[tuple, tuple[int, int]] = {}
        psutil.cpu_percent(percpu=True)
        self.gpu = GPU()

    def sistema(self) -> dict:
        ahora = time.monotonic()
        dt = max(ahora - self.t, 0.001)
        self.t = ahora
        nucleos = psutil.cpu_percent(percpu=True)
        m, s = psutil.virtual_memory(), psutil.swap_memory()
        d = psutil.disk_io_counters()
        lee, esc = (d.read_bytes - self.disco.read_bytes) / dt, (d.write_bytes - self.disco.write_bytes) / dt
        self.disco = d
        red = psutil.net_io_counters(pernic=True)
        ifaces = []
        stats = psutil.net_if_stats()
        for n, c in red.items():
            if _interfaz_util(n) and n in self.red and stats.get(n) and stats[n].isup:
                a = self.red[n]
                ifaces.append({"n": n, "baja": max(0, c.bytes_recv - a.bytes_recv) / dt,
                               "sube": max(0, c.bytes_sent - a.bytes_sent) / dt})
        self.red = red
        f = psutil.cpu_freq()
        return {"cpu": sum(nucleos) / len(nucleos), "nucleos": nucleos, "ghz": f.current / 1000 if f else 0,
                "carga": os.getloadavg(), "ram": m.used, "ram_t": m.total, "ram_pct": m.percent, "swap": s.used, "swap_t": s.total,
                "lee": lee, "esc": esc, "ifaces": ifaces, "temp": temperaturas(), "bat": bateria(), "gpu": self.gpu.ultima,
                "n_proc": len(psutil.pids())}

    def apps_red(self) -> list[dict]:
        """Tráfico TCP por app. Solo se ven las apps de tu usuario (sin permisos de root)."""
        try:
            salida = subprocess.run(["ss", "-tinpH", "state", "established"], capture_output=True, text=True, timeout=4).stdout
        except (OSError, subprocess.SubprocessError):
            return []
        return self._calcular(salida, time.monotonic())

    def _calcular(self, salida: str, ahora: float) -> list[dict]:
        dt = max(ahora - getattr(self, "t_red", ahora - 1), 0.001)
        self.t_red = ahora
        apps: dict[str, dict] = {}
        nuevos: dict[tuple, tuple[int, int]] = {}
        lineas = salida.splitlines()
        for cab, det in zip(lineas[::2], lineas[1::2]):
            u, ext = RE_USER.search(cab), RE_LINEA.match(cab)
            if not u or not ext or ext.group(2).startswith(("127.", "[::1]")):
                continue
            rx = int(m.group(1)) if (m := RE_RX.search(det)) else 0
            tx = int(m.group(1)) if (m := RE_TX.search(det)) else 0
            clave = (ext.group(1), ext.group(2), u.group(2))
            nuevos[clave] = (rx, tx)
            a = apps.setdefault(u.group(1), {"app": u.group(1), "conex": 0, "baja": 0.0, "sube": 0.0, "peers": set(), "rx": 0, "tx": 0})
            a["conex"] += 1
            a["rx"] += rx
            a["tx"] += tx
            a["peers"].add(ext.group(2).rsplit(":", 1)[0])
            if clave in self.socks:  # lo primero que se ve de una conexión no cuenta como velocidad
                r0, t0 = self.socks[clave]
                a["baja"] += max(0, rx - r0) / dt
                a["sube"] += max(0, tx - t0) / dt
        self.socks = nuevos
        r = list(apps.values())
        for a in r:
            a["peers"] = sorted(a["peers"])[:3]
        return sorted(r, key=lambda a: (-(a["baja"] + a["sube"]), -a["conex"], a["app"]))


def procesos() -> list[dict]:
    r = []
    for p in psutil.process_iter(["pid", "name", "username", "memory_info", "cmdline", "status"]):
        try:
            i = p.info
            nombre = i["name"] or "?"
            cmd = i["cmdline"] or []
            if nombre.startswith("python") and len(cmd) > 1:  # que se vea el programa, no "python3"
                nombre = Path(next((c for c in cmd[1:] if not c.startswith("-")), nombre)).name
            r.append({"pid": i["pid"], "nombre": nombre, "real": i["name"] or "", "user": i["username"] or "",
                      "cpu": p.cpu_percent(None), "mem": i["memory_info"].rss if i["memory_info"] else 0,
                      "cmd": " ".join(cmd)[:300]})
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
    return r


def filtrar(lista: list[dict], q: str, orden: str, limite: int = 80) -> list[dict]:
    q = q.lower().strip()
    v = [p for p in lista if not q or q in p["nombre"].lower() or q in p["cmd"].lower() or q == str(p["pid"])]
    if orden == "nombre":
        v.sort(key=lambda p: p["nombre"].lower())
    else:
        v.sort(key=lambda p: -p[orden])
    return v[:limite]


def senal(pid: int, forzar: bool) -> str | None:
    """None si se envió; si no, el motivo en palabras."""
    try:
        p = psutil.Process(pid)
        n = p.name()
    except psutil.NoSuchProcess:
        return "Ese proceso ya terminó"
    if pid <= 2 or pid == os.getpid() or n in PROTEGIDOS:
        return f"{n} es parte de tu sesión: no se cierra desde aquí"
    try:
        os.kill(pid, signal.SIGKILL if forzar else signal.SIGTERM)
    except PermissionError:
        return f"{n} es del sistema: necesita permisos de administrador"
    except ProcessLookupError:
        return "Ese proceso ya terminó"
    return None
