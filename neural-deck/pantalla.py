#!/usr/bin/env python3
"""Protector de la vista de NEURAL//DECK: maneja wlsunset según la sección
"pantalla" de ~/.config/live-wallpaper/ajustes.json (la escribe NEURAL//AJUSTES).

    protector     "apagado" | "siempre" | "horario"
    temp_noche    temperatura cálida en K (cuanto más baja, más naranja)
    temp_dia      temperatura de día en K (6500 = sin filtro)
    atardecer     "HH:MM" en que empieza el filtro (modo horario)
    amanecer      "HH:MM" en que se quita (modo horario)
    transicion    minutos que tarda el paso de día a noche
    gamma         corrección de gamma (1.0 = sin tocar)

Uso:
    pantalla.py --arrancar   (autostart de wayfire.ini) se convierte en wlsunset con
                             los argumentos guardados; si está apagado, sale.
    pantalla.py --aplicar    reinicia wlsunset con lo guardado (lo usa AJUSTES)."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import energia  # noqa: E402  (leer_todo / guardar_seccion de ajustes.json)

LOG = Path.home() / ".cache" / "neural-wlsunset.log"
MODOS = ("apagado", "siempre", "horario")
DEFECTO = {"protector": "horario", "temp_noche": 4000, "temp_dia": 6500, "atardecer": "19:00",
           "amanecer": "07:00", "transicion": 30, "gamma": 1.0}


def _hora(v, defecto: str) -> str:
    try:
        h, m = str(v).split(":")
        if 0 <= int(h) < 24 and 0 <= int(m) < 60:
            return f"{int(h):02d}:{int(m):02d}"
    except ValueError:
        pass
    return defecto


def leer() -> dict:
    d = energia.leer_todo().get("pantalla", {})
    out = dict(DEFECTO)
    if not isinstance(d, dict):
        return out
    if d.get("protector") in MODOS:
        out["protector"] = d["protector"]
    for k, lo, hi in (("temp_noche", 1900, 6400), ("temp_dia", 4000, 6500), ("transicion", 1, 180)):
        try:
            out[k] = max(lo, min(hi, int(d.get(k, out[k]))))
        except (TypeError, ValueError):
            pass
    try:
        out["gamma"] = max(0.5, min(1.5, float(d.get("gamma", 1.0))))
    except (TypeError, ValueError):
        pass
    out["atardecer"] = _hora(d.get("atardecer"), DEFECTO["atardecer"])
    out["amanecer"] = _hora(d.get("amanecer"), DEFECTO["amanecer"])
    return out


def guardar(cfg: dict) -> None:
    energia.guardar_seccion("pantalla", cfg)


def argumentos(cfg: dict) -> list[str] | None:
    """Línea de wlsunset para esa configuración; None = protector apagado."""
    if cfg["protector"] == "apagado":
        return None
    noche = cfg["temp_noche"]
    gamma = ["-g", f"{cfg['gamma']:.2f}"] if abs(cfg["gamma"] - 1.0) > 0.001 else []
    if cfg["protector"] == "siempre":
        # wlsunset no tiene «siempre»: día y noche con casi la misma temperatura
        return ["wlsunset", "-t", str(noche), "-T", str(noche + 1), "-S", "00:00", "-s", "00:01",
                "-d", "60", *gamma]
    dia = max(cfg["temp_dia"], noche + 1)
    return ["wlsunset", "-t", str(noche), "-T", str(dia), "-S", cfg["amanecer"], "-s", cfg["atardecer"],
            "-d", str(cfg["transicion"] * 60), *gamma]


def corriendo() -> bool:
    uid = str(os.getuid())
    for p in Path("/proc").glob("[0-9]*"):
        try:
            if (p / "comm").read_text().strip() == "wlsunset" and str(p.stat().st_uid) == uid:
                return True
        except OSError:
            pass
    return False


def aplicar(cfg: dict | None = None) -> str | None:
    """Para el wlsunset de este usuario y arranca uno nuevo con lo guardado.
    Devuelve un error legible o None."""
    cfg = cfg or leer()
    subprocess.run(["pkill", "-x", "-U", str(os.getuid()), "wlsunset"], check=False)
    args = argumentos(cfg)
    if args is None:
        return None
    if shutil.which("wlsunset") is None:
        return "falta wlsunset (sudo pacman -S wlsunset)"
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "ab") as log:
        subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    return None


def main(argv: list[str]) -> int:
    if "--arrancar" in argv:
        args = argumentos(leer())
        if args is None:
            return 0
        os.execvp(args[0], args)
    if "--aplicar" in argv:
        err = aplicar()
        if err:
            print(err, file=sys.stderr)
            return 1
        return 0
    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
