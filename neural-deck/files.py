#!/usr/bin/env python3
"""NEURAL//FILES: gestor de archivos con el estilo NEURAL//DECK.

- Lateral: lugares (carpetas XDG que existan), favoritos (~/.config/gtk-3.0/bookmarks,
  los mismos del selector de archivos de GTK), sistema, papelera, discos montados
  con su ocupación y USB / EXTERNOS (UDisks2, ver usb.py): clic = montar y abrir,
  ⏏ = desmontar y apagar el disco, clic derecho = solo lectura / desmontar.
- Vista cuadrícula (miniaturas reales de PNG/JPEG/GIF, caché freedesktop en
  ~/.cache/thumbnails) o lista con columnas; panel de vista previa (F9).
- Copiar/cortar/pegar, arrastrar y soltar, renombrar, papelera (con restaurar),
  comprimir/extraer (bsdtar), nueva carpeta/archivo, abrir terminal aquí.
- Abrir: carpetas → navegar; imágenes → visor propio; texto → vim en NEURAL//TERM;
  el resto → app predeterminada (o «Abrir con…», que puede fijarla).
- Búsqueda: escribir filtra la carpeta; Enter busca en subcarpetas.

Atajos: Alt+←/→/↑ (o Retroceso) navegar · Ctrl+L escribir ruta · Ctrl+F buscar
Ctrl+H ocultos · Ctrl+1/2 cuadrícula/lista · F9 vista previa · F2 renombrar
Supr papelera · Shift+Supr eliminar · Ctrl+C/X/V · Ctrl+A · Ctrl+Shift+N carpeta
F5 recargar · Ctrl+N ventana nueva · Ctrl+W cerrar
Uso: files.py [CARPETA | IMAGEN]
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import unquote, urlparse

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, GObject, Gtk, Pango  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from estilo import MONO, SANS, css_color  # noqa: E402
from ventana import AppNeural, VentanaNeural, boton, lbl, nombrar_proceso, on, tam_humano  # noqa: E402

try:
    import usb  # noqa: E402  (UDisks2: discos USB / externos)
except ImportError:
    usb = None

APP_ID = "org.local.NeuralFiles"
PROC = "neural-files"
DECK = Path(__file__).resolve().parent
HOME = Path.home()
TRASH = HOME / ".local/share/Trash"
MARCADORES = HOME / ".config/gtk-3.0/bookmarks"
MINIS = HOME / ".cache/thumbnails/normal"
ATR = ("standard::name,standard::display-name,standard::type,standard::size,standard::content-type,"
       "standard::symbolic-icon,standard::is-hidden,standard::is-backup,standard::is-symlink,"
       "standard::symlink-target,time::modified,unix::mode,owner::user")
IMAGENES = ("image/png", "image/jpeg", "image/gif")
ARCHIVADOS = ("application/zip", "application/x-tar", "application/x-compressed-tar", "application/x-xz-compressed-tar",
              "application/x-bzip-compressed-tar", "application/x-zstd-compressed-tar", "application/x-7z-compressed",
              "application/x-xz", "application/gzip", "application/vnd.rar", "application/x-cpio",
              "application/x-iso9660-image", "application/x-rpm", "application/vnd.debian.binary-package")
EJECUTABLES = ("application/x-executable", "application/x-pie-executable", "application/x-sharedlib",
               "application/x-shellscript", "application/x-appimage", "application/vnd.appimage",
               "text/x-python", "text/x-python3", "application/x-perl", "text/x-perl")
LOGS_EJEC = HOME / ".cache/neural-files/ejecutados"
LUGARES = [("Inicio", HOME, "user-home-symbolic"), ("Escritorio", HOME / "Escritorio", "user-desktop-symbolic"),
           ("Documentos", HOME / "Documentos", "folder-documents-symbolic"),
           ("Descargas", HOME / "Descargas", "folder-download-symbolic"),
           ("Imágenes", HOME / "Imágenes", "folder-pictures-symbolic"),
           ("Capturas", HOME / "Imágenes/Capturas", "camera-photo-symbolic"),
           ("Música", HOME / "Música", "folder-music-symbolic"), ("Vídeos", HOME / "Vídeos", "folder-videos-symbolic")]
ORDENES = [("nombre", "NOMBRE"), ("tam", "TAMAÑO"), ("fecha", "FECHA"), ("tipo", "TIPO")]


def _ajustes() -> dict:
    try:
        from energia import leer_todo
        d = leer_todo().get("archivos", {})
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _guardar_ajustes(d: dict) -> None:
    try:
        from energia import guardar_seccion
        guardar_seccion("archivos", d)
    except Exception as e:
        print(f"files: no se guardaron ajustes ({e})", flush=True)


def clave_natural(s: str):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s.casefold())]


def nombre_libre(carpeta: Path, nombre: str, sufijo: str = "copia") -> Path:
    """carpeta/nombre, o «nombre (copia).ext», «nombre (copia 2).ext»… si existe."""
    destino = carpeta / nombre
    if not destino.exists() and not destino.is_symlink():
        return destino
    p = Path(nombre)
    base, ext = (p.stem, p.suffix) if not nombre.startswith(".") or p.suffix else (nombre, "")
    for doble in (".tar.gz", ".tar.xz", ".tar.zst", ".tar.bz2"):
        if nombre.endswith(doble):
            base, ext = nombre[: -len(doble)], doble
    for i in range(1, 1000):
        cand = carpeta / f"{base} ({sufijo}{'' if i == 1 else ' ' + str(i)}){ext}"
        if not cand.exists() and not cand.is_symlink():
            return cand
    return carpeta / f"{base} ({sufijo} {time.time():.0f}){ext}"


def leer_marcadores() -> list[tuple[str, Path]]:
    out = []
    try:
        for linea in MARCADORES.read_text().splitlines():
            if not linea.startswith("file://"):
                continue
            uri, _, etiqueta = linea.partition(" ")
            ruta = Path(unquote(urlparse(uri).path))
            out.append((etiqueta or ruta.name or str(ruta), ruta))
    except OSError:
        pass
    return out


def guardar_marcadores(lista: list[tuple[str, Path]]) -> None:
    MARCADORES.parent.mkdir(parents=True, exist_ok=True)
    lineas = []
    for etiqueta, ruta in lista:
        uri = Gio.File.new_for_path(str(ruta)).get_uri()
        lineas.append(uri if etiqueta == ruta.name else f"{uri} {etiqueta}")
    MARCADORES.write_text("\n".join(lineas) + ("\n" if lineas else ""))


def discos() -> list[dict]:
    vistos, out = set(), []
    try:
        lineas = Path("/proc/self/mounts").read_text().splitlines()
    except OSError:
        return out
    for linea in lineas:
        campos = linea.split()
        if len(campos) < 3 or not campos[0].startswith("/dev/"):
            continue
        dev, punto = campos[0], campos[1].replace("\\040", " ")
        if dev in vistos or punto.startswith(("/snap", "/var/lib/flatpak", "/run/media/")):
            continue
        vistos.add(dev)
        try:
            st = os.statvfs(punto)
        except OSError:
            continue
        total, libre = st.f_blocks * st.f_frsize, st.f_bavail * st.f_frsize
        if total == 0:
            continue
        nombre = "SISTEMA" if punto == "/" else Path(punto).name.upper() or punto
        if "mapper" in dev and punto == "/":
            nombre = "SISTEMA · LUKS"
        out.append({"nombre": nombre, "ruta": Path(punto), "total": total, "libre": libre, "dev": dev})
    return out


def es_ejecutable(info: Gio.FileInfo) -> bool:
    """Archivo (no carpeta) con permiso de ejecución que sea un programa o un script con #!."""
    if es_dir(info) or not (info.get_attribute_uint32("unix::mode") & 0o111):
        return False
    ct = info.get_content_type() or ""
    if ct in EJECUTABLES:
        return True
    ruta = archivo_de(info).get_path()
    try:
        with open(ruta, "rb") as fh:
            cabeza = fh.read(4)
    except OSError:
        return False
    return cabeza[:2] == b"#!" or cabeza == b"\x7fELF"


def info_de(f: Gio.File) -> Gio.FileInfo | None:
    try:
        info = f.query_info(ATR, Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS, None)
    except GLib.Error:
        return None
    info.set_attribute_object("standard::file", f)
    return info


def archivo_de(info: Gio.FileInfo) -> Gio.File:
    return info.get_attribute_object("standard::file")


def es_dir(info: Gio.FileInfo) -> bool:
    if info.get_file_type() == Gio.FileType.DIRECTORY:
        return True
    if info.get_is_symlink():
        p = archivo_de(info).get_path()
        return bool(p) and os.path.isdir(p)
    return False


def icono_simbolico(info: Gio.FileInfo):
    """El icono simbólico del tipo, o uno genérico si el tema no tiene ninguno."""
    icono = info.get_symbolic_icon()
    if isinstance(icono, Gio.ThemedIcon):
        tema = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
        if not any(n.endswith("-symbolic") and tema.has_icon(n) for n in icono.get_names()):
            return Gio.ThemedIcon.new("text-x-generic-symbolic")
    return icono or Gio.ThemedIcon.new("text-x-generic-symbolic")


def clase_icono(info: Gio.FileInfo) -> str:
    if es_dir(info):
        return "nf-ic-dir"
    ct = info.get_content_type() or ""
    if ct.startswith(("image/", "video/")):
        return "nf-ic-img"
    if ct in ARCHIVADOS:
        return "nf-ic-arc"
    if info.get_attribute_uint32("unix::mode") & 0o111 and not ct.startswith("text/"):
        return "nf-ic-exe"
    return "nf-ic-file"


# ---------------------------------------------------------------- miniaturas
class Miniaturas:
    """Miniaturas freedesktop (128 px) generadas en 2 hilos."""

    def __init__(self):
        self.pool = ThreadPoolExecutor(max_workers=2)
        self.pendientes: set[str] = set()
        self.fallidas: set[str] = set()

    @staticmethod
    def ruta(uri: str) -> Path:
        return MINIS / (hashlib.md5(uri.encode()).hexdigest() + ".png")

    def lista(self, uri: str, mtime: int) -> Path | None:
        """Ruta de la miniatura si existe y está al día."""
        r = self.ruta(uri)
        try:
            if r.stat().st_mtime >= mtime:
                return r
        except OSError:
            pass
        return None

    def pedir(self, uri: str, ruta: str, mtime: int, listo) -> None:
        if uri in self.pendientes or uri in self.fallidas:
            return
        self.pendientes.add(uri)
        self.pool.submit(self._hacer, uri, ruta, mtime, listo)

    def _hacer(self, uri, ruta, mtime, listo):
        destino = self.ruta(uri)
        ok = False
        try:
            if os.path.getsize(ruta) <= 60 * 1024 * 1024:
                pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(ruta, 128, 128, True)
                pb = pb.apply_embedded_orientation() or pb
                MINIS.mkdir(parents=True, exist_ok=True)
                tmp = destino.with_suffix(f".{os.getpid()}.tmp")
                pb.savev(str(tmp), "png", ["tEXt::Thumb::URI", "tEXt::Thumb::MTime"], [uri, str(mtime)])
                os.replace(tmp, destino)
                ok = True
        except (GLib.Error, OSError):
            pass
        GLib.idle_add(self._fin, uri, ok, listo)

    def _fin(self, uri, ok, listo):
        self.pendientes.discard(uri)
        if not ok:
            self.fallidas.add(uri)
        else:
            listo(uri, self.ruta(uri))
        return False


# ---------------------------------------------------------------- celdas
class Celda(Gtk.Box):
    """Celda de cuadrícula o fila de lista; guarda la posición y el uri."""

    def __init__(self, ventana: "Ventana", modo: str):
        super().__init__(orientation=Gtk.Orientation.VERTICAL if modo == "grid" else Gtk.Orientation.HORIZONTAL,
                         spacing=4 if modo == "grid" else 8)
        self.li = None  # Gtk.ListItem; su posición cambia al reordenar sin volver a llamar a bind
        self.uri = ""
        self.modo = modo
        self.img = Gtk.Image(pixel_size=56 if modo == "grid" else 18)
        if modo == "grid":
            self.add_css_class("nf-celda")
            self.img.set_size_request(72, 64)
            self.nombre = lbl("", "nf-nombre", wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, lines=2,
                              ellipsize=Pango.EllipsizeMode.MIDDLE, justify=Gtk.Justification.CENTER,
                              width_chars=13, max_width_chars=13)
        else:
            self.nombre = lbl("", "nf-nombre", xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.MIDDLE)
        self.pic = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, can_shrink=True, visible=False)
        if modo == "grid":
            self.pic.set_size_request(96, 64)
            self.pic.add_css_class("nf-mini")
        self.append(self.img)
        self.append(self.pic)
        self.append(self.nombre)
        ventana.preparar_arrastre(self)
        ventana.celdas.append(self)


    @property
    def pos(self) -> int:
        if self.li is None or not self.uri:
            return -1
        p = self.li.get_position()
        return -1 if p == Gtk.INVALID_LIST_POSITION else p


