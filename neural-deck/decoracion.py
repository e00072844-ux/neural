"""Barras de titulo y bordes de TODAS las ventanas (plugin decoration de
Wayfire) con los colores del tema NEURAL activo.

Wayfire solo deja elegir colores, fuente, alto de la barra y grosor del
borde (no esquinas cortadas, brillo ni otros botones): eso queda para las
apps propias (p. ej. una terminal NEURAL//TERM con su marco HUD).
Se aplica por la IPC (wayfire/set-config-options), sin tocar wayfire.ini;
app.py lo llama al cambiar de tema y si se edita wayfire.ini (al recargarlo
Wayfire volveria a sus valores)."""
from __future__ import annotations

from board import load_board
from estilo import load_themes
from wayfire_ipc import WayfireIPC

FONT = "Rajdhani Bold"
TITLE_H = 26
BORDE = 2


def medidas() -> tuple[int, int]:
    """(alto de la barra de título, grosor del borde): sección "apariencia" de
    ajustes.json (NEURAL//AJUSTES > APARIENCIA) o los de siempre."""
    import json
    from board import CONFIG_DIR
    try:
        d = json.loads((CONFIG_DIR / "ajustes.json").read_text()).get("apariencia", {})
        return (max(16, min(48, int(d.get("titulo_alto", TITLE_H)))), max(0, min(8, int(d.get("borde", BORDE)))))
    except (OSError, ValueError, TypeError, AttributeError):
        return TITLE_H, BORDE


def _hex(rgb, alpha: float = 1.0) -> str:
    r, g, b = (max(0, min(255, round(v * 255))) for v in rgb)
    return f"#{r:02X}{g:02X}{b:02X}{round(alpha * 255):02X}"


def opciones(theme_id: str | None = None) -> dict:
    themes = load_themes()
    t = themes.get(theme_id or load_board().get("theme", "red")) or next(iter(themes.values()))
    acc, text = t.get("line", t["accent"]), t["text"]
    base = (0.02, 0.02, 0.027)
    # activa: negro teñido del acento; inactiva: casi negra
    activa = tuple(b * 0.8 + a * 0.2 for b, a in zip(base, (acc.red, acc.green, acc.blue)))
    return {
        "decoration/active_color": _hex(activa, 0.97),
        "decoration/inactive_color": _hex(base, 0.92),
        "decoration/font_color": _hex((text.red, text.green, text.blue)),
        "decoration/font": FONT,
        "decoration/title_height": medidas()[0],
        "decoration/border_size": medidas()[1],
    }


def color_holo(theme_id: str | None = None) -> str:
    """Color del efecto holograma (plugin neural-holo) = acento del tema."""
    themes = load_themes()
    t = themes.get(theme_id or load_board().get("theme", "red")) or next(iter(themes.values()))
    acc = t["accent"]
    return _hex((acc.red, acc.green, acc.blue))


def aplicar(theme_id: str | None = None) -> bool:
    try:
        ipc = WayfireIPC()
        ipc.call("wayfire/set-config-options", opciones(theme_id))
    except Exception:  # sin IPC o sin plugin: no pasa nada
        return False
    holo = {"neural-holo/color": color_holo(theme_id)}
    try:
        ipc.call("wayfire/set-config-options", holo)
    except Exception:
        # XML de neural-holo instalado con Wayfire ya en marcha: recargar metadata y reintentar
        try:
            ipc.call("wayfire/reload-config-metadata")
            ipc.call("wayfire/set-config-options", holo)
        except Exception:
            pass
    return True
