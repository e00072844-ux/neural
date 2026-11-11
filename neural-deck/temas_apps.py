#!/usr/bin/env python3
"""Tema NEURAL para las apps GTK3 y GTK4/libadwaita (las del sistema y las
Flatpak): escribe ~/.config/gtk-3.0/gtk.css y ~/.config/gtk-4.0/gtk.css con
los colores del tema activo (fondo negro puro, acento del tema).

Dos intensidades (AJUSTES > APARIENCIA, sección "apps" de ajustes.json):
  suave     las apps conservan su aspecto oscuro y solo toman el acento NEURAL
            (selección, botón principal, foco, casillas, barras de progreso)
  completo  fondo negro puro, bordes y botones con el color del tema
Las apps Qt usan un tema Kvantum "NEURAL" generado aquí (QT_STYLE_OVERRIDE=kvantum
en ~/.config/environment.d) y los Flatpak reciben permiso de solo lectura para
leer estas carpetas (una vez, con flatpak override --user).

app.py lo llama al cambiar de tema (como decoracion.aplicar). Las ventanas ya
abiertas no cambian: el tema se ve al abrir la app de nuevo.
Uso a mano:  python3 ~/neural-deck/temas_apps.py [--tema ID]"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import energia
from board import load_board
from estilo import load_themes

CONFIG = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
CABECERA = "/* Lo escribe ~/neural-deck/temas_apps.py con el tema NEURAL activo: no editar a mano.\n   Retoques propios: ponlos en neural-extra.css de esta carpeta. */\n"


def _rgb(c) -> tuple[float, float, float]:
    return (c.red, c.green, c.blue)


def _hex(rgb) -> str:
    return "#" + "".join(f"{max(0, min(255, round(v * 255))):02x}" for v in rgb)


def _mezcla(a, b, t: float):
    """t=0 -> a, t=1 -> b"""
    return tuple(x * (1 - t) + y * t for x, y in zip(a, b))


def _luz(rgb) -> float:
    r, g, b = rgb
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def paleta(theme_id: str | None = None) -> dict[str, str]:
    themes = load_themes()
    t = themes.get(theme_id or load_board().get("theme", "red")) or next(iter(themes.values()))
    negro = (0.0, 0.0, 0.0)
    acc, alt, warn = _rgb(t["accent"]), _rgb(t["alt"]), _rgb(t["warn"])
    text, muted = _rgb(t["text"]), _rgb(t["muted"])
    # tema con `line` (NEGRO): bordes de ese color, casi invisibles
    borde = _hex(_rgb(t["line"])) if "line" in t else _hex(_mezcla(negro, acc, 0.30))
    return {
        "bg": "#000000",
        "raised": _hex(_mezcla(negro, acc, 0.06)),   # botones, entradas, tarjetas
        "hover": _hex(_mezcla(negro, acc, 0.14)),
        "border": borde,
        "sel": _hex(_mezcla(negro, acc, 0.40)),      # selección (texto claro encima)
        "sel_suave": _hex(_mezcla(negro, acc, 0.62)),  # selección sobre fondos grises
        "accent": _hex(acc),
        "accent_hi": _hex(_mezcla(acc, (1, 1, 1), 0.3)),
        "on_accent": "#000000" if _luz(acc) > 0.35 else "#ffffff",
        "alt": _hex(alt),
        "warn": _hex(warn),
        "text": _hex(text),
        "muted": _hex(muted),
    }


def _comun(p: dict) -> str:
    """Reglas que valen en GTK3 y GTK4 (sin libadwaita, o por encima de él)."""
    return f"""
