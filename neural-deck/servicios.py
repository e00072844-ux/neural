"""Procesos de NEURAL//DECK que arranca Wayfire ([autostart] de wayfire.ini):
ver si están vivos y reiniciarlos sin cerrar la sesión. Sin GTK.

Reiniciar = SIGTERM a los procesos de ese script (y SIGKILL si no salen en
3 s) y volver a lanzar la MISMA orden del [autostart], en su propia sesión
para que no muera con quien la lanzó."""
from __future__ import annotations

import os
import re
import signal
import subprocess
import time
from pathlib import Path

from animaciones import _leer_ini

NOMBRES = {  # clave de [autostart] → (nombre visible, descripción)
    "neural_deck": ("FONDO / ESCRITORIO", "El tablero HUD y la terminal F12 (se cierra la terminal F12)."),
    "neural_bar": ("BARRA", "La barra superior y el panel rápido."),
    "neural_dock": ("DOCK", "El dock de apps y ventanas."),
    "neural_notify": ("NOTIFICACIONES", "Los avisos en pantalla."),
    "neural_usb": ("AVISOS USB", "Aviso al conectar memorias y discos."),
    "neural_clip": ("PORTAPAPELES", "El historial de NEURAL//CLIP (Super+V)."),
    "neural_respaldo": ("RESPALDO", "Copias automáticas al conectar el disco."),
    "neural_idle": ("INACTIVIDAD", "Bloqueo, apagado de pantalla y suspensión."),
    "neural_auth": ("AUTENTICACIÓN", "Las ventanas de contraseña (polkit)."),
    "neural_power": ("ENERGÍA", "Perfiles de energía y umbrales de batería."),
    "neural_tile": ("MOSAICO", "El mosaico de ventanas en cinta."),
    "gamma": ("PROTECTOR DE LA VISTA", "wlsunset con lo elegido en PANTALLA."),
}


def _script(orden: str) -> str | None:
    m = re.search(r"(\S+\.py)\b", orden)
    return m.group(1) if m else None


def lista() -> list[dict]:
    """[{clave, nombre, desc, orden, script}] en el orden de NOMBRES."""
    ini = _leer_ini()
    out = []
    for clave, (nombre, desc) in NOMBRES.items():
        orden = ini.get(("autostart", clave))
        if orden:
            out.append({"clave": clave, "nombre": nombre, "desc": desc, "orden": orden,
                        "script": _script(orden)})
    return out


def _pids(s: dict) -> list[int]:
    yo, uid = os.getpid(), os.getuid()
    objetivo = s["script"]
    out = []
    for p in Path("/proc").glob("[0-9]*"):
        try:
            if p.stat().st_uid != uid or int(p.name) == yo:
                continue
            args = (p / "cmdline").read_bytes().split(b"\0")
            comm = (p / "comm").read_text().strip()
        except (OSError, ValueError):
            continue
        texto = [a.decode(errors="replace") for a in args if a]
        if objetivo:
            # el intérprete con el script como argumento (no el «sh -c» que lo lanzó)
            if texto and not texto[0].endswith("sh") and objetivo in texto:
                out.append(int(p.name))
        elif s["clave"] == "gamma" and comm == "wlsunset":
            out.append(int(p.name))
    return out


def vivo(s: dict) -> bool:
    if s["clave"] == "gamma":  # pantalla.py --arrancar se convierte en wlsunset
        return bool(_pids({"clave": "gamma", "script": None}))
    return bool(_pids(s))


def reiniciar(s: dict) -> None:
    pids = _pids({"clave": "gamma", "script": None}) if s["clave"] == "gamma" else _pids(s)
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    fin = time.monotonic() + 3
    while time.monotonic() < fin and any(Path(f"/proc/{p}").exists() for p in pids):
        time.sleep(0.1)
    for pid in pids:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    subprocess.Popen(["sh", "-c", s["orden"]], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True, cwd=str(Path.home()))