# ---------------------------------------------------------------- visor de imágenes
class Visor(VentanaNeural):
    def __init__(self, app, imagenes: list[Path], i: int):
        super().__init__(app, "SYS//VIEW", "NEURAL//VIEW", (1100, 760))
        self.imagenes, self.i = imagenes, i
        self.pic = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, can_shrink=True, vexpand=True, hexpand=True)
        self.cuerpo.append(self.pic)
        self.cab_centro.append(boton("◀", "nx-btn", lambda: self.mover(-1), "Anterior (←)"))
        self.cab_centro.append(boton("▶", "nx-btn", lambda: self.mover(1), "Siguiente (→)"))
        self.cab_centro.append(boton("CARPETA", "nx-chip", self._carpeta, "Abrir la carpeta en FILES"))
        k = Gtk.EventControllerKey()
        k.connect("key-pressed", self._tecla)
        self.add_controller(k)
        self.mostrar()

    def mostrar(self):
        p = self.imagenes[self.i]
        try:
            tex = Gdk.Texture.new_from_filename(str(p))
            self.pic.set_paintable(tex)
            dim = f"{tex.get_width()}×{tex.get_height()}"
        except GLib.Error as e:
            self.pic.set_paintable(None)
            dim = "no se pudo abrir: " + e.message
        try:
            peso = tam_humano(p.stat().st_size)
        except OSError:
            peso = "?"
        self.p_izq.set_text(f"{p.name}  //  {dim}  ·  {peso}  //  {self.i + 1} DE {len(self.imagenes)}")
        self.set_title(f"{p.name} · NEURAL//VIEW")

    def mover(self, d):
        self.i = (self.i + d) % len(self.imagenes)
        self.mostrar()

    def _carpeta(self):
        self.app.crear_ventana([str(self.imagenes[self.i].parent)], None)

    def _tecla(self, _c, kv, _code, estado):
        if kv in (Gdk.KEY_Right, Gdk.KEY_space, Gdk.KEY_Page_Down):
            self.mover(1)
        elif kv in (Gdk.KEY_Left, Gdk.KEY_BackSpace, Gdk.KEY_Page_Up):
            self.mover(-1)
        elif kv == Gdk.KEY_Escape or (kv == Gdk.KEY_w and estado & Gdk.ModifierType.CONTROL_MASK):
            self.close()
        elif kv == Gdk.KEY_F11:
            self.unfullscreen() if self.is_fullscreen() else self.fullscreen()
        else:
            return False
        return True


