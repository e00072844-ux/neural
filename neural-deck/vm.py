#!/usr/bin/env python3
"""NEURAL//VM: gestor de máquinas virtuales QEMU/KVM (sin libvirt).

Izquierda: tus VMs y su estado. Derecha: la VM elegida:
  - vista en vivo de su pantalla (captura cada pocos segundos)
  - arrancar, apagar (ACPI), pausar, reiniciar, forzar, VER (SPICE), CLONAR
  - pestañas de características:
      GENERAL      RAM, CPU, ISO, ISO de drivers, UEFI, arranque por red, sonido
      PANTALLA     ventana QEMU / SPICE / sin ventana, gráficos (3D virgl), monitores
      RED          tarjetas (NAT, red interna, puente por cable, solo entre VMs),
                   puertos reenviados y las redes del equipo
      DISCOS       bus, caché, ampliar, compactar, discos extra, snapshots
      USB          dispositivos del equipo: al arrancar y en caliente
      CARPETAS     carpetas compartidas (virtiofs)
      RENDIMIENTO  modelo de CPU, núcleos fijos, hugepages
  - NUEVA VM, IMPORTAR (VirtualBox / VMware / Hyper-V / .ova), DESDE USB (un sistema ya
    instalado en un disco USB, usado entero: los cambios se guardan en el disco), AÑADIR CARPETA
La lógica está en vm_motor.py y vm_red.py; las VMs viven en ~/vms/<nombre>/vm.json.
"""
from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ventana import AppNeural, VentanaNeural, boton, lbl, nombrar_proceso, on, tam_humano  # noqa: E402
from estilo import css_color  # noqa: E402
import vm_motor as M  # noqa: E402
import vm_red as R  # noqa: E402

APP_ID = "org.local.NeuralVM"
PROC = "neural-vm"
CACHE = Path.home() / ".cache/neural-vm"
ESTADOS = {"apagada": "APAGADA", "encendida": "ENCENDIDA", "pausada": "EN PAUSA", "ajena": "ENCENDIDA (A MANO)"}
TOTAL_CPUS = len(os.sched_getaffinity(0))
try:
    TOTAL_RAM = int(int(next(l for l in open("/proc/meminfo") if l.startswith("MemTotal")).split()[1]) / 1048576)
except (OSError, StopIteration, ValueError):
    TOTAL_RAM = 16
PESTANAS = [("general", "GENERAL"), ("pantalla", "PANTALLA"), ("red", "RED"), ("discos", "DISCOS"),
            ("usb", "USB"), ("carpetas", "CARPETAS"), ("rendimiento", "RENDIMIENTO")]
NOTA_PANTALLA = {
    "gtk": "La ventana de QEMU de siempre. Ctrl+Alt+G suelta el ratón. Sin portapapeles compartido.",
    "spice": "Se abre en remote-viewer: portapapeles compartido, resolución automática, varias pantallas y "
             "menú para pasar USB. Dentro de la VM: spice-vdagent (Linux) o virtio-win-guest-tools (Windows).",
    "sin_ventana": "Arranca sin ventana. Con VER abres remote-viewer cuando quieras; al cerrarlo la VM sigue.",
}


def en_hilo(trabajo, listo=None, error=None):
    """Ejecuta trabajo() fuera de la interfaz; listo(res) / error(exc) vuelven al hilo GTK."""
    def correr():
        try:
            res = trabajo()
        except Exception as e:  # noqa: BLE001 — se muestra en el pie
            if error:
                GLib.idle_add(error, e)
            return
        if listo:
            GLib.idle_add(listo, res)
    threading.Thread(target=correr, daemon=True).start()


def fila(*hijos, sp=8, **kw) -> Gtk.Box:
    b = Gtk.Box(spacing=sp, **kw)
    for h in hijos:
        b.append(h)
    return b


def columna(*hijos, sp=6, **kw) -> Gtk.Box:
    b = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=sp, **kw)
    for h in hijos:
        b.append(h)
    return b


def fluir(*hijos, sp=6, **kw) -> Gtk.FlowBox:
    """Fila que pasa a varias líneas cuando no cabe (ventana en mosaico)."""
    f = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=False, row_spacing=sp, column_spacing=sp,
                    min_children_per_line=1, max_children_per_line=30, hexpand=True, valign=Gtk.Align.START, **kw)
    for h in hijos:
        f.append(h)
        h.get_parent().set_focusable(False)
    return f


def vaciar(caja: Gtk.Box) -> None:
    while (h := caja.get_first_child()) is not None:
        caja.remove(h)


def boton_solo(texto: str, cls: str) -> Gtk.Button:
    """Botón sin acción directa (se le engancha una confirmación)."""
    b = Gtk.Button(label=texto)
    for c in cls.split():
        b.add_css_class(c)
    return b


def giro(minimo, maximo, valor) -> Gtk.SpinButton:
    s = Gtk.SpinButton.new_with_range(minimo, maximo, 1)
    s.set_value(valor)
    s.add_css_class("nx-entry")
    return s


def entrada(pista: str = "", texto: str = "", **kw) -> Gtk.Entry:
    e = Gtk.Entry(placeholder_text=pista, text=texto, **kw)
    e.add_css_class("nx-entry")
    return e


class Opciones(Gtk.DropDown):
    """Desplegable de un catálogo {clave: nombre visible}."""
    def __init__(self, opciones: dict):
        super().__init__()
        self.claves = list(opciones)
        self.set_model(Gtk.StringList.new(list(opciones.values())))

    def clave(self) -> str:
        i = self.get_selected()
        return self.claves[i] if 0 <= i < len(self.claves) else self.claves[0]

    def poner(self, k) -> None:
        self.set_selected(self.claves.index(k) if k in self.claves else 0)


def rejilla(filas: list[tuple[str, Gtk.Widget]]) -> Gtk.Grid:
    g = Gtk.Grid(column_spacing=10, row_spacing=6)
    for i, (t, w) in enumerate(filas):
        g.attach(lbl(t, "nx-dim", xalign=0), 0, i, 1, 1)
        g.attach(w, 1, i, 1, 1)
    return g


