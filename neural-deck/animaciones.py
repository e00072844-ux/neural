"""NEURAL//ANIM: efectos de ventana (plugin animate de Wayfire + neural-holo).

La fuente de verdad es ~/.config/wayfire.ini (secciones [animate] y
[neural-holo]): así sobrevive a reinicios sin ningún demonio. Al cambiar algo:
  1) se reescribe solo la línea afectada en wayfire.ini (Wayfire lo recarga solo),
  2) se manda también por IPC para que se note al momento.
app.py vuelve a poner el color del holograma cuando wayfire.ini cambia."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from wayfire_ipc import WayfireIPC

INI = Path.home() / ".config" / "wayfire.ini"

# (valor en animate, nombre visible, descripción)
EFECTOS = [
    ("holograma", "HOLOGRAMA", "Se proyecta de abajo arriba con haz, barrido y glitch."),
    ("zoom", "ZOOM", "Crece / encoge desde el centro."),
    ("fade", "FUNDIDO", "Aparece y desaparece suavemente."),
    ("zap", "ZAP", "Se despliega como una descarga."),
    ("fire", "FUEGO", "Arde al cerrar."),
    ("spin", "GIRO", "Gira mientras aparece."),
    ("squeezimize", "ABSORBER", "Se encoge hacia el dock (solo minimizar)."),
    ("none", "NINGUNO", "Sin animación."),
]
NOMBRE = {e: n for e, n, _ in EFECTOS}
PARA = {  # qué efectos tienen sentido en cada momento
    "open": ["holograma", "zoom", "fade", "zap", "spin", "none"],
    "close": ["holograma", "zoom", "fade", "zap", "fire", "spin", "none"],
    "minimize": ["holograma", "squeezimize", "zoom", "fade", "none"],
}
VELOCIDADES = [  # (clave, nombre, ms holograma, ms resto)
    ("rapida", "RÁPIDA", 320, 250),
    ("normal", "NORMAL", 520, 400),
    ("lenta", "LENTA", 850, 650),
]
DEFECTO = {"open": "holograma", "close": "holograma", "minimize": "holograma", "velocidad": "normal"}

# opción de Wayfire → (sección del ini, clave)
_OPC = {
    "open": ("animate", "open_animation"),
    "close": ("animate", "close_animation"),
    "minimize": ("animate", "minimize_animation"),
    "dur_holo": ("neural-holo", "duration"),
    "dur": ("animate", "duration"),
    "dur_squeeze": ("animate", "squeezimize_duration"),
}


def _leer_ini() -> dict[tuple[str, str], str]:
    out, sec = {}, None
    try:
        for line in INI.read_text().splitlines():
            s = line.strip()
            if s.startswith("[") and s.endswith("]"):
                sec = s[1:-1]
            elif sec and "=" in s and not s.startswith("#"):
                k, v = s.split("=", 1)
                out[(sec, k.strip())] = v.strip()
    except OSError:
        pass
    return out


def leer() -> dict:
    ini = _leer_ini()
    cfg = dict(DEFECTO)
    for clave in ("open", "close", "minimize"):
        v = ini.get(_OPC[clave])
        if v:
            cfg[clave] = v
    ms = re.match(r"\s*(\d+)", ini.get(_OPC["dur_holo"], ""))
    if ms:
        n = int(ms.group(1))
        cfg["velocidad"] = min(VELOCIDADES, key=lambda x: abs(x[2] - n))[0]
    return cfg


def _poner_ini(cambios: dict[tuple[str, str], str]) -> None:
    """Cambia/añade claves en sus secciones sin tocar nada más (comentarios incluidos).
    Se escribe en el mismo archivo (no rename) para que el inotify de Wayfire lo vea."""
    lineas = INI.read_text().splitlines()
    pendientes = dict(cambios)
    sec, fin_sec = None, {}
    for i, line in enumerate(lineas):
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            sec = s[1:-1]
            continue
        if sec is None:
            continue
        if s and not s.startswith("#"):
            fin_sec[sec] = i
        if "=" in s and not s.startswith("#"):
            k = s.split("=", 1)[0].strip()
            if (sec, k) in pendientes:
                lineas[i] = f"{k} = {pendientes.pop((sec, k))}"
    # claves que no estaban: tras la última línea de su sección (o sección nueva al final)
    nuevas: dict[str, list[str]] = {}
    for (sec, k), v in sorted(pendientes.items(), key=lambda x: -fin_sec.get(x[0][0], -1)):
        if sec in fin_sec:
            lineas.insert(fin_sec[sec] + 1, f"{k} = {v}")
        else:
            nuevas.setdefault(sec, []).append(f"{k} = {v}")
    for sec, claves in nuevas.items():  # una sola cabecera por sección nueva
        lineas += ["", f"[{sec}]", *claves]
    texto = "\n".join(lineas) + "\n"
    with open(INI, "r+") as f:
        f.write(texto)
        f.truncate()


def guardar(cfg: dict) -> None:
    vel = next((v for v in VELOCIDADES if v[0] == cfg.get("velocidad")), VELOCIDADES[1])
    valores = {
        "open": cfg["open"], "close": cfg["close"], "minimize": cfg["minimize"],
        "dur_holo": f"{vel[2]}ms linear",
        "dur": f"{vel[3]}ms circle",
        "dur_squeeze": f"{vel[3]}ms linear",
    }
    _poner_ini({_OPC[k]: v for k, v in valores.items()})
    try:
        WayfireIPC().call("wayfire/set-config-options",
                          {f"{s}/{k}": valores[c] for c, (s, k) in _OPC.items()})
    except Exception:  # sin IPC: ya lo recargará Wayfire desde el ini
        pass


def probar() -> None:
    """Abre una ventanita que se cierra sola: se ven la apertura y el cierre."""
    try:
        subprocess.Popen(["foot", "--app-id", "neural-prueba-anim", "--window-size-chars", "60x14",
                          "sh", "-c", "printf '\\n   NEURAL//ANIM  ·  prueba de efecto\\n'; sleep 1.4"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        pass
