#!/usr/bin/env python3
"""NEURAL//PULSO: monitor del sistema (reemplaza btop, glances y amdgpu_top)."""
from __future__ import annotations

import sys
import threading
import time
from collections import deque
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk, Pango  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pulso_datos as D  # noqa: E402
from ventana import AppNeural, VentanaNeural, boton, lbl, nombrar_proceso, on, tam_humano  # noqa: E402

APP_ID = "org.local.NeuralPulso"
TIC = 1.5
HIST = 80


class Curva(Gtk.DrawingArea):
    """Historial en línea, con el color del tema."""

    def __init__(self, ventana, maximo=100.0, alto=46):
        super().__init__(content_height=alto, hexpand=True)
        self.v, self.maximo = ventana, maximo
        self.datos: deque = deque(maxlen=HIST)
        self.set_draw_func(self._dibujar)

    def poner(self, x):
        self.datos.append(x)
        self.queue_draw()

    def _dibujar(self, _a, cr, w, h):
        if len(self.datos) < 2:
            return
        c = self.v.theme["accent"]
        tope = self.maximo or max(max(self.datos), 1024.0)
        n = HIST - 1
        ini = HIST - len(self.datos)
        pts = [((ini + i) * w / n, h - 1 - min(1, x / tope) * (h - 3)) for i, x in enumerate(self.datos)]
        cr.move_to(pts[0][0], h)
        for x, y in pts:
            cr.line_to(x, y)
        cr.line_to(pts[-1][0], h)
        cr.close_path()
        cr.set_source_rgba(c.red, c.green, c.blue, 0.18)
        cr.fill()
        cr.set_line_width(1.4)
        cr.set_source_rgba(c.red, c.green, c.blue, 1)
        cr.move_to(*pts[0])
        for p in pts[1:]:
            cr.line_to(*p)
        cr.stroke()


class Nucleos(Gtk.DrawingArea):
    def __init__(self, ventana):
        super().__init__(content_height=26, hexpand=True)
        self.v = ventana
        self.valores: list[float] = []
        self.set_draw_func(self._dibujar)

    def poner(self, v):
        self.valores = v
        self.queue_draw()

    def _dibujar(self, _a, cr, w, h):
        n = len(self.valores)
        if not n:
            return
        a, b = self.v.theme["accent"], w / n
        for i, x in enumerate(self.valores):
            cr.set_source_rgba(a.red, a.green, a.blue, 0.14)
            cr.rectangle(i * b + 1, 0, b - 2, h)
            cr.fill()
            cr.set_source_rgba(a.red, a.green, a.blue, 1)
            alto = h * x / 100
            cr.rectangle(i * b + 1, h - alto, b - 2, alto)
            cr.fill()


def _tarjeta(titulo):
    c = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, hexpand=True, vexpand=True)
    c.add_css_class("nx-card")
    c.append(lbl(titulo, "nx-h2", xalign=0))
    return c


def _temp(t):
    return f"{t:.0f} °C" if t else "—"


def _vel(b):
    return f"{tam_humano(b)}/s"


