"""Estilo comun de NEURAL//DECK nativo (dock, barra y panel rapido).

Temas leidos de web/themes.js (los mismos del wallpaper), las fuentes de
web/fonts, y el dibujo con cairo: marco HUD con esquinas cortadas y brillo,
pestaña tipo "TASK//" e iconos de linea. Importar despues de ensure_preload()
(ver layer.py), porque carga Gtk."""
from __future__ import annotations

import ctypes
import math
import re
from pathlib import Path

import cairo
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Graphene", "1.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, Graphene, Gtk, Pango, PangoCairo  # noqa: E402

WEB_DIR = Path(__file__).parent / "web"
PAD = 8  # margen alrededor de cada marco para el brillo y las escuadras
MONO = "Share Tech Mono"
SANS = "Rajdhani"


# ---------- temas (los mismos de web/themes.js) ----------
def rgba(css: str) -> Gdk.RGBA:
    c = Gdk.RGBA()
    if not c.parse(css):
        c.parse("#ffffff")
    return c


def load_themes() -> dict[str, dict]:
    """{id: {style, glow, accent, alt, warn, text, muted, panel}} leidos de
    themes.js (un objeto por tema: id, style, glow opcional, colors)."""
    themes: dict[str, dict] = {}
    try:
        text = (WEB_DIR / "themes.js").read_text()
    except OSError:
        text = ""
    for chunk in re.split(r"\{\s*\n\s*id:", text)[1:]:
        tid = re.match(r'\s*"([^"]+)"', chunk)
        cols = re.search(r"colors:\s*\{([^}]*)\}", chunk)
        if not tid or not cols:
            continue
        style = re.search(r'style:\s*"(\w+)"', chunk)
        style = style.group(1) if style else "grid"
        glow = re.search(r"glow:\s*([\d.]+)", chunk)
        theme = {"style": style, "glow": float(glow.group(1)) if glow else (0.5 if style == "synth" else 0.22)}
        theme.update({k: rgba(v) for k, v in re.findall(r'(\w+):\s*"([^"]+)"', cols.group(1))})
        # `line` (tema NEGRO): lineas y bordes de otro color que el acento.
        # Como en el CSS del wallpaper, el acento translúcido (bordes, fondos
        # de hover) sale de `line` y el sólido (textos, rellenos) del acento.
        if "line" in theme and "accent" in theme:
            theme["accent"]._line = theme["line"]
        themes[tid.group(1)] = theme
    if not themes:  # sin themes.js: el 2077
        themes["red"] = {"style": "grid", "glow": 0.22, "accent": rgba("#ff2d3f"), "alt": rgba("#00eaff"),
                         "warn": rgba("#fcee0a"), "text": rgba("#e9e4e4"), "muted": rgba("#8a5458"),
                         "panel": rgba("rgba(5, 5, 7, 0.94)")}
    return themes


def _linea(c: Gdk.RGBA, alpha: float) -> Gdk.RGBA:
    return getattr(c, "_line", c) if alpha < 1 else c


def css_color(c: Gdk.RGBA, alpha: float = 1.0) -> str:
    c = _linea(c, alpha)
    return f"rgba({round(c.red * 255)}, {round(c.green * 255)}, {round(c.blue * 255)}, {c.alpha * alpha:.3f})"


def register_fonts() -> None:
    """Las fuentes del wallpaper (web/fonts) tambien para Pango, sin instalarlas."""
    try:
        fc = ctypes.CDLL("libfontconfig.so.1")
        fc.FcConfigAppFontAddDir(None, str(WEB_DIR / "fonts").encode())
    except (OSError, AttributeError):
        pass


# ---------- dibujo ----------
def source(cr, c: Gdk.RGBA, alpha: float = 1.0) -> None:
    c = _linea(c, alpha)
    cr.set_source_rgba(c.red, c.green, c.blue, c.alpha * alpha)


def shape(cr, x, y, w, h, cut, corners) -> None:
    """Rectangulo con las esquinas de `corners` (tl, tr, br, bl) cortadas en diagonal."""
    tl, tr, br, bl = (cut if k in corners else 0 for k in ("tl", "tr", "br", "bl"))
    cr.move_to(x + tl, y)
    cr.line_to(x + w - tr, y)
    cr.line_to(x + w, y + tr)
    cr.line_to(x + w, y + h - br)
    cr.line_to(x + w - br, y + h)
    cr.line_to(x + bl, y + h)
    cr.line_to(x, y + h - bl)
    cr.line_to(x, y + tl)
    cr.close_path()


