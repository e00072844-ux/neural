#!/usr/bin/env python3
"""NEURAL//SHOT — capturas de pantalla con grim/slurp.

Uso: captura.py [pantalla|zona|ventana]
  pantalla  toda la pantalla (por defecto)
  zona      seleccionar un rectángulo con el ratón (slurp)
  ventana   la ventana activa (geometría vía IPC de Wayfire)

Guarda PNG en ~/Imágenes/Capturas/NEURAL_AAAA-MM-DD_HH-MM-SS.png, avisa con
miniatura y, si existe wl-copy, la deja también en el portapapeles.
"""
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

CARPETA = Path.home() / "Imágenes" / "Capturas"


def avisar(titulo: str, texto: str, icono: str = "camera-photo") -> None:
    try:
        from gi.repository import Gio, GLib
        V = GLib.Variant
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        bus.call_sync("org.freedesktop.Notifications", "/org/freedesktop/Notifications",
                      "org.freedesktop.Notifications", "Notify",
                      V("(susssasa{sv}i)", ("NEURAL//SHOT", 0, icono, titulo, texto, [],
                                            {"urgency": V("y", 0)}, 4000)),
                      None, Gio.DBusCallFlags.NONE, 1000, None)
    except Exception as e:  # el aviso es opcional
        print(f"captura: sin aviso ({e})", file=sys.stderr)


def geometria_ventana() -> str | None:
    from wayfire_ipc import WayfireIPC
    info = (WayfireIPC().call("window-rules/get-focused-view") or {}).get("info") or {}
    g = info.get("geometry")
    if not g or info.get("role") not in ("toplevel", None):
        return None
    return f"{int(g['x'])},{int(g['y'])} {int(g['width'])}x{int(g['height'])}"


def main() -> int:
    modo = sys.argv[1] if len(sys.argv) > 1 else "pantalla"
    if modo not in ("pantalla", "zona", "ventana"):
        print(__doc__)
        return 2

    geo = None
    if modo == "zona":
        r = subprocess.run(["slurp", "-d", "-b", "#00000088", "-c", "#1FB6FFFF",
                            "-s", "#1FB6FF22", "-w", "2"], capture_output=True, text=True)
        if r.returncode != 0 or not r.stdout.strip():
            return 1  # cancelado con Esc
        geo = r.stdout.strip()
    elif modo == "ventana":
        try:
            geo = geometria_ventana()
        except Exception as e:
            print(f"captura: IPC falló ({e})", file=sys.stderr)
        if not geo:
            avisar("CAPTURA FALLIDA", "No hay ventana activa")
            return 1

    CARPETA.mkdir(parents=True, exist_ok=True)
    archivo = CARPETA / time.strftime("NEURAL_%Y-%m-%d_%H-%M-%S.png")
    cmd = ["grim"] + (["-g", geo] if geo else []) + [str(archivo)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not archivo.exists():
        avisar("CAPTURA FALLIDA", (r.stderr or "grim falló").strip()[:200])
        return 1

    extra = ""
    if shutil.which("wl-copy"):
        with open(archivo, "rb") as f:
            subprocess.Popen(["wl-copy", "-t", "image/png"], stdin=f,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
        extra = " · copiada"
    nombre = {"pantalla": "PANTALLA", "zona": "ZONA", "ventana": "VENTANA"}[modo]
    avisar(f"CAPTURA · {nombre}", f"~/Imágenes/Capturas/{archivo.name}{extra}", str(archivo))
    print(archivo)
    return 0


if __name__ == "__main__":
    sys.exit(main())
