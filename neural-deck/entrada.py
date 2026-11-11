"""Teclado, ratón y touchpad de NEURAL//DECK: sección [input] de ~/.config/wayfire.ini.

Igual que animaciones.py: la fuente de verdad es wayfire.ini (sobrevive a
reinicios sin demonio). Al cambiar algo se reescribe solo esa línea (Wayfire
recarga el archivo solo) y se manda también por IPC para que se note ya.
Sin GTK."""
from __future__ import annotations

from pathlib import Path

from animaciones import _leer_ini, _poner_ini
from wayfire_ipc import WayfireIPC

# opción de [input] → valor por defecto de Wayfire (metadata/input.xml)
DEFECTO: dict[str, object] = {
    "xkb_layout": "us", "xkb_variant": "", "xkb_options": "",
    "kb_repeat_delay": 400, "kb_repeat_rate": 40, "kb_numlock_default_state": False,
    "mouse_cursor_speed": 0.0, "mouse_accel_profile": "default", "mouse_natural_scroll": False,
    "mouse_scroll_speed": 1.0, "left_handed_mode": False, "middle_emulation": False,
    "touchpad_cursor_speed": 0.0, "touchpad_accel_profile": "default", "tap_to_click": True,
    "tap_and_drag": True, "natural_scroll": False, "disable_touchpad_while_typing": False,
    "disable_touchpad_while_mouse": False, "click_method": "default", "scroll_method": "default",
    "touchpad_scroll_speed": 1.0,
    "cursor_theme": "default", "cursor_size": 24,
}

# (layout, variante, nombre visible)
DISTRIBUCIONES = [
    ("us", "", "EE. UU."),
    ("us", "intl", "EE. UU. INTERNACIONAL (TILDES CON ')"),
    ("us", "altgr-intl", "EE. UU. ALTGR-INTL"),
    ("latam", "", "LATINOAMÉRICA"),
    ("es", "", "ESPAÑA"),
]
OPCIONES_XKB = [  # (opción xkb, nombre visible)
    ("caps:escape", "BLOQ MAYÚS = ESC"),
    ("caps:ctrl_modifier", "BLOQ MAYÚS = CTRL"),
    ("compose:ralt", "ALT DERECHO = COMPONER"),
    ("altwin:swap_lalt_lwin", "CAMBIAR ALT Y SUPER"),
]


def _a_valor(clave: str, texto: str):
    d = DEFECTO[clave]
    try:
        if isinstance(d, bool):
            return texto.strip().lower() in ("true", "1", "yes")
        if isinstance(d, int):
            return int(float(texto))
        if isinstance(d, float):
            return float(texto)
    except ValueError:
        return d
    return texto.strip()


def _a_texto(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        return f"{v:.2f}"
    return str(v)


def leer() -> dict:
    ini = _leer_ini()
    return {k: (_a_valor(k, ini[("input", k)]) if ("input", k) in ini else d) for k, d in DEFECTO.items()}


def guardar(**cambios) -> None:
    validos = {k: v for k, v in cambios.items() if k in DEFECTO}
    if not validos:
        return
    _poner_ini({("input", k): _a_texto(v) for k, v in validos.items()})
    try:
        WayfireIPC().call("wayfire/set-config-options", {f"input/{k}": v for k, v in validos.items()})
    except Exception:  # sin IPC: Wayfire lo recarga del ini
        pass


def temas_cursor() -> list[str]:
    """Temas de cursor instalados (carpetas con cursors/)."""
    vistos = set()
    for base in (Path.home() / ".local/share/icons", Path.home() / ".icons", Path("/usr/share/icons")):
        try:
            for d in base.iterdir():
                if (d / "cursors").is_dir():
                    vistos.add(d.name)
        except OSError:
            pass
    return sorted(vistos, key=str.lower)


def hay_touchpad() -> bool:
    try:
        texto = Path("/proc/bus/input/devices").read_text().lower()
    except OSError:
        return True
    return "touchpad" in texto or "trackpad" in texto
