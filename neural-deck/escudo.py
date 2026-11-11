#!/usr/bin/env python3
"""NEURAL//ESCUDO: cortafuegos (ufw) y qué está expuesto o conectado en este equipo.

Mirar no pide permisos; cambiar reglas usa neural-escudo-ayudante por polkit."""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk, Pango  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import escudo_datos as D  # noqa: E402
from ventana import AppNeural, VentanaNeural, boton, lbl, nombrar_proceso, on  # noqa: E402

APP_ID = "org.local.NeuralEscudo"
TEXTO_EST = {"ABIERTO": "ABIERTO A TODOS", "SOLO_RED": "SOLO TU RED", "CERRADO": "BLOQUEADO", "LOCAL": "SOLO ESTE EQUIPO"}
CLASE_EST = {"ABIERTO": "nx-tag warn", "SOLO_RED": "nx-tag acc", "CERRADO": "nx-tag", "LOCAL": "nx-tag"}


def limpiar(c):
    while h := c.get_first_child():
        c.remove(h)


def caja_lista():
    c = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
    s = Gtk.ScrolledWindow(child=c, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
    s.add_css_class("nx-scroll")
    return c, s


class Ventana(VentanaNeural):
    def __init__(self, app, pestana="reglas"):
        super().__init__(app, "SYS//SHIELD", "NEURAL//ESCUDO", (1040, 660))
        self.tabs = {}
        for k, t in (("reglas", "REGLAS"), ("expo", "PUERTOS"), ("con", "CONEXIONES"), ("blo", "BLOQUEOS")):
            b = boton(t, "nx-tab", lambda k=k: self.ir(k))
            self.tabs[k] = b
            self.cab_centro.append(b)
        self.pila = Gtk.Stack(vexpand=True, margin_top=8)
        self.cuerpo.append(self.pila)
        self.pila.add_named(self._armar_reglas(), "reglas")
        self.c_expo, s = caja_lista()
        self.pila.add_named(self._con_titulo("QUÉ ESTÁ ESCUCHANDO Y QUIÉN PUEDE LLEGAR", s), "expo")
        self.c_con, s = caja_lista()
        self.pila.add_named(self._con_titulo("CONEXIONES ACTIVAS", s), "con")
        self.c_blo, s = caja_lista()
        self.pila.add_named(self._con_titulo("INTENTOS BLOQUEADOS (ÚLTIMOS 7 DÍAS)", s), "blo")
        self.conf = {}
        self.ocupado = False
        self.firma = {}
        self.ir(pestana)
        self.refrescar()
        GLib.timeout_add(3000, self.refrescar)

    @staticmethod
    def _con_titulo(t, w):
        b = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        b.append(lbl(t, "nx-h2", xalign=0))
        b.append(w)
        return b

    def ir(self, k):
        self.tab = k
        for j, b in self.tabs.items():
            on(b, "on", j == k)
        self.pila.set_visible_child_name(k)
        self.refrescar()

    def refrescar(self):
        def trabajo():
            t = self.tab
            d = D.puertos() if t == "expo" else D.conexiones() if t == "con" else D.bloqueos() if t == "blo" else None
            GLib.idle_add(self.pintar, t, d)
        threading.Thread(target=trabajo, daemon=True).start()
        return True

    def pintar(self, t, d):
        activo = D.activo()
        inst = D.ayudante_instalado()
        self.p_izq.set_text(("CORTAFUEGOS ACTIVO" if activo else "CORTAFUEGOS APAGADO") +
                            ("" if inst else " · FALTA INSTALAR EL AYUDANTE: sudo ~/neural-deck/root/instalar.sh"))
        self._pintar_cab(activo)
        if t == "reglas":
            self._pintar_reglas()
        elif t != self.tab or d is None:
            pass
        else:
            f = repr(d)
            if self.firma.get(t) != f:
                self.firma[t] = f
                {"expo": self._pintar_expo, "con": self._pintar_con, "blo": self._pintar_blo}[t](d)
        return False

    # ---- reglas ----
    def _armar_reglas(self):
        b = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        cab = Gtk.Box(spacing=8)
        self.l_estado = lbl("", "nx-h1", xalign=0, hexpand=True)
        cab.append(self.l_estado)
        self.b_interr = boton("", "nx-accion", self.alternar)
        cab.append(self.b_interr)
        b.append(cab)
        self.c_reg, s = caja_lista()
        b.append(s)
        b.append(lbl("NUEVA REGLA DE ENTRADA", "nx-h2", xalign=0))
        f = Gtk.Box(spacing=6)
        self.e_p = Gtk.Entry(placeholder_text="Puerto o 8000:8010", width_chars=16)
        self.dd = Gtk.DropDown(model=Gtk.StringList.new(["TCP", "UDP"]))
        self.e_o = Gtk.Entry(text=D.red_local(), width_chars=20, tooltip_text='Una IP o red, o "any" para cualquiera')
        self.e_c = Gtk.Entry(placeholder_text="Para qué es (opcional)", hexpand=True)
        for w in (self.e_p, self.e_o, self.e_c):
            w.add_css_class("nx-entry")
        for w in (self.e_p, self.dd, self.e_o, self.e_c):
            f.append(w)
        f.append(boton("PERMITIR", "nx-accion fuerte", lambda: self.agregar("permitir")))
        f.append(boton("BLOQUEAR", "nx-accion peligro", lambda: self.agregar("denegar")))
        b.append(f)
        self.f_reg = None
        return b

    def _pintar_cab(self, activo):
        self.l_estado.set_text("PROTEGIDO" if activo else "SIN PROTECCIÓN")
        on(self.l_estado, "estado-mal", not activo)
        if not self.conf.get("interr"):
            self.b_interr.set_label("APAGAR" if activo else "ENCENDER")

    def _pintar_reglas(self):
        rs = D.reglas()
        f = repr(rs)
        if f == self.f_reg:
            return
        self.f_reg = f
        limpiar(self.c_reg)
        if not rs:
            self.c_reg.append(lbl("Sin reglas: no entra nada de fuera.", "nx-dim", xalign=0))
        for r in rs:
            c = Gtk.Box(spacing=10)
            c.add_css_class("nx-card")
            c.append(lbl("PERMITE" if r["accion"] in ("allow", "limit") else "BLOQUEA", "nx-tag acc" if r["accion"] in ("allow", "limit") else "nx-tag warn"))
            c.append(lbl(f"{r['puerto']} / {r['proto'].upper()}", "nx-txt", xalign=0, width_chars=18))
            c.append(lbl("CUALQUIERA" if r["origen"] == "any" else r["origen"], "nx-code on" if r["origen"] != "any" else "nx-code warn", xalign=0, width_chars=20))
            c.append(lbl(r["comentario"], "nx-dim", xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END))
            if r["editable"]:
                b = boton("BORRAR", "nx-accion peligro", lambda: None)
                b.connect("clicked", self._borrar, r)
                c.append(b)
            self.c_reg.append(c)

    def _borrar(self, b, r):
        k = repr(r)
        if time.monotonic() - self.conf.get(k, 0) > 3:
            self.conf[k] = time.monotonic()
            b.set_label("¿SEGURO?")
            GLib.timeout_add(3000, lambda: (b.set_label("BORRAR"), False)[1])
            return
        self.conf.pop(k, None)
        self.correr("Regla borrada", "borrar", r["accion"], r["proto"], r["puerto"], r["origen"])

    def agregar(self, verbo):
        p, o, c = self.e_p.get_text().strip(), self.e_o.get_text().strip() or "any", self.e_c.get_text().strip()
        if not p:
            self.aviso("Falta el puerto", True)
            return
        proto = ("tcp", "udp")[self.dd.get_selected()]
        if o == "any" and verbo == "permitir" and time.monotonic() - self.conf.get("any", 0) > 4:
            self.conf["any"] = time.monotonic()
            self.aviso("Eso lo abre a CUALQUIER red. Pulsa otra vez para confirmar", True, 4000)
            return
        self.conf.pop("any", None)
        args = [verbo, proto, p, o] + ([c] if c else [])
        if self.correr("Regla añadida", *args):
            self.e_p.set_text("")
            self.e_c.set_text("")

    def alternar(self):
        activo = D.activo()
        if activo and time.monotonic() - self.conf.get("apagar", 0) > 4:
            self.conf["apagar"] = time.monotonic()
            self.conf["interr"] = True
            self.b_interr.set_label("¿APAGAR?")
            def volver():
                self.conf.pop("interr", None)
                self.refrescar()
                return False
            GLib.timeout_add(4000, volver)
            return
        self.conf.pop("apagar", None)
        self.conf.pop("interr", None)
        self.correr("Cortafuegos apagado" if activo else "Cortafuegos encendido", "desactivar" if activo else "activar")

    def correr(self, ok, *args) -> bool:
        if self.ocupado:
            return False
        self.ocupado = True
        self.aviso("Pidiendo permiso…", ms=120000)

        def trabajo():
            e = D.ayudante(*args)
            GLib.idle_add(self._hecho, ok, e)
        threading.Thread(target=trabajo, daemon=True).start()
        return True

    def _hecho(self, ok, e):
        self.ocupado = False
        self.aviso(e or ok, bool(e), 6000)
        self.f_reg = None
        self.refrescar()
        return False

    # ---- listas ----
    @staticmethod
    def _fila(izq, sub, *der):
        c = Gtk.Box(spacing=10)
        c.add_css_class("nx-card")
        v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        v.append(lbl(izq, "nx-txt", xalign=0, ellipsize=Pango.EllipsizeMode.END))
        v.append(lbl(sub, "nx-code", xalign=0, ellipsize=Pango.EllipsizeMode.END))
        c.append(v)
        for t, cl in der:
            c.append(lbl(t, cl))
        return c

    def _pintar_expo(self, d):
        limpiar(self.c_expo)
        for x in d:
            self.c_expo.append(self._fila(f"{x['puerto']} / {x['proto'].upper()} · {x['proc']}", x["ip"], (TEXTO_EST[x["estado"]], CLASE_EST[x["estado"]])))

    def _pintar_con(self, d):
        limpiar(self.c_con)
        if not d:
            self.c_con.append(lbl("Sin conexiones activas", "nx-dim", xalign=0))
        for x in d:
            self.c_con.append(self._fila(x["app"], f"{x['ip']}:{x['puerto']} · {x['proto'].upper()}", (x["tipo"], "nx-tag warn" if x["tipo"] == "INTERNET" else "nx-tag acc")))

    def _pintar_blo(self, d):
        limpiar(self.c_blo)
        if not d:
            self.c_blo.append(lbl("Nadie ha intentado entrar.", "nx-dim", xalign=0))
        for x in d[:200]:
            self.c_blo.append(self._fila(f"{x['src']} → puerto {x['puerto'] or '?'} / {x['proto']}", f"última vez {x['ult'][:19].replace('T', ' ')} · {x['iface']}",
                                         (f"{x['veces']} VECES", "nx-code"), (x["tipo"], "nx-tag warn" if x["tipo"] == "INTERNET" else "nx-tag acc")))


class App(AppNeural):
    def __init__(self):
        super().__init__(APP_ID)

    def css_extra(self, t):
        from estilo import css_color
        return f".nx-h1.estado-mal {{ color: {css_color(t['warn'])}; }}\n"

    def crear_ventana(self, args, cwd):
        v = self.get_windows()
        if v:
            v[0].present()
            return
        Ventana(self, args[0] if args and args[0] in ("reglas", "expo", "con", "blo") else "reglas").present()


if __name__ == "__main__":
    nombrar_proceso("neural-escudo")
    GLib.set_prgname(APP_ID)
    sys.exit(App().run(sys.argv))
