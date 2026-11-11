#!/usr/bin/env python3
"""NEURAL//PAK: gestor visual de Flatpak (instalación de usuario, Flathub).

Pestañas:
  INSTALADAS       apps instaladas (abrir, desinstalar, permisos) + runtimes
  ACTUALIZACIONES  busca novedades (refresca el catálogo) y actualiza una o todas
  PARA TI          recomendaciones elegidas según lo que haces
  EXPLORAR         todo Flathub desde el catálogo AppStream local (sin red)

Las operaciones (instalar/actualizar/desinstalar) van en cola, de una en una,
con barra de progreso y registro. No necesita root (todo es --user).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ventana import AppNeural, VentanaNeural, boton, lbl, nombrar_proceso, on  # noqa: E402
from estilo import css_color  # noqa: E402

APP_ID = "org.local.NeuralPak"
PROC = "neural-pak"
REMOTO = "flathub"
AS_DIR = Path.home() / ".local/share/flatpak/appstream" / REMOTO / "x86_64" / "active"
CACHE = Path.home() / ".cache/neural-pak"
XL = "{http://www.w3.org/XML/1998/namespace}lang"
MAX_RES = 120

CATEGORIAS = [("Development", "DESARROLLO"), ("Graphics", "GRÁFICOS"), ("AudioVideo", "AUDIO/VÍDEO"),
              ("Game", "JUEGOS"), ("Office", "OFICINA"), ("Network", "RED"), ("System", "SISTEMA"),
              ("Utility", "UTILIDADES"), ("Security", "SEGURIDAD"), ("Education", "EDUCACIÓN"),
              ("Science", "CIENCIA")]

# Recomendaciones generales, agrupadas por uso.
RECOMENDADAS = [
    ("ESENCIALES", [
        ("com.github.tchx84.Flatseal", "Ver y recortar los permisos de cada Flatpak; útil en un sistema endurecido."),
        ("io.missioncenter.MissionCenter", "Monitor de CPU, GPU, RAM y procesos tipo administrador de tareas."),
        ("org.localsend.localsend_app", "Pasar archivos entre equipos y móviles por la LAN, sin nube."),
        ("io.gitlab.adhami3310.Impression", "Grabar ISOs en un USB: instaladores y sistemas de rescate."),
    ]),
    ("DESARROLLO", [
        ("com.google.AndroidStudio", "Desarrollo de apps Android (Java/Kotlin)."),
        ("org.godotengine.Godot", "Motor de juegos 2D y 3D libre."),
        ("com.vscodium.codium", "Editor ligero sin telemetría para Python/C++ de NEURAL//DECK."),
        ("org.zealdocs.Zeal", "Documentación offline (Python, GTK, C++, Kotlin): útil cuando no hay red."),
        ("com.github.marhkb.Pods", "Interfaz para contenedores Podman."),
    ]),
    ("SEGURIDAD", [
        ("org.wireshark.Wireshark", "Analizar capturas .pcap (el Flatpak NO captura: graba con tcpdump)."),
        ("org.nmap.Zenmap", "Nmap con interfaz: mapear la red y comprobar los puertos abiertos."),
        ("org.ghidra_sre.Ghidra", "Ingeniería inversa para CTF y para analizar binarios."),
        ("org.keepassxc.KeePassXC", "Contraseñas en un archivo cifrado local, sin servidor."),
        ("com.belmoussaoui.Authenticator", "Códigos 2FA (TOTP) en el escritorio."),
        ("io.github.mpobaschnig.Vaults", "Carpetas cifradas (gocryptfs/CryFS) que se montan cuando las necesitas."),
    ]),
    ("HOMELAB", [
        ("org.remmina.Remmina", "SSH, RDP y VNC a servidores y VMs desde una sola app."),
        ("org.jellyfin.JellyfinDesktop", "Cliente de escritorio para un servidor Jellyfin."),
        ("com.moonlight_stream.Moonlight", "Jugar en streaming desde otro PC de la red."),
        ("org.virt_manager.virt-manager", "Gestionar VMs con libvirt/QEMU por SSH."),
    ]),
    ("IA LOCAL", [
        ("com.jeffser.Alpaca", "Chat con modelos locales (Ollama); para probar modelos sin nube."),
        ("ai.jan.Jan", "Asistente 100 % offline con API compatible con OpenAI en localhost."),
        ("ai.lmstudio.lm-studio", "Descargar y comparar modelos GGUF antes de llevarlos al llama-server."),
    ]),
    ("ESCRITURA", [
        ("org.gnome.gitlab.somas.Apostrophe", "Markdown sin distracciones."),
        ("md.obsidian.Obsidian", "Notas enlazadas en archivos Markdown locales."),
        ("org.onlyoffice.desktopeditors", "Documentos, hojas de cálculo y presentaciones compatibles con Office."),
    ]),
    ("CREAR", [
        ("org.blender.Blender", "Modelado, animación y render 3D."),
        ("org.kde.krita", "Pintura digital: arte, texturas y portadas."),
        ("org.inkscape.Inkscape", "Iconos SVG como los de NEURAL//DECK."),
        ("io.mpv.Mpv", "Reproductor ligero, va bien con Wayland."),
        ("com.obsproject.Studio", "Grabar o transmitir la pantalla."),
    ]),
]

# Permisos de Flatpak → (texto, ¿delicado?)
PERMISOS = {
    "network": ("Red / Internet", False), "ipc": ("IPC compartido con el sistema", False),
    "x11": ("X11 (puede ver otras ventanas X)", True), "fallback-x11": ("X11 de respaldo", False),
    "wayland": ("Wayland", False), "pulseaudio": ("Audio (micrófono y altavoces)", False),
    "pcsc": ("Lector de tarjetas", True), "cups": ("Impresoras", False), "ssh-auth": ("Agente SSH", True),
    "gpg-agent": ("Agente GPG", True), "system-bus": ("Bus de sistema D-Bus COMPLETO", True),
    "session-bus": ("Bus de sesión D-Bus COMPLETO", True), "inherit-wayland-socket": ("Socket Wayland heredado", False),
    "dri": ("GPU (aceleración)", False), "all": ("TODOS los dispositivos (cámara, USB…)", True),
    "kvm": ("KVM (virtualización)", False), "shm": ("Memoria compartida", False), "input": ("Dispositivos de entrada", True),
    "usb": ("USB", True), "bluetooth": ("Bluetooth", False), "devel": ("Depuración (ptrace)", True),
    "multiarch": ("Binarios de 32 bits", False), "canbus": ("Bus CAN", False),
}


# ---------------------------------------------------------------- catálogo
def _texto(el) -> str:
    return re.sub(r"\s+", " ", "".join(el.itertext())).strip() if el is not None else ""


def _localizado(el, tag) -> str:
    mejor, base = None, None
    for e in el.findall(tag):
        lang = e.get(XL)
        if lang is None:
            base = base if base is not None else e
        elif lang == "es":
            mejor = e
        elif lang.startswith("es") and mejor is None:
            mejor = e
    return _texto(mejor if mejor is not None else base)


def _descripcion(el) -> str:
    elegido, base = None, None
    for d in el.findall("description"):
        lang = d.get(XL)
        if lang is None:
            base = base if base is not None else d
        elif lang.startswith("es") and elegido is None:
            elegido = d
    d = elegido if elegido is not None else base
    if d is None:
        return ""
    partes = []
    if d.text and d.text.strip():
        partes.append(re.sub(r"\s+", " ", d.text).strip())
    for h in d:
        if h.tag in ("ul", "ol"):
            partes.append("\n".join("•  " + _texto(li) for li in h if li.tag == "li"))
        else:
            partes.append(_texto(h))
    return "\n\n".join(p for p in partes if p)


def parsear_catalogo(xml: Path) -> list[dict]:
    apps = []
    for _ev, el in ET.iterparse(xml, events=("end",)):
        if el.tag != "component":
            continue
        try:
            if el.get("type") not in ("desktop-application", "console-application", "desktop"):
                continue
            b = el.find("bundle")
            ref = (b.text or "").split("/") if b is not None else []
            if len(ref) < 4 or ref[0] != "app":
                continue
            icono = ""
            for tam in ("128", "64"):
                for ic in el.findall("icon"):
                    if ic.get("type") == "cached" and ic.get("height") == tam and not ic.get("scale"):
                        icono = f"{tam}x{tam}/{ic.text}"
                        break
                if icono:
                    break
            rel = el.find("releases/release")
            dev = el.find("developer/name")
            web = next((u.text for u in el.findall("url") if u.get("type") == "homepage"), "")
            ver = el.find(".//custom/value[@key='flathub::verification::verified']")
            apps.append({
                "id": ref[1], "rama": ref[3], "nombre": _localizado(el, "name") or ref[1],
                "resumen": _localizado(el, "summary"), "desc": _descripcion(el),
                "icono": icono, "dev": _texto(dev) or _texto(el.find("developer_name")),
                "lic": _texto(el.find("project_license")), "web": web or "",
                "cats": [c.text for c in el.findall("categories/category") if c.text],
                "kw": [k.text.lower() for k in el.findall("keywords/keyword") if k.text and k.get(XL) is None],
                "ver": rel.get("version", "") if rel is not None else "",
                "ts": int(rel.get("timestamp", "0") or 0) if rel is not None else 0,
                "verif": ver is not None and (ver.text or "").strip() == "true",
            })
        finally:
            el.clear()
    return apps


def cargar_catalogo() -> list[dict]:
    """Del caché JSON si sigue valiendo; si no, parsea el XML (unos segundos)."""
    try:
        clave = os.path.realpath(AS_DIR)
    except OSError:
        return []
    cache = CACHE / "catalogo.json"
    try:
        d = json.loads(cache.read_text())
        if d.get("clave") == clave:
            return d["apps"]
    except (OSError, json.JSONDecodeError, KeyError):
        pass
    xml = AS_DIR / "appstream.xml"
    if not xml.exists():
        gz = AS_DIR / "appstream.xml.gz"
        if not gz.exists():
            return []
        import gzip
        with gzip.open(gz) as f:
            apps = parsear_catalogo(f)
    else:
        apps = parsear_catalogo(xml)
    CACHE.mkdir(parents=True, exist_ok=True)
    tmp = cache.with_suffix(".tmp")
    tmp.write_text(json.dumps({"clave": clave, "apps": apps}, ensure_ascii=False))
    tmp.replace(cache)
    return apps


def _tabla(salida: str, campos: list[str]) -> list[dict]:
    filas = []
    for linea in salida.splitlines():
        if not linea.strip():
            continue
        v = linea.split("\t")
        filas.append({k: (v[i].strip() if i < len(v) else "") for i, k in enumerate(campos)})
    return filas


def leer_permisos(texto: str) -> list[tuple[str, str, bool]]:
    """[Context] etc. → [(grupo, texto, delicado)]"""
    out, seccion = [], ""
    for linea in texto.splitlines():
        linea = linea.strip()
        if linea.startswith("["):
            seccion = linea.strip("[]")
            continue
        if "=" not in linea:
            continue
        k, v = linea.split("=", 1)
        vals = [x for x in v.split(";") if x]
        if seccion == "Context":
            if k in ("shared", "sockets", "devices", "features"):
                for x in vals:
                    neg = x.startswith("!")
                    txt, delic = PERMISOS.get(x.lstrip("!"), (x, False))
                    out.append(("ACCESO", ("SIN " if neg else "") + txt, delic and not neg))
            elif k == "filesystems":
                for x in vals:
                    base = x.split(":")[0]
                    ro = x.endswith(":ro")
                    nombre = {"host": "TODO el sistema de archivos", "host-os": "Sistema operativo del anfitrión",
                              "host-etc": "/etc del anfitrión", "home": "TODA tu carpeta personal",
                              "xdg-download": "Descargas", "xdg-documents": "Documentos",
                              "xdg-pictures": "Imágenes", "xdg-music": "Música", "xdg-videos": "Vídeos",
                              "xdg-desktop": "Escritorio"}.get(base, base)
                    delic = base in ("host", "host-os", "host-etc", "home") and not ro
                    out.append(("ARCHIVOS", nombre + (" (solo lectura)" if ro else ""), delic))
            elif k == "persistent":
                for x in vals:
                    out.append(("ARCHIVOS", f"~/{x} (privado de la app)", False))
        elif seccion in ("Session Bus Policy", "System Bus Policy"):
            grupo = "D-BUS SESIÓN" if seccion.startswith("Session") else "D-BUS SISTEMA"
            out.append((grupo, f"{k} ({v})", seccion.startswith("System")))
        elif seccion == "Environment":
            out.append(("ENTORNO", f"{k}={v}", False))
    return out


# ---------------------------------------------------------------- utilidades
def _icono(app: dict | None, fid: str, px: int) -> Gtk.Image:
    img = Gtk.Image(pixel_size=px)
    ruta = AS_DIR / "icons" / app["icono"] if app and app.get("icono") else None
    if ruta and ruta.exists():
        img.set_from_file(str(ruta))
    else:
        tema = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
        img.set_from_icon_name(fid if tema.has_icon(fid) else "application-x-executable")
    return img


def ejecutar(argv: list[str], al_terminar) -> None:
    """Corre argv sin bloquear; al_terminar(ok, salida)."""
    try:
        p = Gio.Subprocess.new(argv, Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_MERGE)
    except GLib.Error as e:
        al_terminar(False, e.message)
        return

    def fin(proc, res):
        try:
            _ok, out, _err = proc.communicate_utf8_finish(res)
        except GLib.Error as e:
            al_terminar(False, e.message)
            return
        al_terminar(proc.get_successful(), out or "")

    p.communicate_utf8_async(None, None, fin)


# ---------------------------------------------------------------- ventana
class Ventana(VentanaNeural):
    def __init__(self, app: "App"):
        super().__init__(app, "SYS//PKG", "NEURAL//PAK", (1120, 720))
        self.catalogo: list[dict] = []
        self.por_id: dict[str, dict] = {}
        self.instaladas: dict[str, dict] = {}
        self.runtimes: list[dict] = []
        self.updates: dict[str, dict] = {}
        self.updates_ts = 0.0
        self.sel: str | None = None
        self.categoria: str | None = None
        self.cola: list[tuple[str, list[str], str]] = []
        self.op: Gio.Subprocess | None = None
        self._busca_id = 0

        # pestañas + buscador
        self.tabs = {}
        for clave, txt in (("inst", "INSTALADAS"), ("upd", "ACTUALIZACIONES"), ("rec", "PARA TI"),
                           ("exp", "EXPLORAR")):
            b = boton(txt, "nx-tab", lambda c=clave: self.ir(c))
            self.tabs[clave] = b
            self.cab_centro.append(b)
        hueco = Gtk.Box(hexpand=True)
        self.cab_centro.append(hueco)
        self.entrada = Gtk.SearchEntry(placeholder_text="buscar en Flathub…", width_chars=8, max_width_chars=26,
                                      hexpand=True)
        self.entrada.add_css_class("nx-entry")
        self.entrada.connect("search-changed", lambda *_: self._programar_busqueda())
        self.cab_centro.append(self.entrada)

        # contenido | detalle
        fila = Gtk.Box(vexpand=True)
        self.cuerpo.append(fila)
        self.pila = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=120,
                              hexpand=True, vexpand=True)
        fila.append(self.pila)
        self.paginas: dict[str, Gtk.Box] = {}
        for clave in self.tabs:
            sc = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
            sc.add_css_class("nx-scroll")
            caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=8, margin_end=8)
            sc.set_child(caja)
            self.pila.add_named(sc, clave)
            self.paginas[clave] = caja
        self.lado = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_LEFT, transition_duration=160)
        sc = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, width_request=380)
        sc.add_css_class("nx-scroll")
        self.lado_caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.lado_caja.add_css_class("nx-lado")
        sc.set_child(self.lado_caja)
        self.lado.set_child(sc)
        fila.append(self.lado)

        # operaciones en curso
        self.ops = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_UP, transition_duration=140)
        ops = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=6)
        f = Gtk.Box(spacing=10)
        self.op_txt = lbl("", "nx-code on", xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        self.op_cola = lbl("", "nx-code", xalign=1)
        f.append(self.op_txt)
        f.append(self.op_cola)
        f.append(boton("REGISTRO", "nx-chip", self._ver_log))
        self.b_cancelar = boton("CANCELAR", "nx-chip", self._cancelar)
        f.append(self.b_cancelar)
        ops.append(f)
        self.prog = Gtk.ProgressBar()
        self.prog.add_css_class("nx-prog")
        ops.append(self.prog)
        self.log_rev = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN)
        lsc = Gtk.ScrolledWindow(min_content_height=140)
        lsc.add_css_class("nx-scroll")
        self.log = Gtk.TextView(editable=False, cursor_visible=False, monospace=True,
                                wrap_mode=Gtk.WrapMode.CHAR)
        self.log.add_css_class("nx-log")
        lsc.set_child(self.log)
        self.log_rev.set_child(lsc)
        ops.append(self.log_rev)
        self.ops.set_child(ops)
        self.cuerpo.append(self.ops)

        teclas = Gtk.EventControllerKey()
        teclas.connect("key-pressed", self._tecla)
        self.add_controller(teclas)
        self.connect("close-request", self._al_cerrar)

        self.ir("inst")
        self.aviso("Cargando catálogo de Flathub…", ms=60000)
        threading.Thread(target=self._hilo_catalogo, daemon=True).start()
        self.refrescar_instaladas()

    # ---------------- datos ----------------
    def _hilo_catalogo(self):
        t0 = time.time()
        try:
            apps = cargar_catalogo()
        except Exception as e:  # catálogo roto → seguimos con lo instalado
            print(f"pak: catálogo: {e}", flush=True)
            apps = []
        GLib.idle_add(self._catalogo_listo, apps, time.time() - t0)

    def _catalogo_listo(self, apps, seg):
        self.catalogo = apps
        self.por_id = {a["id"]: a for a in apps}
        self.aviso(f"Catálogo: {len(apps)} apps ({seg:.1f} s)")
        self.redibujar()
        return False

    def refrescar_instaladas(self, luego=None):
        campos = ["id", "nombre", "version", "rama", "origen", "inst", "tam"]

        def apps_listas(ok, out):
            self.instaladas = {f["id"]: f for f in _tabla(out, campos)} if ok else {}
            ejecutar(["flatpak", "list", "--runtime", "--columns=application,branch,size"], rt_listos)

        def rt_listos(ok, out):
            self.runtimes = _tabla(out, ["id", "rama", "tam"]) if ok else []
            self.redibujar()
            if luego:
                luego()

        ejecutar(["flatpak", "list", "--app", "--columns=application,name,version,branch,origin,installation,size"],
                 apps_listas)

    def buscar_updates(self, refrescar_catalogo=True):
        self.aviso("Buscando actualizaciones…", ms=60000)
        self.updates_buscando = True
        self.redibujar_pagina("upd")

        def listar(*_):
            ejecutar(["flatpak", "remote-ls", "--updates", "--user",
                      "--columns=application,name,version,branch,download-size"], listo)

        def listo(ok, out):
            self.updates_buscando = False
            if not ok:
                self.aviso("No se pudo consultar Flathub (¿sin red?)", warn=True)
                self.updates = {}
            else:
                self.updates = {f["id"]: f for f in _tabla(out, ["id", "nombre", "version", "rama", "bajada"])}
                self.updates_ts = time.time()
                n = len(self.updates)
                self.aviso(f"{n} actualización{'es' if n != 1 else ''} disponible{'s' if n != 1 else ''}"
                           if n else "Todo al día")
            self.redibujar()

        if refrescar_catalogo:
            # baja también el appstream nuevo (versiones, apps nuevas)
            ejecutar(["flatpak", "update", "--appstream", "--user", REMOTO], lambda ok, _o: (
                ok and threading.Thread(target=self._hilo_catalogo, daemon=True).start(), listar()))
        else:
            listar()

    # ---------------- navegación ----------------
    def ir(self, clave: str):
        self.actual = clave
        for k, b in self.tabs.items():
            on(b, "on", k == clave)
        self.pila.set_visible_child_name(clave)
        if clave == "upd" and not self.updates_ts and not getattr(self, "updates_buscando", False):
            self.buscar_updates()
        self.redibujar_pagina(clave)

    def _programar_busqueda(self):
        if self._busca_id:
            GLib.source_remove(self._busca_id)
        self._busca_id = GLib.timeout_add(180, self._buscar)

    def _buscar(self):
        self._busca_id = 0
        if self.actual != "exp":
            self.ir("exp")
        else:
            self.redibujar_pagina("exp")
        return False

    def _tecla(self, _c, kv, _code, estado):
        ctrl = estado & Gdk.ModifierType.CONTROL_MASK
        if ctrl and kv in (Gdk.KEY_f, Gdk.KEY_F):
            self.entrada.grab_focus()
            return True
        if kv == Gdk.KEY_Escape:
            if self.lado.get_reveal_child():
                self.cerrar_detalle()
                return True
        if ctrl and Gdk.KEY_1 <= kv <= Gdk.KEY_4:
            self.ir(list(self.tabs)[kv - Gdk.KEY_1])
            return True
        if kv == Gdk.KEY_F5:
            self.refrescar_instaladas()
            self.buscar_updates()
            return True
        return False

    # ---------------- dibujo ----------------
    def redibujar(self):
        n_upd = len(self.updates)
        self.tabs["inst"].set_label(f"INSTALADAS · {len(self.instaladas)}")
        self.tabs["upd"].set_label(f"ACTUALIZACIONES · {n_upd}" if n_upd else "ACTUALIZACIONES")
        on(self.tabs["upd"], "warn", n_upd > 0)
        self.p_izq.set_text(f"{REMOTO.upper()} · USER  //  {len(self.instaladas)} APPS · {len(self.runtimes)} RUNTIMES"
                            f"  //  CATÁLOGO {len(self.catalogo)}"
                            + (f"  //  REVISADO {time.strftime('%H:%M', time.localtime(self.updates_ts))}"
                               if self.updates_ts else ""))
        self.redibujar_pagina(self.actual)
        if self.sel:
            self.mostrar_detalle(self.sel)

    def _vaciar(self, caja: Gtk.Box):
        while (h := caja.get_first_child()) is not None:
            caja.remove(h)

    def redibujar_pagina(self, clave: str):
        caja = self.paginas[clave]
        self._vaciar(caja)
        getattr(self, f"_pagina_{clave}")(caja)

    def _seccion(self, caja, titulo: str, extra: Gtk.Widget | None = None):
        f = Gtk.Box(spacing=10, margin_top=10, margin_bottom=2)
        f.append(lbl(titulo, "nx-h2", xalign=0, hexpand=True))
        if extra is not None:
            f.append(extra)
        caja.append(f)
        sep = Gtk.Box()
        sep.add_css_class("nx-sep")
        caja.append(sep)

    def _vacio(self, caja, texto):
        caja.append(lbl(texto, "nx-dim", margin_top=40, wrap=True, justify=Gtk.Justification.CENTER))

    def tarjeta(self, fid: str, motivo: str | None = None) -> Gtk.Widget:
        app = self.por_id.get(fid)
        inst = self.instaladas.get(fid)
        upd = self.updates.get(fid)
        c = Gtk.Box(spacing=12)
        c.add_css_class("nx-card")
        on(c, "sel", fid == self.sel)
        c.append(_icono(app, fid, 44))
        t = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1, hexpand=True, valign=Gtk.Align.CENTER)
        f = Gtk.Box(spacing=6)
        nombre = (inst or {}).get("nombre") or (app or {}).get("nombre") or fid
        f.append(lbl(nombre, "nx-txt", xalign=0, ellipsize=Pango.EllipsizeMode.END))
        if app and app.get("verif"):
            f.append(lbl("✓ VERIFICADA", "nx-tag", valign=Gtk.Align.CENTER))
        if inst and motivo is not None:
            f.append(lbl("INSTALADA", "nx-tag acc", valign=Gtk.Align.CENTER))
        if upd:
            f.append(lbl("ACTUALIZAR", "nx-tag warn", valign=Gtk.Align.CENTER))
        t.append(f)
        sub = motivo or (app or {}).get("resumen") or fid
        t.append(lbl(sub, "nx-dim", xalign=0, ellipsize=Pango.EllipsizeMode.END))
        if inst:
            meta = f"{fid}  ·  v{inst['version'] or '?'}  ·  {inst['tam']}"
        else:
            meta = f"{fid}" + (f"  ·  v{app['ver']}" if app and app.get("ver") else "")
        t.append(lbl(meta, "nx-code", xalign=0, ellipsize=Pango.EllipsizeMode.END))
        c.append(t)
        acciones = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
        if upd:
            acciones.append(boton("ACTUALIZAR", "nx-accion fuerte", lambda: self.actualizar(fid)))
        if inst:
            acciones.append(boton("ABRIR", "nx-accion", lambda: self.abrir(fid)))
        elif app:
            ocupado = self._en_cola(fid)
            b = boton("EN COLA" if ocupado else "INSTALAR", "nx-accion fuerte", lambda: self.instalar(fid))
            b.set_sensitive(not ocupado)
            acciones.append(b)
        c.append(acciones)
        g = Gtk.GestureClick()
        g.connect("released", lambda *_: self.mostrar_detalle(fid))
        c.add_controller(g)
        c.set_cursor_from_name("pointer")
        return c

    def _pagina_inst(self, caja):
        if not self.instaladas:
            return self._vacio(caja, "No hay apps Flatpak instaladas.\nMira PARA TI o EXPLORAR.")
        self._seccion(caja, f"APLICACIONES · {len(self.instaladas)}")
        for fid in sorted(self.instaladas, key=lambda i: self.instaladas[i]["nombre"].lower()):
            caja.append(self.tarjeta(fid))
        self._seccion(caja, f"RUNTIMES Y COMPONENTES · {len(self.runtimes)}",
                      boton("LIMPIAR NO USADOS", "nx-accion", self.limpiar))
        for r in self.runtimes:
            f = Gtk.Box(spacing=10)
            f.add_css_class("nx-card")
            f.append(lbl(r["id"], "nx-code", xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END))
            f.append(lbl(r["rama"], "nx-code", xalign=1))
            f.append(lbl(r["tam"], "nx-code on", xalign=1, width_chars=10))
            caja.append(f)

    def _pagina_upd(self, caja):
        buscando = getattr(self, "updates_buscando", False)
        b = boton("BUSCANDO…" if buscando else "BUSCAR AHORA", "nx-accion", lambda: self.buscar_updates())
        b.set_sensitive(not buscando)
        extra = Gtk.Box(spacing=6)
        extra.append(b)
        if self.updates:
            extra.append(boton("ACTUALIZAR TODO", "nx-accion fuerte", lambda: self.actualizar(None)))
        hora = time.strftime("%H:%M", time.localtime(self.updates_ts)) if self.updates_ts else "—"
        self._seccion(caja, f"ACTUALIZACIONES · REVISADO {hora}", extra)
        if buscando:
            return self._vacio(caja, "Consultando Flathub…")
        if not self.updates:
            return self._vacio(caja, "Todo al día." if self.updates_ts else "Pulsa BUSCAR AHORA.")
        for fid, u in self.updates.items():
            if fid in self.instaladas or fid in self.por_id:
                caja.append(self.tarjeta(fid))
            else:  # runtime / extensión
                f = Gtk.Box(spacing=10)
                f.add_css_class("nx-card")
                f.append(lbl(fid, "nx-code", xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END))
                f.append(lbl(u["rama"], "nx-code", xalign=1))
                f.append(lbl(u.get("bajada", ""), "nx-code on", xalign=1, width_chars=10))
                caja.append(f)

    def _pagina_rec(self, caja):
        if not self.catalogo:
            return self._vacio(caja, "Cargando catálogo…")
        caja.append(lbl("Una selección de apps útiles, agrupadas por uso.", "nx-dim", xalign=0, wrap=True, margin_top=4))
        for grupo, items in RECOMENDADAS:
            items = [(i, m) for i, m in items if i in self.por_id]
            if not items:
                continue
            hechas = sum(1 for i, _ in items if i in self.instaladas)
            self._seccion(caja, f"{grupo}  ·  {hechas}/{len(items)}")
            for fid, motivo in items:
                caja.append(self.tarjeta(fid, motivo))

    def _pagina_exp(self, caja):
        if not self.catalogo:
            return self._vacio(caja, "Cargando catálogo…")
        chips = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=12,
                            column_spacing=4, row_spacing=4, homogeneous=False)
        for cat, txt in [(None, "NOVEDADES")] + CATEGORIAS:
            b = boton(txt, "nx-chip", lambda c=cat: self._categoria(c))
            on(b, "on", cat == self.categoria and not self.entrada.get_text().strip())
            chips.append(b)
        caja.append(chips)
        q = self.entrada.get_text().strip().lower()
        if q:
            res = self._resultados(q)
            self._seccion(caja, f"RESULTADOS «{q}» · {len(res)}{'+' if len(res) >= MAX_RES else ''}")
        elif self.categoria:
            res = sorted((a for a in self.catalogo if self.categoria in a["cats"]),
                         key=lambda a: -a["ts"])[:MAX_RES]
            self._seccion(caja, dict(CATEGORIAS)[self.categoria] + " · RECIENTES")
        else:
            res = sorted(self.catalogo, key=lambda a: -a["ts"])[:60]
            self._seccion(caja, "NOVEDADES EN FLATHUB")
        if not res:
            return self._vacio(caja, "Nada encontrado.")
        for a in res:
            caja.append(self.tarjeta(a["id"]))

    def _categoria(self, cat):
        self.categoria = cat
        if self.entrada.get_text():
            self.entrada.set_text("")  # dispara la búsqueda → redibuja
        else:
            self.redibujar_pagina("exp")

    def _resultados(self, q: str) -> list[dict]:
        palabras = q.split()
        puntos = []
        for a in self.catalogo:
            n, r, i = a["nombre"].lower(), a["resumen"].lower(), a["id"].lower()
            kw = " ".join(a["kw"])
            s = 0
            for p in palabras:
                if n.startswith(p):
                    s += 10
                elif p in n:
                    s += 6
                elif p in i:
                    s += 4
                elif p in kw:
                    s += 3
                elif p in r:
                    s += 2
                else:
                    s = 0
                    break
            if s:
                puntos.append((s + (1 if a["verif"] else 0), a))
        puntos.sort(key=lambda x: (-x[0], x[1]["nombre"].lower()))
        return [a for _s, a in puntos[:MAX_RES]]

    # ---------------- detalle ----------------
    def cerrar_detalle(self):
        self.sel = None
        self.lado.set_reveal_child(False)
        self.redibujar_pagina(self.actual)

    def mostrar_detalle(self, fid: str):
        cambio = self.sel != fid
        self.sel = fid
        app = self.por_id.get(fid) or {}
        inst = self.instaladas.get(fid)
        upd = self.updates.get(fid)
        caja = self.lado_caja
        self._vaciar(caja)
        cab = Gtk.Box(spacing=12)
        cab.append(_icono(app, fid, 72))
        t = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, hexpand=True)
        t.append(lbl((inst or {}).get("nombre") or app.get("nombre") or fid, "nx-h1", xalign=0, wrap=True))
        if app.get("dev"):
            t.append(lbl(app["dev"], "nx-dim", xalign=0, wrap=True))
        tags = Gtk.Box(spacing=4, margin_top=3)
        if app.get("verif"):
            tags.append(lbl("✓ VERIFICADA", "nx-tag"))
        if inst:
            tags.append(lbl("INSTALADA", "nx-tag acc"))
        if upd:
            tags.append(lbl("ACTUALIZACIÓN", "nx-tag warn"))
        t.append(tags)
        cab.append(t)
        x = boton("✕", "nx-btn", self.cerrar_detalle, "Cerrar (Esc)")
        x.set_valign(Gtk.Align.START)
        cab.append(x)
        caja.append(cab)

        acc = Gtk.Box(spacing=6, margin_top=4)
        if inst:
            acc.append(boton("ABRIR", "nx-accion fuerte", lambda: self.abrir(fid)))
            if upd:
                acc.append(boton("ACTUALIZAR", "nx-accion fuerte", lambda: self.actualizar(fid)))
            self.b_desinst = boton("DESINSTALAR", "nx-accion peligro", lambda: self.desinstalar(fid))
            acc.append(self.b_desinst)
        elif app:
            ocupado = self._en_cola(fid)
            b = boton("EN COLA" if ocupado else "INSTALAR", "nx-accion fuerte", lambda: self.instalar(fid))
            b.set_sensitive(not ocupado)
            acc.append(b)
        if app.get("web"):
            acc.append(boton("WEB ↗", "nx-accion", lambda: Gtk.UriLauncher.new(app["web"]).launch(self, None,
                                                                                                    None, None)))
        caja.append(acc)

        datos = [("ID", fid)]
        if inst:
            datos += [("VERSIÓN", inst["version"] or "?"), ("RAMA", inst["rama"]), ("TAMAÑO", inst["tam"]),
                      ("DATOS", f"~/.var/app/{fid}")]
        if upd:
            datos.append(("NUEVA", f"{upd['version'] or '?'}  ({upd.get('bajada', '')})"))
        if app.get("ver") and not inst:
            datos.append(("VERSIÓN", app["ver"] + (time.strftime("  ·  %d-%m-%Y", time.localtime(app["ts"]))
                                                     if app.get("ts") else "")))
        if app.get("lic"):
            datos.append(("LICENCIA", app["lic"]))
        if app.get("cats"):
            datos.append(("CATEGORÍAS", ", ".join(app["cats"][:4])))
        g = Gtk.Grid(column_spacing=10, row_spacing=2, margin_top=6)
        for i, (k, v) in enumerate(datos):
            g.attach(lbl(k, "nx-code", xalign=0, valign=Gtk.Align.START), 0, i, 1, 1)
            g.attach(lbl(v, "nx-code on", xalign=0, wrap=True, selectable=True,
                         wrap_mode=Pango.WrapMode.WORD_CHAR), 1, i, 1, 1)
        caja.append(g)

        if app.get("resumen"):
            caja.append(lbl(app["resumen"], "nx-txt", xalign=0, wrap=True, margin_top=6))
        if app.get("desc"):
            caja.append(lbl(app["desc"], "nx-dim", xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR))

        # permisos
        caja.append(lbl("PERMISOS", "nx-h2", xalign=0, margin_top=8))
        self.perm_caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        caja.append(self.perm_caja)
        if inst:
            self._cargar_permisos(fid, ["flatpak", "info", "--show-permissions", fid])
        else:
            self.perm_caja.append(boton("CONSULTAR (red)", "nx-chip", lambda: self._cargar_permisos(
                fid, ["flatpak", "remote-info", "--show-metadata", "--user", REMOTO,
                      f"app/{fid}/x86_64/{app.get('rama', 'stable')}"])))

        self.lado.set_reveal_child(True)
        if cambio:
            self.redibujar_pagina(self.actual)

    def _cargar_permisos(self, fid, argv):
        self._vaciar(self.perm_caja)
        self.perm_caja.append(lbl("leyendo…", "nx-code", xalign=0))

        def listo(ok, out):
            if self.sel != fid:
                return
            self._vaciar(self.perm_caja)
            if not ok:
                self.perm_caja.append(lbl("No disponible: " + out.strip()[:120], "nx-code warn", xalign=0, wrap=True))
                return
            perms = leer_permisos(out)
            if not perms:
                self.perm_caja.append(lbl("Ninguno especial (aislada).", "nx-code on", xalign=0))
            delicados = sum(1 for p in perms if p[2])
            if delicados:
                self.perm_caja.append(lbl(f"⚠ {delicados} permiso{'s' if delicados > 1 else ''} delicado"
                                          f"{'s' if delicados > 1 else ''}", "nx-code warn", xalign=0))
            grupo_prev = None
            for grupo, txt, delic in perms:
                if grupo != grupo_prev:
                    self.perm_caja.append(lbl(grupo, "nx-code", xalign=0, margin_top=4))
                    grupo_prev = grupo
                self.perm_caja.append(lbl(("⚠ " if delic else "·  ") + txt,
                                          "nx-code warn" if delic else "nx-code on", xalign=0, wrap=True,
                                          wrap_mode=Pango.WrapMode.WORD_CHAR, margin_start=8))
            if "com.github.tchx84.Flatseal" in self.instaladas:
                self.perm_caja.append(boton("EDITAR EN FLATSEAL", "nx-chip", lambda: self.abrir(
                    "com.github.tchx84.Flatseal"), None))

        ejecutar(argv, listo)

    # ---------------- acciones ----------------
    def abrir(self, fid: str):
        subprocess.Popen(["flatpak", "run", fid], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
        self.aviso(f"Abriendo {fid}…")

    def _en_cola(self, fid: str) -> bool:
        return any(f == fid for _d, _a, f in self.cola) or getattr(self, "op_fid", None) == fid

    def instalar(self, fid: str):
        app = self.por_id.get(fid, {})
        self.encolar(f"INSTALAR {app.get('nombre', fid)}",
                     ["flatpak", "install", "--user", "-y", "--noninteractive", REMOTO,
                      f"app/{fid}/x86_64/{app.get('rama', 'stable')}"], fid)

    def actualizar(self, fid: str | None):
        if fid:
            self.encolar(f"ACTUALIZAR {fid}", ["flatpak", "update", "--user", "-y", "--noninteractive", fid], fid)
        else:
            self.encolar("ACTUALIZAR TODO", ["flatpak", "update", "--user", "-y", "--noninteractive"], "*")

    def desinstalar(self, fid: str):
        b = self.b_desinst
        if not b.has_css_class("armed"):
            b.add_css_class("armed")
            b.set_label("¿SEGURO? PULSA OTRA VEZ")
            GLib.timeout_add(3500, lambda: (b.remove_css_class("armed"), b.set_label("DESINSTALAR"), False)[2])
            return
        self.encolar(f"DESINSTALAR {fid}", ["flatpak", "uninstall", "--user", "-y", "--noninteractive", fid], fid)

    def limpiar(self):
        self.encolar("LIMPIAR RUNTIMES NO USADOS",
                     ["flatpak", "uninstall", "--unused", "--user", "-y", "--noninteractive"], "*")

    def encolar(self, desc: str, argv: list[str], fid: str):
        self.cola.append((desc, argv, fid))
        self._siguiente()
        self.redibujar()

    def _siguiente(self):
        if self.op is not None or not self.cola:
            self.op_cola.set_text(f"{len(self.cola)} EN COLA" if self.cola else "")
            return
        desc, argv, fid = self.cola.pop(0)
        self.op_desc, self.op_fid = desc, fid
        self.op_cola.set_text(f"{len(self.cola)} EN COLA" if self.cola else "")
        self.op_txt.set_text(desc + "…")
        self.prog.set_fraction(0)
        self.b_cancelar.set_sensitive(True)
        self.ops.set_reveal_child(True)
        self._log(f"\n$ {' '.join(argv)}\n")
        try:
            self.op = Gio.Subprocess.new(argv, Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_MERGE)
        except GLib.Error as e:
            self._log(e.message + "\n")
            self.op = None
            return self._fin_op(False)
        self.op.get_stdout_pipe().read_bytes_async(4096, GLib.PRIORITY_DEFAULT, None, self._leer, self.op)
        self.op.wait_async(None, lambda p, r: self._fin_op(p.get_successful()))
        self._pulso = GLib.timeout_add(250, self._pulsar)

    def _pulsar(self):
        if self.op is None:
            return False
        if self.prog.get_fraction() == 0:
            self.prog.pulse()
        return True

    def _leer(self, stream, res, proc):
        try:
            datos = stream.read_bytes_finish(res).get_data()
        except GLib.Error:
            return
        if not datos:
            return
        texto = datos.decode(errors="replace")
        for trozo in re.split(r"[\r\n]", texto):
            trozo = trozo.strip()
            if not trozo:
                continue
            m = re.findall(r"(\d{1,3})\s*%", trozo)
            if m:
                self.prog.set_fraction(min(int(m[-1]), 100) / 100)
            limpio = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", trozo)
            self.op_txt.set_text(f"{self.op_desc}  ·  {limpio[:120]}")
        self._log(re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", texto.replace("\r", "\n")))
        stream.read_bytes_async(4096, GLib.PRIORITY_DEFAULT, None, self._leer, proc)

    def _log(self, texto: str):
        buf = self.log.get_buffer()
        buf.insert(buf.get_end_iter(), texto)
        if buf.get_line_count() > 2000:
            buf.delete(buf.get_start_iter(), buf.get_iter_at_line(buf.get_line_count() - 1500)[1])
        self.log.scroll_to_mark(buf.get_insert(), 0, False, 0, 1)

    def _ver_log(self):
        self.log_rev.set_reveal_child(not self.log_rev.get_reveal_child())

    def _cancelar(self):
        if self.op is not None:
            self.op.send_signal(15)
            self._log("\n[cancelado]\n")

    def _fin_op(self, ok: bool):
        desc = self.op_desc
        self.op = None
        self.op_fid = None
        self.b_cancelar.set_sensitive(False)
        self.prog.set_fraction(1 if ok else 0)
        self.op_txt.set_text(f"{desc}  ·  {'HECHO' if ok else 'FALLÓ (mira REGISTRO)'}")
        self.aviso(f"{desc}: {'hecho' if ok else 'falló'}", warn=not ok)
        self._notificar(desc, ok)

        def tras():
            if ok and ("ACTUALIZAR" in desc):
                self.buscar_updates(refrescar_catalogo=False)
            if not self.cola and self.op is None and ok:
                GLib.timeout_add(4000, lambda: (self.op is None and not self.cola
                                                and self.ops.set_reveal_child(False), False)[1])
            self._siguiente()

        self.refrescar_instaladas(tras)
        return False

    def _notificar(self, desc, ok):
        if self.is_active():
            return
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            V = GLib.Variant
            bus.call_sync("org.freedesktop.Notifications", "/org/freedesktop/Notifications",
                          "org.freedesktop.Notifications", "Notify",
                          V("(susssasa{sv}i)", ("NEURAL//PAK", 0, APP_ID, desc,
                                                "Hecho" if ok else "Falló — abre el registro", [],
                                                {"urgency": V("y", 1 if ok else 2)}, 5000)),
                          None, Gio.DBusCallFlags.NONE, 1000, None)
        except GLib.Error:
            pass

    def _al_cerrar(self, *_):
        if self.op is not None or self.cola:
            self.aviso("Hay operaciones en marcha: espera o CANCELAR", warn=True)
            return True
        return False


class App(AppNeural):
    def __init__(self):
        super().__init__(APP_ID)

    def css_extra(self, t):
        return f".nx-tab.warn {{ color: {css_color(t['warn'])}; }}\n"

    def crear_ventana(self, args, cwd):
        ventanas = self.get_windows()
        if ventanas:
            self.traer(ventanas[0])
            w = ventanas[0]
        else:
            w = Ventana(self)
            w.present()
        if "--actualizaciones" in args:
            w.ir("upd")
        elif "--tab" in args and args.index("--tab") + 1 < len(args):
            w.ir(args[args.index("--tab") + 1])
        if "--ver" in args and args.index("--ver") + 1 < len(args):
            fid = args[args.index("--ver") + 1]
            GLib.timeout_add(400, lambda: (w.mostrar_detalle(fid), False)[1])
        elif "--buscar" in args and args.index("--buscar") + 1 < len(args):
            w.entrada.set_text(args[args.index("--buscar") + 1])


if __name__ == "__main__":
    nombrar_proceso(PROC)
    GLib.set_prgname(APP_ID)
    sys.exit(App().run(sys.argv))
