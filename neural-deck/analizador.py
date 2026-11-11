"""Analizador de espacio para NEURAL//DISCO (pestaña ESPACIO).

Recorre una carpeta en un hilo (os.scandir, sin seguir enlaces y sin saltar a otro
disco montado dentro) y dibuja un mapa por bloques (treemap): cada rectángulo es
una carpeta o archivo, con área proporcional a lo que ocupa de verdad en disco.
Clic en una carpeta = entrar; SUBIR o la ruta de arriba = volver. A la derecha,
la lista ordenada de lo que hay en la carpeta actual.
"""
from __future__ import annotations

import os
import stat
import threading
from pathlib import Path

from gi.repository import Gio, GLib, Gtk, Pango, PangoCairo

from ventana import boton, lbl
from estilo import MONO, source
from disco_motor import tam_txt

DECK = Path(__file__).resolve().parent
CHICO = 0.004  # lo que ocupe menos del 0,4 % de la carpeta se agrupa en «más pequeños»

CATEGORIAS = {
    "video": {"mp4", "mkv", "avi", "mov", "webm", "m4v", "wmv", "flv"},
    "imagen": {"jpg", "jpeg", "png", "gif", "webp", "heic", "svg", "bmp", "tif", "tiff", "raw", "cr2", "nef"},
    "audio": {"mp3", "flac", "ogg", "opus", "wav", "m4a", "aac"},
    "archivo": {"zip", "tar", "gz", "xz", "zst", "bz2", "7z", "rar", "iso", "img", "qcow2", "apk", "deb", "rpm"},
}


def categoria(nombre: str) -> str:
    ext = nombre.rsplit(".", 1)[-1].lower() if "." in nombre else ""
    for c, exts in CATEGORIAS.items():
        if ext in exts:
            return c
    return "otro"


class Nodo:
    __slots__ = ("nombre", "tam", "hijos", "padre", "archivos", "cat")

    def __init__(self, nombre: str, padre: "Nodo | None" = None):
        self.nombre = nombre
        self.tam = 0
        self.hijos: list[Nodo] | None = None  # None = archivo
        self.padre = padre
        self.archivos = 0
        self.cat = ""

    @property
    def ruta(self) -> str:
        partes = []
        n = self
        while n is not None:
            partes.append(n.nombre)
            n = n.padre
        return os.path.join(*reversed(partes))


class Escaneo:
    """Recorre `raiz` en un hilo. Mientras: .archivos y .bytes crecen; al acabar, .arbol."""

    def __init__(self, raiz: str, listo):
        self.raiz = raiz
        self.listo = listo
        self.archivos = 0
        self.bytes = 0
        self.sin_permiso = 0
        self.cancelado = False
        self.arbol: Nodo | None = None
        threading.Thread(target=self._correr, daemon=True).start()

    def _correr(self):
        try:
            dispositivo = os.stat(self.raiz).st_dev
        except OSError as e:
            GLib.idle_add(self.listo, self, str(e))
            return
        raiz = Nodo(self.raiz)
        raiz.hijos = []
        pila = [raiz]
        vistos: set[tuple[int, int]] = set()  # enlaces duros: contar una vez
        while pila and not self.cancelado:
            dir_ = pila.pop()
            try:
                it = os.scandir(dir_.ruta)
            except OSError:
                self.sin_permiso += 1
                continue
            with it:
                for e in it:
                    try:
                        st = e.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    n = Nodo(e.name, dir_)
                    if stat.S_ISDIR(st.st_mode):
                        n.hijos = []
                        dir_.hijos.append(n)
                        if st.st_dev == dispositivo:  # no meterse en otro disco montado aquí
                            pila.append(n)
                        continue
                    if st.st_nlink > 1:
                        clave = (st.st_dev, st.st_ino)
                        if clave in vistos:
                            continue
                        vistos.add(clave)
                    n.tam = st.st_blocks * 512  # lo que ocupa de verdad (archivos dispersos, etc.)
                    n.cat = categoria(e.name)
                    dir_.hijos.append(n)
                    self.archivos += 1
                    self.bytes += n.tam
        if self.cancelado:
            return
        self._sumar(raiz)
        self.arbol = raiz
        GLib.idle_add(self.listo, self, "")

    @staticmethod
    def _sumar(raiz: Nodo):
        """Tamaños de carpetas de abajo arriba (sin recursión: hay árboles muy hondos)."""
        orden, pila = [], [raiz]
        while pila:
            n = pila.pop()
            orden.append(n)
            if n.hijos:
                pila.extend(h for h in n.hijos if h.hijos is not None)
        for n in reversed(orden):
            n.tam = sum(h.tam for h in n.hijos)
            n.archivos = sum(h.archivos if h.hijos is not None else 1 for h in n.hijos)
            n.hijos.sort(key=lambda h: h.tam, reverse=True)


