#!/usr/bin/env python3
"""NEURAL//VISOR: imágenes y PDF con el estilo NEURAL. Uso: visor.py [archivo]

← → anterior/siguiente (imagen o página) · + − zoom · 0 ajustar · 1 tamaño real
R girar la vista · F11 pantalla completa · Ctrl+F buscar en el PDF · Ctrl+rueda zoom
En PDF: arrastrar selecciona texto (doble clic palabra, triple línea), Ctrl+C copia, Ctrl+A toda la
página; el botón central o la rueda mueven la página.
Solo lee: nunca modifica ni borra el archivo."""
from __future__ import annotations

import math
import re
import subprocess
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Poppler", "0.18")
import cairo  # noqa: E402
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, Poppler  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ventana import AppNeural, VentanaNeural, boton, lbl, nombrar_proceso, tam_humano  # noqa: E402

APP_ID = "org.local.NeuralVisor"
EXT_IMG = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".avif", ".heic", ".heif", ".jxl", ".svg",
           ".ico", ".dng", ".cr2", ".nef", ".arw", ".qoi", ".tga"}
ZMIN, ZMAX = 0.05, 16.0
MAX_PX = 12000  # lado máximo de una página renderizada


def orden_natural(p: Path):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", p.name.lower())]


def cargar_imagen(ruta: Path) -> GdkPixbuf.Pixbuf:
    """Con la orientación EXIF aplicada (las fotos del teléfono vienen giradas)."""
    return GdkPixbuf.Pixbuf.new_from_file(str(ruta)).apply_embedded_orientation()


def abrir_pdf(ruta: Path) -> Poppler.Document:
    return Poppler.Document.new_from_gfile(Gio.File.new_for_path(str(ruta)), None, None)


def rectangulo(x1: float, y1: float, x2: float, y2: float) -> Poppler.Rectangle:
    r = Poppler.Rectangle()
    r.x1, r.y1, r.x2, r.y2 = x1, y1, x2, y2
    return r


def color_poppler(r: float, g: float, b: float) -> Poppler.Color:
    c = Poppler.Color()
    c.red, c.green, c.blue = int(r * 65535), int(g * 65535), int(b * 65535)
    return c


def pagina_desde_vista(x: float, y: float, w: float, h: float, giro: int) -> tuple[float, float]:
    """De un punto de la vista (en puntos de PDF, ya girada) a la página sin girar."""
    if giro == 90:
        return y, h - x
    if giro == 180:
        return w - x, h - y
    if giro == 270:
        return w - y, x
    return x, y


def render_pagina(pag: Poppler.Page, escala: float, marcas=(), giro: int = 0, sel=None) -> Gdk.Texture:
    w0, h0 = pag.get_size()
    escala = min(escala, MAX_PX / max(w0, h0))
    w, h = max(1, int(w0 * escala)), max(1, int(h0 * escala))
    ancho, alto = (h, w) if giro % 180 else (w, h)
    sup = cairo.ImageSurface(cairo.FORMAT_ARGB32, ancho, alto)
    cr = cairo.Context(sup)
    cr.set_source_rgb(1, 1, 1)
    cr.paint()
    cr.translate(ancho / 2, alto / 2)
    cr.rotate(math.radians(giro))
    cr.translate(-w / 2, -h / 2)
    cr.scale(escala, escala)
    pag.render(cr)
    if sel:
        pag.render_selection(cr, rectangulo(*sel[:4]), rectangulo(0, 0, 0, 0), sel[4], color_poppler(1, 1, 1),
                             color_poppler(0.12, 0.42, 0.85))
    cr.set_source_rgba(1.0, 0.82, 0.25, 0.45)
    for r in marcas:  # Poppler da y desde abajo
        cr.rectangle(r.x1, h0 - r.y2, r.x2 - r.x1, r.y2 - r.y1)
        cr.fill()
    sup.flush()
    return Gdk.MemoryTexture.new(ancho, alto, Gdk.MemoryFormat.B8G8R8A8_PREMULTIPLIED, GLib.Bytes.new(bytes(sup.get_data())),
                                 sup.get_stride())


