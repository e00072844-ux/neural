"""NEURAL//CINTA: vista de todas las ventanas del escritorio en fila (Super).

Vive en el proceso del dock (como NEURAL//APPS); Super (tecla sola) manda
SIGWINCH a neural-dock y esto se abre/cierra. Habla con el demonio del mosaico
(mosaico.py, socket) para leer la cinta y moverla:
  ← / →         recorrer: en modo cinta la pantalla de detrás se mueve EN VIVO
  Shift+← / →   llevar la ventana elegida a la izquierda / derecha en la cinta
  Enter, Super, clic   quedarse ahí (cierra la vista)
  X / Supr      cerrar la ventana elegida
  Esc           volver a donde estabas
En modo LIBRE sirve de selector: Enter trae y enfoca la ventana elegida.
Se importa desde dock.py (después de ensure_preload)."""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gdk, GLib, Gtk, Pango  # noqa: E402

import mosaico  # noqa: E402
from estilo import MONO, SANS, css_color  # noqa: E402
from wayfire_ipc import WayfireIPC  # noqa: E402

ANCHO, ALTO = 300, 188  # tarjeta / miniatura


def cinta_css(t: dict) -> str:
    acc, alt, warn, text, muted = t["accent"], t["alt"], t["warn"], t["text"], t["muted"]
    return f"""
window.neural-cinta {{
  background-color: rgba(0, 0, 0, 0.55);
  background-image: repeating-linear-gradient(0deg, {css_color(acc, 0.035)} 0px, {css_color(acc, 0.035)} 1px,
                                              transparent 1px, transparent 4px);
}}
.nc-banda {{ background: rgba(0, 0, 0, 0.78); border-top: 1px solid {css_color(acc, 0.6)};
            border-bottom: 1px solid {css_color(acc, 0.6)}; padding: 18px 0 16px;
            box-shadow: 0 0 40px {css_color(acc, 0.18)}; }}
.nc-titulo {{ font-family: "{SANS}"; font-weight: 700; font-size: 20px; letter-spacing: 4px; color: {css_color(acc)};
             text-shadow: 0 0 12px {css_color(acc, 0.5)}; }}
.nc-code {{ font-family: "{MONO}"; font-size: 11px; letter-spacing: 2px; color: {css_color(muted)}; }}
.nc-card {{ padding: 8px; border: 1px solid {css_color(acc, 0.35)}; background: rgba(0, 0, 0, 0.6); }}
.nc-card.vista {{ border-color: {css_color(acc, 0.85)}; background: {css_color(acc, 0.10)}; }}
.nc-card.sel {{ border: 2px solid {css_color(alt)}; background: {css_color(alt, 0.14)};
               box-shadow: 0 0 18px {css_color(alt, 0.45)}; }}
.nc-card.min {{ opacity: 0.72; }}
.nc-card.sel.min {{ opacity: 1; }}
.nc-nombre {{ font-family: "{SANS}"; font-weight: 700; font-size: 14px; color: {css_color(text)}; }}
.nc-card.sel .nc-nombre {{ color: {css_color(alt)}; }}
.nc-tag {{ font-family: "{MONO}"; font-size: 10px; letter-spacing: 1px; padding: 1px 6px;
          border: 1px solid {css_color(acc, 0.7)}; color: {css_color(acc)}; }}
.nc-tag.warn {{ border-color: {css_color(warn)}; color: {css_color(warn)}; }}
.nc-mini {{ background: rgba(255, 255, 255, 0.03); border: 1px solid {css_color(acc, 0.2)}; }}
.nc-letra {{ font-family: "{SANS}"; font-weight: 700; font-size: 54px; color: {css_color(acc, 0.7)}; }}
.nc-vacio {{ font-family: "{MONO}"; font-size: 13px; letter-spacing: 2px; color: {css_color(muted)}; }}
scrolledwindow.nc-scroll, scrolledwindow.nc-scroll > viewport {{ background: transparent; }}
"""


