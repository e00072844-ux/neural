"""Base común de las apps de ventana NEURAL (PAK, FILES…): marco HUD sin
decoración, cabecera arrastrable con código + título, botones de ventana,
pie con mensajes, redimensionar desde el brillo del marco y tema en vivo
(board.json). Cada app añade su CSS con AppNeural.css_extra(t).

Uso:
    class Mia(VentanaNeural):
        def __init__(self, app):
            super().__init__(app, "SYS//XXX", "NEURAL//XXX", (1000, 640))
            self.cab_centro.append(...)   # pestañas, buscador…
            self.cuerpo.append(...)        # contenido
"""
from __future__ import annotations

import ctypes
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from board import CONFIG_PATH, load_board  # noqa: E402
from estilo import MONO, PAD, SANS, Frame, css_color, load_themes, register_fonts  # noqa: E402

BORDE = PAD + 4  # zona para redimensionar (el brillo del marco)


def css_base(t: dict) -> str:
    acc, alt, warn, text, muted = t["accent"], t["alt"], t["warn"], t["text"], t["muted"]
    return f"""
window.neural-win {{ background: transparent; }}
.nx-code {{ font-family: "{MONO}"; font-size: 10px; letter-spacing: 1px; color: {css_color(muted)}; }}
.nx-code.on {{ color: {css_color(alt)}; }}
.nx-code.warn {{ color: {css_color(warn)}; }}
.nx-title {{ font-family: "{SANS}"; font-weight: 700; font-size: 15px; letter-spacing: 2px; color: {css_color(acc)}; }}
.nx-h1 {{ font-family: "{SANS}"; font-weight: 700; font-size: 20px; letter-spacing: 1px; color: {css_color(text)}; }}
.nx-h2 {{ font-family: "{SANS}"; font-weight: 700; font-size: 13px; letter-spacing: 3px; color: {css_color(acc)}; }}
.nx-txt {{ font-family: "{SANS}"; font-weight: 500; font-size: 14px; color: {css_color(text)}; }}
.nx-dim {{ font-family: "{SANS}"; font-weight: 500; font-size: 13px; color: {css_color(muted)}; }}
.nx-head {{ border-bottom: 1px solid {css_color(acc, 0.45)}; padding-bottom: 4px; }}
.nx-foot {{ border-top: 1px solid {css_color(acc, 0.25)}; padding-top: 3px; }}
.nx-btn {{ all: unset; min-width: 26px; padding: 1px 4px; font-family: "{MONO}"; font-size: 13px;
          color: {css_color(muted)}; border: 1px solid transparent; }}
.nx-btn:hover {{ color: {css_color(text)}; border-color: {css_color(acc, 0.6)}; background: {css_color(acc, 0.14)}; }}
.nx-btn.cerrar:hover {{ color: #000; background: {css_color(warn)}; border-color: {css_color(warn)}; }}
.nx-btn:disabled {{ opacity: 0.35; }}
.nx-tab {{ all: unset; padding: 4px 12px; font-family: "{SANS}"; font-weight: 700; font-size: 13px;
          letter-spacing: 2px; color: {css_color(muted)}; background: rgba(0, 0, 0, 0.45);
          border-bottom: 2px solid {css_color(acc, 0.25)}; }}
.nx-tab:hover {{ background: {css_color(acc, 0.12)}; color: {css_color(text)}; }}
.nx-tab.on {{ background: {css_color(acc, 0.18)}; border-bottom-color: {css_color(acc)}; color: {css_color(text)}; }}
.nx-accion {{ all: unset; padding: 3px 12px; font-family: "{SANS}"; font-weight: 700; font-size: 12px;
             letter-spacing: 2px; color: {css_color(acc)}; border: 1px solid {css_color(acc, 0.7)};
             background: {css_color(acc, 0.08)}; }}
.nx-accion:hover {{ background: {css_color(acc, 0.28)}; color: {css_color(text)}; }}
.nx-accion.fuerte {{ background: {css_color(acc)}; color: #000; }}
.nx-accion.fuerte:hover {{ background: {css_color(alt)}; border-color: {css_color(alt)}; }}
.nx-accion.peligro {{ color: {css_color(warn)}; border-color: {css_color(warn, 0.7)}; background: {css_color(warn, 0.06)}; }}
.nx-accion.peligro:hover {{ background: {css_color(warn)}; color: #000; }}
.nx-accion:disabled {{ opacity: 0.35; }}
.nx-chip {{ all: unset; padding: 2px 10px; font-family: "{MONO}"; font-size: 11px; letter-spacing: 1px;
           color: {css_color(muted)}; border: 1px solid {css_color(acc, 0.3)}; }}
.nx-chip:hover {{ color: {css_color(text)}; border-color: {css_color(acc)}; }}
.nx-chip.on {{ color: #000; background: {css_color(acc)}; border-color: {css_color(acc)}; }}
.nx-tag {{ font-family: "{MONO}"; font-size: 9px; letter-spacing: 1px; padding: 0 5px;
          color: {css_color(alt)}; border: 1px solid {css_color(alt, 0.6)}; }}
.nx-tag.warn {{ color: {css_color(warn)}; border-color: {css_color(warn, 0.6)}; }}
.nx-tag.acc {{ color: {css_color(acc)}; border-color: {css_color(acc, 0.6)}; }}
entry.nx-entry, .nx-entry {{ background: rgba(0, 0, 0, 0.6); border: 1px solid {css_color(acc, 0.6)}; border-radius: 0;
                 color: {css_color(text)}; font-family: "{MONO}"; min-height: 24px; box-shadow: none; outline: none; }}
.nx-entry:focus-within {{ border-color: {css_color(alt)}; }}
scrolledwindow.nx-scroll, scrolledwindow.nx-scroll > viewport {{ background: transparent; }}
scrolledwindow.nx-scroll scrollbar {{ background: transparent; border: none; }}
scrolledwindow.nx-scroll scrollbar slider {{ background: {css_color(acc, 0.55)}; border-radius: 0; min-width: 4px; }}
list.nx-lista, list.nx-lista > row {{ background: transparent; }}
list.nx-lista > row {{ padding: 0; border-radius: 0; outline: none; }}
list.nx-lista > row:selected, list.nx-lista > row:hover {{ background: transparent; }}
.nx-card {{ padding: 8px 10px; background: rgba(0, 0, 0, 0.35); border-left: 2px solid {css_color(acc, 0.25)}; }}
.nx-card:hover {{ background: {css_color(acc, 0.10)}; border-left-color: {css_color(acc)}; }}
.nx-card.sel {{ background: {css_color(acc, 0.16)}; border-left-color: {css_color(alt)}; }}
.nx-lado {{ background: rgba(0, 0, 0, 0.45); border-left: 1px solid {css_color(acc, 0.35)}; padding: 10px 12px; }}
.nx-sep {{ background: {css_color(acc, 0.25)}; min-height: 1px; }}
progressbar.nx-prog trough {{ background: rgba(0, 0, 0, 0.6); border-radius: 0; min-height: 4px; border: none; }}
progressbar.nx-prog progress {{ background: {css_color(acc)}; border-radius: 0; min-height: 4px; }}
textview.nx-log, textview.nx-log text {{ background: rgba(0, 0, 0, 0.55); color: {css_color(text)};
                                     font-family: "{MONO}"; font-size: 11px; }}
popover.nx-menu > contents {{ background: rgba(5, 5, 8, 0.96); border: 1px solid {css_color(acc, 0.7)};
                             border-radius: 0; padding: 4px; box-shadow: 0 0 10px {css_color(acc, 0.35)}; }}
popover.nx-menu > arrow {{ background: transparent; border: none; }}
.nx-item {{ all: unset; padding: 4px 14px 4px 8px; font-family: "{SANS}"; font-weight: 600; font-size: 13px;
           letter-spacing: 1px; color: {css_color(text)}; border-left: 2px solid transparent; }}
.nx-item:hover {{ background: {css_color(acc, 0.16)}; border-left-color: {css_color(acc)}; }}
.nx-item.peligro {{ color: {css_color(warn)}; }}
.nx-item-k {{ font-family: "{MONO}"; font-size: 10px; color: {css_color(muted)}; }}
"""