def draw_frame(cr, width, height, t: dict, cut, corners, brackets) -> None:
    x, y, w, h = PAD, PAD, width - 2 * PAD, height - 2 * PAD
    acc = t.get("line", t["accent"])
    # brillo: trazos anchos y tenues por fuera (por dentro los tapa el fondo)
    for lw, a in ((12, 0.25), (7, 0.45), (3.5, 0.8)):
        shape(cr, x, y, w, h, cut, corners)
        source(cr, acc, a * t["glow"])
        cr.set_line_width(lw)
        cr.stroke()
    shape(cr, x, y, w, h, cut, corners)
    source(cr, t["panel"])
    cr.fill_preserve()
    shine = cairo.LinearGradient(0, y, 0, y + h * 0.45)
    shine.add_color_stop_rgba(0, acc.red, acc.green, acc.blue, 0.07)
    shine.add_color_stop_rgba(1, acc.red, acc.green, acc.blue, 0)
    cr.set_source(shine)
    cr.fill()
    border = cairo.LinearGradient(x, y, x + w * 0.35, y + h)
    for stop, a in ((0, 1), (0.55, 0.45), (1, 1)):
        border.add_color_stop_rgba(stop, acc.red, acc.green, acc.blue, a)
    shape(cr, x + 0.75, y + 0.75, w - 1.5, h - 1.5, cut, corners)
    cr.set_source(border)
    cr.set_line_width(1.5)
    cr.stroke()
    if brackets:  # escuadras fuera del marco, como en los paneles
        source(cr, t.get("line", t["alt"]), 0.85)
        cr.set_line_width(2)
        bx0, bx1, by = x - 4, x + w + 4, y + h + 4
        cr.move_to(bx0, by - 11)
        cr.line_to(bx0, by)
        cr.line_to(bx0 + 11, by)
        cr.move_to(bx1, by - 11)
        cr.line_to(bx1, by)
        cr.line_to(bx1 - 11, by)
        cr.stroke()


def draw_tab(cr, x, y, t: dict, text: str) -> None:
    layout = PangoCairo.create_layout(cr)
    layout.set_font_description(Pango.FontDescription.from_string(f"{MONO} 7.5"))
    layout.set_text(text, -1)
    tw, th = layout.get_pixel_size()
    w, h = tw + 22, 13
    cr.move_to(x, y)
    cr.line_to(x + w, y)
    cr.line_to(x + w - 8, y + h)
    cr.line_to(x, y + h)
    cr.close_path()
    acc = t["accent"]
    if t["style"] == "synth":
        source(cr, acc)
        cr.fill()
        cr.set_source_rgb(0.04, 0.008, 0.016)
    else:
        cr.set_source_rgb(0.02, 0.02, 0.027)
        cr.fill()
        source(cr, acc)
        cr.rectangle(x, y + h - 1, w - 8, 1)
        cr.rectangle(x, y, 2, h)
        cr.fill()
        source(cr, acc)
    cr.move_to(x + 8, y + (h - th) / 2)
    PangoCairo.show_layout(cr, layout)