class Ventana(VentanaNeural):
    def __init__(self, app):
        super().__init__(app, "SYS//VIEW", "NEURAL//VISOR", (1040, 700))
        self.ruta: Path | None = None
        self.lista: list[Path] = []
        self.pos = 0
        self.doc = None
        self.pag = 0
        self.pix = None
        self.zoom = 1.0
        self.ajustar = True
        self.giro = 0
        self.ult_ancho = (0, 0)
        self.busca_res: list[tuple[int, list]] = []
        self.busca_i = -1
        self._busca_txt = ""
        self.sel = None  # (x1, y1, x2, y2, estilo) en puntos de la página
        self.sel_ini = None
        self.modo_arr = "pan"
        self.arrastro = False
        self.repinta = False

        c = self.cab_centro
        for t, a, tip in (("ABRIR", self.elegir, "Abrir archivo"), ("‹", lambda: self.mover(-1), "Anterior"),
                          ("›", lambda: self.mover(1), "Siguiente"), ("−", lambda: self.cambiar_zoom(1 / 1.25), "Alejar"),
                          ("+", lambda: self.cambiar_zoom(1.25), "Acercar"), ("AJUSTAR", self.ajustar_vista, "Ajustar a la ventana"),
                          ("100%", self.real, "Tamaño real"), ("GIRAR", self.girar, "Girar la vista")):
            c.append(boton(t, "nx-chip", a, tip))
        self.b_copiar = boton("COPIAR", "nx-accion", self.copiar, "Copiar el texto seleccionado (Ctrl+C)")
        self.b_copiar.set_visible(False)
        c.append(self.b_copiar)
        self.e_busca = Gtk.Entry(placeholder_text="Buscar en el PDF…", width_chars=20, visible=False)
        self.e_busca.add_css_class("nx-entry")
        self.e_busca.connect("activate", lambda *_: self.buscar())
        c.append(self.e_busca)

        self.pic = Gtk.Picture(can_shrink=True, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        self.scroll = Gtk.ScrolledWindow(child=self.pic, vexpand=True, hexpand=True, margin_top=6)
        self.scroll.add_css_class("nx-scroll")
        self.mensaje = lbl("Abre una imagen o un PDF", "nx-dim", vexpand=True)
        self.pila = Gtk.Stack(vexpand=True)
        self.pila.add_named(self.mensaje, "msg")
        self.pila.add_named(self.scroll, "ver")
        self.cuerpo.append(self.pila)

        arr = Gtk.GestureDrag(button=0)
        arr.connect("drag-begin", self._arr_ini)
        arr.connect("drag-update", self._arr_mov)
        arr.connect("drag-end", self._arr_fin)
        self.scroll.add_controller(arr)
        clic = Gtk.GestureClick(button=1)
        clic.connect("pressed", self._clic)
        clic.connect("released", self._clic_fin)
        self.scroll.add_controller(clic)
        rueda = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL)
        rueda.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        rueda.connect("scroll", self._rueda)
        self.scroll.add_controller(rueda)
        tecla = Gtk.EventControllerKey()
        tecla.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        tecla.connect("key-pressed", self._tecla)
        self.add_controller(tecla)
        soltar = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        soltar.connect("drop", self._soltar)
        self.add_controller(soltar)
        GLib.timeout_add(250, self._vigilar_tam)

    # ---- abrir ----
    def elegir(self):
        Gtk.FileDialog(title="Abrir").open(self, None, self._elegido)

    def _elegido(self, d, r):
        try:
            self.abrir(Path(d.open_finish(r).get_path()))
        except GLib.Error:
            pass

    def _soltar(self, _t, v, _x, _y):
        fs = [f.get_path() for f in v.get_files() if f.get_path()]
        if fs:
            self.abrir(Path(fs[0]))
        return True

    def abrir(self, ruta: Path, relistar: bool = True):
        self.ruta = ruta
        self.giro = 0
        self.ajustar = True
        self.doc = self.pix = None
        self.busca_res, self.busca_i = [], -1
        self.e_busca.set_visible(False)
        self.quitar_sel(pintar=False)
        if relistar and ruta.suffix.lower() in EXT_IMG:
            try:
                self.lista = sorted((p for p in ruta.parent.iterdir() if p.is_file() and p.suffix.lower() in EXT_IMG),
                                    key=orden_natural)
                self.pos = self.lista.index(ruta)
            except (OSError, ValueError):
                self.lista, self.pos = [ruta], 0
        elif relistar:
            self.lista, self.pos = [ruta], 0
        try:
            if ruta.suffix.lower() == ".pdf":
                self.doc = abrir_pdf(ruta)
                self.pag = 0
                self.e_busca.set_visible(True)
                self.pic.set_cursor_from_name("text")
            else:
                self.pix = cargar_imagen(ruta)
                self.pic.set_cursor_from_name(None)
        except GLib.Error as e:
            protegido = "password" in e.message.lower() or "encrypted" in e.message.lower()
            self.mensaje.set_text("PDF protegido con contraseña: aún no se admite" if protegido else
                                  f"No se pudo abrir {ruta.name}\n{e.message}")
            self.pila.set_visible_child_name("msg")
            self.p_izq.set_text(ruta.name)
            return
        self.pila.set_visible_child_name("ver")
        GLib.idle_add(self.pintar)

    # ---- pintar ----
    def _dimension(self):
        if self.doc:
            w, h = self.doc.get_page(self.pag).get_size()
        else:
            w, h = self.pix.get_width(), self.pix.get_height()
        return (h, w) if self.giro % 180 else (w, h)

    def pintar(self):
        if not (self.doc or self.pix):
            return False
        w, h = self._dimension()
        vw, vh = max(50, self.scroll.get_width()), max(50, self.scroll.get_height())
        self.ult_ancho = (vw, vh)
        if self.ajustar:
            self.zoom = min(1.0 if self.pix else 16.0, (vw - 16) / w, (vh - 16) / h if self.pix else (vw - 16) / w)
        if self.doc:
            marcas = next((m for p, m in self.busca_res if p == self.pag), ()) if self.busca_res else ()
            tex = render_pagina(self.doc.get_page(self.pag), self.zoom * self._hidpi(), marcas, self.giro, self.sel)
        else:
            p = self.pix
            if self.giro:
                p = p.rotate_simple({90: GdkPixbuf.PixbufRotation.CLOCKWISE, 180: GdkPixbuf.PixbufRotation.UPSIDEDOWN,
                                     270: GdkPixbuf.PixbufRotation.COUNTERCLOCKWISE}[self.giro])
            formato = Gdk.MemoryFormat.R8G8B8A8 if p.get_has_alpha() else Gdk.MemoryFormat.R8G8B8
            tex = Gdk.MemoryTexture.new(p.get_width(), p.get_height(), formato, p.read_pixel_bytes(), p.get_rowstride())
        self.pic.set_paintable(tex)
        self.pic.set_size_request(max(1, int(w * self.zoom)), max(1, int(h * self.zoom)))
        self._pie()
        return False

    def _hidpi(self) -> float:
        return float(self.get_scale_factor() or 1)

    def _pie(self):
        p = self.ruta
        try:
            peso = tam_humano(p.stat().st_size)
        except OSError:
            peso = "?"
        if self.doc:
            self.p_izq.set_text(f"{p.name} · PÁGINA {self.pag + 1} / {self.doc.get_n_pages()} · {peso} · {self.zoom * 100:.0f}%")
        else:
            self.p_izq.set_text(f"{p.name} · {self.pix.get_width()}×{self.pix.get_height()} · {peso} · "
                                f"{self.pos + 1} / {len(self.lista)} · {self.zoom * 100:.0f}%")

    def _vigilar_tam(self):
        if self.ajustar and (self.doc or self.pix) and (self.scroll.get_width(), self.scroll.get_height()) != self.ult_ancho:
            self.pintar()
        return True

    # ---- acciones ----
    def cambiar_zoom(self, k):
        if self.doc or self.pix:
            self.ajustar = False
            self.zoom = max(ZMIN, min(ZMAX, self.zoom * k))
            self.pintar()

    def ajustar_vista(self):
        self.ajustar = True
        self.pintar()

    def real(self):
        self.ajustar = False
        self.zoom = 1.0
        self.pintar()

    def girar(self):
        self.giro = (self.giro + 90) % 360
        # la selección está en puntos de la página: sigue siendo válida al girar
        self.pintar()

    def mover(self, d):
        if self.doc:
            n = max(0, min(self.doc.get_n_pages() - 1, self.pag + d))
            if n != self.pag:
                self.pag = n
                self.quitar_sel(pintar=False)
                self.pintar()
                self.scroll.get_vadjustment().set_value(0)
        elif self.pix and len(self.lista) > 1:
            self.pos = (self.pos + d) % len(self.lista)
            self.abrir(self.lista[self.pos], relistar=False)

    def buscar(self):
        t = self.e_busca.get_text().strip()
        if not self.doc or not t:
            return
        if t != self._busca_txt:
            self._busca_txt = t
            self.busca_res = [(i, m) for i in range(self.doc.get_n_pages()) if (m := self.doc.get_page(i).find_text(t))]
            self.busca_i = -1
        if not self.busca_res:
            self.aviso("Sin resultados", True)
            return
        self.busca_i = (self.busca_i + 1) % len(self.busca_res)
        self.pag = self.busca_res[self.busca_i][0]
        self.quitar_sel(pintar=False)
        self.pintar()
        self.aviso(f"Página {self.busca_i + 1} de {len(self.busca_res)} con resultados")

    # ---- selección de texto (PDF) ----
    def _a_pagina(self, x, y):
        """Punto del área de vista → puntos de la página, dentro de sus límites."""
        r = self.scroll.translate_coordinates(self.pic, x, y)
        if not r:
            return 0.0, 0.0
        px, py = r[-2:]
        vw, vh = self._dimension()
        k = (self.pic.get_width() or 1) / vw
        w, h = self.doc.get_page(self.pag).get_size()
        return pagina_desde_vista(max(0.0, min(vw, px / k)), max(0.0, min(vh, py / k)), w, h, self.giro)

    def _texto_sel(self) -> str:
        if not (self.doc and self.sel):
            return ""
        return self.doc.get_page(self.pag).get_selected_text(self.sel[4], rectangulo(*self.sel[:4])) or ""

    def _poner_sel(self, sel):
        self.sel = sel
        self.b_copiar.set_visible(bool(sel))
        if not self.repinta:  # varios movimientos del ratón, un solo repintado
            self.repinta = True
            GLib.idle_add(self._repintar)

    def _repintar(self):
        self.repinta = False
        self.pintar()
        return False

    def quitar_sel(self, pintar=True):
        if self.sel is not None:
            if pintar:
                self._poner_sel(None)
            else:
                self.sel = None
        self.b_copiar.set_visible(False)

    def _clic(self, _g, n, x, y):
        self.arrastro = False
        if self.doc and n >= 2:
            px, py = self._a_pagina(x, y)
            estilo = Poppler.SelectionStyle.WORD if n == 2 else Poppler.SelectionStyle.LINE
            self._poner_sel((px, py, px, py, estilo))
            self._publicar()

    def _clic_fin(self, _g, n, _x, _y):
        if self.doc and n == 1 and not self.arrastro:
            self.quitar_sel()

    def _publicar(self):
        """Como en cualquier app: lo seleccionado pasa a la selección del sistema y CLIP lo copia."""
        t = self._texto_sel()
        if t.strip():
            try:
                subprocess.run(["wl-copy", "--primary"], input=t.encode(), stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=3)
                self.aviso(f"Seleccionado: {len(t)} caracteres")
            except (OSError, subprocess.SubprocessError):
                pass

    def _arr_fin(self, _g, _dx, _dy):
        if self.modo_arr == "sel" and self.arrastro:
            self._publicar()

    def copiar(self):
        t = self._texto_sel()
        if t:
            self.get_clipboard().set(t)
            self.aviso(f"Copiado: {len(t)} caracteres")

    def seleccionar_pagina(self):
        if self.doc:
            w, h = self.doc.get_page(self.pag).get_size()
            self._poner_sel((0.0, 0.0, w, h, Poppler.SelectionStyle.GLYPH))

    def _arr_ini(self, g, x, y):
        self.arr0 = (self.scroll.get_hadjustment().get_value(), self.scroll.get_vadjustment().get_value())
        self.modo_arr = "sel" if self.doc and g.get_current_button() == 1 else "pan"
        self.arr_xy = (x, y)
        if self.modo_arr == "sel":
            self.sel_ini = self._a_pagina(x, y)

    def _arr_mov(self, _g, dx, dy):
        if self.modo_arr == "sel":
            if abs(dx) + abs(dy) > 3:
                self.arrastro = True
                p = self._a_pagina(self.arr_xy[0] + dx, self.arr_xy[1] + dy)
                self._poner_sel((*self.sel_ini, *p, Poppler.SelectionStyle.GLYPH))
            return
        self.scroll.get_hadjustment().set_value(self.arr0[0] - dx)
        self.scroll.get_vadjustment().set_value(self.arr0[1] - dy)

    def _rueda(self, c, _dx, dy):
        if c.get_current_event_state() & Gdk.ModifierType.CONTROL_MASK:
            self.cambiar_zoom(1 / 1.15 if dy > 0 else 1.15)
            return True
        return False

    def _tecla(self, _c, k, _code, estado):
        ctrl = bool(estado & Gdk.ModifierType.CONTROL_MASK)
        if self.e_busca.has_focus() or self.e_busca.get_focus_child():
            if k == Gdk.KEY_Escape:
                self.scroll.grab_focus()
                return True
            return False
        if ctrl and k == Gdk.KEY_f and self.doc:
            self.e_busca.grab_focus()
        elif ctrl and k == Gdk.KEY_c and self.doc:
            self.copiar()
        elif ctrl and k == Gdk.KEY_a and self.doc:
            self.seleccionar_pagina()
        elif k in (Gdk.KEY_Right, Gdk.KEY_Page_Down):
            self.mover(1)
        elif k in (Gdk.KEY_Left, Gdk.KEY_Page_Up):
            self.mover(-1)
        elif k in (Gdk.KEY_plus, Gdk.KEY_KP_Add, Gdk.KEY_equal):
            self.cambiar_zoom(1.25)
        elif k in (Gdk.KEY_minus, Gdk.KEY_KP_Subtract):
            self.cambiar_zoom(1 / 1.25)
        elif k == Gdk.KEY_0:
            self.ajustar_vista()
        elif k == Gdk.KEY_1:
            self.real()
        elif k in (Gdk.KEY_r, Gdk.KEY_R):
            self.girar()
        elif k == Gdk.KEY_F11:
            self.unfullscreen() if self.is_fullscreen() else self.fullscreen()
        elif k == Gdk.KEY_Escape and self.sel:
            self.quitar_sel()
        elif k == Gdk.KEY_Escape and self.is_fullscreen():
            self.unfullscreen()
        else:
            return False
        return True


class App(AppNeural):
    def __init__(self):
        super().__init__(APP_ID)

    def crear_ventana(self, args, cwd):
        v = self.get_windows()
        w = v[0] if v else Ventana(self)
        w.present()
        if args:
            w.abrir(Path(cwd or ".", args[0]).resolve())


if __name__ == "__main__":
    nombrar_proceso("neural-visor")
    GLib.set_prgname(APP_ID)
    sys.exit(App().run(sys.argv))
