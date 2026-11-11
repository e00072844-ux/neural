#!/usr/bin/env python3
"""NEURAL//POWER: perfiles de energía y umbrales de carga de la batería.

Lee ~/.config/live-wallpaper/ajustes.json, sección "energia":
    {"modo": "fijo" | "auto",
     "perfil": "ahorro" | "normal" | "rendimiento",        (modo fijo)
     "perfil_corriente": "...", "perfil_bateria": "...",    (modo auto)
     "carga_inicio": 75, "carga_fin": 80}                   (umbrales; null = no tocar)
y lo aplica:
- perfil: con power-profiles-daemon (powerprofilesctl, sin root) si está
  activo, como en CachyOS; si no, con el ayudante.
- umbrales de carga: con /usr/libexec/neural-energia vía pkexec (política
  org.local.neural.energia: sin contraseña en la sesión activa). Se instala
  con: sudo ~/neural-deck/root/instalar.sh

Como proceso ("neural-power", desde [autostart]): aplica al entrar, vuelve a
aplicar si cambia ajustes.json (lo escribe NEURAL//AJUSTES) y, en modo auto,
al enchufar / desenchufar. Solo llama al ayudante si el sistema no está ya así.
Se importa desde ajustes.py para leer el estado (estado(), bateria())."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from board import CONFIG_DIR  # noqa: E402

PROC = "neural-power"
AJUSTES = CONFIG_DIR / "ajustes.json"
AYUDANTE = "/usr/libexec/neural-energia"
PERFILES = ("ahorro", "normal", "rendimiento")
_FIRMWARE = {"ahorro": "low-power", "normal": "balanced", "rendimiento": "performance"}
_PPD = {"ahorro": "power-saver", "normal": "balanced", "rendimiento": "performance"}
DEFECTO = {"modo": "fijo", "perfil": "normal", "perfil_corriente": "normal", "perfil_bateria": "ahorro",
           "carga_inicio": None, "carga_fin": None}
SYS_PERFIL = Path("/sys/firmware/acpi/platform_profile")
CPU0 = Path("/sys/devices/system/cpu/cpu0/cpufreq")
SUPPLY = Path("/sys/class/power_supply")


def _read(p: Path) -> str:
    try:
        return p.read_text().strip()
    except OSError:
        return ""


def _bat() -> Path | None:
    return next((p for p in sorted(SUPPLY.glob("*")) if _read(p / "type") == "Battery"), None)


# ---------- ajustes.json (compartido por todo NEURAL//DECK) ----------
def leer_todo() -> dict:
    try:
        d = json.loads(AJUSTES.read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def guardar_seccion(nombre: str, datos: dict) -> None:
    """Reescribe solo una sección, de forma atómica (tmp + rename) para que
    quien vigila el archivo nunca lea medio JSON."""
    todo = leer_todo()
    todo[nombre] = datos
    AJUSTES.parent.mkdir(parents=True, exist_ok=True)
    tmp = AJUSTES.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(todo, indent=2, ensure_ascii=False) + "\n")
    os.replace(tmp, AJUSTES)


def leer() -> dict:
    d = leer_todo().get("energia", {})
    out = dict(DEFECTO)
    if isinstance(d, dict):
        if d.get("modo") in ("fijo", "auto"):
            out["modo"] = d["modo"]
        for k in ("perfil", "perfil_corriente", "perfil_bateria"):
            if d.get(k) in PERFILES:
                out[k] = d[k]
        try:
            ini, fin = d.get("carga_inicio"), d.get("carga_fin")
            if ini is not None and fin is not None and 0 <= int(ini) < int(fin) <= 100 and int(fin) >= 50:
                out["carga_inicio"], out["carga_fin"] = int(ini), int(fin)
        except (TypeError, ValueError):
            pass
    return out


# ---------- estado del sistema ----------
def en_corriente() -> bool:
    return any(_read(p / "online") == "1" for p in SUPPLY.glob("*") if _read(p / "type") == "Mains")


def _ppd(*args: str) -> str | None:
    """powerprofilesctl; None si no está o el demonio no responde."""
    if shutil.which("powerprofilesctl") is None:
        return None
    try:
        r = subprocess.run(["powerprofilesctl", *args], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def ppd_activo() -> bool:
    return _ppd("get") is not None


def perfil_actual() -> str | None:
    ppd = _ppd("get")
    if ppd is not None:
        return next((k for k, v in _PPD.items() if v == ppd), None)
    fw = _read(SYS_PERFIL)
    return next((k for k, v in _FIRMWARE.items() if v == fw), None)


def estado() -> dict:
    bat = _bat()
    umb = None
    if bat is not None and (bat / "charge_control_end_threshold").exists():
        try:
            umb = (int(_read(bat / "charge_control_start_threshold")), int(_read(bat / "charge_control_end_threshold")))
        except ValueError:
            umb = None
    return {"perfil": perfil_actual(), "firmware": _read(SYS_PERFIL),
            "governor": _read(CPU0 / "scaling_governor"), "epp": _read(CPU0 / "energy_performance_preference"),
            "umbral": umb, "corriente": en_corriente(), "ayudante": Path(AYUDANTE).exists(),
            "ppd": ppd_activo()}


def bateria() -> dict | None:
    """capacidad %, estado, salud %, ciclos, vatios, horas estimadas."""
    b = _bat()
    if b is None:
        return None

    def num(n):
        try:
            return int(_read(b / n))
        except ValueError:
            return None

    full, design, now, power = num("energy_full"), num("energy_full_design"), num("energy_now"), num("power_now")
    st = _read(b / "status")
    horas = None
    if power:
        if st == "Discharging" and now:
            horas = now / power
        elif st == "Charging" and now is not None and full:
            horas = max(0, full - now) / power
    return {"capacidad": num("capacity"), "estado": st,
            "salud": round(100 * full / design) if full and design else None,
            "ciclos": num("cycle_count"), "vatios": power / 1e6 if power else 0.0, "horas": horas,
            "wh": full / 1e6 if full else None, "wh_diseno": design / 1e6 if design else None,
            "modelo": f"{_read(b / 'manufacturer')} {_read(b / 'model_name')}".strip(),
            "tecnologia": _read(b / "technology")}


def perfil_deseado(cfg: dict) -> str:
    if cfg["modo"] == "auto":
        return cfg["perfil_corriente"] if en_corriente() else cfg["perfil_bateria"]
    return cfg["perfil"]


def _ayudante(*args: str) -> bool:
    try:
        r = subprocess.run(["pkexec", AYUDANTE, *args], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f"energia: {' '.join(args)}: {e}", file=sys.stderr, flush=True)
        return False
    if r.returncode != 0:
        print(f"energia: {' '.join(args)} falló ({r.returncode}): {r.stderr.strip()}", file=sys.stderr, flush=True)
    return r.returncode == 0


def aplicar(cfg: dict | None = None) -> None:
    cfg = cfg or leer()
    est = estado()
    hay_ayudante = Path(AYUDANTE).exists()
    quiero = perfil_deseado(cfg)
    if est["perfil"] != quiero:
        donde = "corriente" if est["corriente"] else "batería"
        if est["ppd"]:
            if _ppd("set", _PPD[quiero]) is not None:
                print(f"energia: perfil {quiero} ({donde}, power-profiles-daemon)", flush=True)
        elif hay_ayudante:
            if _ayudante("perfil", quiero):
                print(f"energia: perfil {quiero} ({donde})", flush=True)
        else:
            print(f"energia: no puedo cambiar el perfil: ni power-profiles-daemon ni {AYUDANTE}",
                  file=sys.stderr, flush=True)
    if cfg["carga_fin"] is not None and est["umbral"] is not None \
            and est["umbral"] != (cfg["carga_inicio"], cfg["carga_fin"]):
        if not hay_ayudante:
            print(f"energia: umbrales sin aplicar: falta {AYUDANTE}", file=sys.stderr, flush=True)
            return
        if _ayudante("bateria", str(cfg["carga_inicio"]), str(cfg["carga_fin"])):
            print(f"energia: carga {cfg['carga_inicio']} → {cfg['carga_fin']} %", flush=True)


# ---------- proceso ----------
def main() -> int:
    if "--aplicar" in sys.argv:  # una vez y salir
        aplicar()
        return 0
    import ctypes
    import signal

    import gi

    gi.require_version("Gio", "2.0")
    from gi.repository import Gio, GLib, GLibUnix

    try:
        ctypes.CDLL(None).prctl(15, PROC.encode(), 0, 0, 0)
    except (OSError, AttributeError):
        pass
    # instancia única: candado que el kernel suelta solo si el proceso muere
    import fcntl
    run = Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}")
    candado = open(run / "neural-power.lock", "a+")
    try:
        fcntl.flock(candado, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("energia: ya está corriendo", file=sys.stderr)
        return 0
    loop = GLib.MainLoop()
    pendiente = [0]

    def programar(espera=400):
        if pendiente[0]:
            GLib.source_remove(pendiente[0])

        def ya():
            pendiente[0] = 0
            aplicar()
            return GLib.SOURCE_REMOVE

        pendiente[0] = GLib.timeout_add(espera, ya)

    AJUSTES.parent.mkdir(parents=True, exist_ok=True)
    mon = Gio.File.new_for_path(str(AJUSTES)).monitor_file(Gio.FileMonitorFlags.NONE, None)
    mon.connect("changed", lambda *_: programar())
    corriente = [en_corriente()]

    def vigilar():  # enchufar / desenchufar (sysfs no avisa por inotify)
        ahora = en_corriente()
        if ahora != corriente[0]:
            corriente[0] = ahora
            print(f"energia: {'enchufado' if ahora else 'con batería'}", flush=True)
            programar(100)
        return GLib.SOURCE_CONTINUE

    GLib.timeout_add_seconds(3, vigilar)
    for sig in (signal.SIGTERM, signal.SIGINT):
        GLibUnix.signal_add(GLib.PRIORITY_DEFAULT, sig, lambda *_: (loop.quit(), GLib.SOURCE_REMOVE)[1])
    aplicar()
    loop.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
