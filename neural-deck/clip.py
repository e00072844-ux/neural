#!/usr/bin/env python3
"""NEURAL//CLIP: historial del portapapeles con el estilo de NEURAL//DOCK.

Proceso residente (clip.py --fondo, desde [autostart]) que vigila el portapapeles
con wl-paste --watch. Super+V (SIGUSR1 a neural-clip) abre el panel:
  escribir = buscar · ↑↓ = moverse · Enter = copiar y cerrar · Tab = filtro
  Ctrl+P = fijar · Supr = borrar · Esc = limpiar búsqueda o cerrar
No guarda lo que las apps marcan como contraseña. Solo local (~/.local/state/neural-clip)."""
from __future__ import annotations

import signal
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from layer import ensure_preload  # noqa: E402

ensure_preload()

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, GLibUnix, Gtk, Pango  # noqa: E402

import clip_datos as D  # noqa: E402
from estilo import MONO, PAD, Frame, css_color  # noqa: E402
from ventana import AppNeural, boton, lbl, nombrar_proceso, on, tam_humano  # noqa: E402

APP_ID = "org.local.NeuralClip"
FILTROS = [("todo", "TODO"), ("texto", "TEXTO"), ("imagen", "IMÁGENES"), ("archivos", "ARCHIVOS"), ("fijados", "FIJADOS")]
ETIQUETA = {"texto": "TXT", "imagen": "IMG", "archivos": "ARCH"}
PAUSA = D.BASE / "pausa"


def miniatura(mime: str, datos: bytes) -> bytes | None:
    try:
        carga = GdkPixbuf.PixbufLoader.new_with_mime_type(mime)
        carga.write(datos)
        carga.close()
        p = carga.get_pixbuf()
        k = min(1.0, 56 / p.get_height(), 96 / p.get_width())
        p = p.scale_simple(max(1, int(p.get_width() * k)), max(1, int(p.get_height() * k)), GdkPixbuf.InterpType.BILINEAR)
        ok, b = p.save_to_bufferv("png", [], [])
        return bytes(b) if ok else None
    except GLib.Error:
        return None


def hace(ts: float) -> str:
    s = int(time.time() - ts)
    if s < 60:
        return "AHORA"
    if s < 3600:
        return f"{s // 60} MIN"
    if s < 86400:
        return f"{s // 3600} H"
    return f"{s // 86400} D"


def textura(datos: bytes) -> Gdk.Texture | None:
    try:
        return Gdk.Texture.new_from_bytes(GLib.Bytes.new(datos))
    except GLib.Error:
        return None