# ---------------------------------------------------------------- ventana
class Ventana(VentanaNeural):
    def __init__(self, app: "App", carpeta: Path):
        super().__init__(app, "SYS//FS", "NEURAL//FILES", (1180, 720))
        aj = _ajustes()
        self.vista = aj.get("vista", "grid") if aj.get("vista") in ("grid", "lista") else "grid"
        self.ocultos = bool(aj.get("ocultos", False))
        self.orden = aj.get("orden", "nombre") if aj.get("orden") in dict(ORDENES) else "nombre"
        self.desc = bool(aj.get("desc", False))
        self.previa_on = bool(aj.get("previa", True))
        self.carpeta: Path = carpeta
        self.modo = "dir"  # dir | buscar | papelera
        self.historial: list[Path] = []
        self.hpos = -1
        self.claves: dict[str, list] = {}
        self.busqueda_hilo: threading.Event | None = None
        self.op_activa = False
        self.celdas: list[Celda] = []

        # --- barra: navegación, ruta, búsqueda, vista ---
        nav = Gtk.Box(spacing=2)
        self.b_atras = boton("◀", "nx-btn", lambda: self.navegar(-1), "Atrás (Alt+←)")
        self.b_adelante = boton("▶", "nx-btn", lambda: self.navegar(1), "Adelante (Alt+→)")
        self.b_arriba = boton("▲", "nx-btn", self.subir, "Carpeta superior (Alt+↑)")
        for b in (self.b_atras, self.b_adelante, self.b_arriba):
            nav.append(b)
        self.cab_centro.append(nav)
        self.ruta_pila = Gtk.Stack(hexpand=True, transition_type=Gtk.StackTransitionType.CROSSFADE)
        sc = Gtk.ScrolledWindow(vscrollbar_policy=Gtk.PolicyType.NEVER, hscrollbar_policy=Gtk.PolicyType.EXTERNAL,
                                hexpand=True)
        self.migas = Gtk.Box(spacing=2)
        sc.set_child(self.migas)
        sc.set_propagate_natural_height(True)
        self.migas_sc = sc
        sc.get_hadjustment().connect("notify::upper", lambda a, _p: a.set_value(a.get_upper()))
        self.ruta_pila.add_named(sc, "migas")
        self.ruta_entry = Gtk.Entry()
        self.ruta_entry.add_css_class("nx-entry")
        self.ruta_entry.connect("activate", self._ruta_escrita)
        self.ruta_pila.add_named(self.ruta_entry, "entry")
        self.cab_centro.append(self.ruta_pila)
        self.buscar = Gtk.SearchEntry(placeholder_text="filtrar · Enter = subcarpetas", width_chars=17)
        self.buscar.add_css_class("nx-entry")
        self.buscar.connect("search-changed", lambda *_: self._filtrar_cambio())
        self.buscar.connect("activate", lambda *_: self.buscar_recursivo())
        self.buscar.connect("stop-search", lambda *_: self._limpiar_busqueda())
        self.cab_centro.append(self.buscar)
        self.b_vista = boton("☰", "nx-btn", self.alternar_vista, "Cuadrícula / lista (Ctrl+1/2)")
        self.b_previa = boton("◧", "nx-btn", self.alternar_previa, "Vista previa (F9)")
        self.b_menu = boton("⋮", "nx-btn", self._menu_vista, "Orden y ocultos")
        for b in (self.b_vista, self.b_previa, self.b_menu):
            self.cab_centro.append(b)

        # --- barra de entrada (renombrar, nueva carpeta, confirmar) ---
        self.entrada_rev = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN,
                                        transition_duration=120)
        fila = Gtk.Box(spacing=8, margin_top=4, margin_bottom=2)
        fila.add_css_class("nf-pregunta")
        self.entrada_txt = lbl("", "nx-code on")
        self.entrada = Gtk.Entry(hexpand=True)
        self.entrada.add_css_class("nx-entry")
        self.entrada.connect("activate", lambda *_: self._entrada_ok())
        self.entrada_b = boton("ACEPTAR", "nx-accion fuerte", self._entrada_ok)
        fila.append(self.entrada_txt)
        fila.append(self.entrada)
        fila.append(self.entrada_b)
        fila.append(boton("CANCELAR", "nx-accion", self._entrada_cerrar))
        self.entrada_rev.set_child(fila)
        self.cuerpo.append(self.entrada_rev)
        self._entrada_fn = None

        # --- cuerpo: lateral | vista | previa ---
        cuerpo = Gtk.Box(vexpand=True, spacing=0, margin_top=6)
        self.cuerpo.append(cuerpo)
        lat_sc = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, width_request=210, hexpand=False)
        lat_sc.add_css_class("nx-scroll")
        self.lateral = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        self.lateral.add_css_class("nf-lateral")
        lat_sc.set_child(self.lateral)
        cuerpo.append(lat_sc)

        # modelo: base → filtro → orden → selección múltiple
        self.dlist = Gtk.DirectoryList(attributes=ATR, monitored=True)
        self.dlist.connect("notify::loading", lambda *_: self._cargado())
        self.dlist.connect("notify::error", lambda *_: self._error_dir())
        self.store = Gio.ListStore(item_type=Gio.FileInfo)
        self.filtro = Gtk.CustomFilter.new(self._pasa_filtro)
        self.fmodel = Gtk.FilterListModel(model=self.dlist, filter=self.filtro)
        self.sorter = Gtk.CustomSorter.new(self._comparar)
        self.smodel = Gtk.SortListModel(model=self.fmodel, sorter=self.sorter)
        self.sel = Gtk.MultiSelection(model=self.smodel)
        self.sel.connect("selection-changed", lambda *_: self._sel_cambio())
        self.smodel.connect("items-changed", lambda *_: self._modelo_cambio())
        self._previa_id = 0

        self.pila_vistas = Gtk.Stack(hexpand=True, vexpand=True)
        self.grid = Gtk.GridView(model=self.sel, factory=self._fabrica("grid"), max_columns=30, min_columns=2,
                                 enable_rubberband=True)
        self.grid.add_css_class("nf-grid")
        self.grid.connect("activate", lambda _v, pos: self.abrir_pos(pos))
        self.lista = Gtk.ColumnView(model=self.sel, enable_rubberband=True, show_column_separators=False,
                                    reorderable=False)
        self.lista.add_css_class("nf-lista")
        self.lista.connect("activate", lambda _v, pos: self.abrir_pos(pos))
        for titulo, campo, expande in (("NOMBRE", "nombre", True), ("TAMAÑO", "tam", False),
                                       ("MODIFICADO", "fecha", False), ("TIPO", "tipo", False)):
            col = Gtk.ColumnViewColumn(title=titulo, factory=self._fabrica_col(campo), expand=expande,
                                       resizable=True)
            col.set_fixed_width({"nombre": 260, "tam": 80, "fecha": 130, "tipo": 140}[campo])
            self.lista.append_column(col)
        for nombre, vista in (("grid", self.grid), ("lista", self.lista)):
            s = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
            s.add_css_class("nx-scroll")
            s.set_child(vista)
            self.pila_vistas.add_named(s, nombre)
            clic = Gtk.GestureClick(button=3)
            clic.connect("pressed", self._clic_derecho, vista)
            vista.add_controller(clic)
            clic_medio = Gtk.GestureClick(button=2)
            clic_medio.connect("pressed", self._clic_medio, vista)
            vista.add_controller(clic_medio)
            vacio = Gtk.GestureClick(button=1)
            vacio.connect("released", self._clic_vacio, vista)
            vista.add_controller(vacio)
        destino = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY | Gdk.DragAction.MOVE)
        destino.connect("drop", lambda _t, valor, _x, _y: self._soltar(valor, self.carpeta))
        self.pila_vistas.add_controller(destino)
        self.vacio_lbl = lbl("", "nx-dim", vexpand=True, valign=Gtk.Align.CENTER)
        superpuesto = Gtk.Overlay(hexpand=True, vexpand=True)
        superpuesto.set_child(self.pila_vistas)
        superpuesto.add_overlay(self.vacio_lbl)
        self.vacio_lbl.set_can_target(False)
        cuerpo.append(superpuesto)

        self.previa = Gtk.Revealer(hexpand=False, transition_type=Gtk.RevealerTransitionType.SLIDE_LEFT, transition_duration=140)
        pv_sc = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, width_request=300, hexpand=False)
        pv_sc.add_css_class("nx-scroll")
        self.previa_caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.previa_caja.add_css_class("nx-lado")
        pv_sc.set_child(self.previa_caja)
        self.previa.set_child(pv_sc)
        cuerpo.append(self.previa)

        # --- operaciones en segundo plano ---
        self.ops = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_UP, transition_duration=140)
        ob = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3, margin_top=4)
        f = Gtk.Box(spacing=10)
        self.op_txt = lbl("", "nx-code on", xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.MIDDLE)
        f.append(self.op_txt)
        self.b_op_cancelar = boton("CANCELAR", "nx-chip", self._cancelar_op)
        f.append(self.b_op_cancelar)
        ob.append(f)
        self.prog = Gtk.ProgressBar()
        self.prog.add_css_class("nx-prog")
        ob.append(self.prog)
        self.ops.set_child(ob)
        self.cuerpo.append(self.ops)
        self.cancelar_op = threading.Event()

        teclas = Gtk.EventControllerKey()
        teclas.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        teclas.connect("key-pressed", self._tecla)
        self.add_controller(teclas)
        self.connect("close-request", self._al_cerrar)

        self.mon_trash = None
        self.aplicar_vista()
        self.previa.set_reveal_child(self.previa_on)
        on(self.b_previa, "on", self.previa_on)
        self.ir(carpeta)
        self.actualizar_previa()

    # ================================================================ fábricas
    def _fabrica(self, modo):
        f = Gtk.SignalListItemFactory()
        f.connect("setup", lambda _f, li: li.set_child(Celda(self, modo)))
        f.connect("bind", lambda _f, li: self._bind(li.get_child(), li.get_item(), li))
        f.connect("unbind", lambda _f, li: setattr(li.get_child(), "uri", ""))
        return f

    def _fabrica_col(self, campo):
        f = Gtk.SignalListItemFactory()
        if campo == "nombre":
            f.connect("setup", lambda _f, li: li.set_child(Celda(self, "lista")))
            f.connect("bind", lambda _f, li: self._bind(li.get_child(), li.get_item(), li))
            f.connect("unbind", lambda _f, li: setattr(li.get_child(), "uri", ""))
        else:
            f.connect("setup", lambda _f, li: li.set_child(lbl("", "nf-col", xalign=1 if campo == "tam" else 0,
                                                                ellipsize=Pango.EllipsizeMode.END)))
            f.connect("bind", lambda _f, li: li.get_child().set_text(self._columna(li.get_item(), campo)))
        return f

    def _columna(self, info, campo) -> str:
        if campo == "tam":
            return "—" if es_dir(info) else tam_humano(info.get_size())
        if campo == "fecha":
            dt = info.get_modification_date_time()
            return dt.to_local().format("%d-%m-%Y %H:%M") if dt else ""
        if campo == "tipo":
            if self.modo == "papelera":
                return info.get_attribute_string("trash::orig-path") or ""
            if self.modo == "buscar":
                p = archivo_de(info).get_parent()
                return self._corta(Path(p.get_path())) if p else ""
            return "carpeta" if es_dir(info) else (Gio.content_type_get_description(info.get_content_type() or "")
                                                   or "")
        return ""

    def _bind(self, celda: Celda, info: Gio.FileInfo, li: Gtk.ListItem):
        celda.li = li
        f = archivo_de(info)
        celda.uri = f.get_uri()
        celda.nombre.set_text(info.get_display_name())
        for c in ("nf-ic-dir", "nf-ic-img", "nf-ic-arc", "nf-ic-exe", "nf-ic-file"):
            celda.img.remove_css_class(c)
        celda.img.add_css_class(clase_icono(info))
        on(celda, "oculto", info.get_is_hidden() or info.get_is_backup())
        on(celda, "cortado", self._cortado(f.get_path()))
        celda.img.set_from_gicon(icono_simbolico(info))
        celda.set_tooltip_text(f.get_path() if self.modo == "buscar" else
                               info.get_attribute_string("trash::orig-path") if self.modo == "papelera" else None)
        celda.img.set_visible(True)
        celda.pic.set_visible(False)
        celda.pic.set_paintable(None)
        if celda.modo == "grid" and (info.get_content_type() or "") in IMAGENES:
            mtime = int(info.get_attribute_uint64("time::modified"))
            hecha = self.app.minis.lista(celda.uri, mtime)
            if hecha:
                self._poner_mini(celda, hecha)
            elif f.get_path():
                uri = celda.uri
                self.app.minis.pedir(uri, f.get_path(), mtime,
                                     lambda u, ruta, c=celda: c.uri == u and self._poner_mini(c, ruta))

    def _poner_mini(self, celda, ruta):
        try:
            celda.pic.set_paintable(Gdk.Texture.new_from_filename(str(ruta)))
            celda.pic.set_visible(True)
            celda.img.set_visible(False)
        except GLib.Error:
            pass

    def _cortado(self, ruta) -> bool:
        modo, rutas = self.app.portapapeles
        return modo == "cortar" and ruta in rutas

    # ================================================================ modelo
    def _pasa_filtro(self, info) -> bool:
        if not self.ocultos and self.modo == "dir" and (info.get_is_hidden() or info.get_is_backup()):
            return False
        q = self.buscar.get_text().strip().casefold() if self.modo == "dir" else ""
        return not q or q in info.get_display_name().casefold()

    def _clave(self, nombre: str):
        k = self.claves.get(nombre)
        if k is None:
            k = self.claves[nombre] = clave_natural(nombre)
        return k

    def _comparar(self, a, b, *_):
        da, db = es_dir(a), es_dir(b)
        if da != db:
            return -1 if da else 1
        if self.orden == "tam" and not da:
            ka, kb = a.get_size(), b.get_size()
        elif self.orden == "fecha":
            ka, kb = a.get_attribute_uint64("time::modified"), b.get_attribute_uint64("time::modified")
        elif self.orden == "tipo" and not da:
            ka, kb = a.get_content_type() or "", b.get_content_type() or ""
        else:
            ka, kb = 0, 0
        if ka == kb:
            ka, kb = self._clave(a.get_display_name()), self._clave(b.get_display_name())
            r = (ka > kb) - (ka < kb)
            return -r if self.desc and self.orden == "nombre" else r
        r = (ka > kb) - (ka < kb)
        return -r if self.desc else r

    def _refiltrar(self):
        self.filtro.changed(Gtk.FilterChange.DIFFERENT)

    def _reordenar(self):
        self.sorter.changed(Gtk.SorterChange.DIFFERENT)

    # ================================================================ navegación
    def ir(self, carpeta: Path, registrar: bool = True):
        carpeta = Path(carpeta)
        if not carpeta.is_dir():
            self.aviso(f"No es una carpeta: {carpeta}", warn=True)
            return
        if not os.access(carpeta, os.R_OK | os.X_OK):
            self.aviso(f"Sin permiso para entrar en {carpeta}", warn=True)
            return
        self._parar_busqueda()
        self.modo = "dir"
        self.carpeta = carpeta
        if registrar:
            del self.historial[self.hpos + 1:]
            self.historial.append(carpeta)
            self.hpos = len(self.historial) - 1
        if self.buscar.get_text():
            self.buscar.set_text("")
        self.claves.clear()
        self.fmodel.set_model(self.dlist)
        self.dlist.set_file(Gio.File.new_for_path(str(carpeta)))
        self._actualizar_cabecera()

    def navegar(self, d):
        n = self.hpos + d
        if 0 <= n < len(self.historial):
            self.hpos = n
            self.ir(self.historial[n], registrar=False)

    def subir(self):
        if self.modo != "dir":
            return self.ir(self.carpeta)
        if self.carpeta.parent != self.carpeta:
            anterior = self.carpeta
            self.ir(self.carpeta.parent)
            self._seleccionar_al_cargar = anterior.name

    def _cargado(self):
        if self.dlist.is_loading():
            return
        self._contar()
        nombre = getattr(self, "_seleccionar_al_cargar", None)
        if nombre:
            self._seleccionar_al_cargar = None
            self.seleccionar_nombres([nombre])

    def _error_dir(self):
        e = self.dlist.get_error()
        if e:
            self.aviso(e.message, warn=True)

    def _corta(self, p: Path) -> str:
        s = str(p)
        h = str(HOME)
        return "~" + s[len(h):] if s == h or s.startswith(h + "/") else s

    def _actualizar_cabecera(self):
        self.b_atras.set_sensitive(self.hpos > 0)
        self.b_adelante.set_sensitive(self.hpos < len(self.historial) - 1)
        self.b_arriba.set_sensitive(self.modo != "dir" or self.carpeta.parent != self.carpeta)
        while (h := self.migas.get_first_child()) is not None:
            self.migas.remove(h)
        if self.modo == "papelera":
            self.migas.append(lbl("PAPELERA", "nf-miga on"))
        else:
            partes = []
            p = self.carpeta
            raiz_home = self.carpeta == HOME or HOME in self.carpeta.parents
            while True:
                if raiz_home and p == HOME:
                    partes.append(("INICIO", p))
                    break
                if p.parent == p:
                    partes.append(("/", p))
                    break
                partes.append((p.name, p))
                p = p.parent
            partes.reverse()
            for i, (txt, ruta) in enumerate(partes):
                if i:
                    self.migas.append(lbl("›", "nx-code"))
                b = boton(txt, "nf-miga" + (" on" if i == len(partes) - 1 and self.modo == "dir" else ""),
                          lambda r=ruta: self.ir(r))
                dt = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY | Gdk.DragAction.MOVE)
                dt.connect("drop", lambda _t, v, _x, _y, r=ruta: self._soltar(v, r))
                b.add_controller(dt)
                self.migas.append(b)
            if self.modo == "buscar":
                self.migas.append(lbl(f"›  BÚSQUEDA «{self.buscar.get_text().strip()}»", "nf-miga on"))
        self.set_title(f"{self.carpeta.name or '/'} · NEURAL//FILES" if self.modo == "dir"
                       else "Papelera · NEURAL//FILES" if self.modo == "papelera" else "Búsqueda · NEURAL//FILES")
        self.construir_lateral()
        self._contar()
        GLib.timeout_add(80, self._migas_al_final)

    def _migas_al_final(self):
        adj = self.migas_sc.get_hadjustment()
        adj.set_value(adj.get_upper())
        return False

    def _ruta_escrita(self, *_):
        texto = os.path.expanduser(self.ruta_entry.get_text().strip())
        self.ruta_pila.set_visible_child_name("migas")
        if texto:
            p = Path(texto)
            if p.is_file():
                self.ir(p.parent)
                self._seleccionar_al_cargar = p.name
            else:
                self.ir(p)

    def editar_ruta(self):
        self.ruta_entry.set_text(str(self.carpeta) + ("/" if self.carpeta != Path("/") else ""))
        self.ruta_pila.set_visible_child_name("entry")
        self.ruta_entry.grab_focus()
        self.ruta_entry.set_position(-1)

    # ================================================================ lateral
    def construir_lateral(self):
        caja = self.lateral
        while (h := caja.get_first_child()) is not None:
            caja.remove(h)

        def titulo(t):
            caja.append(lbl(t, "nx-h2", xalign=0, margin_top=10, margin_bottom=2, margin_start=6))

        def fila(texto, icono, ruta, accion=None, extra=None, fav=False):
            actual = (self.modo == "dir" and ruta is not None and self.carpeta == ruta) or \
                     (ruta is None and texto == "Papelera" and self.modo == "papelera")
            b = Gtk.Button()
            b.add_css_class("nf-lugar")
            on(b, "on", actual)
            c = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            f = Gtk.Box(spacing=8)
            img = Gtk.Image.new_from_icon_name(icono)
            img.add_css_class("nf-ic-dir")
            f.append(img)
            f.append(lbl(texto, "nf-lugar-txt", xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END))
            c.append(f)
            if extra:
                c.append(extra)
            b.set_child(c)
            b.connect("clicked", lambda *_: accion() if accion else self.ir(ruta))
            if ruta is not None:
                dt = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY | Gdk.DragAction.MOVE)
                dt.connect("drop", lambda _t, v, _x, _y: self._soltar(v, ruta))
                b.add_controller(dt)
            if fav:
                g = Gtk.GestureClick(button=3)
                g.connect("pressed", lambda *_: self._menu_favorito(b, ruta))
                b.add_controller(g)
            caja.append(b)

        titulo("LUGARES")
        for texto, ruta, icono in LUGARES:
            if ruta == HOME or ruta.is_dir():
                fila(texto, icono, ruta)
        favs = leer_marcadores()
        if favs:
            titulo("FAVORITOS")
            for etiqueta, ruta in favs:
                fila(etiqueta, "starred-symbolic" if ruta.is_dir() else "dialog-warning-symbolic", ruta, fav=True)
        titulo("SISTEMA")
        fila("Raíz /", "drive-harddisk-system-symbolic", Path("/"))
        n = self._n_papelera()
        fila("Papelera" + (f"  · {n}" if n else ""), "user-trash-full-symbolic" if n else "user-trash-symbolic",
             None, accion=self.ver_papelera)
        titulo("DISCOS")
        for d in discos():
            uso = 1 - d["libre"] / d["total"]
            extra = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
            barra = Gtk.ProgressBar(fraction=uso)
            barra.add_css_class("nx-prog")
            on(barra, "lleno", uso > 0.9)
            extra.append(barra)
            extra.append(lbl(f"{tam_humano(d['libre'])} libres de {tam_humano(d['total'])}", "nf-lugar-sub",
                             xalign=0))
            fila(d["nombre"], "drive-harddisk-symbolic", d["ruta"], extra=extra)
            self.lateral.get_last_child().set_tooltip_text(f"{d['dev']} → {d['ruta']}")
        self._lateral_usb(caja, titulo)

    # ================================================================ USB / externos
    def _lateral_usb(self, caja, titulo):
        u = self.app.usb
        if u is None:
            return
        try:
            lista = u.dispositivos()
        except GLib.Error:
            return
        titulo("USB / EXTERNOS")
        if not lista:
            caja.append(lbl("Conecta un disco o memoria", "nf-lugar-sub", xalign=0, margin_start=10,
                            margin_top=2, margin_bottom=4))
            return
        for d in lista:
            punto = Path(d["puntos"][0]) if d["puntos"] else None
            actual = punto is not None and self.modo == "dir" and (self.carpeta == punto or punto in self.carpeta.parents)
            fila = Gtk.Box(spacing=0)
            b = Gtk.Button(hexpand=True)
            b.add_css_class("nf-lugar")
            on(b, "on", actual)
            c = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            f = Gtk.Box(spacing=8)
            icono = ("channel-secure-symbolic" if d["cifrado"] else
                     "drive-removable-media-symbolic" if d["usb"] else "drive-harddisk-symbolic")
            img = Gtk.Image.new_from_icon_name(icono)
            img.add_css_class("nf-ic-dir" if punto else "nf-ic-file")
            f.append(img)
            f.append(lbl(d["nombre"], "nf-lugar-txt", xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END))
            c.append(f)
            if punto is not None:
                try:
                    st = os.statvfs(punto)
                    total, libre = st.f_blocks * st.f_frsize, st.f_bavail * st.f_frsize
                except OSError:
                    total = libre = 0
                if total:
                    uso = 1 - libre / total
                    barra = Gtk.ProgressBar(fraction=uso)
                    barra.add_css_class("nx-prog")
                    on(barra, "lleno", uso > 0.9)
                    c.append(barra)
                ro = self._usb_ro(punto)
                c.append(lbl(f"{tam_humano(libre)} libres de {tam_humano(total)}" + ("  · SOLO LECTURA" if ro else ""),
                             "nf-lugar-sub", xalign=0))
            else:
                estado = "CIFRADO · clic para abrir" if d["cifrado"] else "SIN MONTAR · clic para abrir"
                c.append(lbl(f"{estado} · {tam_humano(d['tam'])}" + (f" · {d['tipo']}" if d["tipo"] and not d["cifrado"] else ""),
                             "nf-lugar-sub", xalign=0))
            b.set_child(c)
            b.set_tooltip_text(" · ".join(x for x in (d["modelo"], d["dev"], str(punto) if punto else "") if x))
            b.connect("clicked", lambda _b, d=d: self._usb_abrir(d))
            g = Gtk.GestureClick(button=3)
            g.connect("pressed", lambda *_a, w=b, d=d: self._menu_usb(w, d))
            b.add_controller(g)
            if punto is not None:
                dt = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY | Gdk.DragAction.MOVE)
                dt.connect("drop", lambda _t, v, _x, _y, p=punto: self._soltar(v, p))
                b.add_controller(dt)
            fila.append(b)
            if punto is not None or d["drive"] != "/":
                x = Gtk.Button(label="⏏", valign=Gtk.Align.CENTER,
                               tooltip_text="Desmontar y apagar el disco (ya se puede desconectar)")
                x.add_css_class("nf-lugar")
                x.connect("clicked", lambda _b, d=d: self._usb_expulsar(d))
                fila.append(x)
            caja.append(fila)

    @staticmethod
    def _usb_ro(punto: Path) -> bool:
        try:
            return bool(os.statvfs(punto).f_flag & os.ST_RDONLY)
        except OSError:
            return False

    def _usb_abrir(self, d, solo_lectura=False):
        if d["puntos"]:
            self.ir(Path(d["puntos"][0]))
            return
        if d["cifrado"]:
            self._usb_desbloquear(d, solo_lectura)
            return
        self.aviso(f"Montando {d['nombre']}…")

        def fin(ok, texto):
            if not ok:
                return self.aviso(f"No se pudo montar: {texto}", warn=True, ms=7000)
            self.aviso(f"Montado en {texto}" + (" (solo lectura)" if solo_lectura else ""))
            self.ir(Path(texto))
        self.app.usb.montar(d["ruta"], fin, solo_lectura)

    def _usb_desbloquear(self, d, solo_lectura=False):
        def con_clave(clave):
            self.aviso(f"Abriendo {d['nombre']}…")

            def fin(ok, texto):
                if not ok:
                    return self.aviso(f"No se pudo abrir: {texto}", warn=True, ms=7000)
                # el contenido (dm-…) aparece como otro dispositivo: montarlo
                GLib.timeout_add(600, lambda: (self._usb_montar_claro(texto, solo_lectura), False)[1])
            self.app.usb.desbloquear(d["ruta"], clave, fin)
        self.preguntar(f"Contraseña de {d['nombre']}:", "", con_clave, "ABRIR", clave=True)

    def _usb_montar_claro(self, ruta_claro, solo_lectura):
        for d in self.app.usb.dispositivos():
            if d["ruta"] == ruta_claro:
                return self._usb_abrir(d, solo_lectura)
        self.aviso("Desbloqueado, pero no tiene un sistema de archivos que montar", warn=True)

    def _usb_salir_de(self, d):
        """Si esta ventana está dentro del disco, salir a Inicio (si no, no se puede desmontar)."""
        for p in d["puntos"]:
            p = Path(p)
            if self.modo == "dir" and (self.carpeta == p or p in self.carpeta.parents):
                self.ir(HOME)
        for w in self.app.get_windows():
            if isinstance(w, Ventana) and w is not self:
                w._usb_salir_de_silencio(d)

    def _usb_salir_de_silencio(self, d):
        for p in d["puntos"]:
            p = Path(p)
            if self.modo == "dir" and (self.carpeta == p or p in self.carpeta.parents):
                self.ir(HOME)

    def _usb_desmontar(self, d):
        self._usb_salir_de(d)

        def fin(ok, texto):
            self.aviso(f"Desmontado: {d['nombre']}" if ok else f"No se pudo desmontar: {texto}", warn=not ok, ms=6000)
        GLib.timeout_add(300, lambda: (self.app.usb.desmontar(d["ruta"], fin), False)[1])

    def _usb_expulsar(self, d):
        self._usb_salir_de(d)
        self.aviso(f"Expulsando {d['nombre']}…")

        def fin(ok, texto):
            if ok:
                self.aviso(f"{d['nombre']}: ya puedes desconectarlo", ms=6000)
            else:
                self.aviso(f"No se pudo expulsar: {texto}", warn=True, ms=7000)
        if d["drive"] == "/":
            return GLib.timeout_add(300, lambda: (self.app.usb.desmontar(d["ruta"], fin), False)[1])
        GLib.timeout_add(300, lambda: (self.app.usb.expulsar(d["drive"], fin), False)[1])

    def _menu_usb(self, widget, d):
        items = []
        if d["puntos"]:
            items += [("ABRIR", lambda: self._usb_abrir(d), ""),
                      ("ABRIR EN VENTANA NUEVA", lambda: Ventana(self.app, Path(d["puntos"][0])).present(), ""),
                      ("COPIAR RUTA", lambda: self.get_clipboard().set(d["puntos"][0]), ""),
                      None,
                      ("DESMONTAR", lambda: self._usb_desmontar(d), "")]
        else:
            items += [("ABRIR", lambda: self._usb_abrir(d), ""),
                      ("ABRIR SOLO LECTURA", lambda: self._usb_abrir(d, True), "")]
        if d["drive"] != "/":
            items += [None, ("EXPULSAR Y APAGAR", lambda: self._usb_expulsar(d), "⏏")]
        self._popover(widget, None, items, pie=" · ".join(x for x in (d["modelo"], d["dev"], d["tipo"]) if x))

    def usb_cambio(self):
        # si el disco que se estaba viendo desapareció, volver a Inicio
        if self.modo == "dir" and str(self.carpeta).startswith("/run/media/") and not self.carpeta.is_dir():
            self.ir(HOME)
            self.aviso("El disco se desconectó", warn=True)
        self.construir_lateral()

    def _menu_favorito(self, widget, ruta):
        self._popover(widget, None, [("QUITAR DE FAVORITOS", lambda: self.quitar_favorito(ruta), "")])

    def fijar_favorito(self, ruta: Path):
        favs = leer_marcadores()
        if any(r == ruta for _e, r in favs):
            return self.aviso("Ya está en favoritos")
        favs.append((ruta.name or str(ruta), ruta))
        guardar_marcadores(favs)
        self.construir_lateral()
        self.aviso(f"Fijado: {ruta.name}")

    def quitar_favorito(self, ruta: Path):
        guardar_marcadores([(e, r) for e, r in leer_marcadores() if r != ruta])
        self.construir_lateral()

    # ================================================================ selección
    def seleccionados(self) -> list[Gio.FileInfo]:
        bs = self.sel.get_selection()
        return [self.smodel.get_item(bs.get_nth(i)) for i in range(bs.get_size())]

    def rutas_sel(self) -> list[Path]:
        return [Path(archivo_de(i).get_path()) for i in self.seleccionados() if archivo_de(i).get_path()]

    def seleccionar_nombres(self, nombres: list[str]):
        nombres = set(nombres)
        self.sel.unselect_all()
        primero = None
        for i in range(self.smodel.get_n_items()):
            if self.smodel.get_item(i).get_name() in nombres:
                self.sel.select_item(i, False)
                primero = i if primero is None else primero
        if primero is not None:
            vista = self.grid if self.vista == "grid" else self.lista
            try:
                vista.scroll_to(primero, Gtk.ListScrollFlags.FOCUS, None)
            except (AttributeError, TypeError):
                pass

    def _modelo_cambio(self):
        self._contar()
        if self.previa.get_reveal_child() and not self._previa_id:
            self._previa_id = GLib.timeout_add(120, self._previa_diferida)

    def _previa_diferida(self):
        self._previa_id = 0
        self.actualizar_previa()
        return False

    def _sel_cambio(self):
        self._contar()
        if self.previa.get_reveal_child():
            self.actualizar_previa()

    def _contar(self):
        n = self.smodel.get_n_items()
        sel = self.seleccionados()
        dirs = sum(1 for i in range(n) if es_dir(self.smodel.get_item(i))) if n < 20000 else 0
        texto = f"{n} ELEMENTOS · {dirs} CARPETAS · {n - dirs} ARCHIVOS"
        if sel:
            peso = sum(i.get_size() for i in sel if not es_dir(i))
            texto += f"  //  {len(sel)} SELECCIONADOS" + (f" · {tam_humano(peso)}" if peso else "")
        if self.modo == "dir":
            try:
                st = os.statvfs(self.carpeta)
                texto += f"  //  LIBRE {tam_humano(st.f_bavail * st.f_frsize)}"
            except OSError:
                pass
        self.p_izq.set_text(texto)
        cargando = self.modo == "dir" and self.dlist.is_loading()
        if n == 0 and not cargando:
            self.vacio_lbl.set_text("Papelera vacía" if self.modo == "papelera" else
                                    "Sin resultados" if self.modo == "buscar" or self.buscar.get_text() else
                                    "Carpeta vacía")
            self.vacio_lbl.set_visible(True)
        else:
            self.vacio_lbl.set_visible(False)

    # ================================================================ vista
    def aplicar_vista(self):
        self.pila_vistas.set_visible_child_name(self.vista)
        self.b_vista.set_label("☰" if self.vista == "grid" else "▦")

    def alternar_vista(self, cual: str | None = None):
        self.vista = cual or ("lista" if self.vista == "grid" else "grid")
        self.aplicar_vista()
        self._guardar()

    def alternar_previa(self):
        self.previa_on = not self.previa.get_reveal_child()
        self.previa.set_reveal_child(self.previa_on)
        on(self.b_previa, "on", self.previa_on)
        if self.previa_on:
            self.actualizar_previa()
        self._guardar()

    def alternar_ocultos(self):
        self.ocultos = not self.ocultos
        self._refiltrar()
        self.aviso("Mostrando ocultos" if self.ocultos else "Ocultos escondidos")
        self._guardar()

    def poner_orden(self, orden):
        if self.orden == orden:
            self.desc = not self.desc
        else:
            self.orden, self.desc = orden, orden in ("tam", "fecha")
        self._reordenar()
        self._guardar()

    def _guardar(self):
        _guardar_ajustes({"vista": self.vista, "ocultos": self.ocultos, "orden": self.orden, "desc": self.desc,
                          "previa": self.previa_on})

    def _menu_vista(self):
        items = [(("● " if self.orden == k else "   ") + t + ((" ↓" if self.desc else " ↑") if self.orden == k else ""),
                  lambda k=k: self.poner_orden(k), "") for k, t in ORDENES]
        items += [None, (("● " if self.ocultos else "   ") + "MOSTRAR OCULTOS", self.alternar_ocultos, "Ctrl+H"),
                  (("● " if self.vista == "lista" else "   ") + "VISTA DE LISTA", self.alternar_vista, "Ctrl+2")]
        self._popover(self.b_menu, None, items)

    # ================================================================ previa
    def actualizar_previa(self):
        caja = self.previa_caja
        while (h := caja.get_first_child()) is not None:
            caja.remove(h)
        sel = self.seleccionados()
        if not sel:
            caja.append(lbl("VISTA PREVIA", "nx-h2", xalign=0))
            caja.append(lbl("Selecciona algo para ver sus detalles.", "nx-dim", xalign=0, wrap=True))
            if self.modo == "dir":
                self._datos(caja, [("CARPETA", str(self.carpeta))])
            return
        if len(sel) > 1:
            peso = sum(i.get_size() for i in sel if not es_dir(i))
            nd = sum(1 for i in sel if es_dir(i))
            caja.append(lbl(f"{len(sel)} ELEMENTOS", "nx-h1", xalign=0))
            self._datos(caja, [("CARPETAS", str(nd)), ("ARCHIVOS", str(len(sel) - nd)),
                               ("TAMAÑO", tam_humano(peso) + (" (sin carpetas)" if nd else ""))])
            return
        info = sel[0]
        f = archivo_de(info)
        ruta = f.get_path() or ""
        ct = info.get_content_type() or ""
        if ct in IMAGENES or ct in ("image/tiff", "image/bmp"):
            pic = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, can_shrink=True, height_request=200)
            try:
                if info.get_size() < 40 * 1024 * 1024:
                    pic.set_paintable(Gdk.Texture.new_from_filename(ruta))
            except GLib.Error:
                pass
            caja.append(pic)
        else:
            img = Gtk.Image(pixel_size=96)
            img.set_from_gicon(icono_simbolico(info))
            img.add_css_class(clase_icono(info))
            caja.append(img)
        caja.append(lbl(info.get_display_name(), "nx-h1", xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR,
                        selectable=True))
        datos = []
        if es_dir(info):
            try:
                n = sum(1 for _ in os.scandir(ruta))
                datos.append(("CONTENIDO", f"{n} elementos"))
            except OSError:
                datos.append(("CONTENIDO", "sin permiso"))
        else:
            datos.append(("TAMAÑO", f"{tam_humano(info.get_size())}  ({info.get_size():,} B)".replace(",", ".")))
        datos.append(("TIPO", (Gio.content_type_get_description(ct) or ct) + (f"\n{ct}" if ct else "")))
        dt = info.get_modification_date_time()
        if dt:
            datos.append(("MODIFICADO", dt.to_local().format("%d-%m-%Y %H:%M:%S")))
        modo = info.get_attribute_uint32("unix::mode")
        if modo:
            datos.append(("PERMISOS", f"{stat.filemode(modo)}  {modo & 0o7777:04o}"))
        if info.get_attribute_string("owner::user"):
            datos.append(("DUEÑO", info.get_attribute_string("owner::user")))
        if info.get_is_symlink():
            datos.append(("ENLACE A", info.get_symlink_target() or "?"))
        if info.get_attribute_string("trash::orig-path"):
            datos.append(("ORIGEN", info.get_attribute_string("trash::orig-path")))
            datos.append(("BORRADO", info.get_attribute_string("trash::deletion-date") or ""))
        datos.append(("RUTA", ruta))
        self._datos(caja, datos)
        if es_dir(info):
            caja.append(boton("CALCULAR TAMAÑO", "nx-chip", lambda: self._du(ruta)))
            self.du_lbl = lbl("", "nx-code on", xalign=0)
            caja.append(self.du_lbl)
        elif (Gio.content_type_is_a(ct, "text/plain") or ct in ("application/json", "application/xml")) \
                and info.get_size() < 5 * 1024 * 1024:
            try:
                with open(ruta, "rb") as fh:
                    texto = fh.read(3000).decode("utf-8", errors="replace")
                lineas = texto.splitlines()[:40]
                caja.append(lbl("CONTENIDO", "nx-code", xalign=0, margin_top=6))
                caja.append(lbl("\n".join(l[:60] for l in lineas), "nf-texto", xalign=0, selectable=True))
            except OSError:
                pass
        acciones = Gtk.Box(spacing=6, margin_top=6)
        acciones.append(boton("ABRIR", "nx-accion fuerte", lambda: self.abrir_info(info)))
        if not es_dir(info):
            acciones.append(boton("ABRIR CON…", "nx-accion", lambda: self.abrir_con(info, self.b_previa)))
        caja.append(acciones)

    def _datos(self, caja, datos):
        g = Gtk.Grid(column_spacing=10, row_spacing=3, margin_top=4)
        for i, (k, v) in enumerate(datos):
            g.attach(lbl(k, "nx-code", xalign=0, valign=Gtk.Align.START), 0, i, 1, 1)
            g.attach(lbl(v, "nx-code on", xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR,
                         selectable=True, max_width_chars=30), 1, i, 1, 1)
        caja.append(g)

    def _du(self, ruta):
        self.du_lbl.set_text("calculando…")
        etiqueta = self.du_lbl

        def hilo():
            total, n = 0, 0
            for raiz, _dirs, archivos in os.walk(ruta):
                for a in archivos:
                    try:
                        total += os.lstat(os.path.join(raiz, a)).st_size
                        n += 1
                    except OSError:
                        pass
            GLib.idle_add(lambda: (etiqueta.set_text(f"{tam_humano(total)} en {n} archivos"), False)[1])

        threading.Thread(target=hilo, daemon=True).start()

    # ================================================================ abrir
    def abrir_pos(self, pos):
        info = self.smodel.get_item(pos)
        sel = self.seleccionados()
        if len(sel) > 1 and info in sel and not any(es_dir(i) for i in sel):
            for i in sel:
                self.abrir_info(i)
            return
        self.abrir_info(info)

    def abrir_info(self, info):
        f = archivo_de(info)
        ruta = f.get_path()
        if self.modo == "papelera":
            return self.aviso("Restaura el archivo para abrirlo", warn=True)
        if es_dir(info):
            return self.ir(Path(ruta))
        ct = info.get_content_type() or ""
        if es_ejecutable(info):
            nombre = Path(ruta).name
            return self.preguntar(f"¿Ejecutar «{nombre}»? Solo si confías en su origen.", None,
                                  lambda _v: self.ejecutar(Path(ruta)), "EJECUTAR")
        defecto = Gio.AppInfo.get_default_for_type(ct, False)
        if defecto and defecto.get_id() != f"{APP_ID}.desktop":
            return self._lanzar(defecto, f)
        if ct in IMAGENES or ct in ("image/tiff", "image/bmp"):
            return self.abrir_visor(Path(ruta))
        if Gio.content_type_is_a(ct, "text/plain") or ct in ("application/json", "application/xml",
                                                              "application/x-desktop", "inode/x-empty"):
            return self.editar(Path(ruta))
        if ct in ARCHIVADOS:
            return self.extraer([Path(ruta)])
        self.abrir_con(info, None)

    def ejecutar(self, ruta: Path):
        """Lanza un programa/script en su carpeta, desligado de FILES; la salida va a un log
        para poder ver por qué falla. AppImage sin libfuse2: se extrae y corre sola."""
        LOGS_EJEC.mkdir(parents=True, exist_ok=True)
        log = LOGS_EJEC / f"{ruta.name}.log"
        env = dict(os.environ, APPIMAGE_EXTRACT_AND_RUN="1")
        try:
            with open(log, "wb") as fh:
                proc = subprocess.Popen([str(ruta)], cwd=str(ruta.parent), stdin=subprocess.DEVNULL, stdout=fh,
                                        stderr=subprocess.STDOUT, start_new_session=True, env=env)
        except OSError as e:
            return self.aviso(f"No se pudo ejecutar: {e.strerror}", warn=True, ms=7000)
        self.aviso(f"Ejecutando {ruta.name}…")

        def revisar():
            r = proc.poll()
            if r is None:
                return GLib.SOURCE_REMOVE
            try:
                salida = log.read_text(errors="replace")
            except OSError:
                salida = ""
            if "cannot open display" in salida or "Authorization required" in salida:
                self.aviso(f"{ruta.name} se reinició como root (pkexec) y root no tiene acceso a la pantalla · "
                           f"ejecuta: xhost +SI:localuser:root", warn=True, ms=12000)
            elif r != 0:
                self.aviso(f"{ruta.name} terminó con error {r} · log: {log}", warn=True, ms=10000)
            elif salida.strip():
                ultima = salida.strip().splitlines()[-1][:120]
                self.aviso(f"{ruta.name} terminó: {ultima}", warn=True, ms=10000)
            return GLib.SOURCE_REMOVE
        GLib.timeout_add(5000, revisar)

    def _lanzar(self, app: Gio.AppInfo, f: Gio.File):
        try:
            ctx = self.get_display().get_app_launch_context()
            app.launch([f], ctx)
            self.aviso(f"Abriendo con {app.get_display_name()}")
        except GLib.Error as e:
            self.aviso(f"{app.get_display_name()}: {e.message}", warn=True)

    def editar(self, ruta: Path):
        editor = os.environ.get("EDITOR") or "vim"
        self._spawn(["python3", str(DECK / "term.py"), "--dir", str(ruta.parent), "-e", editor, str(ruta)])

    def terminal_aqui(self, ruta: Path | None = None):
        self._spawn(["python3", str(DECK / "term.py"), "--dir", str(ruta or self.carpeta)])

    def _spawn(self, argv):
        subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)

    def abrir_visor(self, ruta: Path):
        try:
            imgs = sorted((p for p in ruta.parent.iterdir()
                           if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".gif", ".tif", ".tiff", ".bmp")),
                          key=lambda p: clave_natural(p.name))
        except OSError:
            imgs = [ruta]
        if ruta not in imgs:
            imgs.insert(0, ruta)
        Visor(self.app, imgs, imgs.index(ruta)).present()

    def abrir_con(self, info, widget):
        ct = info.get_content_type() or ""
        f = archivo_de(info)
        apps = Gio.AppInfo.get_all_for_type(ct) if ct else []
        ids = {a.get_id() for a in apps}
        otras = [a for a in Gio.AppInfo.get_all() if a.should_show() and a.get_id() not in ids]
        items = []
        for a in apps + [None] + sorted(otras, key=lambda a: a.get_display_name().lower()):
            if a is None:
                items.append(None)
                continue
            if a.get_id() == f"{APP_ID}.desktop":
                continue
            items.append((a.get_display_name().upper(), lambda a=a: self._lanzar(a, f), "",
                          lambda a=a: self._fijar_defecto(a, ct)))
        items = [("EDITAR EN VIM (TERM)", lambda: self.editar(Path(f.get_path())), "")] + \
                ([("VER EN NEURAL//VIEW", lambda: self.abrir_visor(Path(f.get_path())), "")]
                 if ct.startswith("image/") else []) + [None] + items
        self._popover(widget or (self.grid if self.vista == "grid" else self.lista), None, items,
                      pie=f"clic derecho en una app = predeterminada para {ct}")

    def _fijar_defecto(self, app, ct):
        try:
            app.set_as_default_for_type(ct)
            self.aviso(f"{app.get_display_name()} abrirá {Gio.content_type_get_description(ct)}")
        except GLib.Error as e:
            self.aviso(e.message, warn=True)

    # ================================================================ menús
    def _popover(self, widget, punto, items, pie: str | None = None):
        """items: (texto, accion, atajo[, accion_derecha]) o None (separador)."""
        pop = Gtk.Popover(has_arrow=False, autohide=True)
        pop.add_css_class("nx-menu")
        pop.set_parent(widget)
        if punto is not None:
            r = Gdk.Rectangle()
            r.x, r.y, r.width, r.height = int(punto[0]), int(punto[1]), 1, 1
            pop.set_pointing_to(r)
            pop.set_position(Gtk.PositionType.BOTTOM)
        sc = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_height=True,
                                max_content_height=560)
        sc.add_css_class("nx-scroll")
        caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        sc.set_child(caja)
        for it in items:
            if it is None:
                if caja.get_last_child() is not None and not caja.get_last_child().has_css_class("nx-sep"):
                    s = Gtk.Box(margin_top=3, margin_bottom=3)
                    s.add_css_class("nx-sep")
                    caja.append(s)
                continue
            texto, accion, atajo = it[:3]
            b = Gtk.Button()
            b.add_css_class("nx-item")
            if texto.startswith(("ELIMINAR", "VACIAR", "MOVER A LA PAPELERA")):
                b.add_css_class("peligro")
            f = Gtk.Box(spacing=18)
            f.append(lbl(texto, "", xalign=0, hexpand=True))
            if atajo:
                f.append(lbl(atajo, "nx-item-k"))
            b.set_child(f)
            b.connect("clicked", lambda _b, a=accion: (pop.popdown(), GLib.idle_add(lambda: (a(), False)[1])))
            if len(it) > 3:
                g = Gtk.GestureClick(button=3)
                g.connect("pressed", lambda *_a, a=it[3]: (pop.popdown(), a()))
                b.add_controller(g)
            caja.append(b)
        if pie:
            caja.append(lbl(pie, "nx-item-k", xalign=0, margin_top=4, margin_start=8))
        pop.set_child(sc)
        pop.connect("closed", lambda p: GLib.idle_add(lambda: (p.unparent(), False)[1]))
        pop.popup()

    def _celda_en(self, vista, x, y) -> Celda | None:
        w = vista.pick(x, y, Gtk.PickFlags.DEFAULT)
        while w is not None and w is not vista:
            if isinstance(w, Celda):
                return w
            # en la lista, la celda de nombre está en la misma fila que otra columna
            w = w.get_parent()
        return None

    def _pos_en(self, vista, x, y) -> int:
        c = self._celda_en(vista, x, y)
        if c is not None:
            return c.pos
        if vista is self.lista:  # clic en otra columna de la fila: buscar la celda de nombre de esa fila
            w = vista.pick(x, y, Gtk.PickFlags.DEFAULT)
            while w is not None and w is not vista:
                hijo = w.get_first_child()
                while hijo is not None:
                    celda = self._buscar_celda(hijo)
                    if celda is not None:
                        return celda.pos
                    hijo = hijo.get_next_sibling()
                if w.get_css_name() == "row":
                    break
                w = w.get_parent()
        return -1

    def _buscar_celda(self, w):
        if isinstance(w, Celda):
            return w
        h = w.get_first_child()
        while h is not None:
            c = self._buscar_celda(h)
            if c is not None:
                return c
            h = h.get_next_sibling()
        return None

    def _clic_vacio(self, gesto, n, x, y, vista):
        if n == 1 and self._pos_en(vista, x, y) < 0:
            self.sel.unselect_all()

    def _clic_medio(self, gesto, _n, x, y, vista):
        pos = self._pos_en(vista, x, y)
        if pos >= 0:
            info = self.smodel.get_item(pos)
            if es_dir(info):
                self.app.crear_ventana([archivo_de(info).get_path()], None)

    def _clic_derecho(self, gesto, _n, x, y, vista):
        gesto.set_state(Gtk.EventSequenceState.CLAIMED)
        pos = self._pos_en(vista, x, y)
        if pos >= 0 and not self.sel.is_selected(pos):
            self.sel.select_item(pos, True)
        elif pos < 0:
            self.sel.unselect_all()
        self.menu_contextual(vista, (x, y))

    def menu_contextual(self, widget, punto=None):
        sel = self.seleccionados()
        hay_pegar = bool(self.app.portapapeles[1])
        if self.modo == "papelera":
            if sel:
                items = [("RESTAURAR", self.restaurar, ""), None,
                         ("ELIMINAR DEFINITIVAMENTE", self.eliminar, "Shift+Supr")]
            else:
                items = [("VACIAR PAPELERA", self.vaciar_papelera, "")]
            return self._popover(widget, punto, items)
        if sel:
            uno = sel[0] if len(sel) == 1 else None
            items = [("ABRIR", lambda: self.abrir_pos(self.sel.get_selection().get_nth(0)), "Enter")]
            if uno and es_ejecutable(uno):
                rx = Path(archivo_de(uno).get_path())
                items = [("EJECUTAR", lambda: self.ejecutar(rx), "Enter"),
                         ("EJECUTAR EN TERMINAL", lambda: self._spawn(["python3", str(DECK / "term.py"), "--dir",
                                                                       str(rx.parent), "-e", "sh", "-c", 'cd "$(dirname "$0")"; "$0"; echo; echo "[terminado: $?] Enter para cerrar"; read _',
                                                                       str(rx)]), "")]
                if (uno.get_content_type() or "") not in ("application/x-executable", "application/x-pie-executable",
                                                          "application/x-sharedlib"):
                    items.append(("EDITAR", lambda: self.editar(rx), ""))
            if uno and not es_dir(uno):
                items.append(("ABRIR CON…", lambda: self.abrir_con(uno, widget), ""))
            if uno and es_dir(uno):
                ruta = Path(archivo_de(uno).get_path())
                items += [("ABRIR EN VENTANA NUEVA", lambda: self.app.crear_ventana([str(ruta)], None), "clic medio"),
                          ("ABRIR TERMINAL AQUÍ", lambda: self.terminal_aqui(ruta), ""),
                          ("FIJAR EN FAVORITOS", lambda: self.fijar_favorito(ruta), "")]
            if self.modo == "buscar" and uno:
                items.append(("MOSTRAR EN SU CARPETA", lambda: self._mostrar_en_carpeta(uno), ""))
            items += [None, ("CORTAR", self.cortar, "Ctrl+X"), ("COPIAR", self.copiar, "Ctrl+C")]
            if hay_pegar and uno and es_dir(uno):
                items.append(("PEGAR DENTRO", lambda: self.pegar(Path(archivo_de(uno).get_path())), ""))
            if uno:
                items.append(("RENOMBRAR", self.renombrar, "F2"))
            items.append(("DUPLICAR", self.duplicar, ""))
            items.append(("COPIAR RUTA", self.copiar_ruta, "Ctrl+Shift+C"))
            items.append(None)
            if uno and (uno.get_content_type() or "") in ARCHIVADOS:
                items.append(("EXTRAER AQUÍ", lambda: self.extraer(self.rutas_sel()), ""))
            items += [("COMPRIMIR .tar.gz", lambda: self.comprimir("tar.gz"), ""),
                      ("COMPRIMIR .zip", lambda: self.comprimir("zip"), ""), None,
                      ("MOVER A LA PAPELERA", self.a_papelera, "Supr"),
                      ("ELIMINAR DEFINITIVAMENTE", self.eliminar, "Shift+Supr")]
        else:
            items = [("NUEVA CARPETA", self.nueva_carpeta, "Ctrl+Shift+N"),
                     ("NUEVO ARCHIVO", self.nuevo_archivo, "")]
            if hay_pegar:
                n = len(self.app.portapapeles[1])
                items.append((f"PEGAR {n} ELEMENTO{'S' if n > 1 else ''}", lambda: self.pegar(), "Ctrl+V"))
            items += [None, ("ABRIR TERMINAL AQUÍ", self.terminal_aqui, ""),
                      ("FIJAR CARPETA EN FAVORITOS", lambda: self.fijar_favorito(self.carpeta), ""),
                      ("SELECCIONAR TODO", self.sel.select_all, "Ctrl+A"),
                      (("● " if self.ocultos else "") + "MOSTRAR OCULTOS", self.alternar_ocultos, "Ctrl+H"),
                      ("RECARGAR", self.recargar, "F5")]
        self._popover(widget, punto, items)

    def _mostrar_en_carpeta(self, info):
        f = archivo_de(info)
        self.ir(Path(f.get_parent().get_path()))
        self._seleccionar_al_cargar = f.get_basename()

    # ================================================================ barra de entrada
    def preguntar(self, texto: str, valor: str | None, fn, boton_txt="ACEPTAR", peligro=False, seleccion=None,
                  clave=False):
        self._entrada_clave = clave
        self.entrada.set_visibility(not clave)
        self.entrada_txt.set_text(texto)
        self.entrada.set_visible(valor is not None)
        self.entrada_b.set_label(boton_txt)
        on(self.entrada_b, "peligro", peligro)
        on(self.entrada_b, "fuerte", not peligro)
        self._entrada_fn = fn
        self.entrada_rev.set_reveal_child(True)
        if valor is not None:
            self.entrada.set_text(valor)
            self.entrada.grab_focus()
            if seleccion is not None:
                self.entrada.select_region(0, seleccion)
        else:
            self.entrada_b.grab_focus()

    def _entrada_ok(self):
        fn = self._entrada_fn
        if fn is None:
            return
        if getattr(self, "_entrada_clave", False):
            clave = self.entrada.get_text()
            if not clave:
                return
            self.entrada.set_text("")
            self._entrada_cerrar()
            return fn(clave)
        valor = self.entrada.get_text().strip() if self.entrada.get_visible() else None
        if self.entrada.get_visible() and not valor:
            return self.aviso("El nombre no puede estar vacío", warn=True)
        if valor is not None and ("/" in valor or valor in (".", "..")):
            return self.aviso("Nombre no válido", warn=True)
        self._entrada_cerrar()
        fn(valor)

    def _entrada_cerrar(self):
        self._entrada_fn = None
        if getattr(self, "_entrada_clave", False):
            self._entrada_clave = False
            self.entrada.set_text("")
            self.entrada.set_visibility(True)
        self.entrada_rev.set_reveal_child(False)
        (self.grid if self.vista == "grid" else self.lista).grab_focus()

    # ================================================================ operaciones simples
    def nueva_carpeta(self):
        def crear(nombre):
            destino = self.carpeta / nombre
            try:
                destino.mkdir()
                self._seleccionar_al_cargar = nombre
                GLib.timeout_add(250, lambda: (self.seleccionar_nombres([nombre]), False)[1])
            except OSError as e:
                self.aviso(e.strerror or str(e), warn=True)
        self.preguntar("NUEVA CARPETA", nombre_libre(self.carpeta, "Nueva carpeta", "").name.replace(" ()", ""),
                       crear, "CREAR", seleccion=-1)

    def nuevo_archivo(self):
        def crear(nombre):
            destino = self.carpeta / nombre
            if destino.exists():
                return self.aviso("Ya existe", warn=True)
            try:
                destino.touch()
                GLib.timeout_add(250, lambda: (self.seleccionar_nombres([nombre]), False)[1])
            except OSError as e:
                self.aviso(e.strerror or str(e), warn=True)
        self.preguntar("NUEVO ARCHIVO", "nuevo.txt", crear, "CREAR", seleccion=5)

    def renombrar(self):
        sel = self.rutas_sel()
        if len(sel) != 1:
            return
        ruta = sel[0]

        def hecho(nuevo):
            if nuevo == ruta.name:
                return
            destino = ruta.parent / nuevo
            if destino.exists():
                return self.aviso(f"Ya existe «{nuevo}»", warn=True)
            try:
                ruta.rename(destino)
                self._actualizar_favorito(ruta, destino)
                GLib.timeout_add(250, lambda: (self.seleccionar_nombres([nuevo]), False)[1])
            except OSError as e:
                self.aviso(e.strerror or str(e), warn=True)
        stem = len(ruta.name) if ruta.is_dir() or not ruta.suffix or ruta.name.startswith(".") and \
            ruta.name.count(".") == 1 else len(ruta.name) - len(ruta.suffix)
        self.preguntar("RENOMBRAR", ruta.name, hecho, "RENOMBRAR", seleccion=stem)

    def _actualizar_favorito(self, viejo: Path, nuevo: Path):
        favs = leer_marcadores()
        if any(r == viejo for _e, r in favs):
            guardar_marcadores([(nuevo.name if e == viejo.name else e, nuevo if r == viejo else r) for e, r in favs])
            self.construir_lateral()

    def copiar_ruta(self):
        rutas = [str(p) for p in self.rutas_sel()] or [str(self.carpeta)]
        self.get_clipboard().set("\n".join(rutas))
        self.aviso("Ruta copiada" if len(rutas) == 1 else f"{len(rutas)} rutas copiadas")

    def copiar(self):
        self._portapapeles("copiar")

    def cortar(self):
        self._portapapeles("cortar")

    def _portapapeles(self, modo):
        rutas = [str(p) for p in self.rutas_sel()]
        if not rutas:
            return
        self.app.portapapeles = (modo, rutas)
        self.get_clipboard().set("\n".join(rutas))  # para pegar rutas en la terminal
        self.aviso(f"{len(rutas)} elemento{'s' if len(rutas) > 1 else ''} "
                   f"{'cortado' if modo == 'cortar' else 'copiado'}{'s' if len(rutas) > 1 else ''}")
        self.app.refrescar_vistas()

    def pegar(self, destino: Path | None = None):
        modo, rutas = self.app.portapapeles
        if not rutas or self.modo == "papelera":
            return
        destino = destino or self.carpeta
        self.trabajo("mover" if modo == "cortar" else "copiar", [Path(r) for r in rutas], destino)
        if modo == "cortar":
            self.app.portapapeles = ("", [])

    def duplicar(self):
        rutas = self.rutas_sel()
        if rutas:
            self.trabajo("copiar", rutas, rutas[0].parent)

    def _soltar(self, valor, destino: Path):
        try:
            rutas = [Path(f.get_path()) for f in valor.get_files() if f.get_path()]
        except AttributeError:
            return False
        rutas = [r for r in rutas if r.parent != destino and r != destino]
        if not rutas or self.modo == "papelera":
            return False
        try:
            mismo = os.stat(rutas[0]).st_dev == os.stat(destino).st_dev
        except OSError:
            mismo = False
        self.trabajo("mover" if mismo else "copiar", rutas, destino)
        return True

    def preparar_arrastre(self, celda: Celda):
        fuente = Gtk.DragSource(actions=Gdk.DragAction.COPY | Gdk.DragAction.MOVE)

        def preparar(_s, _x, _y):
            if celda.pos < 0 or self.modo == "papelera":
                return None
            if not self.sel.is_selected(celda.pos):
                self.sel.select_item(celda.pos, True)
            archivos = [archivo_de(i) for i in self.seleccionados()]
            if not archivos:
                return None
            return Gdk.ContentProvider.new_for_value(Gdk.FileList.new_from_list(archivos))

        fuente.connect("prepare", preparar)
        fuente.connect("drag-begin", lambda s, _d: s.set_icon(
            Gtk.WidgetPaintable.new(celda.pic if celda.pic.get_visible() else celda.img), 16, 16))
        celda.add_controller(fuente)

    def recargar(self):
        if self.modo == "papelera":
            return self.ver_papelera()
        if self.modo == "buscar":
            return self.buscar_recursivo()
        self.claves.clear()
        self.dlist.set_file(None)
        self.dlist.set_file(Gio.File.new_for_path(str(self.carpeta)))
        self.construir_lateral()

    # ================================================================ trabajos en hilo
    def trabajo(self, tipo: str, rutas: list[Path], destino: Path | None = None, extra=None):
        if self.op_activa:
            return self.aviso("Espera a que termine la operación en curso", warn=True)
        self.op_activa = True
        self.cancelar_op.clear()
        self.prog.set_fraction(0)
        self.op_txt.set_text(f"{tipo.upper()}…")
        self.ops.set_reveal_child(True)
        threading.Thread(target=self._hilo_trabajo, args=(tipo, rutas, destino, extra), daemon=True).start()

    def _progreso(self, frac, texto):
        GLib.idle_add(lambda: (self.prog.set_fraction(max(0, min(1, frac))), self.op_txt.set_text(texto), False)[2])

    def _hilo_trabajo(self, tipo, rutas, destino, extra):
        nuevos, error = [], None
        try:
            if tipo in ("copiar", "mover"):
                nuevos = self._copiar_mover(tipo, rutas, destino)
            elif tipo == "papelera":
                sin_papelera = []
                for i, r in enumerate(rutas):
                    if self.cancelar_op.is_set():
                        break
                    self._progreso(i / len(rutas), f"PAPELERA  ·  {r.name}")
                    try:
                        Gio.File.new_for_path(str(r)).trash(None)
                    except GLib.Error as e:
                        if e.matches(Gio.io_error_quark(), Gio.IOErrorEnum.NOT_SUPPORTED):
                            sin_papelera.append(r)
                        else:
                            raise
                if sin_papelera:
                    GLib.idle_add(self._sin_papelera, sin_papelera)
            elif tipo == "eliminar":
                for i, r in enumerate(rutas):
                    if self.cancelar_op.is_set():
                        break
                    self._progreso(i / len(rutas), f"ELIMINAR  ·  {r.name}")
                    if r.is_dir() and not r.is_symlink():
                        shutil.rmtree(r)
                    else:
                        r.unlink()
                    info = TRASH / "info" / (r.name + ".trashinfo")
                    if r.parent == TRASH / "files" and info.exists():
                        info.unlink()
            elif tipo == "restaurar":
                for i, r in enumerate(rutas):
                    self._progreso(i / len(rutas), f"RESTAURAR  ·  {r.name}")
                    info = TRASH / "info" / (r.name + ".trashinfo")
                    origen = Path(extra[str(r)])
                    origen.parent.mkdir(parents=True, exist_ok=True)
                    final = nombre_libre(origen.parent, origen.name, "restaurado")
                    shutil.move(str(r), str(final))
                    info.unlink(missing_ok=True)
            elif tipo in ("comprimir", "extraer"):
                nuevos = self._archivar(tipo, rutas, destino, extra)
        except Exception as e:  # noqa: BLE001 — se muestra al usuario
            error = getattr(e, "strerror", None) or getattr(e, "message", None) or str(e)
        GLib.idle_add(self._fin_trabajo, tipo, nuevos, error)

    def _copiar_mover(self, tipo, rutas, destino: Path) -> list[str]:
        # tamaño total para la barra
        total = 0
        for r in rutas:
            if r.is_dir() and not r.is_symlink():
                for raiz, _d, archivos in os.walk(r):
                    for a in archivos:
                        try:
                            total += os.lstat(os.path.join(raiz, a)).st_size
                        except OSError:
                            pass
            else:
                try:
                    total += r.lstat().st_size
                except OSError:
                    pass
        hecho = [0]
        nuevos = []
        verbo = "MOVER" if tipo == "mover" else "COPIAR"

        def copiar_archivo(src, dst):
            if os.path.islink(src):
                os.symlink(os.readlink(src), dst)
                return
            # Sin fsync la copia "termina" cuando los datos aún están en RAM (en una USB lenta,
            # cientos de MB por escribir): se sincroniza cada 32 MB y al final, así la barra
            # muestra lo que de verdad llegó al disco y «terminado» significa escrito.
            pendiente = 0
            with open(src, "rb") as fi, open(dst, "wb") as fo:
                while True:
                    if self.cancelar_op.is_set():
                        raise InterruptedError("cancelado")
                    trozo = fi.read(1024 * 1024)
                    if not trozo:
                        break
                    fo.write(trozo)
                    pendiente += len(trozo)
                    if pendiente >= 32 * 1024 * 1024:
                        fo.flush()
                        os.fdatasync(fo.fileno())
                        pendiente = 0
                    hecho[0] += len(trozo)
                    self._progreso(hecho[0] / total if total else 0,
                                   f"{verbo}  ·  {os.path.basename(src)}  ·  {tam_humano(hecho[0])} / "
                                   f"{tam_humano(total)}")
                fo.flush()
                self._progreso(hecho[0] / total if total else 0,
                               f"{verbo}  ·  {os.path.basename(src)}  ·  escribiendo al disco…")
                os.fsync(fo.fileno())
            shutil.copystat(src, dst, follow_symlinks=False)

        for r in rutas:
            if self.cancelar_op.is_set():
                break
            if destino == r or destino.is_relative_to(r):
                raise OSError(0, f"No se puede {verbo.lower()} «{r.name}» dentro de sí misma")
            final = nombre_libre(destino, r.name)
            if tipo == "mover":
                try:
                    os.rename(r, final)  # mismo disco: instantáneo
                    nuevos.append(final.name)
                    self._actualizar_favorito_hilo(r, final)
                    continue
                except OSError as e:
                    if e.errno != 18:  # EXDEV → copiar y borrar
                        raise
            if r.is_dir() and not r.is_symlink():
                shutil.copytree(r, final, symlinks=True, copy_function=copiar_archivo)
            else:
                copiar_archivo(str(r), str(final))
            nuevos.append(final.name)
            if tipo == "mover":
                shutil.rmtree(r) if r.is_dir() and not r.is_symlink() else r.unlink()
        return nuevos

    def _actualizar_favorito_hilo(self, viejo, nuevo):
        GLib.idle_add(lambda: (self._actualizar_favorito(viejo, nuevo), False)[1])

    def _archivar(self, tipo, rutas, destino, formato) -> list[str]:
        if tipo == "comprimir":
            base = rutas[0].name if len(rutas) == 1 else destino.name or "archivo"
            final = nombre_libre(destino, f"{base}.{formato}")
            argv = ["bsdtar", "-a" if formato == "zip" else "-z", "-cf", str(final), "-C", str(destino)] + \
                   [r.name for r in rutas]
            self._progreso(0, f"COMPRIMIR  ·  {final.name}")
            self._correr(argv)
            return [final.name]
        nuevos = []
        for r in rutas:
            nombre = r.name
            for ext in (".tar.gz", ".tar.xz", ".tar.zst", ".tar.bz2", ".tgz", ".zip", ".tar", ".7z", ".xz", ".gz",
                        ".rar", ".iso", ".deb", ".rpm", ".cpio"):
                if nombre.lower().endswith(ext):
                    nombre = nombre[: -len(ext)]
                    break
            # se extrae en una carpeta oculta y se renombra una sola vez al final: si una carpeta
            # visible aparece y se renombra enseguida, la lista de GTK se queda con una fila fantasma
            tmp = nombre_libre(r.parent, f".neural-extraer-{os.getpid()}", "extraído")
            tmp.mkdir()
            self._progreso(0, f"EXTRAER  ·  {r.name}")
            try:
                self._correr(["bsdtar", "-xf", str(r), "-C", str(tmp)])
                # si dentro hay una sola carpeta, subirla un nivel
                hijos = list(tmp.iterdir())
                if len(hijos) == 1 and hijos[0].is_dir():
                    final = nombre_libre(r.parent, hijos[0].name, "extraído")
                    os.rename(hijos[0], final)
                    tmp.rmdir()
                else:
                    final = nombre_libre(r.parent, nombre, "extraído")
                    os.rename(tmp, final)
            except Exception:
                shutil.rmtree(tmp, ignore_errors=True)
                raise
            nuevos.append(final.name)
        return nuevos

    def _correr(self, argv):
        p = subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        while p.poll() is None:
            if self.cancelar_op.is_set():
                p.terminate()
                raise InterruptedError("cancelado")
            self._pulso()
            time.sleep(0.15)
        if p.returncode != 0:
            raise OSError(0, (p.stderr.read().decode(errors="replace").strip().splitlines() or ["falló"])[-1])

    def _pulso(self):
        GLib.idle_add(lambda: (self.prog.pulse(), False)[1])

    def _cancelar_op(self):
        self.cancelar_op.set()

    def _fin_trabajo(self, tipo, nuevos, error):
        self.op_activa = False
        nombres = {"copiar": "Copiado", "mover": "Movido", "papelera": "A la papelera", "eliminar": "Eliminado",
                   "restaurar": "Restaurado", "comprimir": "Comprimido", "extraer": "Extraído"}
        if error:
            self.op_txt.set_text(f"{tipo.upper()}  ·  ERROR: {error}")
            self.aviso(f"{tipo}: {error}", warn=True, ms=8000)
            GLib.timeout_add(5000, lambda: (not self.op_activa and self.ops.set_reveal_child(False), False)[1])
        else:
            self.prog.set_fraction(1)
            self.op_txt.set_text(f"{nombres.get(tipo, tipo)}  ·  HECHO")
            self.aviso(nombres.get(tipo, tipo))
            GLib.timeout_add(1500, lambda: (not self.op_activa and self.ops.set_reveal_child(False), False)[1])
        self.app.refrescar_vistas()
        if self.modo == "papelera" or tipo in ("papelera", "restaurar"):
            GLib.timeout_add(200, lambda: (self.modo == "papelera" and self.ver_papelera(), self.construir_lateral(),
                                           False)[2])
        if nuevos and self.modo == "dir":
            GLib.timeout_add(350, lambda: (self.seleccionar_nombres(nuevos), False)[1])
        return False

    # ================================================================ borrar / papelera
    def a_papelera(self):
        rutas = self.rutas_sel()
        if not rutas:
            return
        if self.modo == "papelera":
            return self.eliminar()
        self.trabajo("papelera", rutas)

    def eliminar(self):
        rutas = self.rutas_sel()
        if not rutas:
            return
        que = f"«{rutas[0].name}»" if len(rutas) == 1 else f"{len(rutas)} elementos"
        self.preguntar(f"¿ELIMINAR {que} PARA SIEMPRE? No se puede deshacer.", None,
                       lambda _v: self.trabajo("eliminar", rutas), "ELIMINAR", peligro=True)

    def _sin_papelera(self, rutas):
        que = f"«{rutas[0].name}»" if len(rutas) == 1 else f"{len(rutas)} elementos"
        self.preguntar(f"Este disco no admite papelera. ¿ELIMINAR {que} PARA SIEMPRE?", None,
                       lambda _v: self.trabajo("eliminar", rutas), "ELIMINAR", peligro=True)
        return False

    def _n_papelera(self) -> int:
        try:
            return sum(1 for _ in (TRASH / "files").iterdir())
        except OSError:
            return 0

    def ver_papelera(self):
        self._parar_busqueda()
        self.modo = "papelera"
        self.store.remove_all()
        self.origenes = {}
        carpeta = TRASH / "files"
        if carpeta.is_dir():
            for p in carpeta.iterdir():
                info = info_de(Gio.File.new_for_path(str(p)))
                if info is None:
                    continue
                datos = {}
                try:
                    for linea in (TRASH / "info" / (p.name + ".trashinfo")).read_text().splitlines():
                        k, _, v = linea.partition("=")
                        datos[k] = v
                except OSError:
                    pass
                origen = unquote(datos.get("Path", ""))
                info.set_attribute_string("trash::orig-path", origen)
                info.set_attribute_string("trash::deletion-date", datos.get("DeletionDate", "").replace("T", " "))
                info.set_display_name(Path(origen).name if origen else p.name)
                self.origenes[str(p)] = origen or str(HOME / p.name)
                self.store.append(info)
        self.fmodel.set_model(self.store)
        self._actualizar_cabecera()
        if self.buscar.get_text():
            self.buscar.set_text("")

    def restaurar(self):
        rutas = self.rutas_sel()
        if rutas:
            self.trabajo("restaurar", rutas, extra={str(r): self.origenes.get(str(r)) for r in rutas})

    def vaciar_papelera(self):
        n = self._n_papelera()
        if not n:
            return

        def vaciar(_v):
            rutas = list((TRASH / "files").iterdir())
            self.trabajo("eliminar", rutas)
        self.preguntar(f"¿VACIAR LA PAPELERA ({n} elementos)? No se puede deshacer.", None, vaciar, "VACIAR",
                       peligro=True)

    # ================================================================ comprimir / extraer
    def comprimir(self, formato):
        rutas = self.rutas_sel()
        if rutas:
            self.trabajo("comprimir", rutas, rutas[0].parent, formato)

    def extraer(self, rutas):
        if rutas:
            self.trabajo("extraer", rutas, rutas[0].parent)

    # ================================================================ búsqueda
    def _filtrar_cambio(self):
        if self.modo == "buscar" and not self.buscar.get_text().strip():
            return self.ir(self.carpeta, registrar=False)
        if self.modo == "dir":
            self._refiltrar()
            self._contar()

    def _limpiar_busqueda(self):
        if self.buscar.get_text():
            self.buscar.set_text("")
        elif self.modo != "dir":
            self.ir(self.carpeta, registrar=False)
        (self.grid if self.vista == "grid" else self.lista).grab_focus()

    def _parar_busqueda(self):
        if self.busqueda_hilo is not None:
            self.busqueda_hilo.set()
            self.busqueda_hilo = None

    def buscar_recursivo(self):
        q = self.buscar.get_text().strip().casefold()
        if not q or self.modo == "papelera":
            return
        self._parar_busqueda()
        parar = self.busqueda_hilo = threading.Event()
        self.modo = "buscar"
        self.store.remove_all()
        self.fmodel.set_model(self.store)
        self._actualizar_cabecera()
        self.aviso("Buscando…", ms=60000)
        raiz, ocultos = str(self.carpeta), self.ocultos

        def hilo():
            lote, n, t0 = [], 0, time.time()
            for base, dirs, archivos in os.walk(raiz):
                if parar.is_set():
                    return
                if not ocultos:
                    dirs[:] = [d for d in dirs if not d.startswith(".")]
                for nombre in dirs + archivos:
                    if q in nombre.casefold() and (ocultos or not nombre.startswith(".")):
                        lote.append(os.path.join(base, nombre))
                        n += 1
                if len(lote) >= 50 or (lote and time.time() - t0 > 0.3):
                    GLib.idle_add(self._añadir_resultados, lote, parar)
                    lote, t0 = [], time.time()
                if n >= 2000:
                    break
            GLib.idle_add(self._añadir_resultados, lote, parar, True, n)

        threading.Thread(target=hilo, daemon=True).start()

    def _añadir_resultados(self, rutas, parar, fin=False, n=0):
        if parar.is_set() or self.modo != "buscar":
            return False
        for r in rutas:
            info = info_de(Gio.File.new_for_path(r))
            if info is not None:
                self.store.append(info)
        if fin:
            self.aviso(f"{n} resultado{'s' if n != 1 else ''}" + (" (límite 2000)" if n >= 2000 else ""))
            self.busqueda_hilo = None
        return False

    # ================================================================ teclado
    def _tecla(self, _c, kv, _code, estado):
        ctrl = bool(estado & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(estado & Gdk.ModifierType.SHIFT_MASK)
        alt = bool(estado & Gdk.ModifierType.ALT_MASK)
        foco = self.get_focus()
        escribiendo = isinstance(foco, Gtk.Text) or isinstance(foco, (Gtk.Entry, Gtk.SearchEntry))
        if kv == Gdk.KEY_Escape:
            if self.entrada_rev.get_reveal_child():
                self._entrada_cerrar()
                return True
            if self.ruta_pila.get_visible_child_name() == "entry":
                self.ruta_pila.set_visible_child_name("migas")
                return True
            if self.buscar.get_text() or self.modo == "buscar":
                self._limpiar_busqueda()
                return True
            return False
        if escribiendo and not (ctrl and kv in (Gdk.KEY_l, Gdk.KEY_L, Gdk.KEY_f, Gdk.KEY_w, Gdk.KEY_n)):
            return False
        atajos = {
            (False, True, Gdk.KEY_Left): lambda: self.navegar(-1),
            (False, True, Gdk.KEY_Right): lambda: self.navegar(1),
            (False, True, Gdk.KEY_Up): self.subir,
            (False, False, Gdk.KEY_BackSpace): lambda: self.navegar(-1),
            (True, False, Gdk.KEY_l): self.editar_ruta,
            (True, False, Gdk.KEY_f): lambda: self.buscar.grab_focus(),
            (True, False, Gdk.KEY_h): self.alternar_ocultos,
            (True, False, Gdk.KEY_1): lambda: self.alternar_vista("grid"),
            (True, False, Gdk.KEY_2): lambda: self.alternar_vista("lista"),
            (False, False, Gdk.KEY_F9): self.alternar_previa,
            (False, False, Gdk.KEY_F2): self.renombrar,
            (False, False, Gdk.KEY_F5): self.recargar,
            (False, False, Gdk.KEY_Delete): self.a_papelera,
            (True, False, Gdk.KEY_c): self.copiar,
            (True, False, Gdk.KEY_x): self.cortar,
            (True, False, Gdk.KEY_v): lambda: self.pegar(),
            (True, False, Gdk.KEY_a): self.sel.select_all,
            (True, False, Gdk.KEY_n): lambda: self.app.crear_ventana([str(self.carpeta)], None),
            (True, False, Gdk.KEY_w): self.close,
            (False, False, Gdk.KEY_Menu): lambda: self.menu_contextual(self.grid if self.vista == "grid"
                                                                       else self.lista),
        }
        if shift and kv == Gdk.KEY_Delete:
            self.eliminar()
            return True
        if ctrl and shift and kv in (Gdk.KEY_N, Gdk.KEY_n):
            self.nueva_carpeta()
            return True
        if ctrl and shift and kv in (Gdk.KEY_C, Gdk.KEY_c):
            self.copiar_ruta()
            return True
        if ctrl and shift and kv in (Gdk.KEY_H, Gdk.KEY_h):
            self.ir(HOME)
            return True
        k = Gdk.keyval_to_lower(kv)
        fn = atajos.get((ctrl, alt, k))
        if fn is not None:
            fn()
            return True
        # escribir para filtrar
        if not ctrl and not alt:
            uc = Gdk.keyval_to_unicode(kv)
            if uc and chr(uc).isprintable() and not chr(uc).isspace():
                self.buscar.grab_focus()
                self.buscar.set_text(self.buscar.get_text() + chr(uc))
                self.buscar.set_position(-1)
                return True
        return False

    def _al_cerrar(self, *_):
        if self.op_activa:
            self.aviso("Hay una operación en marcha: espera o CANCELA", warn=True)
            return True
        self._parar_busqueda()
        return False

    def refrescar_celdas(self):
        """Tras cortar/pegar: atenúa las celdas cortadas sin recargar."""
        for c in self.celdas:
            if c.uri:
                on(c, "cortado", self._cortado(Gio.File.new_for_uri(c.uri).get_path()))


# ---------------------------------------------------------------- app
class App(AppNeural):
    def __init__(self):
        super().__init__(APP_ID)
        self.portapapeles: tuple[str, list[str]] = ("", [])
        self.minis = Miniaturas()
        self.usb = None
        if usb is not None:
            try:
                self.usb = usb.Udisks(self._usb_cambio)
            except GLib.Error as e:
                print("neural-files: sin UDisks2:", e.message, file=sys.stderr)

    def _usb_cambio(self):
        for w in self.get_windows():
            if isinstance(w, Ventana):
                w.usb_cambio()

    def css_extra(self, t):
        acc, alt, warn, text, muted = t["accent"], t["alt"], t["warn"], t["text"], t["muted"]
        return f"""
.nf-ic-dir {{ color: {css_color(acc)}; }}
.nf-ic-img {{ color: {css_color(alt)}; }}
.nf-ic-arc {{ color: {css_color(warn)}; }}
.nf-ic-exe {{ color: {css_color(alt)}; }}
.nf-ic-file {{ color: {css_color(muted)}; }}
.nf-mini {{ margin-bottom: 4px; }}
.nf-celda {{ padding: 6px 4px; }}
.nf-celda.oculto, .nf-celda.cortado {{ opacity: 0.45; }}
.nf-nombre {{ font-family: "{SANS}"; font-weight: 600; font-size: 13px; color: {css_color(text)}; }}
.nf-col {{ font-family: "{MONO}"; font-size: 11px; color: {css_color(muted)}; }}
.nf-texto {{ font-family: "{MONO}"; font-size: 10px; color: {css_color(text, 0.8)};
            background: rgba(0, 0, 0, 0.45); padding: 6px; }}
gridview.nf-grid, columnview.nf-lista, columnview.nf-lista > listview {{ background: transparent; }}
gridview.nf-grid > child {{ border-radius: 0; margin: 2px; border: 1px solid transparent; }}
gridview.nf-grid > child:hover {{ background: {css_color(acc, 0.08)}; border-color: {css_color(acc, 0.25)}; }}
gridview.nf-grid > child:selected {{ background: {css_color(acc, 0.22)}; border-color: {css_color(acc, 0.8)}; }}
gridview.nf-grid > child:focus-visible {{ outline: 1px dashed {css_color(alt)}; }}
columnview.nf-lista > listview > row {{ border-radius: 0; }}
columnview.nf-lista > listview > row:hover {{ background: {css_color(acc, 0.08)}; }}
columnview.nf-lista > listview > row:selected {{ background: {css_color(acc, 0.22)}; }}
columnview.nf-lista > header > button {{ all: unset; padding: 3px 8px; font-family: "{MONO}"; font-size: 10px;
    letter-spacing: 1px; color: {css_color(muted)}; border-bottom: 1px solid {css_color(acc, 0.35)}; }}
columnview.nf-lista > listview > row > cell {{ padding: 3px 8px; }}
rubberband {{ background: {css_color(acc, 0.15)}; border: 1px solid {css_color(acc)}; }}
.nf-lateral {{ padding-right: 6px; border-right: 1px solid {css_color(acc, 0.25)}; }}
.nf-lugar {{ all: unset; padding: 5px 8px; border-left: 2px solid transparent; }}
.nf-lugar:hover {{ background: {css_color(acc, 0.10)}; }}
.nf-lugar.on {{ background: {css_color(acc, 0.18)}; border-left-color: {css_color(acc)}; }}
.nf-lugar-txt {{ font-family: "{SANS}"; font-weight: 600; font-size: 14px; color: {css_color(text)}; }}
.nf-lugar-sub {{ font-family: "{MONO}"; font-size: 9px; color: {css_color(muted)}; }}
progressbar.nx-prog.lleno progress {{ background: {css_color(warn)}; }}
.nf-miga {{ all: unset; padding: 2px 8px; font-family: "{SANS}"; font-weight: 700; font-size: 14px;
           letter-spacing: 1px; color: {css_color(muted)}; }}
.nf-miga:hover {{ color: {css_color(text)}; background: {css_color(acc, 0.12)}; }}
.nf-miga.on {{ color: {css_color(acc)}; }}
.nf-pregunta {{ padding: 4px 6px; background: {css_color(acc, 0.08)}; border-left: 2px solid {css_color(acc)}; }}
.nx-btn.on {{ color: {css_color(acc)}; }}
"""

    def refrescar_vistas(self):
        for w in self.get_windows():
            if isinstance(w, Ventana):
                w.refrescar_celdas()

    def crear_ventana(self, args, cwd):
        destino = None
        for a in args:
            if a.startswith("-"):
                continue
            if a.startswith("file://"):
                a = Gio.File.new_for_uri(a).get_path() or a
            p = Path(a if os.path.isabs(a) else os.path.join(cwd or str(HOME), a))
            destino = p
            break
        if destino is not None and destino.is_file():
            ct, _ = Gio.content_type_guess(str(destino), None)
            if ct in IMAGENES or ct in ("image/tiff", "image/bmp"):
                imgs = sorted((p for p in destino.parent.iterdir()
                               if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".gif", ".tif", ".tiff", ".bmp")),
                              key=lambda p: clave_natural(p.name))
                Visor(self, imgs, imgs.index(destino) if destino in imgs else 0).present()
                return
            v = Ventana(self, destino.parent)
            v._seleccionar_al_cargar = destino.name
            v.present()
            return
        Ventana(self, destino if destino is not None and destino.is_dir() else HOME).present()


if __name__ == "__main__":
    nombrar_proceso(PROC)
    GLib.set_prgname(APP_ID)
    sys.exit(App().run(sys.argv))
