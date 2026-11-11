"""NEURAL//APPS: todas las apps instaladas a pantalla completa, con el estilo
de NEURAL//DOCK. Lo abre el dock (boton de rejilla, o Super+D = SIGUSR1 al
proceso neural-dock).

- Capa OVERLAY de wlr-layer-shell que cubre toda la pantalla, con el teclado
  mientras esta abierta: se escribe directamente para buscar.
- Enter abre la primera app que coincide; flechas para moverse; Esc borra la
  busqueda o cierra; clic en el fondo vacio cierra.
- Clic derecho en una app = fijarla / quitarla del dock (◆ = fijada).
- La lista se actualiza sola al instalar / desinstalar (Gio.AppInfoMonitor).
Se importa desde dock.py (despues de ensure_preload)."""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from estilo import MONO, PAD, SANS, Frame, css_color  # noqa: E402

ICONO = 64
COLS = 8


def apps_instaladas() -> list[Gio.DesktopAppInfo]:
    out = []
    for info in Gio.AppInfo.get_all():
        if isinstance(info, Gio.DesktopAppInfo) and info.should_show() and info.get_id():
            out.append(info)
    out.sort(key=lambda i: (i.get_display_name() or i.get_id()).lower())
    return out


def lanzar(info: Gio.DesktopAppInfo) -> bool:
    ctx = Gdk.Display.get_default().get_app_launch_context()
    try:
        return bool(info.launch([], ctx))
    except GLib.Error:
        return False


def lanzador_css(t: dict) -> str:
    acc, alt, warn, text, muted = t["accent"], t["alt"], t["warn"], t["text"], t["muted"]
    return f"""
window.neural-apps {{
  background-color: rgba(0, 0, 0, 0.86);
  background-image: radial-gradient(circle at 50% 0%, {css_color(acc, 0.14)}, transparent 60%),
                    repeating-linear-gradient(0deg, {css_color(acc, 0.03)} 0px, {css_color(acc, 0.03)} 1px,
                                              transparent 1px, transparent 4px);
}}
.na-title {{ font-family: "{SANS}"; font-weight: 700; font-size: 22px; letter-spacing: 4px; color: {css_color(acc)}; }}
.na-code {{ font-family: "{MONO}"; font-size: 11px; letter-spacing: 2px; color: {css_color(muted)}; }}
.na-search {{ background: rgba(0, 0, 0, 0.7); border: 1px solid {css_color(acc, 0.7)}; border-radius: 0;
             color: {css_color(text)}; font-family: "{MONO}"; font-size: 16px; min-height: 34px; }}
.na-search:focus-within {{ border-color: {css_color(alt)}; box-shadow: 0 0 10px {css_color(alt, 0.45)}; }}
flowbox.na-grid {{ background: transparent; }}
flowboxchild.na-app {{ padding: 10px 6px 8px; border: 1px solid transparent; border-radius: 0; background: transparent; }}
flowboxchild.na-app:hover {{ background: {css_color(acc, 0.12)}; border-color: {css_color(acc, 0.45)}; }}
flowboxchild.na-app:selected, flowboxchild.na-app:focus {{ background: {css_color(acc, 0.2)};
  border-color: {css_color(alt)}; outline: none; }}
.na-name {{ font-family: "{SANS}"; font-weight: 600; font-size: 14px; color: {css_color(text)}; }}
.na-pin {{ font-family: "{MONO}"; font-size: 10px; color: {css_color(alt)}; }}
.na-letter {{ border: 1px solid {css_color(acc)}; color: {css_color(acc)}; font-family: "{SANS}";
             font-weight: 700; font-size: 30px; min-width: {ICONO}px; min-height: {ICONO}px; }}
.na-empty {{ font-family: "{MONO}"; font-size: 13px; letter-spacing: 2px; color: {css_color(muted)}; }}
scrolledwindow.na-scroll, scrolledwindow.na-scroll > viewport {{ background: transparent; }}
"""