window, .background, dialog, filechooser, headerbar, .titlebar, actionbar,
scrolledwindow, viewport, list, listview, gridview, columnview, treeview.view,
placessidebar, .sidebar, .navigation-sidebar, paned, popover > contents, popover.background, menu, .menu {{
  background-color: {p['bg']}; background-image: none;
  color: {p['text']}; border-color: {p['border']}; box-shadow: none;
}}
headerbar, .titlebar {{ border-bottom: 1px solid {p['border']}; }}
row:hover, treeview.view:hover {{ background-color: {p['hover']}; }}
row:selected, treeview.view:selected, *:selected, selection {{
  background-color: {p['sel']}; color: {p['text']};
}}
entry, spinbutton, button, combobox button, dropdown > button, .linked > button {{
  background-color: {p['raised']}; background-image: none; color: {p['text']};
  border: 1px solid {p['border']}; box-shadow: none; text-shadow: none;
}}
button.flat {{ background-color: transparent; border-color: transparent; }}
button:hover, button.flat:hover {{ background-color: {p['hover']}; border-color: {p['accent']}; }}
entry:focus, entry:focus-within, spinbutton:focus-within {{ border-color: {p['accent']}; }}
button.suggested-action {{ background-color: {p['accent']}; color: {p['on_accent']}; border-color: {p['accent']}; }}
button.suggested-action:hover {{ background-color: {p['accent_hi']}; }}
button.destructive-action {{ background-color: {p['bg']}; color: {p['warn']}; border-color: {p['warn']}; }}
.dim-label, label.dim-label, .subtitle {{ color: {p['muted']}; }}
link, *:link {{ color: {p['alt']}; }}
check:checked, radio:checked, switch:checked {{ background-color: {p['accent']}; color: {p['on_accent']}; border-color: {p['accent']}; }}
progressbar progress, scale highlight, levelbar block.filled {{ background-color: {p['accent']}; border-color: {p['accent']}; }}
scrollbar, scrollbar trough {{ background-color: {p['bg']}; }}
scrollbar slider {{ background-color: {p['muted']}; }}
scrollbar slider:hover {{ background-color: {p['accent']}; }}
tooltip, tooltip.background {{ background-color: {p['raised']}; color: {p['text']}; border: 1px solid {p['border']}; }}
"""


def intensidad() -> str:
    v = energia.leer_todo().get("apps", {})
    return v.get("intensidad") if isinstance(v, dict) and v.get("intensidad") in ("suave", "completo") else "suave"


def _suave(p: dict) -> str:
    """Solo el acento: fondos, botones normales y formas siguen siendo los de la app."""
    return f"""
*:selected, selection, row:selected, treeview.view:selected, flowboxchild:selected {{
  background-color: {p['sel_suave']}; color: #ffffff;
}}
button.suggested-action, button.suggested-action.flat {{
  background-color: {p['accent']}; background-image: none; color: {p['on_accent']}; border-color: {p['accent']};
}}
button.suggested-action:hover {{ background-color: {p['accent_hi']}; }}
check:checked, radio:checked, switch:checked {{
  background-color: {p['accent']}; background-image: none; color: {p['on_accent']}; border-color: {p['accent']};
}}
progressbar progress, scale highlight, levelbar block.filled {{
  background-color: {p['accent']}; background-image: none; border-color: {p['accent']};
}}
entry:focus, entry:focus-within, spinbutton:focus-within, textview:focus-within {{ border-color: {p['accent']}; }}
link, *:link {{ color: {p['alt']}; }}
"""


def css_gtk3(p: dict, modo: str = "completo") -> str:
    if modo == "suave":
        return (CABECERA + f"""
@define-color theme_selected_bg_color {p['sel_suave']};
@define-color theme_selected_fg_color #ffffff;
@define-color selected_bg_color {p['sel_suave']};
""" + _suave(p) + '\n@import url("neural-extra.css");\n')
    return CABECERA + f"""