# ---------- treemap «squarified» (Bruls, Huizing, van Wijk) ----------
def _peor(fila: list[float], lado: float) -> float:
    s = sum(fila)
    if s <= 0 or lado <= 0:
        return float("inf")
    return max(max(fila) * lado * lado / (s * s), (s * s) / (lado * lado * min(fila)))


def squarify(valores: list[float], x, y, w, h) -> list[tuple[float, float, float, float]]:
    """Rectángulos casi cuadrados con áreas proporcionales a `valores` (ya ordenados de mayor a menor)."""
    total = sum(valores)
    if total <= 0 or w <= 0 or h <= 0:
        return [(x, y, 0, 0)] * len(valores)
    areas = [v * w * h / total for v in valores]
    out = []
    i = 0
    while i < len(areas):
        lado = min(w, h)
        fila = [areas[i]]
        j = i + 1
        while j < len(areas) and _peor(fila + [areas[j]], lado) <= _peor(fila, lado):
            fila.append(areas[j])
            j += 1
        s = sum(fila)
        if w >= h:  # columna a la izquierda
            ancho = s / h
            yy = y
            for a in fila:
                alto = a / ancho
                out.append((x, yy, ancho, alto))
                yy += alto
            x, w = x + ancho, w - ancho
        else:  # fila arriba
            alto = s / w
            xx = x
            for a in fila:
                ancho = a / alto
                out.append((xx, y, ancho, alto))
                xx += ancho
            y, h = y + alto, h - alto
        i = j
    return out


class Mapa(Gtk.DrawingArea):
    def __init__(self, panel: "Analizador"):
        super().__init__(hexpand=True, vexpand=True, has_tooltip=True)
        self.panel = panel
        self.cajas: list[tuple[tuple, Nodo | None, str]] = []
        self.set_draw_func(self._dibujar)
        clic = Gtk.GestureClick()
        clic.connect("pressed", self._clic)
        self.add_controller(clic)
        self.connect("query-tooltip", self._tip)

    def _elementos(self, nodo: Nodo):
        """Hijos que se dibujan + un bloque «más pequeños» con el resto."""
        visibles, resto, n_resto = [], 0, 0
        for h in nodo.hijos:
            if h.tam <= 0:
                continue
            if nodo.tam and h.tam / nodo.tam < CHICO or len(visibles) >= 150:
                resto += h.tam
                n_resto += 1
            else:
                visibles.append(h)
        return visibles, resto, n_resto

    def _color(self, n: Nodo | None):
        t = self.panel.theme()
        if n is None:
            return t["muted"]
        if n.hijos is not None:
            return t["accent"]
        return {"video": t["warn"], "imagen": t["alt"], "audio": t["alt"], "archivo": t["warn"]}.get(n.cat, t["muted"])

    def _dibujar(self, _a, cr, w, h):
        self.cajas = []
        nodo = self.panel.actual
        t = self.panel.theme()
        if nodo is None or not nodo.hijos or nodo.tam <= 0:
            return
        visibles, resto, n_resto = self._elementos(nodo)
        valores = [v.tam for v in visibles] + ([resto] if resto else [])
        rects = squarify(valores, 0, 0, w, h)
        for k, r in enumerate(rects):
            n = visibles[k] if k < len(visibles) else None
            txt = f"({n_resto} más pequeños)" if n is None else n.nombre
            self.cajas.append((r, n, txt))
            x, y, rw, rh = r
            if rw < 2 or rh < 2:
                continue
            c = self._color(n)
            encima = n is not None and n is self.panel.encima
            source(cr, c, 0.32 if encima else (0.18 if n and n.hijos is not None else 0.12))
            cr.rectangle(x + 1, y + 1, rw - 2, rh - 2)
            cr.fill()
            if n is not None and n.hijos:  # anticipo del interior de la carpeta
                sub = [s.tam for s in n.hijos[:30] if s.tam > 0]
                if sub and rw > 40 and rh > 40:
                    source(cr, c, 0.22)
                    cr.set_line_width(0.6)
                    for sx, sy, sw, sh in squarify(sub, x + 4, y + 22, rw - 8, rh - 26):
                        cr.rectangle(sx + 0.5, sy + 0.5, max(sw - 1, 0), max(sh - 1, 0))
                    cr.stroke()
            source(cr, t["alt"] if encima else c, 1.0 if encima else 0.65)
            cr.set_line_width(2 if encima else 1)
            cr.rectangle(x + 1.5, y + 1.5, rw - 3, rh - 3)
            cr.stroke()
            if rw > 46 and rh > 18:
                lay = PangoCairo.create_layout(cr)
                lay.set_font_description(Pango.FontDescription.from_string(f"{MONO} 9"))
                lay.set_width(int((rw - 10) * Pango.SCALE))
                lay.set_ellipsize(Pango.EllipsizeMode.END)
                tam = tam_txt(n.tam if n else resto)
                lay.set_text(f"{txt}{'/' if n and n.hijos is not None else ''} · {tam}", -1)
                source(cr, t["text"], 0.9)
                cr.move_to(x + 5, y + 4)
                PangoCairo.show_layout(cr, lay)

    def _en(self, px, py):
        for (x, y, w, h), n, txt in self.cajas:
            if x <= px < x + w and y <= py < y + h:
                return n, txt
        return None, None

    def _clic(self, _g, _n, px, py):
        n, _t = self._en(px, py)
        if n is not None:
            self.panel.tocar(n)

    def _tip(self, _w, px, py, _k, tip):
        n, txt = self._en(px, py)
        if txt is None:
            return False
        if n is None:
            tip.set_text(txt)
        else:
            extra = f" · {n.archivos} archivos" if n.hijos is not None else ""
            tip.set_text(f"{n.ruta}\n{tam_txt(n.tam)}{extra}")
        return True