class FilaProc(Gtk.Box):
    def __init__(self, v):
        super().__init__(spacing=10)
        self.add_css_class("nx-card")
        self.pid = 0
        self.n = lbl("", "nx-txt", xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        self.a = lbl("", "nx-code", xalign=1, width_chars=8)
        self.u = lbl("", "nx-code", xalign=0, width_chars=9, ellipsize=Pango.EllipsizeMode.END)
        self.c = lbl("", "nx-code on", xalign=1, width_chars=7)
        self.m = lbl("", "nx-code", xalign=1, width_chars=10)
        for w in (self.n, self.u, self.a, self.c, self.m):
            self.append(w)
        g = Gtk.GestureClick()
        g.connect("pressed", lambda *_: v.elegir(self.pid))
        self.add_controller(g)

    def poner(self, p, sel):
        self.pid = p["pid"]
        self.n.set_text(p["nombre"])
        self.a.set_text(str(p["pid"]))
        self.u.set_text(p["user"])
        self.c.set_text(f"{p['cpu']:.1f}%")
        self.m.set_text(tam_humano(p["mem"]))
        on(self, "sel", sel)


class Ventana(VentanaNeural):
    def __init__(self, app, pestana="resumen"):
        super().__init__(app, "SYS//PULSE", "NEURAL//PULSO", (1040, 680))
        self.tab = pestana
        self.tabs = {}
        for k, t in (("resumen", "RESUMEN"), ("procesos", "PROCESOS"), ("red", "RED")):
            b = boton(t, "nx-tab", lambda k=k: self.ir(k))
            self.tabs[k] = b
            self.cab_centro.append(b)
        self.pila = Gtk.Stack(vexpand=True, margin_top=8)
        self.cuerpo.append(self.pila)
        self.pila.add_named(self._armar_resumen(), "resumen")
        self.pila.add_named(self._armar_procesos(), "procesos")
        self.pila.add_named(self._armar_red(), "red")
        self.sel_pid = None
        self.procs: list = []
        self.orden = "cpu"
        self.conf = 0.0
        self.m = D.Muestreo()
        self.vivo = True
        self.ir(pestana)
        self.connect("close-request", self._cerrar)
        threading.Thread(target=self._hilo, daemon=True).start()

    def _cerrar(self, *_):
        self.vivo = False
        self.m.gpu.vivo = False
        return False

    def ir(self, k):
        self.tab = k
        for j, b in self.tabs.items():
            on(b, "on", j == k)
        self.pila.set_visible_child_name(k)

    def _hilo(self):
        psp = D.procesos()  # primera lectura: fija la base del % de CPU
        del psp
        while self.vivo:
            t0 = time.monotonic()
            s = self.m.sistema()
            p = D.procesos() if self.tab == "procesos" else None
            r = self.m.apps_red() if self.tab == "red" else None
            GLib.idle_add(self.aplicar, s, p, r)
            time.sleep(max(0.2, TIC - (time.monotonic() - t0)))

    # ---- resumen ----
    def _armar_resumen(self):
        g = Gtk.Grid(row_spacing=8, column_spacing=8, column_homogeneous=True, row_homogeneous=True, vexpand=True)
        self.c = c = {}
        def tarjeta(k, tit, x, y, curva_max=100.0):
            t = _tarjeta(tit)
            c[k + "_h"] = lbl("—", "nx-h1", xalign=0)
            t.append(c[k + "_h"])
            c[k + "_d"] = lbl("", "nx-dim", xalign=0, wrap=True)
            t.append(c[k + "_d"])
            if curva_max is not False:
                c[k + "_c"] = Curva(self, curva_max)
                t.append(c[k + "_c"])
            g.attach(t, x, y, 1, 1)
            return t
        t = tarjeta("cpu", "CPU", 0, 0)
        self.nucleos = Nucleos(self)
        t.append(self.nucleos)
        tarjeta("ram", "MEMORIA", 1, 0)
        tarjeta("gpu", "GPU", 2, 0)
        tarjeta("bat", "BATERÍA", 0, 1, False)
        tarjeta("dis", "DISCO", 1, 1, 0.0)
        tarjeta("net", "RED", 2, 1, 0.0)
        return g

    def aplicar(self, s, procs, red):
        if not self.vivo:
            return False
        c = self.c
        t = s["temp"]
        c["cpu_h"].set_text(f"{s['cpu']:.0f}%")
        c["cpu_d"].set_text(f"{s['ghz']:.1f} GHz · {_temp(t['cpu'])} · carga {s['carga'][0]:.2f}" +
                            (f" · ventilador {t['ventilador']} RPM" if t["ventilador"] is not None else ""))
        c["cpu_c"].poner(s["cpu"])
        self.nucleos.poner(s["nucleos"])
        c["ram_h"].set_text(f"{s['ram_pct']:.0f}%")
        c["ram_d"].set_text(f"{tam_humano(s['ram'])} de {tam_humano(s['ram_t'])} · swap {tam_humano(s['swap'])}")
        c["ram_c"].poner(s["ram_pct"])
        g = s["gpu"]
        if g:
            c["gpu_h"].set_text(f"{g['gfx']}%")
            c["gpu_d"].set_text(f"VRAM {g['vram']} / {g['vram_t']} MiB · GTT {g['gtt']} MiB · {_temp(t['gpu'])}")
            c["gpu_c"].poner(g["gfx"])
        b = s["bat"]
        if b:
            est = "ENCHUFADA" if b["enchufada"] else "EN BATERÍA"
            c["bat_h"].set_text(f"{b['pct']:.0f}% · {est}")
            p = [f"{b['w']:.1f} W"] if b["w"] else []
            if b["seg"]:
                p.append(f"{b['seg'] // 3600}h {b['seg'] % 3600 // 60:02d}m restantes")
            if b["salud"]:
                p.append(f"salud {b['salud']:.0f}%")
            if b["ciclos"]:
                p.append(f"{b['ciclos']} ciclos")
            c["bat_d"].set_text(" · ".join(p))
        else:
            c["bat_h"].set_text("SIN BATERÍA")
        c["dis_h"].set_text(_vel(s["lee"] + s["esc"]))
        c["dis_d"].set_text(f"lee {_vel(s['lee'])} · escribe {_vel(s['esc'])} · NVMe {_temp(t['nvme'])}")
        c["dis_c"].poner(s["lee"] + s["esc"])
        baja, sube = sum(i["baja"] for i in s["ifaces"]), sum(i["sube"] for i in s["ifaces"])
        c["net_h"].set_text(f"↓ {_vel(baja)}")
        c["net_d"].set_text(f"↑ {_vel(sube)} · " + " ".join(i["n"] for i in s["ifaces"]))
        c["net_c"].poner(baja + sube)
        self.p_izq.set_text(f"{s['n_proc']} PROCESOS · RENOVANDO CADA {TIC:.1f} s")
        if procs is not None:
            self.procs = procs
            self.pintar_procs()
        if red is not None:
            self.pintar_red(red)
        return False

    # ---- procesos ----
    def _armar_procesos(self):
        b = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        f = Gtk.Box(spacing=6)
        self.e_q = Gtk.Entry(placeholder_text="Buscar proceso…", hexpand=True)
        self.e_q.add_css_class("nx-entry")
        self.e_q.connect("changed", lambda *_: self.pintar_procs())
        f.append(self.e_q)
        self.c_orden = {}
        for k, t in (("cpu", "CPU"), ("mem", "MEMORIA"), ("nombre", "NOMBRE")):
            x = boton(t, "nx-chip", lambda k=k: self.ordenar(k))
            self.c_orden[k] = x
            f.append(x)
        on(self.c_orden["cpu"], "on", True)
        b.append(f)
        self.c_filas = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        s = Gtk.ScrolledWindow(child=self.c_filas, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        s.add_css_class("nx-scroll")
        b.append(s)
        self.filas: list[FilaProc] = []
        a = Gtk.Box(spacing=8)
        self.l_sel = lbl("Elige un proceso", "nx-code", xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.MIDDLE)
        a.append(self.l_sel)
        a.append(boton("CERRAR", "nx-accion", lambda: self.senal(False), "Pedirle que se cierre"))
        self.b_forzar = boton("FORZAR", "nx-accion peligro", lambda: self.senal(True), "Matarlo sin que guarde nada")
        a.append(self.b_forzar)
        b.append(a)
        return b

    def ordenar(self, k):
        self.orden = k
        for j, x in self.c_orden.items():
            on(x, "on", j == k)
        self.pintar_procs()

    def pintar_procs(self):
        v = D.filtrar(self.procs, self.e_q.get_text(), self.orden)
        while len(self.filas) < len(v):
            f = FilaProc(self)
            self.filas.append(f)
            self.c_filas.append(f)
        for i, f in enumerate(self.filas):
            if i < len(v):
                f.poner(v[i], v[i]["pid"] == self.sel_pid)
                f.set_visible(True)
            else:
                f.set_visible(False)
        p = next((x for x in self.procs if x["pid"] == self.sel_pid), None)
        self.l_sel.set_text(f"{p['nombre']} · PID {p['pid']} · {p['cmd']}" if p else "Elige un proceso")

    def elegir(self, pid):
        self.sel_pid = pid
        self.pintar_procs()

    def senal(self, forzar):
        if self.sel_pid is None:
            return
        if forzar and time.monotonic() - self.conf > 3:
            self.conf = time.monotonic()
            self.aviso("Pulsa FORZAR otra vez: perderá lo que no haya guardado", True, 3000)
            return
        self.conf = 0.0
        e = D.senal(self.sel_pid, forzar)
        self.aviso(e or ("Forzado" if forzar else "Se le pidió cerrar"), bool(e))

    # ---- red ----
    def _armar_red(self):
        b = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        b.append(lbl("TRÁFICO POR APP (TCP, DE TU USUARIO)", "nx-h2", xalign=0))
        self.c_red = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        s = Gtk.ScrolledWindow(child=self.c_red, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        s.add_css_class("nx-scroll")
        b.append(s)
        return b

    def pintar_red(self, apps):
        while h := self.c_red.get_first_child():
            self.c_red.remove(h)
        if not apps:
            self.c_red.append(lbl("Sin conexiones activas", "nx-dim", xalign=0))
        for a in apps:
            c = Gtk.Box(spacing=10)
            c.add_css_class("nx-card")
            iz = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
            iz.append(lbl(a["app"], "nx-txt", xalign=0))
            iz.append(lbl(f"{a['conex']} conexión(es) · {' '.join(a['peers'])}", "nx-code", xalign=0,
                          ellipsize=Pango.EllipsizeMode.END))
            c.append(iz)
            c.append(lbl(f"↓ {_vel(a['baja'])}", "nx-code on", xalign=1, width_chars=14))
            c.append(lbl(f"↑ {_vel(a['sube'])}", "nx-code", xalign=1, width_chars=14))
            c.append(lbl(f"{tam_humano(a['rx'] + a['tx'])}", "nx-code", xalign=1, width_chars=10))
            self.c_red.append(c)


class App(AppNeural):
    def __init__(self):
        super().__init__(APP_ID)

    def crear_ventana(self, args, cwd):
        v = self.get_windows()
        if v:
            v[0].present()
            return
        Ventana(self, args[0] if args and args[0] in ("resumen", "procesos", "red") else "resumen").present()


if __name__ == "__main__":
    nombrar_proceso("neural-pulso")
    GLib.set_prgname(APP_ID)
    sys.exit(App().run(sys.argv))