@define-color theme_bg_color {p['bg']};
@define-color theme_base_color {p['bg']};
@define-color theme_fg_color {p['text']};
@define-color theme_text_color {p['text']};
@define-color theme_selected_bg_color {p['accent']};
@define-color theme_selected_fg_color {p['on_accent']};
@define-color insensitive_bg_color {p['raised']};
@define-color insensitive_fg_color {p['muted']};
@define-color borders {p['border']};
@define-color unfocused_borders {p['border']};
@define-color warning_color {p['warn']};
""" + _comun(p) + '\n@import url("neural-extra.css");\n'


def css_gtk4(p: dict, modo: str = "completo") -> str:
    if modo == "suave":  # libadwaita: solo el color de acento
        nombres = {"accent_color": p["accent"], "accent_bg_color": p["accent"], "accent_fg_color": p["on_accent"],
                   "theme_selected_bg_color": p["sel_suave"], "theme_selected_fg_color": "#ffffff"}
        defs = "".join(f"@define-color {k} {v};\n" for k, v in nombres.items())
        return CABECERA + "\n" + defs + _suave(p) + '\n@import url("neural-extra.css");\n'

    # nombres de color de libadwaita (las apps sin libadwaita usan las reglas comunes)
    nombres = {
        "accent_color": p["accent"], "accent_bg_color": p["accent"], "accent_fg_color": p["on_accent"],
        "window_bg_color": p["bg"], "window_fg_color": p["text"],
        "view_bg_color": p["bg"], "view_fg_color": p["text"],
        "headerbar_bg_color": p["bg"], "headerbar_fg_color": p["text"],
        "headerbar_backdrop_color": p["bg"], "headerbar_border_color": p["border"],
        "sidebar_bg_color": p["bg"], "sidebar_fg_color": p["text"], "sidebar_backdrop_color": p["bg"],
        "secondary_sidebar_bg_color": p["bg"], "secondary_sidebar_fg_color": p["text"],
        "card_bg_color": p["raised"], "card_fg_color": p["text"],
        "dialog_bg_color": p["bg"], "dialog_fg_color": p["text"],
        "popover_bg_color": p["bg"], "popover_fg_color": p["text"],
        "thumbnail_bg_color": p["raised"], "thumbnail_fg_color": p["text"],
        "warning_color": p["warn"], "warning_bg_color": p["warn"],
        # GTK4 sin libadwaita (tema Default)
        "theme_bg_color": p["bg"], "theme_base_color": p["bg"], "theme_fg_color": p["text"],
        "theme_text_color": p["text"], "theme_selected_bg_color": p["accent"],
        "theme_selected_fg_color": p["on_accent"], "borders": p["border"],
    }
    defs = "".join(f"@define-color {k} {v};\n" for k, v in nombres.items())
    return CABECERA + "\n" + defs + _comun(p) + '\n@import url("neural-extra.css");\n'


def _escribir(ruta: Path, texto: str) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    if ruta.exists() and ruta.read_text() == texto:
        return
    tmp = ruta.with_suffix(".css.tmp")
    tmp.write_text(texto)
    os.replace(tmp, ruta)


# ---------- fastfetch (saludo de la terminal) con los colores del tema ----------
LOGO_FF = r"""$1    ╔╗╔╔═╗╦ ╦╦═╗╔═╗╦
$1    ║║║║╣ ║ ║╠╦╝╠═╣║
$1    ╝╚╝╚═╝╚═╝╩╚═╩ ╩╩═╝
$2   ━━━━━━━━━━━━━━━━━━━━
$2      ◢◤  SYS//DECK  ◥◣
$1         ╭──────╮
$1      ╭──┤ $2◉◉◉◉$1 ├──╮
$1      │  ╰──────╯  │
$1      ╰─── $2T14$1 ────╯
"""


def _ansi(hexc: str) -> str:
    """#rrggbb -> código de color de 24 bits para fastfetch"""
    h = hexc.lstrip("#")
    return "38;2;" + ";".join(str(int(h[i:i + 2], 16)) for i in (0, 2, 4))


def fastfetch_config(p: dict, logo: Path) -> str:
    import json
    acc, alt, muted = _ansi(p["accent"]), _ansi(p["alt"]), _ansi(p["muted"])
    cfg = {
        "logo": {"source": str(logo), "type": "file", "color": {"1": acc, "2": alt}, "padding": {"top": 1, "right": 3}},
        "display": {"separator": "  ", "color": {"keys": acc, "title": alt}, "key": {"width": 12}},
        "modules": [
            {"type": "title", "format": "{user-name}@{host-name} // NEURAL"},
            {"type": "custom", "format": f"\u001b[{muted}m" + "─" * 32},
            {"type": "os", "key": "SYS//OS"},
            {"type": "kernel", "key": "SYS//KERNEL"},
            {"type": "wm", "key": "SYS//WM"},
            {"type": "uptime", "key": "SYS//UPTIME"},
            {"type": "packages", "key": "SYS//PKGS"},
            {"type": "cpu", "key": "HW//CPU", "format": "{name} ({cores-logical})"},
            {"type": "gpu", "key": "HW//GPU", "format": "{name}"},
            {"type": "memory", "key": "HW//RAM"},
            {"type": "disk", "key": "HW//DISK", "folders": "/"},
            {"type": "battery", "key": "HW//BAT"},
            {"type": "localip", "key": "NET//IP", "showIpv6": False},
            "break",
            {"type": "colors", "symbol": "circle"},
        ],
    }
    return ("// Lo escribe ~/neural-deck/temas_apps.py con el tema NEURAL activo: no editar a mano.\n"
            "// Para quitar NEURAL de fastfetch: borra este archivo y crea uno propio\n"
            "// (si la primera línea ya no es esta, temas_apps.py no lo toca).\n"
            + json.dumps(cfg, indent=2, ensure_ascii=False) + "\n")


def aplicar_fastfetch(p: dict) -> None:
    carpeta = CONFIG / "fastfetch"
    cfg = carpeta / "config.jsonc"
    if cfg.exists() and not cfg.read_text().startswith("// Lo escribe ~/neural-deck/temas_apps.py"):
        return  # config propia del usuario: no se pisa
    logo = carpeta / "neural-logo.txt"
    _escribir(logo, LOGO_FF)
    _escribir(cfg, fastfetch_config(p, logo))