def _lbl(text: str, cls: str, **kw) -> Gtk.Label:
    lb = Gtk.Label(label=text, **kw)
    for c in cls.split():
        lb.add_css_class(c)
    return lb


class VistaCinta(Gtk.Window):
    def __init__(self, dock, desktop_info):
        super().__init__(application=dock.get_application(), title="Neural Cinta")
        self.dock = dock
        self.desktop_info = desktop_info
        self.set_decorated(False)
        self.add_css_class("neural-cinta")
        self._layer()
        self.estado: dict = {"modo": 0, "cinta": [], "inicio": 0, "foco": None}
        self.sel = 0
        self.origen = None  # id enfocado al abrir (Esc vuelve ahí)
        self.tarjetas: list[Gtk.Widget] = []

        raiz = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
        banda = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        banda.add_css_class("nc-banda")
        cab = Gtk.Box(spacing=14, margin_start=48, margin_end=48)
        self.titulo = _lbl("NEURAL//CINTA", "nc-titulo", xalign=0)
        cab.append(self.titulo)
        self.info = _lbl("", "nc-code", xalign=0, hexpand=True, valign=Gtk.Align.END)
        cab.append(self.info)
        banda.append(cab)

        self.scroll = Gtk.ScrolledWindow(vscrollbar_policy=Gtk.PolicyType.NEVER,
                                         hscrollbar_policy=Gtk.PolicyType.EXTERNAL)
        self.scroll.add_css_class("nc-scroll")
        self.scroll.set_size_request(-1, ALTO + 86)
        self.fila = Gtk.Box(spacing=14, margin_start=48, margin_end=48, halign=Gtk.Align.CENTER)
        self.scroll.set_child(self.fila)
        banda.append(self.scroll)
        self.ayuda = _lbl("", "nc-code", xalign=0, margin_start=48)
        banda.append(self.ayuda)
        raiz.append(banda)
        self.set_child(raiz)

        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._tecla)
        self.add_controller(keys)
        rueda = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL
                                          | Gtk.EventControllerScrollFlags.DISCRETE)
        rueda.connect("scroll", lambda _c, _dx, dy: (self._paso(1 if dy > 0 else -1), True)[1])
        self.add_controller(rueda)
        clic = Gtk.GestureClick()
        clic.connect("released", self._clic_fondo)
        self.add_controller(clic)

    def _layer(self):
        try:
            gi.require_version("Gtk4LayerShell", "1.0")
            from gi.repository import Gtk4LayerShell as ls
        except (ImportError, ValueError):
            return
        if not ls.is_supported():
            return
        ls.init_for_window(self)
        ls.set_namespace(self, "neural-cinta")
        ls.set_layer(self, ls.Layer.OVERLAY)
        for e in (ls.Edge.LEFT, ls.Edge.RIGHT, ls.Edge.TOP, ls.Edge.BOTTOM):
            ls.set_anchor(self, e, True)
        ls.set_exclusive_zone(self, -1)
        ls.set_keyboard_mode(self, ls.KeyboardMode.EXCLUSIVE)

    # ---------- abrir / cerrar ----------
    def toggle(self):
        if self.get_visible():
            self.fijar()
        else:
            self.abrir()

    def abrir(self):
        mosaico.pedir("captura", timeout=3)   # miniaturas frescas de lo que se ve, antes de taparlo
        est = mosaico.pedir("vista", abierta=True)
        if est is None:  # el demonio no corre: aun así, como selector en modo LIBRE
            est = {"modo": 0, "cinta": [], "inicio": 0, "foco": None, "sin_demonio": True}
        self.estado = est
        self.origen = est.get("foco")
        ids = [c["id"] for c in est["cinta"]]
        self.sel = ids.index(self.origen) if self.origen in ids else min(est.get("inicio", 0), max(0, len(ids) - 1))
        self._pintar()
        self.present()

    def _cerrar(self):
        self.set_visible(False)
        mosaico.pedir("vista", abierta=False)

    def fijar(self):
        """Quedarse en la ventana elegida."""
        c = self._elegida()
        self._cerrar()
        if c is not None:
            mosaico.pedir("ir", id=c["id"]) or self._enfocar(c["id"])

    def volver(self):
        self._cerrar()
        if self.origen is not None:
            mosaico.pedir("ir", id=self.origen) or self._enfocar(self.origen)

    @staticmethod
    def _enfocar(vid):
        try:
            WayfireIPC().call("window-rules/focus-view", {"id": vid})
        except Exception:
            pass
        return True

    # ---------- dibujo ----------
    def _elegida(self):
        cinta = self.estado.get("cinta", [])
        return cinta[self.sel] if 0 <= self.sel < len(cinta) else None

    def _pintar(self):
        while (c := self.fila.get_first_child()) is not None:
            self.fila.remove(c)
        self.tarjetas = []
        est = self.estado
        n, cinta, ini = est.get("modo", 0), est.get("cinta", []), est.get("inicio", 0)
        modo_txt = "LIBRE · SELECTOR" if n == 0 else f"MOSAICO {n} · VENTANILLA {ini + 1}–{min(ini + n, len(cinta))}"
        self.info.set_text(f"   {len(cinta)} VENTANA{'S' if len(cinta) != 1 else ''}  ·  {modo_txt}"
                           + ("  ·  NEURAL//TILE NO ESTÁ CORRIENDO" if est.get("sin_demonio") else ""))
        if n:
            self.ayuda.set_text("← → RECORRER (EN VIVO)  ·  SHIFT + ← → MOVER LA VENTANA  ·  ENTER / SUPER QUEDARSE"
                                "  ·  X CERRAR VENTANA  ·  ESC VOLVER")
        else:
            self.ayuda.set_text("← → ELEGIR  ·  ENTER / SUPER IR  ·  X CERRAR VENTANA  ·  ESC SALIR")
        if not cinta:
            self.fila.append(_lbl("SIN VENTANAS EN ESTE ESCRITORIO", "nc-vacio"))
            return
        for k, c in enumerate(cinta):
            self.tarjetas.append(self._tarjeta(c, k, n and ini <= k < ini + n))
            self.fila.append(self.tarjetas[-1])
        GLib.idle_add(self._centrar)

    def _tarjeta(self, c: dict, k: int, en_vista: bool) -> Gtk.Widget:
        caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        caja.add_css_class("nc-card")
        if en_vista:
            caja.add_css_class("vista")
        if k == self.sel:
            caja.add_css_class("sel")
        if c.get("minimizada") and not en_vista:
            caja.add_css_class("min")

        marco = Gtk.Box(halign=Gtk.Align.FILL)
        marco.add_css_class("nc-mini")
        marco.set_size_request(ANCHO, ALTO)
        if c.get("mini"):
            pic = Gtk.Picture.new_for_filename(c["mini"])
            pic.set_content_fit(Gtk.ContentFit.CONTAIN)
            pic.set_hexpand(True)
            marco.append(pic)
        else:
            info = self.desktop_info(c.get("app_id") or "") if c.get("app_id") else None
            if info is not None and info.get_icon() is not None:
                img = Gtk.Image.new_from_gicon(info.get_icon())
                img.set_pixel_size(84)
            else:
                img = _lbl((c.get("titulo") or c.get("app_id") or "?")[:1].upper(), "nc-letra")
            img.set_hexpand(True)
            img.set_halign(Gtk.Align.CENTER)
            marco.append(img)
        caja.append(marco)

        pie = Gtk.Box(spacing=6)
        nombre = _lbl(c.get("titulo") or c.get("app_id") or "ventana", "nc-nombre", xalign=0, hexpand=True,
                      ellipsize=Pango.EllipsizeMode.END, max_width_chars=26)
        pie.append(nombre)
        if en_vista:
            pie.append(_lbl("EN PANTALLA", "nc-tag"))
        elif c.get("minimizada") and not self.estado.get("modo"):
            pie.append(_lbl("MINIMIZADA", "nc-tag warn"))
        caja.append(pie)
        caja.append(_lbl(f"{k + 1:02d} · {c.get('app_id') or '—'}", "nc-code", xalign=0,
                         ellipsize=Pango.EllipsizeMode.END, max_width_chars=34))

        clic = Gtk.GestureClick()
        clic.connect("released", lambda *_a, k=k: (setattr(self, "sel", k), self.fijar()))
        caja.add_controller(clic)
        return caja

    def _centrar(self):
        """Desplazar la fila para que la tarjeta elegida quede a la vista (centrada si se puede)."""
        if not (0 <= self.sel < len(self.tarjetas)):
            return GLib.SOURCE_REMOVE
        t = self.tarjetas[self.sel]
        ok, r = t.compute_bounds(self.fila)
        if not ok:
            return GLib.SOURCE_REMOVE
        adj = self.scroll.get_hadjustment()
        objetivo = r.get_x() + r.get_width() / 2 - adj.get_page_size() / 2
        adj.set_value(max(adj.get_lower(), min(objetivo, adj.get_upper() - adj.get_page_size())))
        return GLib.SOURCE_REMOVE

    def _refrescar(self):
        est = mosaico.pedir("estado")
        if est is not None:
            self.estado = est
        self.sel = max(0, min(self.sel, len(self.estado.get("cinta", [])) - 1))
        self._pintar()

    # ---------- teclado / ratón ----------
    def _paso(self, d: int):
        cinta = self.estado.get("cinta", [])
        if not cinta:
            return
        self.sel = max(0, min(len(cinta) - 1, self.sel + d))
        if self.estado.get("modo"):
            est = mosaico.pedir("ir", id=cinta[self.sel]["id"])   # la pantalla de detrás la sigue
            if est is not None:
                self.estado = est
        self._pintar()

    def _mover(self, d: int):
        c = self._elegida()
        if c is None or not self.estado.get("modo"):
            return
        est = mosaico.pedir("mover", paso=d, id=c["id"])
        if est is not None:
            self.estado = est
            ids = [x["id"] for x in est["cinta"]]
            self.sel = ids.index(c["id"]) if c["id"] in ids else self.sel
        self._pintar()

    def _cerrar_ventana(self):
        c = self._elegida()
        if c is None:
            return
        try:
            ipc = WayfireIPC()
            # si estaba minimizada, su "¿seguro?" (Brave, TERM...) quedaria escondido
            ipc.call("wm-actions/set-minimized", {"view_id": c["id"], "state": False})
            ipc.call("window-rules/close-view", {"id": c["id"]})
        except Exception:
            pass
        GLib.timeout_add(350, lambda: (self._refrescar(), GLib.SOURCE_REMOVE)[1])

    def _tecla(self, _c, keyval, _code, state):
        shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
        if keyval in (Gdk.KEY_Left, Gdk.KEY_KP_Left, Gdk.KEY_a, Gdk.KEY_h):
            self._mover(-1) if shift else self._paso(-1)
        elif keyval in (Gdk.KEY_Right, Gdk.KEY_KP_Right, Gdk.KEY_d, Gdk.KEY_l):
            self._mover(1) if shift else self._paso(1)
        elif keyval == Gdk.KEY_Home:
            self._paso(-len(self.estado.get("cinta", [])))
        elif keyval == Gdk.KEY_End:
            self._paso(len(self.estado.get("cinta", [])))
        elif keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_space):
            self.fijar()
        elif keyval == Gdk.KEY_Escape:
            self.volver()
        elif keyval in (Gdk.KEY_x, Gdk.KEY_X, Gdk.KEY_Delete):
            self._cerrar_ventana()
        else:
            return False
        return True

    def _clic_fondo(self, _g, _n, x, y):
        if not self.get_visible():
            return
        w = self.pick(x, y, Gtk.PickFlags.DEFAULT)
        while w is not None:
            if w is self.scroll or w.has_css_class("nc-banda"):
                return
            w = w.get_parent()
        self.fijar()