def lbl(text: str, cls: str, **kw) -> Gtk.Label:
    lb = Gtk.Label(label=text, **kw)
    for c in cls.split():
        lb.add_css_class(c)
    return lb


def on(w: Gtk.Widget, cls: str, activo: bool) -> None:
    (w.add_css_class if activo else w.remove_css_class)(cls)


def boton(texto: str, cls: str, accion, tip: str | None = None) -> Gtk.Button:
    b = Gtk.Button(label=texto, tooltip_text=tip)
    for c in cls.split():
        b.add_css_class(c)
    b.connect("clicked", lambda *_: accion())
    return b


def nombrar_proceso(nombre: str) -> None:
    try:
        ctypes.CDLL(None).prctl(15, nombre.encode(), 0, 0, 0)
    except (OSError, AttributeError):
        pass


def tam_humano(n: float) -> str:
    for u in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or u == "TB":
            return f"{n:.0f} {u}" if u == "B" else f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} TB"


class VentanaNeural(Gtk.ApplicationWindow):
    def __init__(self, app: "AppNeural", codigo: str, titulo: str, tam=(1000, 640)):
        super().__init__(application=app, title=titulo)
        self.app = app
        self.theme = self._opaco(app.theme)
        self.set_decorated(False)
        self.add_css_class("neural-win")
        self.set_default_size(*tam)

        self.marco = Frame(self, Gtk.Orientation.VERTICAL, 14, ("tl", "br"), True, None)
        caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_start=PAD + 10,
                       margin_end=PAD + 10, margin_top=PAD + 6, margin_bottom=PAD + 6, vexpand=True)
        self.marco.append(caja)

        cab = Gtk.Box(spacing=12)
        cab.add_css_class("nx-head")
        tit = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
        tit.append(lbl(codigo, "nx-code", xalign=0))
        tit.append(lbl(titulo, "nx-title", xalign=0))
        cab.append(tit)
        self.cab_centro = Gtk.Box(spacing=6, hexpand=True, valign=Gtk.Align.END)
        cab.append(self.cab_centro)
        botones = Gtk.Box(spacing=2, valign=Gtk.Align.START)
        botones.append(boton("─", "nx-btn", self.minimize, "Minimizar"))
        botones.append(boton("□", "nx-btn", self._max, "Maximizar"))
        cerrar = boton("✕", "nx-btn cerrar", self.close, "Cerrar")
        botones.append(cerrar)
        cab.append(botones)
        caja.append(Gtk.WindowHandle(child=cab))

        self.cuerpo = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, vexpand=True, hexpand=True)
        caja.append(self.cuerpo)

        self.pie = Gtk.Box(spacing=14)
        self.pie.add_css_class("nx-foot")
        self.p_izq = lbl("", "nx-code", xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        self.p_msg = lbl("", "nx-code", xalign=1)
        self.pie.append(self.p_izq)
        self.pie.append(self.p_msg)
        caja.append(self.pie)
        self.set_child(self.marco)

        clic = Gtk.GestureClick(button=1)
        clic.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        clic.connect("pressed", self._borde_pulsado)
        self.add_controller(clic)
        mov = Gtk.EventControllerMotion()
        mov.connect("motion", lambda _c, x, y: self.set_cursor_from_name(self._cursor_borde(x, y)))
        mov.connect("leave", lambda *_: self.set_cursor_from_name(None))
        self.add_controller(mov)
        self._aviso_id = 0

    def _max(self):
        self.unmaximize() if self.is_maximized() else self.maximize()

    def aviso(self, texto: str, warn: bool = False, ms: int = 4000):
        self.p_msg.set_text(texto)
        on(self.p_msg, "warn", warn)
        on(self.p_msg, "on", not warn)
        if self._aviso_id:
            GLib.source_remove(self._aviso_id)
        self._aviso_id = GLib.timeout_add(ms, self._borrar_aviso)

    def _borrar_aviso(self):
        self.p_msg.set_text("")
        self._aviso_id = 0
        return False

    @staticmethod
    def _opaco(t: dict) -> dict:
        """Las apps llevan mucho texto: el panel casi opaco para que no se vea el fondo."""
        panel = t["panel"].copy()
        panel.alpha = max(panel.alpha, 0.98)
        return dict(t, panel=panel)

    def nuevo_tema(self):
        self.theme = self._opaco(self.app.theme)
        self.marco.queue_draw()

    # ---- redimensionar ----
    def _borde(self, x, y):
        if self.is_maximized() or self.is_fullscreen():
            return None
        w, h = self.get_width(), self.get_height()
        n, s, o, e = y < BORDE, y > h - BORDE, x < BORDE, x > w - BORDE
        E = Gdk.SurfaceEdge
        return {(1, 0, 1, 0): E.NORTH_WEST, (1, 0, 0, 1): E.NORTH_EAST, (0, 1, 1, 0): E.SOUTH_WEST,
                (0, 1, 0, 1): E.SOUTH_EAST, (1, 0, 0, 0): E.NORTH, (0, 1, 0, 0): E.SOUTH,
                (0, 0, 1, 0): E.WEST, (0, 0, 0, 1): E.EAST}.get((int(n), int(s), int(o), int(e)))

    def _cursor_borde(self, x, y):
        b = self._borde(x, y)
        if b is None:
            return None
        E = Gdk.SurfaceEdge
        return {E.NORTH_WEST: "nw-resize", E.NORTH_EAST: "ne-resize", E.SOUTH_WEST: "sw-resize",
                E.SOUTH_EAST: "se-resize", E.NORTH: "n-resize", E.SOUTH: "s-resize",
                E.WEST: "w-resize", E.EAST: "e-resize"}[b]

    def _borde_pulsado(self, gesto, _n, x, y):
        b = self._borde(x, y)
        if b is None:
            return
        gesto.set_state(Gtk.EventSequenceState.CLAIMED)
        superficie = self.get_surface()
        if isinstance(superficie, Gdk.Toplevel):
            superficie.begin_resize(b, gesto.get_device(), 1, x, y, gesto.get_current_event_time())


class AppNeural(Gtk.Application):
    """Gtk.Application con el tema NEURAL en vivo. Las subclases definen
    css_extra(t) y crear_ventana(args, cwd)."""

    def __init__(self, app_id: str):
        super().__init__(application_id=app_id, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.theme = None
        self.css = None

    def css_extra(self, t: dict) -> str:
        return ""

    def do_startup(self):
        Gtk.Application.do_startup(self)
        register_fonts()
        self._tema()
        self._mon = Gio.File.new_for_path(str(CONFIG_PATH)).monitor_file(Gio.FileMonitorFlags.NONE, None)
        self._mon.connect("changed", lambda *_: GLib.timeout_add(300, lambda: (self._tema(), False)[1]))

    def _tema(self):
        themes = load_themes()
        theme = themes.get(load_board().get("theme", "red"), next(iter(themes.values())))
        if theme == self.theme:
            return
        self.theme = theme
        display = Gdk.Display.get_default()
        if self.css is not None:
            Gtk.StyleContext.remove_provider_for_display(display, self.css)
        self.css = Gtk.CssProvider()
        self.css.load_from_string(css_base(theme) + self.css_extra(theme))
        Gtk.StyleContext.add_provider_for_display(display, self.css, Gtk.STYLE_PROVIDER_PRIORITY_USER + 1)
        for w in self.get_windows():
            if isinstance(w, VentanaNeural):
                w.nuevo_tema()

    def crear_ventana(self, args: list[str], cwd: str | None):
        raise NotImplementedError

    def traer(self, ventana: Gtk.Window):
        """present() no restaura una ventana minimizada en Wayfire (sin token
        de xdg-activation no hace nada), así que la desminimizamos y la
        enfocamos por IPC antes de presentarla."""
        try:
            import os
            from wayfire_ipc import WayfireIPC
            ipc = WayfireIPC()
            for v in ipc.call("window-rules/list-views"):
                if v.get("pid") == os.getpid() and v.get("role") == "toplevel":
                    if v.get("minimized"):
                        ipc.call("wm-actions/set-minimized", {"view_id": v["id"], "state": False})
                    ipc.call("window-rules/focus-view", {"id": v["id"]})
        except Exception:
            pass
        ventana.present()

    def do_command_line(self, linea):
        self.crear_ventana(linea.get_arguments()[1:], linea.get_cwd())
        return 0