class Panel(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="NEURAL//CLIP")
        self.app_ = app
        self.theme = app.theme
        self.h: D.Historial = app.h
        self.filtro = "todo"
        self.ids: list[int] = []
        self.armando = False
        self.confirmar_vaciar = 0
        self.add_css_class("clip-win")
        self.set_decorated(False)

        marco = Frame(self, Gtk.Orientation.VERTICAL, 14, ("tl", "br"), True, "CLIP//HISTORIAL")
        marco.set_size_request(960, 600)
        marco.set_halign(Gtk.Align.CENTER)
        marco.set_valign(Gtk.Align.CENTER)
        self.marco = marco
        caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_start=PAD + 12, margin_end=PAD + 12,
                       margin_top=PAD + 28, margin_bottom=PAD + 10, vexpand=True)
        marco.append(caja)

        self.buscar = Gtk.Entry(placeholder_text="Buscar en el portapapeles…")
        self.buscar.add_css_class("nx-entry")
        self.buscar.connect("changed", lambda *_: self.refrescar())
        caja.append(self.buscar)

        chips = Gtk.Box(spacing=6)
        self.chips = {}
        for k, t in FILTROS:
            b = boton(t, "nx-chip", lambda k=k: self.poner_filtro(k))
            self.chips[k] = b
            chips.append(b)
        chips.append(Gtk.Box(hexpand=True))
        self.b_pausa = boton("PAUSAR", "nx-chip", self.alternar_pausa, "Dejar de guardar lo que copies")
        chips.append(self.b_pausa)
        self.b_auto = boton("AUTO-COPIAR", "nx-chip", self.alternar_auto, "Copiar al seleccionar texto, en todo el sistema")
        chips.append(self.b_auto)
        self.b_vaciar = boton("VACIAR", "nx-chip", self.vaciar, "Borrar todo menos lo fijado")
        chips.append(self.b_vaciar)
        caja.append(chips)

        medio = Gtk.Box(spacing=12, vexpand=True)
        self.lista = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.lista.add_css_class("nx-lista")
        self.lista.set_can_focus(False)
        self.lista.connect("row-selected", self._seleccion)
        self.lista.connect("row-activated", lambda *_: self.usar())
        self.scroll = Gtk.ScrolledWindow(child=self.lista, hexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER,
                                         width_request=520, propagate_natural_width=False)
        self.scroll.add_css_class("nx-scroll")
        medio.append(self.scroll)

        lado = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, width_request=330, hexpand=False)
        lado.add_css_class("nx-lado")
        self.v_info = lbl("", "nx-code", xalign=0)
        lado.append(self.v_info)
        self.v_pila = Gtk.Stack(vexpand=True)
        self.v_texto = lbl("", "clip-texto", xalign=0, yalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR,
                           max_width_chars=44, selectable=False)
        s = Gtk.ScrolledWindow(child=self.v_texto, hscrollbar_policy=Gtk.PolicyType.NEVER)
        s.add_css_class("nx-scroll")
        self.v_pila.add_named(s, "texto")
        self.v_img = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, can_shrink=True)
        self.v_pila.add_named(self.v_img, "imagen")
        lado.append(self.v_pila)
        medio.append(lado)
        caja.append(medio)
        caja.append(lbl("ENTER COPIAR · CTRL+P FIJAR · SUPR BORRAR · TAB FILTRO · ESC CERRAR", "nx-code", xalign=0))

        fondo = Gtk.Box(hexpand=True, vexpand=True)
        marco.set_hexpand(True)
        fondo.append(marco)
        self.set_child(fondo)

        tecla = Gtk.EventControllerKey()
        tecla.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        tecla.connect("key-pressed", self._tecla)
        self.add_controller(tecla)
        clic = Gtk.GestureClick()
        clic.connect("pressed", self._clic_fondo)
        fondo.add_controller(clic)
        self._capa()

    def _capa(self):
        try:
            gi.require_version("Gtk4LayerShell", "1.0")
            from gi.repository import Gtk4LayerShell as ls
        except (ImportError, ValueError):
            return
        if not ls.is_supported():
            return
        ls.init_for_window(self)
        ls.set_namespace(self, "neural-clip")
        ls.set_layer(self, ls.Layer.OVERLAY)
        for e in (ls.Edge.LEFT, ls.Edge.RIGHT, ls.Edge.TOP, ls.Edge.BOTTOM):
            ls.set_anchor(self, e, True)
        ls.set_exclusive_zone(self, -1)
        ls.set_keyboard_mode(self, ls.KeyboardMode.EXCLUSIVE)

    # ---- abrir / cerrar ----
    def abrir(self):
        self.theme = self.app_.theme
        self.filtro = "todo"
        self.buscar.set_text("")
        self.refrescar()
        self._pintar_pausa()
        self.set_visible(True)
        self.present()
        self.buscar.grab_focus()

    def cerrar(self):
        self.set_visible(False)
        self.v_img.set_paintable(None)  # no retener imágenes grandes en memoria

    def alternar(self):
        self.cerrar() if self.get_visible() else self.abrir()

    def _clic_fondo(self, gesto, _n, x, y):
        w = self.pick(x, y, Gtk.PickFlags.DEFAULT)
        while w is not None:
            if w is self.marco:
                return
            w = w.get_parent()
        self.cerrar()

    # ---- lista ----
    def poner_filtro(self, k):
        self.filtro = k
        self.refrescar()

    def refrescar(self):
        self.armando = True
        for k, b in self.chips.items():
            on(b, "on", k == self.filtro)
        while f := self.lista.get_row_at_index(0):
            self.lista.remove(f)
        filas = self.h.buscar(self.buscar.get_text(), self.filtro)
        self.ids = [f["id"] for f in filas]
        for f in filas:
            self.lista.append(self._fila(f))
        self.armando = False
        primera = self.lista.get_row_at_index(0)
        self.lista.select_row(primera)
        if not primera:
            self.v_info.set_text("")
            self.v_texto.set_text("Sin resultados." if self.buscar.get_text() else "Todavía no has copiado nada.")
            self.v_pila.set_visible_child_name("texto")

    def _fila(self, f):
        fila = Gtk.ListBoxRow()
        caja = Gtk.Box(spacing=8)
        caja.add_css_class("nx-card")
        caja.add_css_class("clip-fila")
        caja.append(lbl("◆" if f["fijado"] else ETIQUETA[f["clase"]], "nx-tag acc" if f["fijado"] else "nx-tag"))
        if f["clase"] == "imagen":
            tex = textura(f["miniatura"]) if f["miniatura"] else None
            if tex:
                caja.append(Gtk.Picture(paintable=tex, can_shrink=False, content_fit=Gtk.ContentFit.CONTAIN))
            caja.append(lbl(tam_humano(f["tam"]), "nx-dim", xalign=0, hexpand=True))
        else:
            txt = " ".join(f["texto"].split())[:300]
            caja.append(lbl(txt, "nx-txt", xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END, single_line_mode=True))
        caja.append(lbl(hace(f["ts"]), "nx-code"))
        fila.set_child(caja)
        fila.set_can_focus(False)
        return fila

    def _actual(self) -> int | None:
        f = self.lista.get_selected_row()
        return self.ids[f.get_index()] if f and f.get_index() < len(self.ids) else None

    def _seleccion(self, _l, fila):
        for i, _ in enumerate(self.ids):
            f = self.lista.get_row_at_index(i)
            on(f.get_child(), "sel", f is fila)
        if self.armando or not fila:
            return
        c = self.h.contenido(self.ids[fila.get_index()])
        if not c:
            return
        self.v_info.set_text(f"{c['mime']} · {tam_humano(len(c['datos']))}")
        if c["clase"] == "imagen":
            self.v_img.set_paintable(textura(c["datos"]))
            self.v_pila.set_visible_child_name("imagen")
        else:
            self.v_img.set_paintable(None)
            texto = c["datos"][:6000].decode("utf-8", "replace")
            self.v_texto.set_text(texto + ("\n…" if len(c["datos"]) > 6000 else ""))
            self.v_pila.set_visible_child_name("texto")

    def _mover(self, d):
        f = self.lista.get_selected_row()
        i = (f.get_index() if f else -1) + d
        s = self.lista.get_row_at_index(max(0, i))
        if s:
            self.lista.select_row(s)
            GLib.idle_add(self._mostrar, s)

    def _mostrar(self, fila):
        r = fila.translate_coordinates(self.lista, 0, 0)
        if r:
            y = r[-1]
            aj = self.scroll.get_vadjustment()
            h = fila.get_height()
            if y < aj.get_value():
                aj.set_value(y)
            elif y + h > aj.get_value() + aj.get_page_size():
                aj.set_value(y + h - aj.get_page_size())
        return False

    # ---- acciones ----
    def usar(self):
        cid = self._actual()
        c = self.h.contenido(cid) if cid is not None else None
        self.cerrar()
        if c:
            D.copiar(c["mime"], bytes(c["datos"]))

    def fijar(self):
        cid = self._actual()
        if cid is not None:
            i = self.lista.get_selected_row().get_index()
            self.h.fijar(cid)
            self.refrescar()
            self.lista.select_row(self.lista.get_row_at_index(self.ids.index(cid) if cid in self.ids else min(i, len(self.ids) - 1)))

    def borrar(self):
        cid = self._actual()
        if cid is not None:
            i = self.lista.get_selected_row().get_index()
            self.h.borrar(cid)
            self.refrescar()
            self.lista.select_row(self.lista.get_row_at_index(min(i, len(self.ids) - 1)))

    def vaciar(self):
        ahora = time.monotonic()
        if ahora - self.confirmar_vaciar > 3:
            self.confirmar_vaciar = ahora
            self.b_vaciar.set_label("¿SEGURO?")
            GLib.timeout_add(3000, lambda: (self.b_vaciar.set_label("VACIAR"), False)[1])
            return
        self.confirmar_vaciar = 0
        self.b_vaciar.set_label("VACIAR")
        self.h.vaciar()
        self.refrescar()

    def alternar_pausa(self):
        PAUSA.unlink() if PAUSA.exists() else PAUSA.touch()
        self._pintar_pausa()

    def alternar_auto(self):
        D.SIN_AUTO.unlink() if D.SIN_AUTO.exists() else D.SIN_AUTO.touch()
        self._pintar_pausa()

    def _pintar_pausa(self):
        on(self.b_auto, "on", not D.SIN_AUTO.exists())
        p = PAUSA.exists()
        self.b_pausa.set_label("EN PAUSA" if p else "PAUSAR")
        on(self.b_pausa, "on", p)

    def _tecla(self, _c, k, _code, estado):
        ctrl = bool(estado & Gdk.ModifierType.CONTROL_MASK)
        if k == Gdk.KEY_Escape:
            if self.buscar.get_text():
                self.buscar.set_text("")
            else:
                self.cerrar()
        elif k == Gdk.KEY_Down:
            self._mover(1)
        elif k == Gdk.KEY_Up:
            self._mover(-1)
        elif k in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self.usar()
        elif k in (Gdk.KEY_Tab, Gdk.KEY_ISO_Left_Tab):
            d = -1 if k == Gdk.KEY_ISO_Left_Tab else 1
            orden = [x[0] for x in FILTROS]
            self.poner_filtro(orden[(orden.index(self.filtro) + d) % len(orden)])
        elif ctrl and k in (Gdk.KEY_p, Gdk.KEY_P):
            self.fijar()
        elif k == Gdk.KEY_Delete and not self.buscar.get_text():
            self.borrar()
        else:
            return False
        return True