def draw_icon(cr, name: str, w, h, color: Gdk.RGBA, level: float | None = None, off: bool = False,
              extra: Gdk.RGBA | None = None) -> None:
    """Icono de linea de 16x16 escalado al centro de (w, h): wifi, vol, mic,
    sol, bat, bt, power. `level` 0..1 (barras de wifi, ondas, carga), `off`
    tachado / apagado, `extra` color de la carga cuando la bateria carga."""
    s = min(w, h) / 16
    cr.save()
    cr.translate((w - 16 * s) / 2, (h - 16 * s) / 2)
    cr.scale(s, s)
    cr.set_line_width(1.6)
    cr.set_line_cap(cairo.LINE_CAP_SQUARE)
    cr.set_line_join(cairo.LINE_JOIN_MITER)
    lv = 0 if level is None else max(0.0, min(1.0, level))
    source(cr, color, 0.45 if off else 1)
    if name == "wifi":
        cr.arc(8, 13.5, 1.5, 0, 2 * math.pi)
        cr.fill()
        for i, r in enumerate((4.5, 8, 11.5)):
            source(cr, color, 1 if not off and lv > i / 3 else 0.22)
            cr.arc(8, 13.5, r, -3 * math.pi / 4, -math.pi / 4)
            cr.stroke()
    elif name == "vol":
        cr.move_to(1.5, 5.5)
        cr.line_to(4.5, 5.5)
        cr.line_to(8, 2)
        cr.line_to(8, 14)
        cr.line_to(4.5, 10.5)
        cr.line_to(1.5, 10.5)
        cr.close_path()
        cr.fill()
        if off:
            cr.move_to(10.5, 5.5)
            cr.line_to(14.5, 10.5)
            cr.move_to(14.5, 5.5)
            cr.line_to(10.5, 10.5)
            cr.stroke()
        else:
            for i, r in enumerate((3.2, 6.2)):
                source(cr, color, 1 if lv > i / 2 + 0.01 else 0.22)
                cr.arc(8, 8, r, -math.pi / 4, math.pi / 4)
                cr.stroke()
    elif name == "mic":
        cr.rectangle(6, 1.5, 4, 8)
        cr.fill()
        cr.arc(8, 7, 4.8, 0, math.pi)
        cr.move_to(8, 11.8)
        cr.line_to(8, 14.5)
        cr.move_to(5, 14.5)
        cr.line_to(11, 14.5)
        cr.stroke()
        if off:
            source(cr, color, 1)
            cr.move_to(2, 2)
            cr.line_to(14, 14)
            cr.stroke()
    elif name == "kbd":  # teclado: marco, dos filas de teclas y barra espaciadora; level = luz
        cr.set_line_width(1.3)
        cr.rectangle(1, 4, 14, 9)
        cr.stroke()
        source(cr, color, 0.45 if off else (0.6 + 0.4 * lv))
        for i in range(4):
            cr.rectangle(3 + i * 2.8, 6, 1.6, 1.6)
            cr.rectangle(3 + i * 2.8, 8.4, 1.6, 1.6)
        cr.rectangle(5, 10.8, 6, 1.2)
        cr.fill()
    elif name == "sol":
        cr.arc(8, 8, 3, 0, 2 * math.pi)
        cr.fill() if lv >= 0.5 else cr.stroke()
        for k in range(8):
            a = k * math.pi / 4
            cr.move_to(8 + 5 * math.cos(a), 8 + 5 * math.sin(a))
            cr.line_to(8 + 7 * math.cos(a), 8 + 7 * math.sin(a))
        cr.stroke()
    elif name == "bat":
        cr.set_line_width(1.3)
        cr.rectangle(1.5, 4.5, 11.5, 7)
        cr.stroke()
        cr.rectangle(13.5, 6.5, 1.5, 3)
        cr.fill()
        if extra is not None:
            source(cr, extra)
        cr.rectangle(3, 6, 8.5 * lv, 4)
        cr.fill()
    elif name == "bt":
        cr.set_line_width(1.4)
        cr.move_to(4.5, 5)
        cr.line_to(11.5, 11)
        cr.line_to(8, 14)
        cr.line_to(8, 2)
        cr.line_to(11.5, 5)
        cr.line_to(4.5, 11)
        cr.stroke()
    elif name == "grid":  # todas las apps: 3x3 cuadros
        for i in range(3):
            for j in range(3):
                source(cr, color, 1 if (i + j) % 2 == 0 else 0.6)
                cr.rectangle(1.5 + i * 5, 1.5 + j * 5, 3.4, 3.4)
                cr.fill()
    elif name == "desk":  # ver escritorio: pantalla con pie; level=1 -> ventanas escondidas
        cr.rectangle(1.5, 2, 13, 9)
        cr.stroke()
        cr.move_to(8, 11); cr.line_to(8, 13.5)
        cr.move_to(4.5, 14); cr.line_to(11.5, 14)
        cr.stroke()
        if lv:  # escondidas: flecha arriba (volver)
            cr.move_to(5, 8); cr.line_to(8, 5); cr.line_to(11, 8)
        else:   # visibles: flecha abajo (esconder)
            cr.move_to(5, 5); cr.line_to(8, 8); cr.line_to(11, 5)
        cr.stroke()
    elif name == "bell":
        cr.move_to(3, 12)
        cr.line_to(13, 12)
        cr.move_to(4.5, 12)
        cr.line_to(4.5, 7)
        cr.arc(8, 7, 3.5, math.pi, 0)
        cr.line_to(11.5, 12)
        cr.stroke()
        cr.arc(8, 13.8, 1.4, 0, 2 * math.pi)
        cr.fill()
        if off:  # no molestar: tachada
            source(cr, color, 1)
            cr.move_to(2, 2)
            cr.line_to(14, 14)
            cr.stroke()
    elif name == "power":
        cr.arc(8, 8.8, 5.6, -math.pi / 2 + 0.6, 3 * math.pi / 2 - 0.6)
        cr.move_to(8, 1.5)
        cr.line_to(8, 8)
        cr.stroke()
    cr.restore()


def snapshot_children(widget: Gtk.Widget, snapshot) -> None:
    child = widget.get_first_child()
    while child is not None:
        widget.snapshot_child(child, snapshot)
        child = child.get_next_sibling()


class Frame(Gtk.Box):
    """Caja con el marco HUD dibujado detras de sus hijos (el padding de
    CSS/margenes deja sitio para el brillo, la pestaña y las escuadras).
    `owner` es la ventana que tiene el tema actual en .theme"""

    def __init__(self, owner, orientation, cut, corners, brackets, tab: str | None = None):
        super().__init__(orientation=orientation)
        self.owner, self.cut, self.corners, self.brackets, self.tab = owner, cut, corners, brackets, tab

    def do_snapshot(self, snapshot):
        w, h = self.get_width(), self.get_height()
        if w > 2 * PAD and h > 2 * PAD:
            cr = snapshot.append_cairo(Graphene.Rect().init(0, 0, w, h))
            draw_frame(cr, w, h, self.owner.theme, self.cut, self.corners, self.brackets)
            if self.tab:
                draw_tab(cr, PAD + 14, PAD + 1.5, self.owner.theme, self.tab)
            del cr
        snapshot_children(self, snapshot)