class Tarjeta(Gtk.Box):
    def __init__(self, vm: M.VM):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.add_css_class("nx-card")
        self.vm = vm
        self.t_nombre = lbl(vm.nombre, "nx-txt", xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.t_tipo = lbl(M.TIPOS.get(vm.tipo, vm.tipo.upper()), "nx-tag acc")
        self.t_estado = lbl("…", "nx-code", xalign=1, hexpand=True)
        self.t_datos = lbl("", "nx-dim", xalign=0)
        self.append(fila(self.t_nombre, self.t_estado))
        self.append(fila(self.t_tipo, self.t_datos))
        self.poner(vm, "apagada")

    def poner(self, vm: M.VM, estado: str):
        self.vm = vm
        self.t_nombre.set_text(vm.nombre)
        self.t_datos.set_text(f"{vm.cfg['ram_gb']} GB · {vm.cfg['cpus']} CPU")
        self.t_estado.set_text("● " + ESTADOS[estado] if estado != "apagada" else "○ APAGADA")
        on(self.t_estado, "on", estado in ("encendida", "ajena"))
        on(self.t_estado, "warn", estado == "pausada")


# ------------------------------------------------------------------ filas editables
class FilaRed(Gtk.Box):
    def __init__(self, r: dict, quitar):
        super().__init__(spacing=6)
        self.mac = r.get("mac", "")
        self.modo = Opciones(M.MODOS_RED)
        self.modo.poner(r.get("modo", "nat"))
        self.modelo = Opciones(M.MODELOS_RED)
        self.modelo.poner(r.get("modelo", "auto"))
        self.grupo = giro(1, 250, int(r.get("grupo") or 1))
        self.grupo.set_tooltip_text("Las VMs con el mismo número de grupo se ven entre ellas")
        self.t_grupo = lbl("GRUPO", "nx-code")
        self.modo.connect("notify::selected", lambda *_: self._ver())
        self.append(self.modo)
        self.append(self.modelo)
        self.append(self.t_grupo)
        self.append(self.grupo)
        self.append(lbl(self.mac or "MAC al arrancar", "nx-code", xalign=0, hexpand=True))
        self.append(boton("✕", "nx-chip", lambda: quitar(self), "Quitar esta tarjeta"))
        self._ver()

    def _ver(self):
        a = self.modo.clave() == "aislada"
        self.grupo.set_visible(a)
        self.t_grupo.set_visible(a)

    def datos(self) -> dict:
        return {"modo": self.modo.clave(), "modelo": self.modelo.clave(), "mac": self.mac,
                "grupo": int(self.grupo.get_value())}


class FilaPuerto(Gtk.Box):
    def __init__(self, p: dict, quitar):
        super().__init__(spacing=6)
        self.proto = Opciones({"tcp": "TCP", "udp": "UDP"})
        self.proto.poner(p.get("proto", "tcp"))
        self.host = giro(1, 65535, int(p.get("host") or 2222))
        self.vm = giro(1, 65535, int(p.get("vm") or 22))
        self.lan = Gtk.CheckButton(label="abierto a la red", active=bool(p.get("lan")))
        self.lan.set_tooltip_text("Sin marcar: solo desde este equipo (127.0.0.1). Marcado: también desde otros equipos")
        for w in (self.proto, lbl("EQUIPO", "nx-code"), self.host, lbl("→ VM", "nx-code"), self.vm, self.lan):
            self.append(w)
        self.append(Gtk.Box(hexpand=True))
        self.append(boton("✕", "nx-chip", lambda: quitar(self), "Quitar este puerto"))

    def datos(self) -> dict:
        return {"proto": self.proto.clave(), "host": int(self.host.get_value()), "vm": int(self.vm.get_value()),
                "lan": self.lan.get_active()}


class FilaCarpeta(Gtk.Box):
    def __init__(self, cp: dict, quitar):
        super().__init__(spacing=6)
        self.ruta = cp.get("ruta", "")
        self.etiqueta = entrada("etiqueta", M.etiqueta(cp), width_chars=14)
        self.etiqueta.set_tooltip_text("Nombre con el que se monta dentro de la VM")
        self.ro = Gtk.CheckButton(label="solo lectura", active=bool(cp.get("solo_lectura")))
        self.append(lbl(self.ruta, "nx-txt", xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.MIDDLE))
        self.append(self.etiqueta)
        self.append(self.ro)
        self.append(boton("✕", "nx-chip", lambda: quitar(self), "Dejar de compartir"))

    def datos(self) -> dict:
        d = {"ruta": self.ruta, "etiqueta": self.etiqueta.get_text().strip(), "solo_lectura": self.ro.get_active()}
        d["etiqueta"] = M.etiqueta(d)
        return d


class Ventana(VentanaNeural):
    def __init__(self, app):
        super().__init__(app, "SYS//VM · QEMU/KVM", "NEURAL//VM", (1240, 820))
        CACHE.mkdir(parents=True, exist_ok=True)
        self.vms: dict[Path, M.VM] = {}
        self.estados: dict[Path, str] = {}
        self.tarjetas: dict[Path, Gtk.ListBoxRow] = {}
        self.sel: Path | None = None
        self._ocupado = False
        self._capturando = False
        self.usb_fijos: list[dict] = []   # borrador de cfg["usb"]

        # botones generales: fuera de la cabecera, que no se puede partir en líneas
        self.b_nueva = boton("+ NUEVA VM", "nx-accion fuerte", lambda: self.pop_nueva.popup())
        self.pop_nueva = self._pop_nueva()
        self.pop_nueva.set_parent(self.b_nueva)
        self.b_importar = boton("IMPORTAR", "nx-accion", lambda: self.pop_importar.popup(),
                                "Desde un disco de VirtualBox, VMware o Hyper-V, o un .ova")
        self.pop_importar = self._pop_importar()
        self.pop_importar.set_parent(self.b_importar)
        self.b_usb_disco = boton("DESDE USB", "nx-accion", lambda: self.pop_usb_disco.popup(),
                                 "Arrancar un sistema ya instalado en un disco USB (los cambios se guardan en él)")
        self.pop_usb_disco = self._pop_usb_disco()
        self.pop_usb_disco.set_parent(self.b_usb_disco)
        self.connect("destroy", lambda *_: (self.pop_nueva.unparent(), self.pop_importar.unparent(),
                                            self.pop_usb_disco.unparent(), self.pop_clonar.unparent()))
        self.cuerpo.append(fluir(self.b_nueva, self.b_importar, self.b_usb_disco,
                                 boton("AÑADIR CARPETA", "nx-accion", self._anadir_carpeta,
                                       "Registrar una carpeta que ya tenga vm.json")))

        # cuerpo: lista | detalle
        self.lista = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.lista.add_css_class("nx-lista")
        self.lista.connect("row-selected", self._elegida)
        sc = Gtk.ScrolledWindow(child=self.lista, hscrollbar_policy=Gtk.PolicyType.EXTERNAL, vexpand=True)
        sc.add_css_class("nx-scroll")
        lista_col = columna(lbl("MÁQUINAS", "nx-h2", xalign=0), sc, margin_end=8)

        self.detalle = Gtk.Stack(hexpand=True, vexpand=True)
        self.detalle.add_named(columna(lbl("Ninguna VM elegida", "nx-dim"), valign=Gtk.Align.CENTER), "nada")
        dsc = Gtk.ScrolledWindow(child=self._panel_detalle(), hscrollbar_policy=Gtk.PolicyType.AUTOMATIC)
        dsc.add_css_class("nx-scroll")
        self.detalle.add_named(dsc, "vm")
        self.detalle.add_css_class("nx-lado")
        # la lista cede ancho con la ventana; ningún lado impone un mínimo
        self.reparto = Gtk.Paned(start_child=lista_col, end_child=self.detalle, resize_start_child=False,
                                 shrink_start_child=True, shrink_end_child=True, position=300,
                                 vexpand=True, margin_top=8)
        self.cuerpo.append(self.reparto)
        self.connect("realize", lambda w: w.get_surface().connect("layout", self._cambio_tamano))

        self.p_izq.set_text(f"{M.VMS_DIR} · KVM {'OK' if Path('/dev/kvm').exists() else 'NO'} · "
                            f"{TOTAL_CPUS} CPU · {TOTAL_RAM} GB")
        self.refrescar()
        GLib.timeout_add_seconds(2, self._tic)

    def _cambio_tamano(self, _surface, ancho, _alto):
        GLib.idle_add(lambda: (self.reparto.set_position(max(150, min(300, int(ancho * 0.3)))), False)[1])

    # ------------------------------------------------------------ detalle
    def _panel_detalle(self) -> Gtk.Widget:
        self.d_nombre = lbl("", "nx-h1", xalign=0, ellipsize=Pango.EllipsizeMode.END, hexpand=True)
        self.d_estado = lbl("", "nx-tag")
        self.d_tipo = lbl("", "nx-tag acc")

        self.vista = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, can_shrink=True)
        self.vista_txt = lbl("", "nx-dim", valign=Gtk.Align.CENTER)
        self.vista_pila = Gtk.Stack()
        self.vista_pila.add_named(self.vista, "img")
        self.vista_pila.add_named(self.vista_txt, "txt")
        # alto fijo: que la captura (1920×1080) no estire el panel
        self.vista_marco = Gtk.ScrolledWindow(child=self.vista_pila, vexpand=False,
                                              hscrollbar_policy=Gtk.PolicyType.EXTERNAL,
                                              vscrollbar_policy=Gtk.PolicyType.EXTERNAL)
        self.vista_marco.set_size_request(-1, 230)
        self.vista_marco.add_css_class("nx-scroll")

        self.b_arrancar = boton("ARRANCAR", "nx-accion fuerte", self._arrancar)
        self.b_ver = boton("VER", "nx-accion", self._ver, "Abrir la pantalla SPICE (remote-viewer)")
        self.b_apagar = boton("APAGAR", "nx-accion", self._apagar,
                              "Pide al sistema de la VM que se apague")
        self.b_pausa = boton("PAUSA", "nx-accion", self._pausa)
        self.b_reset = boton_solo("REINICIAR", "nx-accion peligro")
        self.b_forzar = boton_solo("FORZAR", "nx-accion peligro")
        self._confirmar_en(self.b_reset, self._texto_reiniciar, self._reiniciar)
        self._confirmar_en(self.b_forzar, "¿Cortar la corriente? Se pierde lo no guardado.",
                           lambda: self._qmp("forzar", "Apagada a la fuerza"))
        self.b_clonar = boton("CLONAR", "nx-accion", lambda: self.pop_clonar.popup(), "Copia entera de la VM")
        self.pop_clonar = self._pop_clonar()
        self.pop_clonar.set_parent(self.b_clonar)
        acciones = fluir(self.b_arrancar, self.b_ver, self.b_apagar, self.b_pausa, self.b_reset, self.b_forzar,
                         self.b_clonar)

        self.c_aviso = lbl("", "nx-code warn", xalign=0, wrap=True)
        self.c_nota = lbl("", "nx-code", xalign=0, wrap=True)

        # pestañas
        self.tabs: dict[str, Gtk.Button] = {}
        barra = fluir(sp=2)
        for clave, texto in PESTANAS:
            b = boton(texto, "nx-tab", lambda c=clave: self._pestana(c))
            self.tabs[clave] = b
            barra.append(b)
        self.paginas = Gtk.Stack(vhomogeneous=False, hhomogeneous=False)
        self.paginas.add_named(self._pag_general(), "general")
        self.paginas.add_named(self._pag_pantalla(), "pantalla")
        self.paginas.add_named(self._pag_red(), "red")
        self.paginas.add_named(self._pag_discos(), "discos")
        self.paginas.add_named(self._pag_usb(), "usb")
        self.paginas.add_named(self._pag_carpetas(), "carpetas")
        self.paginas.add_named(self._pag_rendimiento(), "rendimiento")

        self.b_guardar = boton("GUARDAR CAMBIOS", "nx-accion fuerte", self._guardar)
        self.d_carpeta = lbl("", "nx-code", xalign=0, ellipsize=Pango.EllipsizeMode.MIDDLE, hexpand=True)
        b_quitar = boton_solo("QUITAR DE LA LISTA", "nx-chip")
        self._confirmar_en(b_quitar, "Solo la quita de NEURAL//VM; los archivos se quedan.", self._quitar)
        self.b_borrar = boton_solo("BORRAR VM", "nx-accion peligro")
        self.b_borrar.set_tooltip_text("Borra del SSD la VM y todos sus archivos")
        self._confirmar_en(self.b_borrar, self._texto_borrar, self._borrar)

        sep = lambda: Gtk.Box(css_classes=["nx-sep"], margin_top=4)  # noqa: E731
        self._pestana("general")
        return columna(
            fila(self.d_nombre, self.d_tipo, self.d_estado),
            self.vista_marco, acciones, self.c_aviso, self.c_nota, sep(),
            barra, self.paginas, sep(),
            fila(self.b_guardar, lbl("las características se aplican al arrancar o con REINICIAR", "nx-code",
                                     xalign=0, wrap=True, hexpand=True)),
            sep(),
            fila(self.d_carpeta, boton("LOG", "nx-chip", self._abrir_log), b_quitar, self.b_borrar, sp=6),
            sp=8, margin_start=4, margin_end=8, margin_bottom=8)

    def _pestana(self, clave: str):
        for k, b in self.tabs.items():
            on(b, "on", k == clave)
        self.paginas.set_visible_child_name(clave)
        if clave == "red":
            self._cargar_redes()
        elif clave == "usb":
            self._cargar_usb()
        elif clave == "rendimiento":
            self._pintar_hugepages()

    # ---- GENERAL
    def _pag_general(self) -> Gtk.Widget:
        self.c_ram = giro(1, max(2, TOTAL_RAM - 2), 4)
        self.c_cpu = giro(1, TOTAL_CPUS, 4)
        self.c_iso = entrada("sin ISO", hexpand=True)
        self.c_iso_arr = Gtk.CheckButton(label="ISO antes que el disco")
        self.c_iso_arr.set_tooltip_text("No hace falta para instalar: con el disco vacío la ISO arranca sola "
                                        "y al terminar entra el sistema instalado. Márcala solo para "
                                        "reinstalar o rescatar un disco que ya arranca.")
        self.c_drv = entrada("sin ISO de drivers (virtio-win para Windows)", hexpand=True)
        self.f_drv = fila(self.c_drv, boton("ELEGIR", "nx-chip", lambda: self._elegir_iso(self.c_drv, False)),
                          boton("BUSCAR", "nx-chip", self._buscar_virtio, "Buscar virtio-win*.iso en ~/ISOs"),
                          boton("✕", "nx-chip", lambda: self.c_drv.set_text("")), sp=4)
        self.c_uefi = Gtk.CheckButton(label="UEFI")
        self.c_uefi.set_tooltip_text("Cambiarlo con un sistema ya instalado puede impedir que arranque")
        self.c_pxe = Gtk.CheckButton(label="Arrancar por red (PXE)")
        self.c_ps2 = Gtk.CheckButton(label="Teclado PS/2")
        self.c_ps2.set_tooltip_text("Para sistemas antiguos sin USB 3 (si la VM no recibe el teclado)")
        self.c_audio = Opciones(M.AUDIOS)
        self.t_drv = lbl("ISO DRIVERS", "nx-dim", xalign=0)
        g = Gtk.Grid(column_spacing=10, row_spacing=6)
        g.attach(lbl("RAM (GB)", "nx-dim", xalign=0), 0, 0, 1, 1)
        g.attach(self.c_ram, 1, 0, 1, 1)
        g.attach(lbl("CPU (hilos)", "nx-dim", xalign=0), 0, 1, 1, 1)
        g.attach(self.c_cpu, 1, 1, 1, 1)
        g.attach(lbl("ISO", "nx-dim", xalign=0), 0, 2, 1, 1)
        g.attach(fila(self.c_iso, boton("ELEGIR", "nx-chip", lambda: self._elegir_iso(self.c_iso, True)),
                      boton("✕", "nx-chip", lambda: self.c_iso.set_text("")), sp=4), 1, 2, 1, 1)
        g.attach(self.t_drv, 0, 3, 1, 1)
        g.attach(self.f_drv, 1, 3, 1, 1)
        g.attach(fluir(self.c_iso_arr, self.c_uefi, self.c_pxe, self.c_ps2, sp=16), 1, 4, 1, 1)
        g.attach(lbl("SONIDO", "nx-dim", xalign=0), 0, 5, 1, 1)
        g.attach(fila(self.c_audio), 1, 5, 1, 1)
        return columna(g, lbl("La ISO de drivers se conecta como segundo CD. En Windows: instala "
                              "virtio-win-guest-tools.exe desde ese CD (red, disco, carpetas, portapapeles).",
                              "nx-code", xalign=0, wrap=True), margin_top=8)

    # ---- PANTALLA
    def _pag_pantalla(self) -> Gtk.Widget:
        self.c_pantalla = Opciones(M.PANTALLAS)
        self.c_graf = Opciones(M.GRAFICOS)
        self.c_monitores = giro(1, 4, 1)
        self.c_vram = giro(0, 512, 0)
        self.c_vram.set_tooltip_text("0 = automático (64 MB; macOS 256 MB)")
        self.t_pantalla = lbl("", "nx-code", xalign=0, wrap=True)
        self.t_graf = lbl("", "nx-code", xalign=0, wrap=True)
        self.c_pantalla.connect("notify::selected", lambda *_: self._notas_pantalla())
        self.c_graf.connect("notify::selected", lambda *_: self._notas_pantalla())
        g = rejilla([("PANTALLA", fila(self.c_pantalla)), ("GRÁFICOS", fila(self.c_graf)),
                     ("MONITORES", self.c_monitores), ("VÍDEO (MB)", self.c_vram)])
        return columna(g, self.t_pantalla, self.t_graf, margin_top=8)

    def _notas_pantalla(self):
        self.t_pantalla.set_text(NOTA_PANTALLA.get(self.c_pantalla.clave(), ""))
        v = self.vm
        g = self.c_graf.clave()
        if v and v.tipo == "macos":
            txt = "macOS usa siempre VMware SVGA (sin 3D)."
        elif g == "3d":
            txt = ("3D con virgl: OpenGL acelerado por la gráfica del equipo. Solo VMs Linux (Mesa ya trae el "
                   "driver). Monitores: hasta 4.")
        elif g == "qxl":
            txt = "QXL: el de SPICE; en Windows con virtio-win-guest-tools da resoluciones altas y varios monitores."
        elif g == "virtio":
            txt = "virtio-gpu sin 3D: el mejor para Linux sin aceleración."
        elif g == "auto":
            txt = "Automático: VIRTIO en Linux, VGA estándar en Windows y otros."
        else:
            txt = ""
        self.t_graf.set_text(txt)
        self.c_monitores.set_sensitive(g in ("3d", "virtio", "qxl") or (g == "auto" and v and v.tipo == "linux"))

    # ---- RED
    def _pag_red(self) -> Gtk.Widget:
        self.redes_caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.puertos_caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.r_interna = lbl("…", "nx-txt", xalign=0, hexpand=True, wrap=True)
        self.r_puente = lbl("…", "nx-txt", xalign=0, hexpand=True, wrap=True)
        self.r_cable = Gtk.DropDown.new_from_strings(["(sin tarjetas)"])
        self.cables: list[str] = []
        b_int = boton("CREAR / ACTIVAR", "nx-chip", self._interna_crear)
        b_int_q = boton_solo("QUITAR", "nx-chip")
        self._confirmar_en(b_int_q, "¿Quitar la red interna? Las VMs que la usen se quedarán sin esa tarjeta.",
                           lambda: self._red_op(R.quitar_interna, "Red interna quitada"))
        b_pu = boton_solo("CREAR PUENTE", "nx-chip")
        self._confirmar_en(b_pu, "Este equipo pasará a conectarse por el puente cuando uses ese cable "
                                 "(sigue teniendo internet igual). ¿Crear el puente?", self._puente_crear)
        b_pu_q = boton_solo("QUITAR", "nx-chip")
        self._confirmar_en(b_pu_q, "¿Quitar el puente? El cable vuelve a su conexión normal.",
                           lambda: self._red_op(R.quitar_puente, "Puente quitado"))
        return columna(
            lbl("TARJETAS DE RED", "nx-h2", xalign=0), self.redes_caja,
            fila(boton("+ TARJETA", "nx-chip", lambda: self._fila_red({"modo": "nat"})), sp=6),
            lbl("NAT: internet sin más, la VM no se ve desde fuera. RED INTERNA: IP 10.77.0.x, las VMs se ven entre "
                "ellas y con este equipo, sale por el WiFi o el cable. PUENTE: la VM es un equipo más de tu red "
                "(solo por cable). SOLO ENTRE VMS: sin internet, solo las del mismo grupo.",
                "nx-code", xalign=0, wrap=True),
            lbl("PUERTOS REENVIADOS (por la primera tarjeta NAT)", "nx-h2", xalign=0, margin_top=6),
            self.puertos_caja,
            fila(boton("+ PUERTO", "nx-chip", lambda: self._fila_puerto({})),
                 boton("+ SSH", "nx-chip", lambda: self._fila_puerto({"host": 2222, "vm": 22})),
                 boton("+ RDP", "nx-chip", lambda: self._fila_puerto({"host": 3389, "vm": 3389})), sp=6),
            lbl("REDES DEL EQUIPO", "nx-h2", xalign=0, margin_top=6),
            fila(lbl("RED INTERNA", "nx-dim", xalign=0, width_chars=12), self.r_interna, b_int, b_int_q, sp=6),
            fila(lbl("PUENTE CABLE", "nx-dim", xalign=0, width_chars=12), self.r_puente, self.r_cable, b_pu, b_pu_q,
                 sp=6),
            margin_top=8)

    def _fila_red(self, r: dict):
        self.redes_caja.append(FilaRed(r, self.redes_caja.remove))

    def _fila_puerto(self, p: dict):
        self.puertos_caja.append(FilaPuerto(p, self.puertos_caja.remove))

    def _cargar_redes(self):
        en_hilo(lambda: (R.estado_red("interna"), R.estado_red("puente"), R.tarjetas_cable()), self._pintar_redes)

    def _pintar_redes(self, res):
        interna, puente, cables = res

        def texto(e, nombre_br):
            if not e["existe"]:
                t = "no creada"
            else:
                t = ("activa · " + (e["ip"] or "sin IP")) if e["activa"] else "creada, sin activar"
                if e.get("puertos"):
                    t += " · cable: " + ", ".join(e["puertos"])
            if not e["permitido"]:
                t += f" · QEMU sin permiso para {nombre_br} (sudo ~/neural-deck/root/instalar.sh)"
            return t
        self.r_interna.set_text(texto(interna, R.INTERNA_BR))
        self.r_puente.set_text(texto(puente, R.PUENTE_BR))
        self.cables = cables
        self.r_cable.set_model(Gtk.StringList.new(cables or ["(sin tarjetas)"]))
        return False

    def _red_op(self, trabajo, texto):
        self.aviso("Configurando la red…")
        en_hilo(trabajo, lambda r: (self.aviso(r if isinstance(r, str) else texto), self._cargar_redes(), False)[-1],
                self._fallo)

    def _interna_crear(self):
        self._red_op(R.asegurar_interna, "Red interna activa (10.77.0.1)")

    def _puente_crear(self):
        i = self.r_cable.get_selected()
        if not self.cables or i >= len(self.cables):
            self.aviso("No hay tarjeta de cable", True)
            return
        dev = self.cables[i]
        self._red_op(lambda: R.crear_puente(dev), "Puente creado")

    # ---- DISCOS
    def _pag_discos(self) -> Gtk.Widget:
        self.d_disco = lbl("", "nx-txt", xalign=0, wrap=True)
        self.c_bus = Opciones(M.BUSES)
        self.c_bus.set_tooltip_text("Cambiarlo en un Windows ya instalado sin los drivers virtio impide que arranque")
        self.c_cache = Opciones(M.CACHES)
        self.t_bus = lbl("BUS", "nx-dim", xalign=0)
        self.c_ampliar = giro(1, 8192, 40)
        b_ampliar = boton_solo("AMPLIAR", "nx-chip")
        self._confirmar_en(b_ampliar, "¿Agrandar el disco? Luego, dentro de la VM, amplía la partición.",
                           lambda: self._disco_op(lambda v: v.ampliar(v.cfg["disco"], int(self.c_ampliar.get_value())),
                                                  "Disco ampliado"))
        b_compactar = boton_solo("COMPACTAR", "nx-chip")
        self._confirmar_en(b_compactar, "Reescribe el disco sin el espacio vacío (puede tardar y necesita espacio "
                                        "libre). ¿Seguir?",
                           lambda: self._disco_op(lambda v: v.compactar(v.cfg["disco"]), "Disco compactado"))
        self.extras_caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.c_extra_gb = giro(1, 8192, 20)
        self.c_extra_bus = Opciones({"virtio": "VIRTIO", "sata": "SATA"})
        self.b_extra = boton("CREAR DISCO", "nx-chip", self._crear_extra)

        self.snaps = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.snap_nombre = entrada("nombre de la snapshot", hexpand=True)
        self.b_snap = boton("CREAR", "nx-accion", self._crear_snap)
        self.snap_nota = lbl("", "nx-code", xalign=0)
        self.b_existente = boton("USAR COMO PRINCIPAL", "nx-chip", lambda: self._elegir_disco(True),
                                 "Copia el disco a la VM (el original no se toca); el actual pasa a DISCOS EXTRA")
        self.b_existente_extra = boton("AÑADIR COMO EXTRA", "nx-chip", lambda: self._elegir_disco(False),
                                       "Copia el disco a la VM (el original no se toca)")
        self.disco_ops = [b_ampliar, b_compactar, self.b_extra, self.b_existente, self.b_existente_extra]
        self.solo_qcow = [b_ampliar, b_compactar, self.c_ampliar]
        g = Gtk.Grid(column_spacing=10, row_spacing=6)
        g.attach(self.t_bus, 0, 0, 1, 1)
        g.attach(fila(self.c_bus), 1, 0, 1, 1)
        g.attach(lbl("CACHÉ", "nx-dim", xalign=0), 0, 1, 1, 1)
        g.attach(fila(self.c_cache), 1, 1, 1, 1)
        g.attach(fila(self.c_ampliar, b_ampliar, b_compactar, sp=6), 1, 2, 1, 1)
        self.t_tam = lbl("TAMAÑO (GB)", "nx-dim", xalign=0)
        g.attach(self.t_tam, 0, 2, 1, 1)
        self.d_nota_disco = lbl("", "nx-code", xalign=0, wrap=True)
        self.t_snaps = lbl("SNAPSHOTS", "nx-h2", xalign=0, margin_top=6)
        self.f_snap = fila(self.snap_nombre, self.b_snap, sp=6)
        return columna(
            lbl("DISCO PRINCIPAL", "nx-h2", xalign=0), self.d_disco, g, self.d_nota_disco,
            lbl("DISCOS EXTRA", "nx-h2", xalign=0, margin_top=6), self.extras_caja,
            fila(lbl("NUEVO (GB)", "nx-dim"), self.c_extra_gb, self.c_extra_bus, self.b_extra, sp=6),
            fila(lbl("DISCO EXISTENTE", "nx-dim"), self.b_existente, self.b_existente_extra, sp=6),
            self.t_snaps, self.snaps, self.f_snap, self.snap_nota,
            margin_top=8)

    def _disco_op(self, trabajo, texto):
        v = self.vm
        if not v:
            return
        self.aviso("Trabajando en el disco…")
        en_hilo(lambda: trabajo(v), lambda _: (self.aviso(texto), self._recargar_vm(), False)[-1], self._fallo)

    def _elegir_disco(self, principal: bool):
        dlg = Gtk.FileDialog(title="Elegir disco virtual")
        filtro = Gtk.FileFilter(name="Discos virtuales")
        for p in M.FORMATOS_IMPORTAR:
            if p != "*.ova":
                filtro.add_pattern(p)
        modelo = Gio.ListStore.new(Gtk.FileFilter)
        modelo.append(filtro)
        dlg.set_filters(modelo)

        def fin(d, res):
            try:
                f = d.open_finish(res)
            except GLib.Error:
                return
            if f and f.get_path():
                bus = self.c_extra_bus.clave()
                self._disco_op(lambda v: v.usar_disco_existente(f.get_path(), principal, bus),
                               "Disco principal cambiado" if principal else "Disco añadido")
        dlg.open(self, None, fin)

    def _crear_extra(self):
        gb, bus = int(self.c_extra_gb.get_value()), self.c_extra_bus.clave()
        self._disco_op(lambda v: v.crear_disco_extra(gb, bus), "Disco extra creado")

    def _pintar_extras(self, info: dict):
        vaciar(self.extras_caja)
        v = self.vm
        extras = v.cfg.get("discos_extra", []) if v else []
        if not extras:
            self.extras_caja.append(lbl("Sin discos extra", "nx-code", xalign=0))
        apagada = self.estados.get(self.sel, "apagada") == "apagada"
        for i, d in enumerate(extras):
            inf = info.get(d["archivo"], {"virtual": 0, "real": 0})
            r = fila(lbl(f"{d['archivo']} · {tam_humano(inf['virtual'])} · {tam_humano(inf['real'])} usados · "
                         f"{d.get('bus', 'virtio').upper()}", "nx-txt", xalign=0, hexpand=True,
                         ellipsize=Pango.EllipsizeMode.END), sp=4)
            bq = boton_solo("QUITAR", "nx-chip")
            bb = boton_solo("BORRAR", "nx-chip peligro")
            self._confirmar_en(bq, "¿Quitarlo de la VM? El archivo se queda en la carpeta.",
                               lambda i=i: self._disco_op(lambda v: v.quitar_disco_extra(i, False), "Disco quitado"))
            self._confirmar_en(bb, f"¿Quitar y BORRAR {d['archivo']}? Se pierde lo que tenga.",
                               lambda i=i: self._disco_op(lambda v: v.quitar_disco_extra(i, True), "Disco borrado"))
            bq.set_sensitive(apagada)
            bb.set_sensitive(apagada)
            r.append(bq)
            r.append(bb)
            self.extras_caja.append(r)

    # ---- USB
    def _pag_usb(self) -> Gtk.Widget:
        self.usb_caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.usb_nota = lbl("", "nx-code warn", xalign=0, wrap=True)
        return columna(
            fila(lbl("DISPOSITIVOS DEL EQUIPO", "nx-h2", xalign=0, hexpand=True),
                 boton("ACTUALIZAR", "nx-chip", self._cargar_usb)),
            self.usb_caja, self.usb_nota,
            lbl("AL ARRANCAR: se conecta a la VM cada vez que arranca (y si lo enchufas con la VM encendida). "
                "CONECTAR: ahora mismo, con la VM encendida; mientras tanto el equipo no lo puede usar. "
                "Con la pantalla SPICE también puedes pasarlos desde el menú de remote-viewer.",
                "nx-code", xalign=0, wrap=True),
            margin_top=8)

    def _cargar_usb(self):
        v = self.vm
        if not v:
            return
        viva = self.estados.get(v.dir) in ("encendida", "pausada")
        en_hilo(lambda: (v.dir, R.usb_dispositivos(), v.usb_conectados() if viva else set()), self._pintar_usb)

    def _pintar_usb(self, res):
        d, disp, conectados = res
        if d != self.sel:
            return False
        vaciar(self.usb_caja)
        viva = self.estados.get(d) in ("encendida", "pausada")
        fijos = {(u["vid"], u["pid"]) for u in self.usb_fijos}
        vistos = set()
        sin_permiso = False
        for u in disp:
            clave = (u["vid"], u["pid"])
            if clave in vistos:
                continue
            vistos.add(clave)
            sin_permiso |= not u["acceso"]
            self.usb_caja.append(self._fila_usb(u, clave in fijos, M.usb_id(u) in conectados, viva, True))
        for u in self.usb_fijos:  # los elegidos que ahora no están enchufados
            if (u["vid"], u["pid"]) not in vistos:
                self.usb_caja.append(self._fila_usb(u, True, False, viva, False))
        if not disp and not self.usb_fijos:
            self.usb_caja.append(lbl("No hay dispositivos USB", "nx-code", xalign=0))
        self.usb_nota.set_text("Algunos dispositivos no tienen permiso: ejecuta sudo ~/neural-deck/root/instalar.sh "
                               "(una vez) para poder pasarlos a las VMs." if sin_permiso else "")
        return False

    def _fila_usb(self, u: dict, fijo: bool, conectado: bool, viva: bool, presente: bool) -> Gtk.Box:
        nombre = u.get("nombre") or f"{u['vid']}:{u['pid']}"
        r = fila(lbl(nombre, "nx-txt", xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END),
                 lbl(f"{u['vid']}:{u['pid']}", "nx-code"), sp=6)
        if not presente:
            r.append(lbl("NO ENCHUFADO", "nx-tag"))
        elif not u.get("acceso", True):
            r.append(lbl("SIN PERMISO", "nx-tag warn"))
        if conectado:
            r.append(lbl("EN LA VM", "nx-tag acc"))
        ch = Gtk.CheckButton(label="al arrancar", active=fijo)

        def cambiar(c, u=u):
            k = (u["vid"], u["pid"])
            self.usb_fijos = [x for x in self.usb_fijos if (x["vid"], x["pid"]) != k]
            if c.get_active():
                self.usb_fijos.append({"vid": u["vid"], "pid": u["pid"], "nombre": nombre})
        ch.connect("toggled", cambiar)
        r.append(ch)
        b = boton("DESCONECTAR" if conectado else "CONECTAR", "nx-chip",
                  lambda: self._usb_vivo(u, not conectado))
        b.set_sensitive(viva and presente)
        r.append(b)
        return r

    def _usb_vivo(self, u: dict, conectar: bool):
        v = self.vm
        if not v:
            return
        trabajo = (lambda: v.usb_conectar(u)) if conectar else (lambda: v.usb_desconectar(u))
        en_hilo(trabajo, lambda _: (self.aviso("USB conectado" if conectar else "USB desconectado"),
                                    GLib.timeout_add(600, lambda: (self._cargar_usb(), False)[1]), False)[-1],
                self._fallo)

    # ---- CARPETAS
    def _pag_carpetas(self) -> Gtk.Widget:
        self.carpetas_caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.carpetas_nota = lbl("", "nx-code", xalign=0, wrap=True, selectable=True)
        return columna(
            lbl("CARPETAS COMPARTIDAS (virtiofs)", "nx-h2", xalign=0), self.carpetas_caja,
            fila(boton("+ CARPETA", "nx-chip", self._elegir_carpeta), sp=6),
            self.carpetas_nota, margin_top=8)

    def _fila_carpeta(self, cp: dict):
        self.carpetas_caja.append(FilaCarpeta(cp, self.carpetas_caja.remove))

    def _elegir_carpeta(self):
        dlg = Gtk.FileDialog(title="Carpeta a compartir con la VM")

        def fin(d, res):
            try:
                f = d.select_folder_finish(res)
            except GLib.Error:
                return
            if f and f.get_path():
                self._fila_carpeta({"ruta": f.get_path()})
        dlg.select_folder(self, None, fin)

    def _nota_carpetas(self):
        v = self.vm
        if not v:
            return
        if v.tipo == "macos":
            self.carpetas_nota.set_text("macOS no tiene drivers virtiofs: aquí no se pueden compartir carpetas.")
            return
        self.carpetas_nota.set_text(
            "Dentro de la VM:\n"
            "  Linux:   sudo mount -t virtiofs ETIQUETA /mnt/ETIQUETA\n"
            "           (fija en /etc/fstab:  ETIQUETA  /mnt/ETIQUETA  virtiofs  defaults,nofail  0 0)\n"
            "  Windows: instala virtio-win-guest-tools y WinFsp, y arranca el servicio «VirtIO-FS Service»: "
            "sale como unidad Z:.")

    # ---- RENDIMIENTO
    def _pag_rendimiento(self) -> Gtk.Widget:
        self.c_cpu_modelo = Opciones(M.CPU_MODELOS)
        self.t_cpu_modelo = lbl("MODELO CPU", "nx-dim", xalign=0)
        self.c_nucleos = entrada(f"todos (el equipo tiene 0-{TOTAL_CPUS - 1})", width_chars=32)
        self.c_nucleos.set_tooltip_text("p. ej. 4-7 o 2,3,6,7: cada CPU de la VM se queda en uno de esos núcleos")
        self.c_hp = Gtk.CheckButton(label="Memoria en hugepages (páginas de 2 MB reservadas)")
        self.t_hp = lbl("", "nx-code", xalign=0, wrap=True)
        g = Gtk.Grid(column_spacing=10, row_spacing=6)
        g.attach(self.t_cpu_modelo, 0, 0, 1, 1)
        g.attach(fila(self.c_cpu_modelo), 1, 0, 1, 1)
        g.attach(lbl("NÚCLEOS FIJOS", "nx-dim", xalign=0), 0, 1, 1, 1)
        g.attach(fila(self.c_nucleos), 1, 1, 1, 1)
        g.attach(self.c_hp, 1, 2, 1, 1)
        return columna(
            g, self.t_hp,
            lbl("HOST: la VM ve tu CPU tal cual (lo más rápido). OCULTO: igual, pero sin decir que es una VM "
                "(para programas que se niegan a correr en una). COMPATIBLE: CPU genérica, para mover la VM a otro "
                "equipo. Windows lleva además las mejoras Hyper-V (menos uso de CPU).",
                "nx-code", xalign=0, wrap=True),
            lbl("Núcleos fijos: menos saltos entre núcleos y latencia más estable (juegos, audio). Deja libres "
                "algunos para el equipo.", "nx-code", xalign=0, wrap=True),
            margin_top=8)

    def _pintar_hugepages(self):
        hp = R.hugepages()
        try:
            thp = Path("/sys/kernel/mm/transparent_hugepage/enabled").read_text()
            thp = thp[thp.index("[") + 1:thp.index("]")]
        except (OSError, ValueError):
            thp = "?"
        mb = hp["kb"] // 1024
        self.t_hp.set_text(
            f"Reservadas: {hp['total']} ({hp['total'] * mb // 1024} GB) · libres: {hp['libres']}. "
            f"Se reservan solas al arrancar y se liberan al apagar (con NEURAL//VM abierto). "
            f"Aun sin esto, el kernel ya usa hugepages transparentes ({thp})."
            + ("" if R.AYUDANTE.exists() else " Falta el ayudante: sudo ~/neural-deck/root/instalar.sh."))

    def _confirmar_en(self, b: Gtk.Button, texto: str, accion):
        def pedir(*_):
            pop = Gtk.Popover()
            pop.add_css_class("nx-menu")
            caja = columna(lbl(texto() if callable(texto) else texto, "nx-txt", wrap=True, max_width_chars=34), sp=8,
                           margin_start=6, margin_end=6, margin_top=6, margin_bottom=6)
            caja.append(fila(boton("SÍ", "nx-accion peligro", lambda: (pop.popdown(), accion())),
                             boton("NO", "nx-accion", pop.popdown), sp=6, halign=Gtk.Align.END))
            pop.set_child(caja)
            pop.set_parent(b)
            pop.connect("closed", lambda p: GLib.idle_add(p.unparent))
            pop.popup()
        b.connect("clicked", pedir)

    # ------------------------------------------------------------ lista
    def refrescar(self):
        def trabajo():
            vms = M.listar()
            pr = M.procesos_qemu()
            return vms, {v.dir: v.estado(pr) for v in vms}
        if self._ocupado:
            return
        self._ocupado = True
        en_hilo(trabajo, self._pintar_lista, lambda e: (setattr(self, "_ocupado", False), self.aviso(str(e), True)))

    def _pintar_lista(self, res):
        self._ocupado = False
        vms, estados = res
        nuevas = {v.dir: v for v in vms}
        apagadas = [d for d, e in estados.items() if e == "apagada" and self.estados.get(d, "apagada") != "apagada"]
        if list(nuevas) != list(self.vms):
            while (r := self.lista.get_row_at_index(0)) is not None:
                self.lista.remove(r)
            self.tarjetas = {}
            for d, v in nuevas.items():
                row = Gtk.ListBoxRow(child=Tarjeta(v))
                row.dir = d
                self.lista.append(row)
                self.tarjetas[d] = row
        cambio_sel = self.sel in self.estados and self.estados.get(self.sel) != estados.get(self.sel)
        # no pisar con lo del disco la VM que se está editando
        if self.sel in nuevas and self.sel in self.vms:
            nuevas[self.sel] = self.vms[self.sel]
        self.vms, self.estados = nuevas, estados
        for d, row in self.tarjetas.items():
            row.get_child().poner(nuevas[d], estados[d])
        if self.sel in self.tarjetas:
            self.lista.select_row(self.tarjetas[self.sel])
            if cambio_sel:
                self._pintar_estado()
                self._pintar_avisos()
        elif self.tarjetas:
            self.lista.select_row(next(iter(self.tarjetas.values())))
        else:
            self.sel = None
            self.detalle.set_visible_child_name("nada")
        if any(nuevas[d].cfg.get("hugepages") for d in apagadas if d in nuevas):
            en_hilo(self._liberar_hugepages)
        return False

    def _liberar_hugepages(self):
        """Tras apagar una VM con hugepages: dejar reservadas solo las de las que siguen encendidas."""
        pr = M.procesos_qemu()
        hacen_falta = sum(R.paginas(int(v.cfg["ram_gb"])) for v in M.listar()
                          if v.cfg.get("hugepages") and v.pid(pr) is not None)
        if R.hugepages()["total"] > hacen_falta and R.AYUDANTE.exists():
            R.reservar_hugepages(hacen_falta)

    def _tic(self):
        self.refrescar()
        if self.sel and self.estados.get(self.sel) in ("encendida", "pausada") and not self._capturando:
            self._capturar()
        return True

    def _elegida(self, _lb, row):
        if row is None:
            return
        nueva = row.dir != self.sel
        self.sel = row.dir
        self.detalle.set_visible_child_name("vm")
        if nueva:
            self._pintar_todo()

    @property
    def vm(self) -> M.VM | None:
        return self.vms.get(self.sel)

    def _recargar_vm(self):
        """Releer vm.json de la elegida (tras una operación que lo cambia) y repintar."""
        if self.sel:
            try:
                self.vms[self.sel] = M.VM(self.sel)
            except (OSError, ValueError):
                pass
        self._pintar_todo()
        self.refrescar()

    # ------------------------------------------------------------ pintar detalle
    def _pintar_todo(self):
        v = self.vm
        if not v:
            return
        c = v.cfg
        mac = v.tipo == "macos"
        self.d_nombre.set_text(v.nombre)
        self.d_tipo.set_text(M.TIPOS.get(v.tipo, v.tipo.upper()))
        # general
        self.c_ram.set_value(c["ram_gb"])
        self.c_cpu.set_value(c["cpus"])
        self.c_iso.set_text(c.get("iso", ""))
        self.c_iso_arr.set_active(bool(c.get("arrancar_iso")))
        self.c_drv.set_text(c.get("drivers_iso", ""))
        self.f_drv.set_visible(not mac)
        self.t_drv.set_visible(not mac)
        self.c_uefi.set_active(bool(c.get("uefi", True)))
        self.c_uefi.set_visible(not mac)
        self.c_pxe.set_active(bool(c.get("arrancar_red")))
        self.c_ps2.set_active(bool(c.get("ps2")))
        self.c_ps2.set_visible(not mac)
        self.c_audio.poner(c.get("audio", "auto"))
        # pantalla
        self.c_pantalla.poner(c.get("pantalla", "gtk"))
        self.c_graf.poner(c.get("graficos", "auto"))
        self.c_graf.set_sensitive(not mac)
        self.c_monitores.set_value(int(c.get("pantallas") or 1))
        self.c_vram.set_value(int(c.get("vram_mb") or 0))
        self._notas_pantalla()
        # red
        vaciar(self.redes_caja)
        for r in c.get("redes", []):
            self._fila_red(r)
        vaciar(self.puertos_caja)
        for p in c.get("puertos", []):
            self._fila_puerto(p)
        # discos
        self.c_bus.poner(c.get("bus_disco", "auto"))
        self.c_bus.set_visible(not mac)
        self.t_bus.set_visible(not mac)
        self.c_cache.poner(c.get("cache_disco", "writeback"))
        # usb y carpetas
        self.usb_fijos = [dict(u) for u in c.get("usb", [])]
        vaciar(self.usb_caja)
        vaciar(self.carpetas_caja)
        for cp in c.get("carpetas", []):
            self._fila_carpeta(cp)
        self._nota_carpetas()
        # rendimiento
        self.c_cpu_modelo.poner(c.get("cpu_modelo", "host"))
        self.c_cpu_modelo.set_visible(not mac)
        self.t_cpu_modelo.set_visible(not mac)
        self.c_nucleos.set_text(c.get("nucleos_fijos", ""))
        self.c_hp.set_active(bool(c.get("hugepages")))

        self.d_carpeta.set_text(str(v.dir))
        self._pintar_avisos()
        self.vista.set_paintable(None)
        self._pintar_estado()
        self._cargar_disco()
        pag = self.paginas.get_visible_child_name()
        if pag in ("red", "usb", "rendimiento"):
            self._pestana(pag)

    def _pintar_avisos(self):
        v = self.vm
        if not v:
            return
        en_hilo(lambda: (v.dir, v.comprobar(), v.avisos()), self._poner_avisos)

    def _poner_avisos(self, res):
        d, mal, av = res
        if d == self.sel:
            self.c_aviso.set_text("\n".join(mal))
            self.c_nota.set_text("\n".join(av))
        return False

    def _pintar_estado(self):
        v = self.vm
        if not v:
            return
        e = self.estados.get(v.dir, "apagada")
        self.d_estado.set_text(ESTADOS[e])
        on(self.d_estado, "warn", e == "pausada")
        apagada, qmp_ok = e == "apagada", e in ("encendida", "pausada")
        self.b_arrancar.set_sensitive(apagada)
        self.b_ver.set_visible(v.spice)
        self.b_ver.set_sensitive(qmp_ok)
        self.b_apagar.set_sensitive(qmp_ok)
        self.b_pausa.set_sensitive(qmp_ok)
        self.b_pausa.set_label("REANUDAR" if e == "pausada" else "PAUSA")
        self.b_reset.set_sensitive(qmp_ok)
        self.b_forzar.set_sensitive(not apagada)
        self.b_clonar.set_sensitive(apagada and not v.fisico)
        self.b_borrar.set_sensitive(apagada)
        self.b_snap.set_sensitive(apagada)
        self.snap_nombre.set_sensitive(apagada)
        for b in self.disco_ops:
            b.set_sensitive(apagada)
        for r in list(self.snaps):
            for b in list(r)[1:]:
                b.set_sensitive(apagada)
        self.snap_nota.set_text("" if apagada else "Apaga la VM para crear o restaurar snapshots y tocar discos.")
        if apagada:
            self.vista_txt.set_text("VM apagada")
            self.vista_pila.set_visible_child_name("txt")
        elif e == "ajena":
            self.vista_txt.set_text("Arrancada fuera de NEURAL//VM (sin control QMP).\n"
                                    "Apágala desde dentro y arráncala desde aquí.")
            self.vista_pila.set_visible_child_name("txt")
        elif self.vista.get_paintable() is None:
            self.vista_txt.set_text("Cargando pantalla…")
            self.vista_pila.set_visible_child_name("txt")
            self._capturar()
        if self.paginas.get_visible_child_name() == "usb":
            self._cargar_usb()

    def _cargar_disco(self):
        v = self.vm

        def trabajo():
            extras = {d["archivo"]: v.info_disco(d["archivo"]) for d in v.cfg.get("discos_extra", [])}
            return v.dir, v.disco_info(), v.snapshots(), extras
        en_hilo(trabajo, self._pintar_disco)

    def _pintar_disco(self, res):
        d, info, snaps, extras = res
        if d != self.sel:
            return False
        fis = self.vm.fisico
        if fis:
            self.d_disco.set_text(f"DISCO USB ENTERO · {Path(self.vm.cfg['disco']).name} · "
                                  f"{tam_humano(info['virtual']) if info['virtual'] else 'no conectado'}")
            self.d_nota_disco.set_text("Los cambios se guardan directamente en el disco (sin snapshots). "
                                       "Se desmonta solo al arrancar; con la VM encendida no lo montes ni "
                                       "lo desconectes. Si no arranca, prueba BUS SATA o VIRTIO.")
        else:
            self.d_disco.set_text(f"{self.vm.cfg['disco']} · {tam_humano(info['virtual'])} virtuales · "
                                  f"{tam_humano(info['real'])} usados")
            self.d_nota_disco.set_text("El espacio que se borra dentro de la VM se devuelve solo (TRIM). "
                                       "COMPACTAR recupera el resto.")
        for w in self.solo_qcow + [self.t_tam, self.t_snaps, self.snaps, self.f_snap]:
            w.set_visible(not fis)
        self.c_cache.set_sensitive(not fis)
        self.c_cache.set_tooltip_text("Disco físico: escritura directa, sin caché" if fis else None)
        self.c_ampliar.set_range(max(1, info["virtual"] // 1024 ** 3 + 1), 8192)
        self.c_ampliar.set_value(max(1, info["virtual"] // 1024 ** 3 + 10))
        self._pintar_extras(extras)
        vaciar(self.snaps)
        if not snaps:
            self.snaps.append(lbl("Sin snapshots", "nx-code", xalign=0))
        for s in snaps:
            r = fila(lbl(f"{s['nombre']}  ·  {s['fecha']}", "nx-txt", xalign=0, hexpand=True,
                         ellipsize=Pango.EllipsizeMode.END), sp=4)
            br = boton_solo("RESTAURAR", "nx-chip")
            bb = boton_solo("BORRAR", "nx-chip")
            n = s["nombre"]
            self._confirmar_en(br, f"¿Volver el disco a «{n}»? Se pierde todo lo posterior.",
                               lambda n=n: self._snap_op("restaurar_snapshot", n, f"Restaurada «{n}»"))
            self._confirmar_en(bb, f"¿Borrar la snapshot «{n}»?",
                               lambda n=n: self._snap_op("borrar_snapshot", n, f"Borrada «{n}»"))
            r.append(br)
            r.append(bb)
            self.snaps.append(r)
        self._pintar_estado()
        return False

    def _capturar(self):
        v = self.vm
        if not v:
            return
        self._capturando = True
        destino = CACHE / f"{M.slug(v.nombre)}.png"

        def hecho(_):
            self._capturando = False
            if v.dir == self.sel:
                try:
                    self.vista.set_paintable(Gdk.Texture.new_from_filename(str(destino)))
                    self.vista_pila.set_visible_child_name("img")
                except GLib.Error:
                    pass
            return False

        def fallo(_e):
            self._capturando = False
            return False
        en_hilo(lambda: v.captura(destino), hecho, fallo)

    # ------------------------------------------------------------ acciones
    def _hecho(self, texto):
        def f(_res=None):
            self.aviso(texto)
            self.refrescar()
            return False
        return f

    def _fallo(self, e):
        self.aviso(f"ERROR: {e}", True, 9000)
        self.refrescar()
        return False

    def _arrancar(self):
        v = self.vm
        if v:
            self.aviso(f"Arrancando {v.nombre}…")
            suelta = "Ctrl+Alt+G suelta el ratón" if not v.spice else "Ctrl+Alt suelta el ratón"
            en_hilo(v.arrancar, self._hecho(f"{v.nombre} arrancada · {suelta}"), self._fallo)

    def _ver(self):
        v = self.vm
        if v:
            en_hilo(v.ver, self._hecho("Abriendo la pantalla…"), self._fallo)

    def _qmp(self, metodo, texto):
        v = self.vm
        if v:
            en_hilo(getattr(v, metodo), self._hecho(texto), self._fallo)

    def _texto_reiniciar(self) -> str:
        v = self.vm
        if v and v.cambios_pendientes():
            return ("Hay cambios guardados: la VM se apaga y arranca de nuevo con ellos. "
                    "Si en 40 s no responde se corta la corriente y se pierde lo no guardado.")
        return "¿Reiniciar en seco? Se pierde lo no guardado."

    def _reiniciar(self):
        v = self.vm
        if not v:
            return
        if not v.cambios_pendientes():
            return self._qmp("reiniciar", "Reiniciada")
        self.aviso(f"Aplicando cambios en {v.nombre}…", ms=45000)

        def listo(_):
            self.aviso(f"{v.nombre} arrancada con los cambios")
            self._pintar_avisos()
            self.refrescar()
            return False
        en_hilo(v.reiniciar_aplicando, listo, self._fallo)

    def _apagar(self):
        v = self.vm
        if not v:
            return
        self._qmp("apagar", "Apagando (ACPI)…")

        def revisar():
            if self.estados.get(v.dir) in ("encendida", "pausada"):
                self.aviso(f"{v.nombre} sigue encendida tras 45 s. Instaladores y sistemas antiguos ignoran "
                           "APAGAR: apágala desde dentro o usa FORZAR", True, 12000)
            return False
        GLib.timeout_add_seconds(45, revisar)

    def _pausa(self):
        if self.estados.get(self.sel) == "pausada":
            self._qmp("reanudar", "Reanudada")
        else:
            self._qmp("pausar", "En pausa")

    def _guardar(self):
        v = self.vm
        if not v:
            return
        nucleos = self.c_nucleos.get_text().strip()
        try:
            lista = M.lista_nucleos(nucleos)
        except ValueError as e:
            self.aviso(str(e), True)
            return
        cpus = int(self.c_cpu.get_value())
        if lista and len(lista) < cpus:
            self.aviso(f"NÚCLEOS FIJOS son los números de núcleo del equipo, uno por CPU de la VM: pon {cpus} "
                       f"(p. ej. {TOTAL_CPUS - cpus}-{TOTAL_CPUS - 1}) o déjalo vacío", True, 9000)
            return
        redes = [w.datos() for w in list(self.redes_caja)]
        puertos = [w.datos() for w in list(self.puertos_caja)]
        if len({p["host"] for p in puertos}) != len(puertos):
            self.aviso("Hay dos puertos del equipo repetidos", True)
            return
        carpetas = [w.datos() for w in list(self.carpetas_caja)]
        if len({cp["etiqueta"] for cp in carpetas}) != len(carpetas):
            self.aviso("Dos carpetas con la misma etiqueta", True)
            return
        v.cfg.update(ram_gb=int(self.c_ram.get_value()), cpus=int(self.c_cpu.get_value()),
                     iso=self.c_iso.get_text().strip(), arrancar_iso=self.c_iso_arr.get_active(),
                     arrancar_red=self.c_pxe.get_active(), audio=self.c_audio.clave(),
                     pantalla=self.c_pantalla.clave(), pantallas=int(self.c_monitores.get_value()),
                     vram_mb=int(self.c_vram.get_value()), redes=redes, puertos=puertos,
                     cache_disco=self.c_cache.clave(), usb=list(self.usb_fijos), nucleos_fijos=nucleos,
                     hugepages=self.c_hp.get_active())
        if v.tipo != "macos":
            v.cfg.update(uefi=self.c_uefi.get_active(), ps2=self.c_ps2.get_active(), drivers_iso=self.c_drv.get_text().strip(),
                         graficos=self.c_graf.clave(), bus_disco=self.c_bus.clave(),
                         cpu_modelo=self.c_cpu_modelo.clave(), carpetas=carpetas)
        try:
            v.guardar()
        except OSError as e:
            self._fallo(e)
            return
        self._pintar_avisos()
        self._pintar_estado()
        en_marcha = self.estados.get(v.dir) != "apagada"
        self.aviso("Guardado" + (" · pulsa REINICIAR para aplicarlo" if en_marcha else ""))
        self.refrescar()

    def _elegir_iso(self, destino: Gtk.Entry, marcar_arranque: bool):
        dlg = Gtk.FileDialog(title="Elegir ISO o imagen")
        filtro = Gtk.FileFilter(name="Imágenes (iso, img)")
        for p in ("*.iso", "*.ISO", "*.img", "*.dmg"):
            filtro.add_pattern(p)
        modelo = Gio.ListStore.new(Gtk.FileFilter)
        modelo.append(filtro)
        dlg.set_filters(modelo)

        def fin(d, res):
            try:
                f = d.open_finish(res)
            except GLib.Error:
                return
            if f and f.get_path():
                destino.set_text(f.get_path())
                if marcar_arranque and self.vm and self.vm.tipo == "macos":
                    self.c_iso_arr.set_active(True)
        dlg.open(self, None, fin)

    def _buscar_virtio(self):
        iso = M.buscar_virtio_win()
        if iso:
            self.c_drv.set_text(iso)
            self.aviso(f"Encontrada {Path(iso).name} · GUARDAR CAMBIOS para usarla")
        else:
            self.aviso(f"No hay virtio-win*.iso en {M.ISOS_DIR}", True)

    def _crear_snap(self):
        n = M.slug(self.snap_nombre.get_text()) if self.snap_nombre.get_text().strip() else ""
        if not n:
            self.aviso("Pon un nombre a la snapshot", True)
            return
        self.snap_nombre.set_text("")
        self._snap_op("crear_snapshot", n, f"Snapshot «{n}» creada")

    def _snap_op(self, metodo, nombre, texto):
        v = self.vm
        self.aviso("Trabajando en el disco…")

        def listo(_):
            self.aviso(texto)
            self._cargar_disco()
            return False
        en_hilo(lambda: getattr(v, metodo)(nombre), listo, self._fallo)

    def _quitar(self):
        v = self.vm
        if v:
            M.olvidar(v.dir)
            self.sel = None
            self.aviso(f"{v.nombre} quitada de la lista (carpeta intacta)")
            self.refrescar()

    def _texto_borrar(self) -> str:
        v = self.vm
        if not v:
            return ""
        gb = v.tamano_carpeta() / 1024 ** 3
        fisico = "\nEl disco físico no se toca." if v.fisico else ""
        return (f"¿Borrar «{v.nombre}» para siempre?\nSe elimina {v.dir} completa ({gb:.1f} GB): "
                f"discos, snapshots y todo lo que contenga. No se puede deshacer.{fisico}\n"
                "Las ISO y lo que esté fuera de esa carpeta se quedan.")

    def _borrar(self):
        v = self.vm
        if not v:
            return
        self.aviso(f"Borrando {v.nombre}…")

        def listo(_):
            if self.sel == v.dir:
                self.sel = None
            self.aviso(f"{v.nombre} borrada del SSD")
            self.refrescar()
            return False
        en_hilo(v.borrar, listo, self._fallo)

    def _abrir_log(self):
        v = self.vm
        if v and v.log.exists():
            Gio.AppInfo.launch_default_for_uri(v.log.as_uri(), None)
        else:
            self.aviso("Esta VM aún no tiene log")

    def _anadir_carpeta(self):
        dlg = Gtk.FileDialog(title="Carpeta con vm.json")

        def fin(d, res):
            try:
                f = d.select_folder_finish(res)
            except GLib.Error:
                return
            p = Path(f.get_path())
            if not (p / "vm.json").is_file():
                self.aviso(f"{p} no tiene vm.json", True)
                return
            M.registrar(p)
            self.sel = p.resolve()
            self.refrescar()
        dlg.select_folder(self, None, fin)

    # ------------------------------------------------------------ nueva / importar / clonar
    def _pop_nueva(self) -> Gtk.Popover:
        pop = Gtk.Popover()
        pop.add_css_class("nx-menu")
        self.n_nombre = entrada("nombre (p. ej. debian)")
        self.n_tipo = Gtk.DropDown.new_from_strings([M.TIPOS[t] for t in ("linux", "windows", "otro")])
        self.n_ram = giro(1, max(2, TOTAL_RAM - 2), 4)
        self.n_cpu = giro(1, TOTAL_CPUS, 4)
        self.n_disco = giro(4, 2048, 40)
        self.n_iso = entrada("ISO de instalación (opcional)", hexpand=True)

        def elegir():
            dlg = Gtk.FileDialog(title="ISO de instalación")

            def fin(d, res):
                try:
                    f = d.open_finish(res)
                    self.n_iso.set_text(f.get_path() or "")
                except GLib.Error:
                    pass
                pop.popup()
            pop.popdown()
            dlg.open(self, None, fin)

        g = Gtk.Grid(column_spacing=10, row_spacing=6, margin_start=8, margin_end=8, margin_top=8,
                     margin_bottom=8)
        g.attach(lbl("NUEVA MÁQUINA", "nx-h2", xalign=0), 0, 0, 2, 1)
        for i, (t, w) in enumerate([("Nombre", self.n_nombre), ("Sistema", self.n_tipo),
                                    ("RAM (GB)", self.n_ram), ("CPU", self.n_cpu),
                                    ("Disco (GB)", self.n_disco)], start=1):
            g.attach(lbl(t, "nx-dim", xalign=0), 0, i, 1, 1)
            g.attach(w, 1, i, 1, 1)
        g.attach(lbl("ISO", "nx-dim", xalign=0), 0, 6, 1, 1)
        g.attach(fila(self.n_iso, boton("ELEGIR", "nx-chip", elegir), sp=4), 1, 6, 1, 1)
        g.attach(lbl("El disco qcow2 crece según se usa: no ocupa el tamaño entero. Se abre con la pantalla "
                     "SPICE; Windows toma sola la ISO virtio-win de ~/ISOs.", "nx-code",
                     xalign=0, wrap=True, max_width_chars=40), 0, 7, 2, 1)
        g.attach(boton("CREAR", "nx-accion fuerte", lambda: self._crear(pop)), 0, 8, 2, 1)
        pop.set_child(g)
        return pop

    def _crear(self, pop):
        nombre = self.n_nombre.get_text().strip()
        if not nombre:
            self.aviso("Ponle nombre a la VM", True)
            return
        tipo = ("linux", "windows", "otro")[self.n_tipo.get_selected()]
        args = (nombre, tipo, int(self.n_ram.get_value()), int(self.n_cpu.get_value()),
                int(self.n_disco.get_value()), self.n_iso.get_text().strip())
        pop.popdown()
        self.aviso(f"Creando {nombre}…")

        def listo(vm):
            self.n_nombre.set_text("")
            self.n_iso.set_text("")
            self.sel = vm.dir
            self.aviso(f"{nombre} creada en {vm.dir}")
            self.refrescar()
            return False
        en_hilo(lambda: M.crear(*args), listo, self._fallo)

    def _pop_importar(self) -> Gtk.Popover:
        pop = Gtk.Popover()
        pop.add_css_class("nx-menu")
        self.i_archivo = entrada("disco o .ova", hexpand=True)
        self.i_nombre = entrada("nombre de la VM")
        self.i_tipo = Gtk.DropDown.new_from_strings([M.TIPOS[t] for t in ("linux", "windows", "otro")])
        self.i_ram = giro(1, max(2, TOTAL_RAM - 2), 4)
        self.i_cpu = giro(1, TOTAL_CPUS, 4)
        self.i_uefi = Gtk.CheckButton(label="UEFI (si la VM original lo usaba)")

        def elegir():
            dlg = Gtk.FileDialog(title="Disco a importar")
            filtro = Gtk.FileFilter(name="Discos de VM (ova, vdi, vmdk, vhd, vhdx, qcow2, img)")
            for p in M.FORMATOS_IMPORTAR:
                filtro.add_pattern(p)
                filtro.add_pattern(p.upper())
            modelo = Gio.ListStore.new(Gtk.FileFilter)
            modelo.append(filtro)
            dlg.set_filters(modelo)

            def fin(d, res):
                try:
                    f = d.open_finish(res)
                    ruta = f.get_path() or ""
                    self.i_archivo.set_text(ruta)
                    if ruta and not self.i_nombre.get_text().strip():
                        self.i_nombre.set_text(Path(ruta).stem)
                except GLib.Error:
                    pass
                pop.popup()
            pop.popdown()
            dlg.open(self, None, fin)

        g = Gtk.Grid(column_spacing=10, row_spacing=6, margin_start=8, margin_end=8, margin_top=8,
                     margin_bottom=8)
        g.attach(lbl("IMPORTAR MÁQUINA", "nx-h2", xalign=0), 0, 0, 2, 1)
        g.attach(lbl("Archivo", "nx-dim", xalign=0), 0, 1, 1, 1)
        g.attach(fila(self.i_archivo, boton("ELEGIR", "nx-chip", elegir), sp=4), 1, 1, 1, 1)
        for i, (t, w) in enumerate([("Nombre", self.i_nombre), ("Sistema", self.i_tipo),
                                    ("RAM (GB)", self.i_ram), ("CPU", self.i_cpu)], start=2):
            g.attach(lbl(t, "nx-dim", xalign=0), 0, i, 1, 1)
            g.attach(w, 1, i, 1, 1)
        g.attach(self.i_uefi, 1, 6, 1, 1)
        g.attach(lbl("Se convierte a qcow2 en ~/vms (el original no se toca). Puede tardar varios minutos.",
                     "nx-code", xalign=0, wrap=True, max_width_chars=40), 0, 7, 2, 1)
        g.attach(boton("IMPORTAR", "nx-accion fuerte", lambda: self._importar(pop)), 0, 8, 2, 1)
        pop.set_child(g)
        return pop

    def _importar(self, pop):
        archivo, nombre = self.i_archivo.get_text().strip(), self.i_nombre.get_text().strip()
        if not archivo or not nombre:
            self.aviso("Elige el archivo y ponle nombre", True)
            return
        tipo = ("linux", "windows", "otro")[self.i_tipo.get_selected()]
        args = (archivo, nombre, tipo, int(self.i_ram.get_value()), int(self.i_cpu.get_value()),
                self.i_uefi.get_active())
        pop.popdown()
        self.aviso(f"Importando {nombre}… (puede tardar)", ms=600000)

        def listo(vm):
            self.i_archivo.set_text("")
            self.i_nombre.set_text("")
            self.sel = vm.dir
            self.aviso(f"{nombre} importada en {vm.dir}")
            self.refrescar()
            return False
        en_hilo(lambda: M.importar(*args), listo, self._fallo)

    def _pop_usb_disco(self) -> Gtk.Popover:
        pop = Gtk.Popover()
        pop.add_css_class("nx-menu")
        self.u_discos: list[dict] = []
        self.u_lista = Gtk.DropDown.new_from_strings(["(buscando…)"])
        self.u_lista.set_hexpand(True)
        self.u_info = lbl("", "nx-code", xalign=0, wrap=True, max_width_chars=44)
        self.u_nombre = entrada("nombre de la VM")
        self.u_tipo = Gtk.DropDown.new_from_strings([M.TIPOS[t] for t in ("linux", "windows", "otro")])
        self.u_ram = giro(1, max(2, TOTAL_RAM - 2), 8)
        self.u_cpu = giro(1, TOTAL_CPUS, 4)

        def info(*_):
            i = self.u_lista.get_selected()
            if not self.u_discos or i >= len(self.u_discos):
                self.u_info.set_text("Conecta el disco USB y pulsa BUSCAR.")
                return
            d = self.u_discos[i]
            partes = []
            if d["particiones"]:
                partes.append("Particiones: " + ", ".join(d["particiones"]))
            if d["montadas"]:
                partes.append("Montado en " + ", ".join(d["montadas"]) + ": se desmonta al arrancar la VM.")
            if not d["acceso"]:
                partes.append("Sin permiso todavía: ejecuta  sudo ~/neural-deck/root/instalar.sh  y vuelve a "
                              "conectar el disco.")
            if d["sistema"]:
                partes.append("Es parte de este sistema: no se puede usar.")
            self.u_info.set_text("\n".join(partes))
            if not self.u_nombre.get_text().strip():
                etiqueta = next((p.split(" ", 1)[1] for p in d["particiones"] if " " in p), "")
                self.u_nombre.set_text((etiqueta.split("_")[0] if etiqueta else "usb").lower())

        def buscar():
            self.u_discos = R.discos_usb()
            textos = [f"{d['nombre']} · {tam_humano(d['tam'])} · {d['dev']}" for d in self.u_discos]
            self.u_lista.set_model(Gtk.StringList.new(textos or ["(ningún disco USB)"]))
            info()
        self.u_lista.connect("notify::selected", info)

        g = Gtk.Grid(column_spacing=10, row_spacing=6, margin_start=8, margin_end=8, margin_top=8,
                     margin_bottom=8)
        g.attach(lbl("ARRANCAR DESDE DISCO USB", "nx-h2", xalign=0), 0, 0, 2, 1)
        g.attach(lbl("Disco", "nx-dim", xalign=0), 0, 1, 1, 1)
        g.attach(fila(self.u_lista, boton("BUSCAR", "nx-chip", buscar), sp=4), 1, 1, 1, 1)
        g.attach(self.u_info, 0, 2, 2, 1)
        for i, (t, w) in enumerate([("Nombre", self.u_nombre), ("Sistema", self.u_tipo),
                                    ("RAM (GB)", self.u_ram), ("CPU", self.u_cpu)], start=3):
            g.attach(lbl(t, "nx-dim", xalign=0), 0, i, 1, 1)
            g.attach(w, 1, i, 1, 1)
        g.attach(lbl("Para un sistema YA INSTALADO en el disco (no una ISO). La VM usa el disco entero y "
                     "todo lo que cambies se guarda en él: luego puedes arrancarlo también en un equipo real.",
                     "nx-code", xalign=0, wrap=True, max_width_chars=44), 0, 7, 2, 1)
        g.attach(boton("CREAR", "nx-accion fuerte", lambda: self._desde_usb(pop)), 0, 8, 2, 1)
        pop.set_child(g)
        pop.connect("show", lambda *_: buscar())
        return pop

    def _desde_usb(self, pop):
        i, nombre = self.u_lista.get_selected(), self.u_nombre.get_text().strip()
        if not self.u_discos or i >= len(self.u_discos):
            self.aviso("Conecta el disco USB y pulsa BUSCAR", True)
            return
        if not nombre:
            self.aviso("Ponle nombre a la VM", True)
            return
        d = self.u_discos[i]
        tipo = ("linux", "windows", "otro")[self.u_tipo.get_selected()]
        args = (d["id"], nombre, tipo, int(self.u_ram.get_value()), int(self.u_cpu.get_value()))
        pop.popdown()

        def listo(vm):
            self.u_nombre.set_text("")
            self.sel = vm.dir
            self.aviso(f"{nombre} creada: usa el disco USB {d['nombre']}")
            self.refrescar()
            return False
        en_hilo(lambda: M.desde_disco(*args), listo, self._fallo)

    def _pop_clonar(self) -> Gtk.Popover:
        pop = Gtk.Popover()
        pop.add_css_class("nx-menu")
        self.cl_nombre = entrada("nombre de la copia", width_chars=24)
        caja = columna(lbl("CLONAR MÁQUINA", "nx-h2", xalign=0), self.cl_nombre,
                       lbl("Copia entera (discos, snapshots, UEFI) con MAC nuevas.", "nx-code", xalign=0, wrap=True,
                           max_width_chars=34),
                       boton("CLONAR", "nx-accion fuerte", lambda: self._clonar(pop)),
                       sp=8, margin_start=8, margin_end=8, margin_top=8, margin_bottom=8)
        pop.set_child(caja)
        pop.connect("show", lambda *_: self.cl_nombre.set_text(f"{self.vm.nombre} copia" if self.vm else ""))
        return pop

    def _clonar(self, pop):
        v, nombre = self.vm, self.cl_nombre.get_text().strip()
        if not v or not nombre:
            return
        pop.popdown()
        self.aviso(f"Clonando {v.nombre}… (puede tardar)", ms=600000)

        def listo(vm):
            self.sel = vm.dir
            self.aviso(f"{nombre} creada en {vm.dir}")
            self.refrescar()
            return False
        en_hilo(lambda: v.clonar(nombre), listo, self._fallo)


class App(AppNeural):
    def __init__(self):
        super().__init__(APP_ID)

    def css_extra(self, t):
        return (f".nx-tag.on {{ color: {css_color(t['alt'])}; }}\n"
                f"spinbutton.nx-entry {{ min-height: 24px; }}\n"
                f"checkbutton {{ color: {css_color(t['text'])}; }}\n")

    def crear_ventana(self, args, cwd):
        ventanas = self.get_windows()
        if ventanas:
            self.traer(ventanas[0])
            return
        Ventana(self).present()


if __name__ == "__main__":
    nombrar_proceso(PROC)
    GLib.set_prgname(APP_ID)
    sys.exit(App().run(sys.argv))