class App(AppNeural):
    def __init__(self):
        super().__init__(APP_ID)
        self.h = D.Historial()
        self.panel: Panel | None = None
        self.vigia = None

    def css_extra(self, t):
        return (f"window.clip-win {{ background-color: rgba(0, 0, 0, 0.62); }}\n"
                f".clip-texto {{ font-family: \"{MONO}\"; font-size: 12px; color: {css_color(t['text'])}; }}\n"
                f".clip-fila {{ min-height: 26px; }}\n")

    def do_startup(self):
        AppNeural.do_startup(self)
        self.hold()
        GLibUnix.signal_add(GLib.PRIORITY_DEFAULT, signal.SIGUSR1, self._senal)
        self.vigilar()
        self.selec_id = 0
        self.ult_sel = None
        self.vigilar_seleccion()

    def _senal(self):
        self.alternar()
        return True

    def alternar(self):
        if self.panel is None:
            self.panel = Panel(self)
        self.panel.alternar()

    def crear_ventana(self, args, cwd):
        if "--fondo" not in args:
            self.alternar()

    # ---- vigía del portapapeles ----
    def vigilar(self):
        try:
            self.vigia = Gio.Subprocess.new(["setpriv", "--pdeathsig", "TERM", "wl-paste", "--watch", "echo", "x"],
                                            Gio.SubprocessFlags.STDOUT_PIPE)  # muere con este proceso
        except GLib.Error:
            GLib.timeout_add_seconds(3, lambda: (self.vigilar(), False)[1])
            return
        self.flujo = Gio.DataInputStream.new(self.vigia.get_stdout_pipe())
        self.flujo.read_line_async(GLib.PRIORITY_DEFAULT, None, self._linea)

    def _linea(self, flujo, res):
        try:
            linea, _ = flujo.read_line_finish(res)
        except GLib.Error:
            linea = None
        if linea is None:  # murió wl-paste: se relanza
            GLib.timeout_add_seconds(2, lambda: (self.vigilar(), False)[1])
            return
        if not PAUSA.exists() and not D.omitiendo():
            threading.Thread(target=self._capturar, daemon=True).start()
        flujo.read_line_async(GLib.PRIORITY_DEFAULT, None, self._linea)

    # ---- copiar al seleccionar ----
    def vigilar_seleccion(self):
        try:
            self.vigia2 = Gio.Subprocess.new(["setpriv", "--pdeathsig", "TERM", "wl-paste", "--primary", "--watch", "echo", "x"],
                                             Gio.SubprocessFlags.STDOUT_PIPE)
        except GLib.Error:
            GLib.timeout_add_seconds(3, lambda: (self.vigilar_seleccion(), False)[1])
            return
        self.flujo2 = Gio.DataInputStream.new(self.vigia2.get_stdout_pipe())
        self.flujo2.read_line_async(GLib.PRIORITY_DEFAULT, None, self._linea2)

    def _linea2(self, flujo, res):
        try:
            linea, _ = flujo.read_line_finish(res)
        except GLib.Error:
            linea = None
        if linea is None:
            GLib.timeout_add_seconds(2, lambda: (self.vigilar_seleccion(), False)[1])
            return
        # mientras arrastras la selección cambia sin parar: se espera a que pare
        if self.selec_id:
            GLib.source_remove(self.selec_id)
        self.selec_id = GLib.timeout_add(350, self._seleccion_lista)
        flujo.read_line_async(GLib.PRIORITY_DEFAULT, None, self._linea2)

    def _seleccion_lista(self):
        self.selec_id = 0
        if not D.SIN_AUTO.exists() and not PAUSA.exists() and not D.omitiendo():
            threading.Thread(target=self._pasar_seleccion, daemon=True).start()
        return False

    def _pasar_seleccion(self):
        d = D.leer_seleccion()
        if d and d != self.ult_sel:
            self.ult_sel = d
            D.copiar("text/plain", d)

    def _capturar(self):
        r = D.leer_portapapeles()
        if not r:
            return
        clase, mime, datos = r
        mini = miniatura(mime, datos) if clase == "imagen" else None
        GLib.idle_add(self._guardar, clase, mime, datos, mini)

    def _guardar(self, clase, mime, datos, mini):
        self.h.agregar(clase, mime, datos, mini)
        if self.panel and self.panel.get_visible():
            self.panel.refrescar()
        return False


if __name__ == "__main__":
    nombrar_proceso("neural-clip")
    GLib.set_prgname(APP_ID)
    sys.exit(App().run(sys.argv))