# ---------- apps Qt: tema Kvantum "NEURAL" (base KvGnomeDark + colores del tema) ----------
KV_BASE = Path("/usr/share/Kvantum/KvGnomeDark")
KV_DIR = CONFIG / "Kvantum"
KV_AZUL = "#15539e"  # acento de KvGnomeDark en su .svg y su paleta
ENTORNO_QT = CONFIG / "environment.d" / "90-neural-qt.conf"


def _kv_colores(p: dict, modo: str) -> dict[str, str]:
    c = {"highlight.color": p["sel_suave"], "inactive.highlight.color": p["sel_suave"],
         "highlight.text.color": "#ffffff", "link.color": p["alt"], "link.visited.color": p["muted"]}
    if modo == "completo":
        c.update({k: p["bg"] for k in ("window.color", "inactive.window.color", "base.color",
                                       "inactive.base.color", "dark.color")})
        c.update({"alt.base.color": p["raised"], "inactive.alt.base.color": p["raised"],
                  "button.color": p["raised"], "mid.color": p["border"], "text.color": p["text"],
                  "window.text.color": p["text"], "button.text.color": p["text"]})
    return c


def aplicar_kvantum(p: dict, modo: str) -> None:
    if not KV_BASE.is_dir():
        return
    dest = KV_DIR / "NEURAL"
    cfg = (KV_BASE / "KvGnomeDark.kvconfig").read_text()
    for k, v in _kv_colores(p, modo).items():
        cfg = re.sub(rf"(?m)^{re.escape(k)}=.*$", f"{k}={v}", cfg)
    svg = (KV_BASE / "KvGnomeDark.svg").read_text().replace(KV_AZUL, p["sel_suave"])
    if modo == "completo":  # fondos grises del svg -> negro con un toque de acento
        for gris, nuevo in (("#353535", p["bg"]), ("#2d2d2d", p["raised"]), ("#373737", p["raised"]),
                            ("#1b1b1b", p["bg"]), ("#1e1e1e", p["bg"])):
            svg = svg.replace(gris, nuevo)
    _escribir(dest / "NEURAL.kvconfig", cfg)
    _escribir(dest / "NEURAL.svg", svg)
    elegido = KV_DIR / "kvantum.kvconfig"
    actual = elegido.read_text() if elegido.exists() else ""
    if "theme=NEURAL" not in actual:
        if actual and not elegido.with_name("kvantum.kvconfig.antes-neural").exists():
            shutil.copy2(elegido, elegido.with_name("kvantum.kvconfig.antes-neural"))
        _escribir(elegido, "[General]\ntheme=NEURAL\n")


def preparar_sistema() -> None:
    """Una vez: modo oscuro, Qt con Kvantum y permisos de lectura para los Flatpak."""
    _escribir(ENTORNO_QT, "# Lo escribe ~/neural-deck/temas_apps.py: apps Qt con el tema Kvantum NEURAL\n"
                          "QT_STYLE_OVERRIDE=kvantum\n")
    try:
        if "prefer-dark" not in subprocess.run(["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"],
                                               capture_output=True, text=True, timeout=5).stdout:
            subprocess.run(["gsettings", "set", "org.gnome.desktop.interface", "color-scheme", "prefer-dark"],
                           timeout=5)
    except (OSError, subprocess.SubprocessError):
        pass
    global_ov = Path.home() / ".local/share/flatpak/overrides/global"
    if shutil.which("flatpak") and "Kvantum" not in (global_ov.read_text() if global_ov.exists() else ""):
        subprocess.run(["flatpak", "override", "--user",
                        "--filesystem=xdg-config/gtk-3.0:ro", "--filesystem=xdg-config/gtk-4.0:ro",
                        "--filesystem=xdg-config/Kvantum:ro", "--env=QT_STYLE_OVERRIDE=kvantum"],
                       capture_output=True, timeout=20)


def aplicar(theme_id: str | None = None) -> bool:
    try:
        p = paleta(theme_id)
        modo = intensidad()
        aplicar_fastfetch(p)
        aplicar_kvantum(p, modo)
        preparar_sistema()
        for carpeta, css in (("gtk-3.0", css_gtk3(p, modo)), ("gtk-4.0", css_gtk4(p, modo))):
            extra = CONFIG / carpeta / "neural-extra.css"
            if not extra.exists():
                _escribir(extra, "/* retoques propios para el tema NEURAL (este archivo no se sobrescribe) */\n")
            _escribir(CONFIG / carpeta / "gtk.css", css)
        return True
    except Exception as e:  # nunca tumbar app.py por esto
        print(f"temas_apps: {e}", file=sys.stderr)
        return False


if __name__ == "__main__":
    tema = sys.argv[sys.argv.index("--tema") + 1] if "--tema" in sys.argv else None
    sys.exit(0 if aplicar(tema) else 1)
