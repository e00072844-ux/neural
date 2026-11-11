#!/usr/bin/env python3
"""NEURAL//BÓVEDA: contraseñas, códigos 2FA y notas, cifrados y solo locales.

Se bloquea tras 5 minutos sin usarla. Lo copiado se borra del portapapeles a los
30 s y va marcado como secreto, así NEURAL//CLIP no lo guarda."""
from __future__ import annotations

import ctypes
import resource
import sys
import threading
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk, Pango  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import boveda_datos as D  # noqa: E402
import clip_datos  # noqa: E402
from ventana import AppNeural, VentanaNeural, boton, lbl, nombrar_proceso, on  # noqa: E402

APP_ID = "org.local.NeuralBoveda"
BLOQUEO_S = 300
LIMPIAR_MS = 30000
SECRETO = "application/x-neural-secret"


def limpiar(c):
    while h := c.get_first_child():
        c.remove(h)


def entrada(ph="", clave=False):
    e = Gtk.PasswordEntry(show_peek_icon=True) if clave else Gtk.Entry()
    e.set_property("placeholder-text", ph)
    e.add_css_class("nx-entry")
    return e


class Ventana(VentanaNeural):
    def __init__(self, app):
        super().__init__(app, "SYS//KEYS", "NEURAL//BÓVEDA", (980, 620))
        self.b = D.Boveda()
        self.ult_uso = time.monotonic()
        self.limpiador = 0
        self.actual: dict | None = None
        self.filas: list[dict] = []
        self.pila = Gtk.Stack(vexpand=True)
        self.cuerpo.append(self.pila)
        self.c_acceso = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, halign=Gtk.Align.CENTER,
                                valign=Gtk.Align.CENTER, width_request=420)
        self.pila.add_named(self.c_acceso, "acceso")
        self._armar_principal()
        self.pantalla_acceso()
        for ev in (Gtk.EventControllerKey(), Gtk.EventControllerMotion()):
            ev.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
            ev.connect("key-pressed" if isinstance(ev, Gtk.EventControllerKey) else "motion", self._uso)
            self.add_controller(ev)
        GLib.timeout_add(1000, self._tic)
        self.connect("close-request", self._al_cerrar)

    def _uso(self, *_):
        self.ult_uso = time.monotonic()
        return False

    def _al_cerrar(self, *_):
        self.bloquear()
        return False

    # ---- acceso ----
    def pantalla_acceso(self):
        limpiar(self.c_acceso)
        nueva = not self.b.existe()
        self.c_acceso.append(lbl("CREAR BÓVEDA" if nueva else "BÓVEDA BLOQUEADA", "nx-h1", xalign=0))
        self.e_m1 = entrada("Contraseña maestra", True)
        self.c_acceso.append(self.e_m1)
        if nueva:
            self.e_m2 = entrada("Repítela", True)
            self.c_acceso.append(self.e_m2)
            self.c_acceso.append(lbl(f"Mínimo {D.MIN_MAESTRA} caracteres. Si la olvidas no hay forma de recuperar "
                                     "nada: anótala y guárdala fuera de esta laptop.", "nx-tag warn", xalign=0, wrap=True))
        self.b_entrar = boton("CREAR" if nueva else "ABRIR", "nx-accion fuerte", self.entrar)
        self.c_acceso.append(self.b_entrar)
        self.e_m1.connect("activate", lambda *_: self.entrar())
        self.pila.set_visible_child_name("acceso")
        self.e_m1.grab_focus()

    def entrar(self):
        m = self.e_m1.get_text()
        nueva = not self.b.existe()
        if nueva and m != self.e_m2.get_text():
            self.aviso("No coinciden", True)
            return
        self.b_entrar.set_sensitive(False)
        self.aviso("Derivando clave…", ms=20000)

        def trabajo():
            try:
                self.b.crear(m) if nueva else self.b.abrir(m)
                GLib.idle_add(self._abierta, None)
            except (D.ErrorBoveda, OSError) as e:
                GLib.idle_add(self._abierta, str(e))
        threading.Thread(target=trabajo, daemon=True).start()

    def _abierta(self, err):
        self.b_entrar.set_sensitive(True)
        if err:
            self.aviso(err, True, 6000)
            return False
        self.aviso("Abierta")
        self.e_m1.set_text("")
        self.ult_uso = time.monotonic()
        self.pila.set_visible_child_name("principal")
        self.refrescar()
        self.buscar.grab_focus()
        return False

    def bloquear(self):
        self.b.bloquear()
        self.actual = None
        self.filas = []
        self._vaciar_portapapeles()
        limpiar(self.c_det)
        while f := self.lista.get_row_at_index(0):
            self.lista.remove(f)
        self.pantalla_acceso()

    def _tic(self):
        if self.b.abierta() and time.monotonic() - self.ult_uso > BLOQUEO_S:
            self.bloquear()
        if self.b.abierta():
            self._pintar_totp()
        return True

    # ---- principal ----
    def _armar_principal(self):
        caja = Gtk.Box(spacing=14, margin_top=8, vexpand=True)
        izq = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, width_request=340)
        self.buscar = entrada("Buscar…")
        self.buscar.connect("changed", lambda *_: self.refrescar())
        izq.append(self.buscar)
        self.lista = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.lista.add_css_class("nx-lista")
        self.lista.connect("row-selected", self._elegida)
        s = Gtk.ScrolledWindow(child=self.lista, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        s.add_css_class("nx-scroll")
        izq.append(s)
        f = Gtk.Box(spacing=6)
        f.append(boton("NUEVA", "nx-accion fuerte", self.nueva))
        f.append(boton("BLOQUEAR", "nx-accion", self.bloquear))
        f.append(boton("MAESTRA", "nx-chip", self.form_maestra, "Cambiar contraseña maestra"))
        izq.append(f)
        caja.append(izq)
        self.c_det = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, hexpand=True)
        s = Gtk.ScrolledWindow(child=self.c_det, hexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        s.add_css_class("nx-scroll")
        caja.append(s)
        self.pila.add_named(caja, "principal")

    def refrescar(self, sel: str | None = None):
        self.filas = self.b.buscar(self.buscar.get_text())
        while f := self.lista.get_row_at_index(0):
            self.lista.remove(f)
        for e in self.filas:
            c = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            c.add_css_class("nx-card")
            c.append(lbl(e.get("titulo") or "(sin título)", "nx-txt", xalign=0, ellipsize=Pango.EllipsizeMode.END))
            c.append(lbl(e.get("usuario", ""), "nx-code", xalign=0, ellipsize=Pango.EllipsizeMode.END))
            self.lista.append(c)
        if sel:
            for i, e in enumerate(self.filas):
                if e["id"] == sel:
                    self.lista.select_row(self.lista.get_row_at_index(i))

    def _elegida(self, _l, fila):
        if not fila or fila.get_index() >= len(self.filas):
            return
        self.actual = self.filas[fila.get_index()]
        self.detalle(self.actual)

    def _fila_dato(self, nombre, texto, copia=None, oculto=False):
        self.c_det.append(lbl(nombre, "nx-code", xalign=0))
        f = Gtk.Box(spacing=8)
        v = lbl("••••••••••••" if oculto else texto, "nx-txt", xalign=0, hexpand=True, wrap=True,
                wrap_mode=Pango.WrapMode.WORD_CHAR, selectable=not oculto)
        f.append(v)
        if oculto:
            ver = [False]

            def alternar():
                ver[0] = not ver[0]
                v.set_text(texto if ver[0] else "••••••••••••")
            f.append(boton("VER", "nx-chip", alternar))
        if copia is not None:
            f.append(boton("COPIAR", "nx-accion", lambda: self.copiar(copia() if callable(copia) else copia)))
        self.c_det.append(f)
        return v

    def detalle(self, e):
        limpiar(self.c_det)
        self.v_totp = None
        self.c_det.append(lbl(e.get("titulo") or "(sin título)", "nx-h1", xalign=0, wrap=True))
        if e.get("usuario"):
            self._fila_dato("USUARIO", e["usuario"], e["usuario"])
        if e.get("clave"):
            self._fila_dato("CONTRASEÑA", e["clave"], e["clave"], oculto=True)
        if e.get("totp"):
            self.v_totp = self._fila_dato("CÓDIGO 2FA", "", lambda: D.totp(e["totp"]))
            self.totp_sec = e["totp"]
            self._pintar_totp()
        if e.get("url"):
            self._fila_dato("SITIO", e["url"], e["url"])
        if e.get("notas"):
            self._fila_dato("NOTAS", e["notas"])
        f = Gtk.Box(spacing=6, margin_top=8)
        f.append(boton("EDITAR", "nx-accion", lambda: self.form(e)))
        f.append(boton("BORRAR", "nx-accion peligro", lambda: self._borrar(e)))
        self.c_det.append(f)

    def _pintar_totp(self):
        if getattr(self, "v_totp", None):
            try:
                c = D.totp(self.totp_sec)
                self.v_totp.set_text(f"{c[:3]} {c[3:]}   ·   {30 - int(time.time()) % 30}s")
            except D.ErrorBoveda as x:
                self.v_totp.set_text(str(x))

    def _borrar(self, e):
        if self.__dict__.get("conf") != e["id"]:
            self.conf = e["id"]
            self.aviso("Pulsa BORRAR otra vez para confirmar", True)
            GLib.timeout_add(4000, lambda: (setattr(self, "conf", None), False)[1])
            return
        self.conf = None
        self.b.borrar(e["id"])
        limpiar(self.c_det)
        self.refrescar()

    def nueva(self):
        self.lista.unselect_all()
        self.form({})

    def form(self, e):
        limpiar(self.c_det)
        self.v_totp = None
        campos = {}
        for k, ph, cl in (("titulo", "Título", False), ("usuario", "Usuario o correo", False), ("clave", "Contraseña", True),
                          ("url", "Sitio web", False), ("totp", "Secreto 2FA (base32, opcional)", True)):
            w = entrada(ph, cl)
            w.set_text(e.get(k, ""))
            campos[k] = w
            self.c_det.append(lbl(ph.upper(), "nx-code", xalign=0))
            self.c_det.append(w)
            if k == "clave":
                self.c_det.append(boton("GENERAR", "nx-chip", lambda w=w: w.set_text(D.generar())))
        self.c_det.append(lbl("NOTAS", "nx-code", xalign=0))
        notas = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, height_request=90)
        notas.add_css_class("nx-log")
        notas.get_buffer().set_text(e.get("notas", ""))
        self.c_det.append(notas)

        def guardar():
            d = {**e, **{k: w.get_text().strip() if k != "clave" else w.get_text() for k, w in campos.items()}}
            b = notas.get_buffer()
            d["notas"] = b.get_text(b.get_start_iter(), b.get_end_iter(), False)
            if not d["titulo"]:
                self.aviso("Falta el título", True)
                return
            if d["totp"] and not D.secreto_valido(d["totp"]):
                self.aviso("El secreto 2FA no es válido", True)
                return
            g = self.b.poner(d)
            self.refrescar(g["id"])
            self.detalle(g)
            self.aviso("Guardado")

        f = Gtk.Box(spacing=6, margin_top=6)
        f.append(boton("GUARDAR", "nx-accion fuerte", guardar))
        f.append(boton("CANCELAR", "nx-accion", lambda: self.detalle(e) if e else limpiar(self.c_det)))
        self.c_det.append(f)

    def form_maestra(self):
        limpiar(self.c_det)
        self.v_totp = None
        self.c_det.append(lbl("CAMBIAR CONTRASEÑA MAESTRA", "nx-h2", xalign=0))
        a, n1, n2 = entrada("Actual", True), entrada("Nueva", True), entrada("Repite la nueva", True)
        for w in (a, n1, n2):
            self.c_det.append(w)

        def cambiar():
            if n1.get_text() != n2.get_text():
                self.aviso("No coinciden", True)
                return
            act, nue = a.get_text(), n1.get_text()
            self.aviso("Cambiando…", ms=20000)

            def trabajo():
                try:
                    self.b.cambiar_maestra(act, nue)
                    GLib.idle_add(self._cambiada, None)
                except (D.ErrorBoveda, OSError) as e:
                    GLib.idle_add(self._cambiada, str(e))
            threading.Thread(target=trabajo, daemon=True).start()
        self.c_det.append(boton("CAMBIAR", "nx-accion fuerte", cambiar))

    def _cambiada(self, err):
        self.aviso(err or "Contraseña maestra cambiada", bool(err), 6000)
        if not err:
            limpiar(self.c_det)
        return False

    # ---- portapapeles ----
    def copiar(self, texto: str):
        clip_datos.marcar_omitir()  # antes de copiar: CLIP no debe guardarlo
        datos = GLib.Bytes.new(texto.encode())
        tipos = [Gdk.ContentProvider.new_for_bytes(m, datos) for m in
                 ("text/plain;charset=utf-8", "text/plain", "UTF8_STRING")]
        tipos.append(Gdk.ContentProvider.new_for_bytes(SECRETO, GLib.Bytes.new(b"1")))
        self.get_clipboard().set_content(Gdk.ContentProvider.new_union(tipos))
        if self.limpiador:
            GLib.source_remove(self.limpiador)
        self.limpiador = GLib.timeout_add(LIMPIAR_MS, self._vaciar_portapapeles)
        self.aviso("Copiado: se borrará en 30 s")

    def _vaciar_portapapeles(self):
        if self.limpiador:
            self.limpiador = 0
            if self.get_clipboard().is_local():  # si copiaste otra cosa después, no se toca
                self.get_clipboard().set_content(None)
        return False


class App(AppNeural):
    def __init__(self):
        super().__init__(APP_ID)

    def crear_ventana(self, args, cwd):
        v = self.get_windows()
        v[0].present() if v else Ventana(self).present()


if __name__ == "__main__":
    # Lo que hay en memoria (claves abiertas) no debe acabar en un volcado ni ser legible por otro proceso
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    ctypes.CDLL(None).prctl(4, 0, 0, 0, 0)  # PR_SET_DUMPABLE = 0
    nombrar_proceso("neural-boveda")
    GLib.set_prgname(APP_ID)
    sys.exit(App().run(sys.argv))
