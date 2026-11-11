#!/usr/bin/env python3
"""NEURAL//RESPALDO: ventana (el trabajo lo hace respaldo_motor.py en segundo plano)."""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk, Pango  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import respaldo_motor as M  # noqa: E402
from ventana import AppNeural, VentanaNeural, boton, lbl, nombrar_proceso, tam_humano  # noqa: E402

APP_ID = "org.local.NeuralRespaldo"
FASES = {"preparando": "PREPARANDO", "copiando": "COPIANDO", "limpiando": "LIMPIANDO", "verificando": "VERIFICANDO"}


def limpiar(c):
    while h := c.get_first_child():
        c.remove(h)


def en_hilo(fn, listo):
    def r():
        try:
            v, e = fn(), None
        except (M.ErrorRespaldo, OSError, ValueError) as x:
            v, e = None, str(x)
        GLib.idle_add(listo, v, e)
    threading.Thread(target=r, daemon=True).start()


class Ventana(VentanaNeural):
    def __init__(self, app):
        super().__init__(app, "SYS//VAULT", "NEURAL//RESPALDO", (980, 620))
        self.sel = None
        izq = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, width_request=350)
        izq.append(lbl("ESTADO", "nx-h2", xalign=0))
        self.l_estado = lbl("", "nx-h1", xalign=0, wrap=True)
        izq.append(self.l_estado)
        self.l_msg = lbl("", "nx-dim", xalign=0, wrap=True)
        izq.append(self.l_msg)
        self.barra = Gtk.ProgressBar(visible=False)
        self.barra.add_css_class("nx-prog")
        izq.append(self.barra)
        self.l_ult = lbl("", "nx-code", xalign=0, wrap=True)
        izq.append(self.l_ult)
        self.b_ahora = boton("RESPALDAR AHORA", "nx-accion fuerte", self.ahora)
        izq.append(self.b_ahora)
        izq.append(Gtk.Box(vexpand=True))
        izq.append(lbl("DESTINO", "nx-h2", xalign=0))
        self.c_cfg = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        izq.append(self.c_cfg)

        der = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, hexpand=True)
        t = Gtk.Box(spacing=8)
        t.append(lbl("COPIAS GUARDADAS", "nx-h2", xalign=0, hexpand=True))
        t.append(boton("ACTUALIZAR", "nx-chip", self.cargar))
        der.append(t)
        self.lista = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.lista.add_css_class("nx-lista")
        self.lista.connect("row-selected", self._elegida)
        s = Gtk.ScrolledWindow(child=self.lista, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        s.add_css_class("nx-scroll")
        der.append(s)
        der.append(lbl("Restaurar nunca sobrescribe: crea una carpeta nueva en ~/Restaurado.", "nx-dim", xalign=0))
        f = Gtk.Box(spacing=6)
        self.e_inc = Gtk.Entry(placeholder_text="Solo esta ruta (vacío = todo), ej. ~/Documentos", hexpand=True)
        self.e_inc.add_css_class("nx-entry")
        f.append(self.e_inc)
        self.b_rest = boton("RESTAURAR", "nx-accion", self.restaurar)
        self.b_rest.set_sensitive(False)
        f.append(self.b_rest)
        der.append(f)

        cuerpo = Gtk.Box(spacing=14, margin_top=8, vexpand=True)
        cuerpo.append(izq)
        cuerpo.append(der)
        self.cuerpo.append(cuerpo)
        self.snaps = []
        self.pintar_cfg()
        GLib.timeout_add(700, self.tic)
        self.tic()
        self.cargar()

    def tic(self):
        e, c = M.estado(), M.config()
        activo = e["fase"] in FASES
        self.b_ahora.set_sensitive(not activo)
        self.barra.set_visible(activo)
        self.barra.set_fraction(e.get("pct", 0) / 100)
        hay = bool(c.get("uuid") or c.get("ruta"))
        if activo:
            self.l_estado.set_text(FASES[e["fase"]])
        elif e.get("error"):
            self.l_estado.set_text("ÚLTIMO INTENTO FALLÓ")
        elif not hay:
            self.l_estado.set_text("SIN CONFIGURAR")
        else:
            self.l_estado.set_text("LISTO")
        self.l_msg.set_text(e.get("error") or e.get("msg") or "")
        u = e.get("ultimo_ok", 0)
        self.l_ult.set_text("ÚLTIMO RESPALDO: " + (time.strftime("%d/%m/%Y %H:%M", time.localtime(u)) if u else "NUNCA"))
        return True

    def ahora(self):
        M.BASE.mkdir(parents=True, exist_ok=True)
        M.PEDIR.touch()
        self.aviso("Respaldo solicitado")

    def pintar_cfg(self):
        limpiar(self.c_cfg)
        c = M.config()
        if c.get("uuid") or c.get("ruta"):
            self.c_cfg.append(lbl(c.get("ruta") or f"Disco {c['uuid']}", "nx-code", xalign=0, wrap=True))
            self.c_cfg.append(boton("CAMBIAR DESTINO", "nx-chip", self.formulario))
        else:
            self.formulario()

    def formulario(self):
        limpiar(self.c_cfg)
        vols = M.disponibles()
        if not vols:
            self.c_cfg.append(lbl("No veo discos externos montados.\nFormatea la USB en NEURAL//DISCO (ext4), "
                                  "ábrela y pulsa BUSCAR.", "nx-dim", xalign=0, wrap=True))
            self.c_cfg.append(boton("BUSCAR", "nx-chip", self.formulario))
            return
        modelo = Gtk.StringList()
        for v in vols:
            modelo.append(f"{v['label'] or v['modelo']} · {v['fs']} · {tam_humano(v['libre'])} libres")
        dd = Gtk.DropDown(model=modelo)
        self.c_cfg.append(dd)
        e1, e2 = (Gtk.PasswordEntry(show_peek_icon=True) for _ in range(2))
        for e, p in ((e1, "Clave del respaldo"), (e2, "Repite la clave")):
            e.set_property("placeholder-text", p)
            e.add_css_class("nx-entry")
            self.c_cfg.append(e)
        self.c_cfg.append(lbl(f"Mínimo {M.MIN_CLAVE} caracteres. ANÓTALA FUERA DE ESTA LAPTOP: sin ella la copia "
                              "no se puede abrir, y nadie puede recuperarla.", "nx-tag warn", xalign=0, wrap=True))

        def guardar():
            if e1.get_text() != e2.get_text():
                self.aviso("Las claves no coinciden", True)
                return
            v, clave = vols[dd.get_selected()], e1.get_text()
            b.set_sensitive(False)
            en_hilo(lambda: M.configurar(clave, uuid=v["uuid"]), self._configurado)

        b = boton("GUARDAR Y USAR ESTE DISCO", "nx-accion fuerte", guardar)
        self.c_cfg.append(b)

    def _configurado(self, nuevo, err):
        if err:
            self.aviso(err, True, 8000)
            self.formulario()
        else:
            self.aviso("Repositorio cifrado creado" if nuevo else "Clave correcta: repositorio existente")
            self.pintar_cfg()
            self.cargar()
        return False

    def cargar(self):
        c = M.config()
        if c.get("uuid") or c.get("ruta"):
            en_hilo(lambda: M.instantaneas(c), self._cargadas)
        return False

    def _cargadas(self, s, err):
        while f := self.lista.get_row_at_index(0):
            self.lista.remove(f)
        self.snaps = s or []
        self.sel = None
        self.b_rest.set_sensitive(False)
        if err:
            self.lista.append(lbl(err, "nx-dim", xalign=0, wrap=True))
        elif not self.snaps:
            self.lista.append(lbl("Todavía no hay copias.", "nx-dim", xalign=0))
        for x in self.snaps:
            caja = Gtk.Box(spacing=8)
            caja.add_css_class("nx-card")
            fecha = x["time"][:19].replace("T", " ")
            caja.append(lbl(fecha, "nx-txt", xalign=0, hexpand=True))
            r = x.get("summary", {})
            if r:
                caja.append(lbl(f"{r.get('total_files_processed', 0)} arch · {tam_humano(r.get('total_bytes_processed', 0))}",
                                "nx-code", ellipsize=Pango.EllipsizeMode.END))
            caja.append(lbl(x["short_id"], "nx-tag"))
            self.lista.append(caja)
        return False

    def _elegida(self, _l, fila):
        self.sel = self.snaps[fila.get_index()] if fila and fila.get_index() < len(self.snaps) else None
        self.b_rest.set_sensitive(bool(self.sel))

    def restaurar(self):
        if not self.sel:
            return
        s, inc = self.sel, self.e_inc.get_text()
        self.b_rest.set_sensitive(False)
        self.aviso("Restaurando…", ms=60000)
        en_hilo(lambda: M.restaurar(s["id"], inc), self._restaurado)

    def _restaurado(self, dst, err):
        self.b_rest.set_sensitive(True)
        if err:
            self.aviso(err, True, 8000)
        else:
            self.aviso(f"Restaurado en {dst}", ms=10000)
            Gio.AppInfo.launch_default_for_uri(Path(dst).as_uri(), None)
        return False


class App(AppNeural):
    def __init__(self):
        super().__init__(APP_ID)

    def crear_ventana(self, args, cwd):
        v = self.get_windows()
        v[0].present() if v else Ventana(self).present()


if __name__ == "__main__":
    nombrar_proceso("neural-resp-gui")
    GLib.set_prgname(APP_ID)
    sys.exit(App().run(sys.argv))