class Analizador(Gtk.Box):
    def __init__(self, ventana):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=8, vexpand=True)
        self.ven = ventana
        self.escaneo: Escaneo | None = None
        self.arbol: Nodo | None = None
        self.actual: Nodo | None = None
        self.encima: Nodo | None = None

        self.ruta_lbl = lbl("Elige qué analizar", "nx-txt", xalign=0, hexpand=True,
                            ellipsize=Pango.EllipsizeMode.START)
        self.b_subir = boton("↑ SUBIR", "nx-accion", self._subir)
        barra = Gtk.Box(spacing=6)
        barra.append(self.b_subir)
        barra.append(self.ruta_lbl)
        barra.append(boton("CARPETA PERSONAL", "nx-chip", lambda: self.analizar(str(Path.home()))))
        barra.append(boton("TODO EL SISTEMA", "nx-chip", lambda: self.analizar("/"),
                           "Solo la partición raíz: no entra en /boot ni en USB montados"))
        barra.append(boton("ELEGIR…", "nx-chip", self._elegir))
        self.b_files = boton("ABRIR EN FILES", "nx-accion", self._abrir_files)
        barra.append(self.b_files)
        self.append(barra)

        self.estado = lbl("", "nx-code", xalign=0)
        self.prog = Gtk.ProgressBar(visible=False)
        self.prog.add_css_class("nx-prog")
        self.b_cancelar = boton("CANCELAR", "nx-chip", self._cancelar)
        self.b_cancelar.set_visible(False)
        fila_estado = Gtk.Box(spacing=8)
        self.estado.set_hexpand(True)
        fila_estado.append(self.estado)
        fila_estado.append(self.b_cancelar)
        self.append(fila_estado)
        self.append(self.prog)

        cuerpo = Gtk.Box(spacing=12, vexpand=True)
        self.mapa = Mapa(self)
        movi = Gtk.EventControllerMotion()
        movi.connect("motion", self._mover)
        movi.connect("leave", lambda *_: self._marcar(None))
        self.mapa.add_controller(movi)
        cuerpo.append(self.mapa)

        self.lista = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        sc = Gtk.ScrolledWindow(child=self.lista, hscrollbar_policy=Gtk.PolicyType.NEVER)
        sc.add_css_class("nx-scroll")
        sc.set_size_request(270, -1)
        lado = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        lado.add_css_class("nx-lado")
        lado.append(lbl("LO QUE MÁS OCUPA", "nx-h2", xalign=0))
        sc.set_vexpand(True)
        lado.append(sc)
        cuerpo.append(lado)
        self.append(cuerpo)
        self._pintar()

    def theme(self):
        return self.ven.theme

    # ---------- escanear ----------
    def analizar(self, ruta: str):
        if self.escaneo is not None:
            self.escaneo.cancelado = True
        self.arbol = self.actual = None
        self.ruta_lbl.set_text(ruta)
        self.escaneo = Escaneo(ruta, self._terminado)
        self.prog.set_visible(True)
        self.b_cancelar.set_visible(True)
        self._pintar()
        GLib.timeout_add(150, self._latido, self.escaneo)

    def _latido(self, esc: Escaneo):
        if esc is not self.escaneo or esc.arbol is not None or esc.cancelado:
            return GLib.SOURCE_REMOVE
        self.prog.pulse()
        self.estado.set_text(f"Analizando… {esc.archivos:,} archivos · {tam_txt(esc.bytes)}".replace(",", "."))
        return GLib.SOURCE_CONTINUE

    def _terminado(self, esc: Escaneo, error: str):
        if esc is not self.escaneo:
            return False
        self.prog.set_visible(False)
        self.b_cancelar.set_visible(False)
        if error:
            self.estado.set_text(f"ERROR: {error}")
            return False
        self.arbol = self.actual = esc.arbol
        aviso = f" · {esc.sin_permiso} carpetas sin permiso (no contadas)" if esc.sin_permiso else ""
        self.estado.set_text(f"{esc.arbol.archivos:,} archivos · {tam_txt(esc.arbol.tam)}".replace(",", ".") + aviso)
        self._pintar()
        return False

    def _cancelar(self):
        if self.escaneo:
            self.escaneo.cancelado = True
            self.escaneo = None
        self.prog.set_visible(False)
        self.b_cancelar.set_visible(False)
        self.estado.set_text("Cancelado")

    # ---------- navegar ----------
    def tocar(self, n: Nodo):
        if n.hijos is not None and n.hijos:
            self.actual = n
            self._pintar()
        elif n.hijos is None:
            self.estado.set_text(f"{n.ruta} · {tam_txt(n.tam)}")

    def _subir(self):
        if self.actual is not None and self.actual.padre is not None:
            self.actual = self.actual.padre
            self._pintar()

    def _mover(self, _c, x, y):
        n, _t = self.mapa._en(x, y)
        self._marcar(n)

    def _marcar(self, n):
        if n is not self.encima:
            self.encima = n
            self.mapa.queue_draw()

    def _pintar(self):
        a = self.actual
        self.b_subir.set_sensitive(a is not None and a.padre is not None)
        self.b_files.set_sensitive(a is not None)
        if a is not None:
            self.ruta_lbl.set_text(f"{a.ruta}  ·  {tam_txt(a.tam)}")
        while (h := self.lista.get_first_child()) is not None:
            self.lista.remove(h)
        if a is None:
            if self.escaneo is None:
                self.lista.append(lbl("Elige arriba qué carpeta analizar,\no pulsa ANALIZAR ESPACIO en una\n"
                                      "partición montada (pestaña DISCOS).", "nx-dim", xalign=0))
        else:
            for h in a.hijos[:200]:
                if h.tam <= 0:
                    continue
                b = Gtk.Button()
                b.add_css_class("nx-item")
                caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
                arriba = Gtk.Box(spacing=6)
                es_dir = h.hijos is not None
                arriba.append(lbl(("▸ " if es_dir else "  ") + h.nombre, "nx-txt", xalign=0, hexpand=True,
                                  ellipsize=Pango.EllipsizeMode.MIDDLE))
                arriba.append(lbl(tam_txt(h.tam), "nx-code on", xalign=1))
                caja.append(arriba)
                pb = Gtk.ProgressBar(fraction=h.tam / a.tam if a.tam else 0)
                pb.add_css_class("nx-prog")
                caja.append(pb)
                b.set_child(caja)
                b.connect("clicked", lambda _b, h=h: self.tocar(h))
                self.lista.append(b)
        self.mapa.queue_draw()

    # ---------- otros ----------
    def _elegir(self):
        dlg = Gtk.FileDialog(title="Carpeta a analizar")

        def fin(d, res):
            try:
                f = d.select_folder_finish(res)
            except GLib.Error:
                return
            if f and f.get_path():
                self.analizar(f.get_path())
        dlg.select_folder(self.ven, None, fin)

    def _abrir_files(self):
        if self.actual is not None:
            Gio.Subprocess.new(["python3", str(DECK / "files.py"), self.actual.ruta], Gio.SubprocessFlags.NONE)
