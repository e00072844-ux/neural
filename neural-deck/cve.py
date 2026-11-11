#!/usr/bin/env python3
"""NEURAL//CVE: ventana del vigilante de seguridad (CachyOS/Arch).

Lee ~/.local/state/neural-cve/ultimo.json (lo escribe cve_motor.py, el timer
diario neural-cve.timer o el botón REVISAR AHORA) y lo enseña en pestañas:
  PENDIENTES  instalado más viejo que la versión corregida
  A MANO      el aviso no dice versión clara: decidir y marcar como revisado
  AL DÍA      lo que ya está bien (y lo marcado como revisado)
  SISTEMA     actualizaciones, kernel en uso, cortafuegos, puertos, Flatpak
A la derecha, el aviso completo y el botón MARCAR REVISADO (con nota).
La ventana no usa root ni toca el sistema: solo lee y escribe en su carpeta.
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gio, GLib, Gtk, Pango  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ventana import AppNeural, VentanaNeural, boton, lbl, nombrar_proceso, on  # noqa: E402
from estilo import css_color  # noqa: E402
import cve_motor  # noqa: E402

APP_ID = "org.local.NeuralCVE"
PROC = "neural-cve-gui"
BASE = cve_motor.BASE
VIGIA = Path(cve_motor.__file__).resolve()
JSON = cve_motor.JSON
TXT = cve_motor.TXT
REVISADOS = cve_motor.REVISADOS
PESTANAS = (("pendientes", "PENDIENTES"), ("a_mano", "A MANO"), ("al_dia", "AL DÍA"), ("sistema", "SISTEMA"))
NOMBRES_SIS = {"actualizaciones": "ACTUALIZACIONES", "kernel": "KERNEL", "cortafuegos": "CORTAFUEGOS",
               "puertos": "PUERTOS", "flatpak": "FLATPAK"}


def en_hilo(trabajo, listo):
    def correr():
        try:
            res = trabajo()
        except Exception as e:  # noqa: BLE001
            res = e
        GLib.idle_add(listo, res)
    threading.Thread(target=correr, daemon=True).start()


def clase_grav(g: str) -> str:
    return {"CRÍTICO": "nx-tag crit", "ALTO": "nx-tag warn", "ERROR": "nx-tag warn",
            "MEDIO": "nx-tag acc"}.get(g, "nx-tag")


def leer_revisados() -> list[str]:
    try:
        return REVISADOS.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return ["# Avisos ya resueltos o que no aplican. El vigilante no vuelve a avisar de ellos.",
                "# id<TAB>nota"]


class Ventana(VentanaNeural):
    def __init__(self, app):
        super().__init__(app, "SYS//CVE", "NEURAL//CVE", (1100, 680))
        self.datos: dict = {}
        self.tab = "pendientes"
        self.sel = None              # id del aviso o nombre de comprobación elegida
        self.revisando = False

        # pestañas
        self.tabs = {}
        for clave, texto in PESTANAS:
            b = boton(texto, "nx-tab", lambda c=clave: self._pestana(c))
            self.tabs[clave] = b
            self.cab_centro.append(b)

        # cabecera de estado
        estado = Gtk.Box(spacing=14, margin_top=8, margin_bottom=8)
        izq = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, spacing=2)
        self.e_titulo = lbl("…", "nx-h1", xalign=0)
        self.e_sub = lbl("", "nx-dim", xalign=0, ellipsize=Pango.EllipsizeMode.END)
        izq.append(self.e_titulo)
        izq.append(self.e_sub)
        estado.append(izq)
        self.b_informe = boton("INFORME", "nx-accion", self._abrir_informe, "Abrir ultimo.txt")
        self.b_revisar = boton("REVISAR AHORA", "nx-accion fuerte", self._revisar,
                               "Descarga los avisos y vuelve a comprobar todo")
        for b in (self.b_informe, self.b_revisar):
            b.set_valign(Gtk.Align.CENTER)
            estado.append(b)
        self.cuerpo.append(estado)
        self.prog = Gtk.ProgressBar(visible=False)
        self.prog.add_css_class("nx-prog")
        self.cuerpo.append(self.prog)

        # lista + detalle
        zona = Gtk.Box(spacing=0, vexpand=True)
        self.lista = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.lista.add_css_class("nx-lista")
        self.lista.connect("row-activated", lambda _l, fila: self._elegir(fila.clave))
        self.vacio = lbl("", "nx-dim", vexpand=True, valign=Gtk.Align.CENTER, wrap=True,
                         justify=Gtk.Justification.CENTER)
        pila = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        pila.append(self.lista)
        pila.append(self.vacio)
        scroll = Gtk.ScrolledWindow(hexpand=True, vexpand=True, child=pila, margin_end=10)
        scroll.add_css_class("nx-scroll")
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        zona.append(scroll)

        self.lado = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, width_request=400)
        self.lado.add_css_class("nx-lado")
        self.d_tags = Gtk.Box(spacing=6)
        self.d_nombre = lbl("", "nx-h1", xalign=0, wrap=True)
        self.d_detalle = lbl("", "nx-txt", xalign=0, wrap=True, selectable=True)
        self.d_id = lbl("", "nx-code", xalign=0)
        self.d_h2 = lbl("AVISO", "nx-h2", xalign=0, margin_top=6)
        self.d_texto = lbl("", "nx-dim", xalign=0, yalign=0, wrap=True, selectable=True,
                           wrap_mode=Pango.WrapMode.WORD_CHAR)
        tscroll = Gtk.ScrolledWindow(vexpand=True, child=self.d_texto)
        tscroll.add_css_class("nx-scroll")
        tscroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.d_nota = Gtk.Entry(placeholder_text="nota: por qué está resuelto o no aplica")
        self.d_nota.add_css_class("nx-entry")
        self.d_nota.connect("activate", lambda *_: self._marcar())
        self.b_marcar = boton("MARCAR REVISADO", "nx-accion", self._marcar,
                              "Apunta el aviso en revisados.tsv: no volverá a avisar")
        self.b_desmarcar = boton("DESMARCAR", "nx-accion peligro", self._desmarcar,
                                 "Quita el aviso de revisados.tsv")
        acciones = Gtk.Box(spacing=6)
        acciones.append(self.b_marcar)
        acciones.append(self.b_desmarcar)
        for w in (self.d_tags, self.d_nombre, self.d_detalle, self.d_id, self.d_h2, tscroll,
                  self.d_nota, acciones):
            self.lado.append(w)
        zona.append(self.lado)
        self.cuerpo.append(zona)

        # recargar solo cuando el timer escribe un informe nuevo
        self._mon = Gio.File.new_for_path(str(JSON)).monitor_file(Gio.FileMonitorFlags.NONE, None)
        self._mon.connect("changed", self._json_cambio)
        self._recarga_id = 0
        self.cargar()
        if not JSON.exists():
            self._revisar()

    # ------------------------------------------------------------ datos
    def _json_cambio(self, *_):
        if self._recarga_id:
            GLib.source_remove(self._recarga_id)
        self._recarga_id = GLib.timeout_add(400, self._recarga_diferida)

    def _recarga_diferida(self):
        self._recarga_id = 0
        self.cargar()
        return False

    def cargar(self):
        try:
            self.datos = json.loads(JSON.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.datos = {}
        self._cabecera()
        self._llenar()

    def _items(self, tab: str) -> list[dict]:
        if tab == "sistema":
            return self.datos.get("sistema", [])
        return self.datos.get(tab, [])

    def _problemas_sistema(self) -> list[dict]:
        return [s for s in self.datos.get("sistema", []) if not s.get("ok")]

    def _cabecera(self):
        d = self.datos
        for clave, texto in PESTANAS:
            n = len(self._problemas_sistema()) if clave == "sistema" else len(self._items(clave))
            self.tabs[clave].set_label(f"{texto} {n}" if clave != "sistema" or n else texto)
            on(self.tabs[clave], "on", clave == self.tab)
            on(self.tabs[clave], "alerta", clave in ("pendientes", "a_mano", "sistema") and n > 0)
        if not d:
            self.e_titulo.set_text("SIN DATOS — pulsa REVISAR AHORA")
            self.e_sub.set_text("")
            return
        pend = len(d.get("pendientes", [])) + len(self._problemas_sistema())
        mano = len(d.get("a_mano", []))
        if d.get("errores") and not d.get("n_avisos"):
            self.e_titulo.set_text("▲ NO SE PUDO REVISAR")
        elif pend:
            self.e_titulo.set_text(f"▲ {pend} {'COSA' if pend == 1 else 'COSAS'} POR ACTUALIZAR")
        elif mano:
            self.e_titulo.set_text(f"◆ {mano} {'AVISO' if mano == 1 else 'AVISOS'} POR REVISAR A MANO")
        else:
            self.e_titulo.set_text("● SISTEMA AL DÍA")
        on(self.e_titulo, "estado-mal", bool(pend) or bool(d.get("errores") and not d.get("n_avisos")))
        on(self.e_titulo, "estado-mano", not pend and bool(mano))
        on(self.e_titulo, "estado-bien", not pend and not mano)
        sub = (f"Revisado {d.get('fecha', '?')} · {d.get('n_avisos', 0)} avisos de {d.get('libro', 'Arch')}"
               f" · {d.get('n_detectados', 0)} paquetes tuyos con avisos · próxima: {self._proxima()}")
        if d.get("errores"):
            sub += " · ERRORES: " + "; ".join(d["errores"])[:160]
        self.e_sub.set_text(sub)

    @staticmethod
    def _proxima() -> str:
        try:
            r = subprocess.run(["systemctl", "--user", "show", "neural-cve.timer", "-p",
                                "NextElapseUSecRealtime", "--value"], capture_output=True, text=True, timeout=5)
            v = r.stdout.strip()
            return " ".join(v.split()[1:3])[:16] if v else "timer apagado"
        except (OSError, subprocess.SubprocessError):
            return "?"

    # ------------------------------------------------------------ lista
    def _pestana(self, clave):
        self.tab = clave
        self.sel = None
        self._cabecera()
        self._llenar()

    def _clave(self, item) -> str:
        return item.get("id") or item.get("nombre", "")

    def _llenar(self):
        while (f := self.lista.get_row_at_index(0)) is not None:
            self.lista.remove(f)
        items = self._items(self.tab)
        vacio = {"pendientes": "Nada que actualizar.\nTodo lo instalado está en la versión corregida o más nueva.",
                 "a_mano": "Ningún aviso pendiente de revisar a mano.",
                 "al_dia": "Aún no hay nada comprobado.", "sistema": "Sin datos del sistema."}[self.tab]
        self.vacio.set_text(vacio if not items else "")
        self.vacio.set_visible(not items)
        for it in items:
            self.lista.append(self._tarjeta(it))
        claves = [self._clave(i) for i in items]
        if self.sel not in claves:
            self.sel = claves[0] if claves else None
        self._marcar_sel()
        self._detalle()

    def _tarjeta(self, it: dict) -> Gtk.ListBoxRow:
        caja = Gtk.Box(spacing=10)
        caja.add_css_class("nx-card")
        if self.tab == "sistema":
            g = "OK" if it.get("ok") else it.get("grav", "")
            tag = lbl(g, "nx-tag" if it.get("ok") else clase_grav(g), valign=Gtk.Align.CENTER)
            nombre = NOMBRES_SIS.get(it["nombre"], it["nombre"].upper())
            texto = it.get("texto", "")
            derecha = ""
        else:
            g = it.get("grav_es", "")
            tag = lbl(g, clase_grav(g) if self.tab != "al_dia" else "nx-tag", valign=Gtk.Align.CENTER)
            nombre, texto = it["paquete"], it.get("detalle", "")
            derecha = it["id"]
        tag.set_size_request(64, -1)
        caja.append(tag)
        caja.append(lbl(nombre, "nx-txt", xalign=0, width_chars=16, max_width_chars=16,
                        ellipsize=Pango.EllipsizeMode.END))
        caja.append(lbl(texto, "nx-dim", xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END))
        if it.get("nuevo") and self.tab in ("pendientes", "a_mano"):
            caja.append(lbl("NUEVO", "nx-tag warn", valign=Gtk.Align.CENTER))
        if it.get("revisado"):
            caja.append(lbl("REVISADO", "nx-tag acc", valign=Gtk.Align.CENTER))
        if derecha:
            caja.append(lbl(derecha, "nx-code", valign=Gtk.Align.CENTER))
        fila = Gtk.ListBoxRow(child=caja, activatable=True)
        fila.clave = self._clave(it)
        fila.caja = caja
        return fila

    def _elegir(self, clave):
        self.sel = clave
        self._marcar_sel()
        self._detalle()

    def _marcar_sel(self):
        i = 0
        while (f := self.lista.get_row_at_index(i)) is not None:
            on(f.caja, "sel", f.clave == self.sel)
            i += 1

    # ------------------------------------------------------------ detalle
    def _detalle(self):
        it = next((i for i in self._items(self.tab) if self._clave(i) == self.sel), None)
        while (t := self.d_tags.get_first_child()) is not None:
            self.d_tags.remove(t)
        self.lado.set_visible(it is not None)
        if it is None:
            return
        if self.tab == "sistema":
            g = "OK" if it.get("ok") else it.get("grav", "")
            self.d_tags.append(lbl(g, "nx-tag" if it.get("ok") else clase_grav(g)))
            self.d_nombre.set_text(NOMBRES_SIS.get(it["nombre"], it["nombre"].upper()))
            self.d_detalle.set_text(it.get("texto", ""))
            self.d_id.set_text("comprobación del sistema")
            self.d_h2.set_visible(False)
            self.d_texto.set_text(self._ayuda_sistema(it))
            for w in (self.d_nota, self.b_marcar, self.b_desmarcar):
                w.set_visible(False)
            return
        g = it.get("grav_es", "")
        self.d_tags.append(lbl(g, clase_grav(g)))
        if it.get("nuevo") and self.tab != "al_dia":
            self.d_tags.append(lbl("NUEVO", "nx-tag warn"))
        if it.get("revisado"):
            self.d_tags.append(lbl("REVISADO", "nx-tag acc"))
        self.d_nombre.set_text(it["paquete"])
        self.d_detalle.set_text(it.get("detalle", ""))
        self.d_id.set_text(f"{it['id']} · {it.get('tipo') or it.get('fecha', '')}")
        self.d_h2.set_visible(True)
        self.d_h2.set_text("AVISO COMPLETO (Arch, en inglés)")
        self.d_texto.set_text(it.get("texto", ""))
        puede_marcar = self.tab in ("pendientes", "a_mano")
        self.d_nota.set_visible(puede_marcar)
        self.b_marcar.set_visible(puede_marcar)
        self.b_desmarcar.set_visible(bool(it.get("revisado")))
        self.d_nota.set_text("")

    @staticmethod
    def _ayuda_sistema(it) -> str:
        n = it["nombre"]
        if it.get("ok"):
            return {"kernel": "La serie del kernel sigue recibiendo parches y estás en su última versión.",
                    "actualizaciones": "Todos los paquetes están en la última versión de los repos "
                                       "(consulta con checkupdates, sin tocar el sistema).",
                    "cortafuegos": "El cortafuegos bloquea lo que entra desde la red y no pediste.",
                    "puertos": "Ningún programa tuyo acepta conexiones desde la red. Lo de «servicios del "
                               "sistema» es normal (nombres en la red local y la red interna de las VMs).",
                    "flatpak": "Flathub no tiene actualizaciones pendientes para tus apps."}.get(n, "")
        lista = "\n\n" + "\n".join(it["lista"]) if it.get("lista") else ""
        return {"actualizaciones": "En una terminal:  sudo pacman -Syu\n(o paru -Syu). Si se actualiza el "
                                   "kernel, reinicia después." + lista,
                "kernel": "Se instaló un kernel nuevo pero sigues con el viejo: reinicia para usarlo.",
                "cortafuegos": "Actívalo con:  sudo systemctl enable --now ufw\ny luego  sudo ufw enable",
                "puertos": "Mira qué programa es con:  sudo ss -tlpn\nSi no lo necesitas, ciérralo o haz que "
                           "escuche solo en 127.0.0.1. Con el cortafuegos activo, desde fuera no se llega "
                           "salvo que abras ese puerto en ufw.",
                "flatpak": "En una terminal:  flatpak update"}.get(n, "")

    # ------------------------------------------------------------ acciones
    def _revisar(self, sin_red=False):
        if self.revisando:
            return
        self.revisando = True
        self.b_revisar.set_sensitive(False)
        self.prog.set_visible(True)
        self._pulso = GLib.timeout_add(120, lambda: (self.prog.pulse(), True)[1])
        self.aviso("Revisando avisos de Arch, actualizaciones, kernel, puertos y Flatpak…" if not sin_red
                   else "Recalculando…", ms=60000)
        cmd = [sys.executable, "-I", str(VIGIA), "--callado"] + (["--sin-red"] if sin_red else [])

        def trabajo():
            return subprocess.run(cmd, capture_output=True, text=True, timeout=300)

        def listo(res):
            self.revisando = False
            self.b_revisar.set_sensitive(True)
            GLib.source_remove(self._pulso)
            self.prog.set_visible(False)
            if isinstance(res, Exception):
                self.aviso(f"Fallo al revisar: {res}", True, 8000)
            elif res.returncode != 0:
                self.aviso("La revisión terminó con errores: " + (res.stderr.strip().splitlines() or ["?"])[-1],
                           True, 8000)
            else:
                self.aviso("Revisión terminada")
            self.cargar()
            return False
        en_hilo(trabajo, listo)

    def _marcar(self):
        it = next((i for i in self._items(self.tab) if self._clave(i) == self.sel), None)
        if not it or "id" not in it:
            return
        nota = " ".join(self.d_nota.get_text().split()).replace("\t", " ")
        if not nota:
            self.aviso("Escribe una nota: por qué está resuelto o no aplica", True)
            self.d_nota.grab_focus()
            return
        lineas = [l for l in leer_revisados() if not l.startswith(it["id"] + "\t")]
        lineas.append(f"{it['id']}\t{nota} ({GLib.DateTime.new_now_local().format('%d-%m-%Y')})")
        self._guardar(lineas, f"{it['id']} marcado como revisado")

    def _desmarcar(self):
        it = next((i for i in self._items(self.tab) if self._clave(i) == self.sel), None)
        if not it or "id" not in it:
            return
        lineas = [l for l in leer_revisados() if not l.startswith(it["id"] + "\t")]
        self._guardar(lineas, f"{it['id']} ya no está marcado")

    def _guardar(self, lineas, mensaje):
        BASE.mkdir(parents=True, exist_ok=True)
        tmp = REVISADOS.with_suffix(".tsv.tmp")
        tmp.write_text("\n".join(lineas) + "\n", encoding="utf-8")
        tmp.replace(REVISADOS)
        self.aviso(mensaje)
        self._revisar(sin_red=True)

    def _abrir_informe(self):
        if not TXT.exists():
            self.aviso("Aún no hay informe", True)
            return
        try:
            Gio.AppInfo.launch_default_for_uri(TXT.as_uri(), None)
        except GLib.Error as e:
            self.aviso(f"No se pudo abrir: {e.message}", True)


class App(AppNeural):
    def __init__(self):
        super().__init__(APP_ID)

    def css_extra(self, t):
        acc, alt, warn = t["accent"], t["alt"], t["warn"]
        return (f".nx-tag.crit {{ color: #000; background: {css_color(warn)}; border-color: {css_color(warn)}; }}\n"
                f".nx-tab.alerta {{ color: {css_color(warn)}; }}\n"
                f".nx-tab.alerta.on {{ border-bottom-color: {css_color(warn)}; }}\n"
                f".nx-h1.estado-mal {{ color: {css_color(warn)}; }}\n"
                f".nx-h1.estado-mano {{ color: {css_color(acc)}; }}\n"
                f".nx-h1.estado-bien {{ color: {css_color(alt)}; }}\n")

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
