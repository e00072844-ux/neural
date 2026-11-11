"""Persiste el tablero del wallpaper: tema de color y la lista de paneles
(posicion, tamaño, tipo, y las apps de cada panel de apps) que el usuario
arma en modo edicion desde la UI.

LIVE_WALLPAPER_CONFIG_DIR permite apuntar a otra carpeta (lo usan las
pruebas para no pisar la config real)."""
from __future__ import annotations

import json
import os
from pathlib import Path

CONFIG_DIR = Path(
    os.environ.get(
        "LIVE_WALLPAPER_CONFIG_DIR", Path.home() / ".config" / "live-wallpaper"
    )
)
CONFIG_PATH = CONFIG_DIR / "board.json"


def load_board() -> dict:
    """Devuelve el tablero guardado. Si no hay, `panels` es None y el JS arma
    el layout por defecto segun el tamaño real de la pantalla."""
    if CONFIG_PATH.exists():
        try:
            board = json.loads(CONFIG_PATH.read_text())
            if isinstance(board, dict) and isinstance(board.get("panels"), list):
                # el catalogo de temas vive en web/themes.js; la pagina
                # vuelve al primero si este id ya no existe
                if not isinstance(board.get("theme"), str):
                    board["theme"] = "red"
                return board
        except (json.JSONDecodeError, OSError):
            pass
    return {"theme": "red", "panels": None}


def save_board(board: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(board, indent=1))
    tmp.replace(CONFIG_PATH)  # atomico: nunca queda un board.json a medias


def app_ids_in(board: dict) -> list[str]:
    ids: list[str] = []
    for panel in board.get("panels") or []:
        for app_id in panel.get("apps") or []:
            if app_id not in ids:
                ids.append(app_id)
    return ids