class Lanzador(Gtk.Window):
    def __init__(self, dock):
        super().__init__(application=dock.get_application(), title="Neural Apps")
        self.dock = dock
        self.theme = dock.theme
        self.set_decorated(False)
        self._layer()
        self.add_css_class("neural-apps")
        self.sucio = True  # la lista hay que rehacerla (al abrir)
        self.hijos: list[Gtk.FlowBoxChild] = []

        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, margin_top=40, margin_bottom=30,
                      margin_start=60, margin_end=60)
        head = Frame(self, Gtk.Orientation.VERTICAL, 14, ("tl", "br"), True, "APPS// 00")
        self.head = head
        head.set_halign(Gtk.Align.CENTER)
        hb = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_start=PAD + 20, margin_end=PAD + 20,
                     margin_top=PAD + 24, margin_bottom=PAD + 14)
        hb.set_size_request(560, -1)
        top = Gtk.Box(spacing=10)
        t = Gtk.Label(label="TODAS LAS APPS", xalign=0, hexpand=True)
        t.add_css_class("na-title")
        top.append(t)
        self.cuenta = Gtk.Label(label="")
        self.cuenta.add_css_class("na-code")
        top.append(self.cuenta)
        hb.append(top)
        self.buscar = Gtk.SearchEntry(placeholder_text="buscar…")
        self.buscar.add_css_class("na-search")
        self.buscar.connect("search-changed", lambda *_: self._filtrar())
        self.buscar.connect("activate", lambda *_: self._abrir_primera())
        self.buscar.connect("stop-search", lambda *_: self._esc())
        hb.append(self.buscar)
        ayuda = Gtk.Label(label="ENTER ABRIR · CLIC DERECHO FIJAR EN EL DOCK · ESC CERRAR", xalign=0)
        ayuda.add_css_class("na-code")
        hb.append(ayuda)
        head.append(hb)
        col.append(head)

        self.grid = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.SINGLE, homogeneous=True,
                                max_children_per_line=COLS, min_children_per_line=3,
                                activate_on_single_click=True, valign=Gtk.Align.START,
                                column_spacing=8, row_spacing=8)
        self.grid.add_css_class("na-grid")
        self.grid.set_filter_func(self._coincide)
        self.grid.connect("child-activated", lambda _g, ch: self._abrir(ch.info))
        self.vacio = Gtk.Label(label="SIN RESULTADOS", visible=False)
        self.vacio.add_css_class("na-empty")
        scroll = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroll.add_css_class("na-scroll")
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        inner.set_size_request(min(COLS * 150, 1200), -1)
        inner.set_halign(Gtk.Align.CENTER)
        inner.append(self.grid)
        inner.append(self.vacio)
        scroll.set_child(inner)
        col.append(scroll)
        self.set_child(col)
        self.col = col

        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)
        click = Gtk.GestureClick()
        click.connect("released", self._on_click_fondo)
        self.add_controller(click)

        self.monitor = Gio.AppInfoMonitor.get()
        self.monitor.connect("changed", lambda *_: self._cambio())

    def _layer(self):
        try:
            gi.require_version("Gtk4LayerShell", "1.0")
            from gi.repository import Gtk4LayerShell as ls
        except (ImportError, ValueError):
            return
        if not ls.is_supported():
            return
        ls.init_for_window(self)
        ls.set_namespace(self, "neural-apps")
        ls.set_layer(self, ls.Layer.OVERLAY)
        for e in (ls.Edge.LEFT, ls.Edge.RIGHT, ls.Edge.TOP, ls.Edge.BOTTOM):
            ls.set_anchor(self, e, True)
        ls.set_exclusive_zone(self, -1)  # encima de todo, tambien de la barra
        ls.set_keyboard_mode(self, ls.KeyboardMode.EXCLUSIVE)

    # ---------- abrir / cerrar ----------
    def toggle(self):
        if self.get_visible():
            self.cerrar()
        else:
            self.abrir()

    def abrir(self):
        self.theme = self.dock.theme
        if self.sucio:
            self._rehacer()
        else:
            self._marcar_fijadas()
        self.buscar.set_text("")
        self._filtrar()
        self.present()
        GLib.idle_add(lambda: (self.buscar.grab_focus(), GLib.SOURCE_REMOVE)[1])

    def cerrar(self):
        self.set_visible(False)
        self.buscar.set_text("")

    def _esc(self):
        if self.buscar.get_text():
            self.buscar.set_text("")
        else:
            self.cerrar()

    def _on_key(self, _c, keyval, _code, _state):
        if keyval == Gdk.KEY_Escape:
            self._esc()
            return True
        return False

    def _on_click_fondo(self, gesture, _n, x, y):
        w = self.pick(x, y, Gtk.PickFlags.DEFAULT)
        while w is not None:
            if isinstance(w, (Gtk.FlowBoxChild, Frame, Gtk.Scrollbar)):
                return
            w = w.get_parent()
        self.cerrar()

    # ---------- lista ----------
    def _cambio(self):
        self.sucio = True
        if self.get_visible():
            self._rehacer()
            self._filtrar()

    def _rehacer(self):
        self.sucio = False
        for ch in self.hijos:
            self.grid.remove(ch)
        self.hijos = []
        for info in apps_instaladas():
            ch = self._tarjeta(info)
            self.grid.append(ch)
            self.hijos.append(ch)
        self._marcar_fijadas()

    def _tarjeta(self, info: Gio.DesktopAppInfo) -> Gtk.FlowBoxChild:
        ch = Gtk.FlowBoxChild()
        ch.add_css_class("na-app")
        ch.info = info
        nombre = info.get_display_name() or info.get_id()
        ch.texto = " ".join(filter(None, [nombre, info.get_id(), info.get_generic_name() or "",
                                          " ".join(info.get_keywords() or [])])).lower()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        gicon = info.get_icon()
        if gicon is not None:
            img = Gtk.Image.new_from_gicon(gicon)
            img.set_pixel_size(ICONO)
        else:
            img = Gtk.Label(label=nombre[:1].upper())
            img.add_css_class("na-letter")
        img.set_halign(Gtk.Align.CENTER)
        box.append(img)
        lb = Gtk.Label(label=nombre, ellipsize=Pango.EllipsizeMode.END, max_width_chars=14, justify=Gtk.Justification.CENTER)
        lb.add_css_class("na-name")
        box.append(lb)
        ch.pin = Gtk.Label(label="◆ EN EL DOCK")
        ch.pin.add_css_class("na-pin")
        box.append(ch.pin)
        ch.set_child(box)
        ch.set_tooltip_text(info.get_description() or nombre)
        der = Gtk.GestureClick(button=3)
        der.connect("released", lambda *_a: self._fijar(ch))
        ch.add_controller(der)
        return ch

    def _marcar_fijadas(self):
        fijadas = set(self.dock.pinned())
        for ch in self.hijos:
            ch.pin.set_opacity(1 if ch.info.get_id() in fijadas else 0)  # opacidad: no cambia el alto

    def _fijar(self, ch):
        self.dock.toggle_pin(ch.info.get_id())
        self._marcar_fijadas()

    def _coincide(self, ch) -> bool:
        q = self.buscar.get_text().strip().lower()
        return not q or all(p in ch.texto for p in q.split())

    def _filtrar(self):
        self.grid.invalidate_filter()
        visibles = [ch for ch in self.hijos if self._coincide(ch)]
        self.vacio.set_visible(not visibles)
        self.cuenta.set_text(f"{len(visibles)} / {len(self.hijos)}")
        self.head.tab = f"APPS// {len(self.hijos):02d}"
        self.head.queue_draw()
        if visibles:
            self.grid.select_child(visibles[0])

    def _abrir_primera(self):
        sel = self.grid.get_selected_children()
        ch = sel[0] if sel else next((c for c in self.hijos if self._coincide(c)), None)
        if ch is not None:
            self._abrir(ch.info)

    def _abrir(self, info):
        self.cerrar()
        lanzar(info)
