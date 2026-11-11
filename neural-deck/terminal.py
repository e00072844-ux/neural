"""Terminal real (VTE) incrustada en el wallpaper.

La pagina dibuja el panel HUD (marco, titulo, pie) y le dice a Python donde
quedo el hueco del cuerpo; aqui se coloca encima un Vte.Terminal con fondo
transparente, asi la terminal se ve "dentro" del panel. Los colores tambien
los manda la pagina (calculados del tema activo), para que cualquier tema
nuevo se aplique solo."""
from __future__ import annotations

import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Vte", "3.91")
from gi.repository import Gdk, GLib, Gtk, Pango, Vte

SHELL = ["/bin/bash"]
FONT = "Source Code Pro Medium 11"
SCROLLBACK = 5000
FONT_SCALES = (0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.35, 1.5, 1.75, 2.0)

_CSS = """
scrolledwindow.wallpaper-term-pane, scrolledwindow.wallpaper-term-pane > viewport {{ background: transparent; }}
vte-terminal.wallpaper-term {{ padding: 4px 6px; background: transparent; }}
scrolledwindow.wallpaper-term-pane scrollbar {{ background: transparent; border: none; }}
scrolledwindow.wallpaper-term-pane scrollbar slider {{ background: {slider}; border-radius: 0; min-width: 4px; }}
"""


def _rgba(css: str) -> Gdk.RGBA:
    c = Gdk.RGBA()
    if not c.parse(css):
        c.parse("#ffffff")
    return c


class TerminalPane(Gtk.ScrolledWindow):
    """Lo que va en el overlay: la VTE dentro de un ScrolledWindow. Hace falta
    el contenedor porque el overlay le da a cada capa su tamaño *natural*, y
    el de la VTE son 80x24 celdas sin importar el hueco del panel; el
    ScrolledWindow en cambio respeta exactamente el tamaño que se le pide.
    De paso da una barra de scroll fina, teñida con el tema."""

    def __init__(self):
        super().__init__()
        self.add_css_class("wallpaper-term-pane")
        self.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.set_overlay_scrolling(True)
        self.set_propagate_natural_width(False)
        self.set_propagate_natural_height(False)
        self.set_halign(Gtk.Align.START)
        self.set_valign(Gtk.Align.START)
        self.set_visible(False)
        self._css = Gtk.CssProvider()
        self._set_css("rgba(255, 255, 255, 0.4)")
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), self._css, Gtk.STYLE_PROVIDER_PRIORITY_USER + 1
        )
        self.vte = WallpaperTerminal()
        self.set_child(self.vte)

    def _set_css(self, slider: str) -> None:
        self._css.load_from_string(_CSS.format(slider=slider))

    # ---- geometria (la decide la pagina) ----
    def place(self, visible: bool, x: float = 0, y: float = 0, w: float = 0, h: float = 0) -> None:
        # un panel puede quedar medio fuera de la pantalla: recortar a lo visible
        x0, y0 = max(0, round(x)), max(0, round(y))
        w = round(w) - (x0 - round(x))
        h = round(h) - (y0 - round(y))
        if not visible or w < 40 or h < 20:
            self.set_visible(False)
            return
        self.set_margin_start(x0)
        self.set_margin_top(y0)
        self.set_size_request(w, h)
        self.set_visible(True)

    # ---- colores (los calcula la pagina a partir del tema) ----
    def apply_palette(self, pal: dict) -> None:
        self.vte.apply_palette(pal)
        if pal.get("cursor"):
            self._set_css(pal["cursor"])

    def focus_terminal(self) -> None:
        self.vte.grab_focus()

    def shutdown(self) -> None:
        self.vte.closing = True


class WallpaperTerminal(Vte.Terminal):
    def __init__(self):
        super().__init__()
        self.add_css_class("wallpaper-term")
        self.set_font(Pango.FontDescription.from_string(FONT))
        self.set_scrollback_lines(SCROLLBACK)
        self.set_cursor_shape(Vte.CursorShape.BLOCK)
        self.set_cursor_blink_mode(Vte.CursorBlinkMode.ON)
        self.set_mouse_autohide(True)
        self.set_audible_bell(False)
        self.set_bold_is_bright(True)
        self.set_allow_hyperlink(True)
        self.set_scroll_on_keystroke(True)
        self.set_scroll_on_output(False)

        self._scale_idx = FONT_SCALES.index(1.0)
        self.child_pid: int | None = None
        self.closing = False

        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)

        self.connect("child-exited", self._on_child_exited)
        self.spawn()

    # ---- shell ----
    def spawn(self) -> None:
        self.reset(True, True)
        self.spawn_async(
            Vte.PtyFlags.DEFAULT,
            os.path.expanduser("~"),
            SHELL,
            # se suma al entorno; el .bashrc la usa para no lanzar fastfetch aqui
            ["LIVE_WALLPAPER_TERM=1"],
            GLib.SpawnFlags.DEFAULT,
            None,
            None,
            -1,
            None,
            self._on_spawned,
        )

    def _on_spawned(self, _term, pid, error, *_):
        if error is not None:
            self.feed(f"\r\n[no se pudo iniciar {SHELL[0]}: {error.message}]\r\n".encode())
            self.child_pid = None
        else:
            self.child_pid = pid

    def _on_child_exited(self, _term, _status):
        # `exit` en la terminal: abrir un bash nuevo (la terminal del
        # wallpaper siempre esta disponible).
        self.child_pid = None
        if not self.closing:
            GLib.timeout_add(300, lambda: (self.spawn(), False)[1])

    def apply_palette(self, pal: dict) -> None:
        palette = [_rgba(c) for c in pal.get("ansi", [])][:16]
        if len(palette) != 16:
            palette = None
        self.set_colors(_rgba(pal.get("fg", "#e0e0e0")), _rgba(pal.get("bg", "rgba(0,0,0,0)")), palette)
        if pal.get("cursor"):
            self.set_color_cursor(_rgba(pal["cursor"]))
            self.set_color_cursor_foreground(_rgba(pal.get("cursorText", "#000000")))
        if pal.get("selection"):
            self.set_color_highlight(_rgba(pal["selection"]))
            self.set_color_highlight_foreground(_rgba(pal.get("selectionText", pal.get("fg", "#ffffff"))))

    # ---- atajos propios de terminal ----
    def _on_key(self, _ctrl, keyval, _code, state):
        mods = state & Gtk.accelerator_get_default_mod_mask()
        ctrl = Gdk.ModifierType.CONTROL_MASK
        shift = Gdk.ModifierType.SHIFT_MASK
        k = Gdk.keyval_to_lower(keyval)
        if mods == ctrl | shift:
            if k == Gdk.KEY_c:
                self.copy_clipboard_format(Vte.Format.TEXT)
                return True
            if k == Gdk.KEY_v:
                self.paste_clipboard()
                return True
        if mods in (ctrl, ctrl | shift):
            if k in (Gdk.KEY_plus, Gdk.KEY_equal, Gdk.KEY_KP_Add):
                self._zoom(+1)
                return True
            if k in (Gdk.KEY_minus, Gdk.KEY_KP_Subtract):
                self._zoom(-1)
                return True
            if k in (Gdk.KEY_0, Gdk.KEY_KP_0):
                self._zoom(0)
                return True
        return False

    def _zoom(self, step: int) -> None:
        if step == 0:
            self._scale_idx = FONT_SCALES.index(1.0)
        else:
            self._scale_idx = max(0, min(len(FONT_SCALES) - 1, self._scale_idx + step))
        self.set_font_scale(FONT_SCALES[self._scale_idx])
