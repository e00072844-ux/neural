#!/usr/bin/env python3
"""NEURAL//NEXO: ventana de la transferencia local de archivos.

Es solo el mando: el trabajo lo hace nexo_motor.py (servicio neural-nexo), así
que cerrar esta ventana no corta nada. Uso: nexo.py [archivos a enviar…]
Arrastra archivos sobre un dispositivo para enviárselos."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from estilo import css_color  # noqa: E402
from ventana import AppNeural, VentanaNeural, boton, lbl, nombrar_proceso, on, tam_humano  # noqa: E402

APP_ID = "org.local.NeuralNexo"
SOCK = Path(os.environ.get("NEXO_SOCK", Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "neural-nexo.sock"))
ESTADOS = {"pendiente": "ESPERA TU RESPUESTA", "ofrecida": "ESPERANDO RESPUESTA", "activa": "EN CURSO", "hecha": "COMPLETADO",
           "rechazada": "RECHAZADO", "cancelada": "CANCELADO", "fallida": "CON FALLOS"}
VIVOS = ("pendiente", "ofrecida", "activa")


def limpiar(caja: Gtk.Box) -> None:
    while h := caja.get_first_child():
        caja.remove(h)


def duracion(s: float) -> str:
    s = int(s)
    return f"{s // 3600}h {s % 3600 // 60:02d}m" if s >= 3600 else f"{s // 60}m {s % 60:02d}s" if s >= 60 else f"{s}s"


class Mando:
    """Conexión al servicio: reintenta sola y entrega cada estado al hilo de GTK."""

    def __init__(self, al_estado):
        self.al_estado = al_estado
        self.s: socket.socket | None = None
        threading.Thread(target=self._hilo, daemon=True).start()

    def _hilo(self):
        arrancado = False
        while True:
            try:
                c = socket.socket(socket.AF_UNIX)
                c.connect(str(SOCK))
                self.s = c
                with c.makefile("rb") as f:
                    for linea in f:
                        GLib.idle_add(self.al_estado, json.loads(linea))
            except (OSError, ValueError):
                if not arrancado:
                    arrancado = True
                    subprocess.run(["systemctl", "--user", "start", "neural-nexo"], check=False,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.s = None
            GLib.idle_add(self.al_estado, None)
            time.sleep(1)

    def orden(self, **d) -> bool:
        try:
            self.s.sendall(json.dumps(d).encode() + b"\n")
            return True
        except (OSError, AttributeError):
            return False


class Ventana(VentanaNeural):
    def __init__(self, app, rutas: list[str]):
        super().__init__(app, "SYS//LINK", "NEURAL//NEXO", (1040, 660))
        self.por_enviar = rutas
        self.est: dict | None = None
        self.f_disp = self.f_trans = self.f_alerta = None
        self.barras: dict[str, tuple] = {}
        self.conf_term = None

        izq = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, width_request=350)
        izq.append(lbl("DISPOSITIVOS", "nx-h2", xalign=0))
        self.c_disp = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        s = Gtk.ScrolledWindow(child=self.c_disp, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        s.add_css_class("nx-scroll")
        izq.append(s)
        fila = Gtk.Box(spacing=6)
        self.e_ip = Gtk.Entry(placeholder_text="Conectar por IP…", hexpand=True)
        self.e_ip.add_css_class("nx-entry")
        self.e_ip.connect("activate", lambda *_: self._ip())
        fila.append(self.e_ip)
        fila.append(boton("IR", "nx-accion", self._ip))
        izq.append(fila)

        der = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, hexpand=True)
        self.c_alerta = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        der.append(self.c_alerta)
        tit = Gtk.Box(spacing=8)
        tit.append(lbl("TRANSFERENCIAS", "nx-h2", xalign=0, hexpand=True))
        tit.append(boton("ABRIR CARPETA", "nx-chip", self._carpeta))
        tit.append(boton("LIMPIAR", "nx-chip", lambda: self.mando.orden(c="limpiar"), "Quitar las ya terminadas"))
        der.append(tit)
        self.c_trans = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        s = Gtk.ScrolledWindow(child=self.c_trans, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        s.add_css_class("nx-scroll")
        der.append(s)

        cuerpo = Gtk.Box(spacing=14, margin_top=8, vexpand=True)
        cuerpo.append(izq)
        cuerpo.append(der)
        self.cuerpo.append(cuerpo)
        self.mando = Mando(self.al_estado)

    # ---- estado ----
    def al_estado(self, d):
        if d is None:
            self.est = None
            self.f_disp = self.f_trans = self.f_alerta = None
            self.p_izq.set_text("SERVICIO DETENIDO · reintentando…")
            return False
        self.est = d
        for a in d.get("avisos", []):
            self.aviso(a)
        yo = d["yo"]
        self.p_izq.set_text(f"{yo['name']} · {' / '.join(yo['ips']) or 'SIN RED'} · "
                            f"BT {'ACTIVO' if yo['bt'] else 'INACTIVO'} · RECIBIDOS {yo['recibidos']}")
        firma = json.dumps([[x["id"], x["name"], x["par"], x["auto"], x["shell"], x["via"], x["ip"]] for x in d["disp"]])
        if firma != self.f_disp:
            self.f_disp = firma
            self._pintar_disp(d["disp"])
        alerta = json.dumps([d["par"], [t["tid"] for t in d["trans"] if t["estado"] == "pendiente"], self.por_enviar,
                             [[t["sid"], t["activa"]] for t in d.get("term", [])]])
        if alerta != self.f_alerta:
            self.f_alerta = alerta
            self._pintar_alertas(d)
        firma = json.dumps([[t["tid"], t["estado"]] for t in d["trans"] if t["estado"] != "pendiente"])
        if firma != self.f_trans:
            self.f_trans = firma
            self._pintar_trans(d["trans"])
        for t in d["trans"]:
            if t["tid"] in self.barras:
                self._progreso(t)
        return False

    def _pintar_disp(self, lista):
        limpiar(self.c_disp)
        if not lista:
            self.c_disp.append(lbl("Buscando en la red…\nAbre NEXO en el otro dispositivo.", "nx-dim", xalign=0))
        for x in sorted(lista, key=lambda v: (not v["par"], not v["via"], v["name"])):
            self.c_disp.append(self._tarjeta(x))

    def _tarjeta(self, x):
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        card.add_css_class("nx-card")
        arriba = Gtk.Box(spacing=8)
        arriba.append(lbl(x["name"], "nx-txt", xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END))
        arriba.append(lbl(x["kind"].upper(), "nx-tag acc"))
        if x["via"]:
            arriba.append(lbl("RED" if x["via"] == "lan" else "BLUETOOTH", "nx-tag"))
        elif x["par"]:
            arriba.append(lbl("SIN CONEXIÓN", "nx-tag warn"))
        card.append(arriba)
        if x["ip"]:
            card.append(lbl(x["ip"], "nx-code", xalign=0))
        abajo = Gtk.Box(spacing=6)
        if x["par"]:
            abajo.append(boton("ENVIAR", "nx-accion fuerte", lambda: self._elegir(x["id"])))
            auto = boton("AUTO-ACEPTAR", "nx-chip", lambda: self.mando.orden(c="auto", id=x["id"], v=not x["auto"]),
                         "Recibir de este dispositivo sin preguntar")
            on(auto, "on", x["auto"])
            abajo.append(auto)
            term = boton("TERMINAL", "nx-chip", lambda: self._terminal(x), "Permitir a este dispositivo abrir una terminal en esta laptop")
            on(term, "on", x["shell"])
            abajo.append(term)
            abajo.append(boton("OLVIDAR", "nx-chip", lambda: self.mando.orden(c="olvidar", id=x["id"])))
            drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
            drop.connect("accept", lambda *_: True)
            drop.connect("enter", lambda *_: (card.add_css_class("sel"), Gdk.DragAction.COPY)[1])
            drop.connect("leave", lambda *_: card.remove_css_class("sel"))
            drop.connect("drop", self._soltar, x["id"])
            card.add_controller(drop)
        else:
            abajo.append(boton("EMPAREJAR", "nx-accion", lambda: self.mando.orden(c="emparejar", id=x["id"])))
        card.append(abajo)
        return card

    def _terminal(self, x):
        if not x["shell"] and self.conf_term != x["id"]:
            self.conf_term = x["id"]
            self.aviso("Dará control total de tu usuario a ese dispositivo. Pulsa TERMINAL otra vez para confirmar", True, 5000)
            GLib.timeout_add(5000, lambda: (setattr(self, "conf_term", None), False)[1])
            return
        self.conf_term = None
        self.mando.orden(c="terminal", id=x["id"], v=not x["shell"])

    def _pintar_alertas(self, d):
        limpiar(self.c_alerta)
        for t in d.get("term", []):
            caja = Gtk.Box(spacing=8)
            caja.add_css_class("nx-card")
            caja.add_css_class("sel")
            estado = "CONECTADA" if t["activa"] else "EN ESPERA (sigue corriendo)"
            caja.append(lbl(f"TERMINAL REMOTA · {t['peer']} · {estado}", "nx-txt", xalign=0, hexpand=True))
            sid = t["sid"]
            caja.append(boton("CORTAR", "nx-accion peligro", lambda sid=sid: self.mando.orden(c="term_cortar", sid=sid)))
            self.c_alerta.append(caja)
        p = d["par"]
        if p:
            caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            caja.add_css_class("nx-card")
            caja.add_css_class("sel")
            caja.append(lbl(f"EMPAREJAR CON {p['name'].upper()}", "nx-h2", xalign=0))
            caja.append(lbl(f"{p['sas'][:3]} {p['sas'][3:]}", "nx-h1 nx-sas", xalign=0))
            caja.append(lbl("Confirma solo si en el otro dispositivo aparece el mismo código.", "nx-dim", xalign=0))
            fila = Gtk.Box(spacing=6)
            if p["yo_ok"]:
                fila.append(lbl("ESPERANDO AL OTRO DISPOSITIVO…", "nx-code on"))
            else:
                fila.append(boton("COINCIDE", "nx-accion fuerte", lambda: self.mando.orden(c="par_si")))
            fila.append(boton("CANCELAR", "nx-accion peligro", lambda: self.mando.orden(c="par_no")))
            caja.append(fila)
            self.c_alerta.append(caja)
        for t in d["trans"]:
            if t["estado"] != "pendiente":
                continue
            caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            caja.add_css_class("nx-card")
            caja.add_css_class("sel")
            caja.append(lbl(f"{t['peer'].upper()} QUIERE ENVIARTE", "nx-h2", xalign=0))
            caja.append(lbl(f"{t['titulo']} · {t['n']} archivo(s) · {tam_humano(t['total'])}", "nx-txt", xalign=0,
                            ellipsize=Pango.EllipsizeMode.MIDDLE))
            fila = Gtk.Box(spacing=6)
            tid = t["tid"]
            fila.append(boton("ACEPTAR", "nx-accion fuerte", lambda tid=tid: self.mando.orden(c="aceptar", tid=tid)))
            fila.append(boton("RECHAZAR", "nx-accion peligro", lambda tid=tid: self.mando.orden(c="rechazar", tid=tid)))
            caja.append(fila)
            self.c_alerta.append(caja)
        if self.por_enviar:
            caja = Gtk.Box(spacing=8)
            caja.add_css_class("nx-card")
            caja.append(lbl(f"{len(self.por_enviar)} elemento(s) listos: pulsa ENVIAR en un dispositivo", "nx-txt",
                            xalign=0, hexpand=True))
            caja.append(boton("QUITAR", "nx-chip", self._quitar_lista))
            self.c_alerta.append(caja)

    def _quitar_lista(self):
        self.por_enviar = []
        self._pintar_alertas(self.est)

    def _pintar_trans(self, lista):
        limpiar(self.c_trans)
        self.barras.clear()
        visibles = [t for t in lista if t["estado"] != "pendiente"]
        if not visibles:
            self.c_trans.append(lbl("Nada todavía. Arrastra archivos sobre un dispositivo.", "nx-dim", xalign=0))
        for t in visibles:
            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            card.add_css_class("nx-card")
            fila = Gtk.Box(spacing=8)
            flecha = "↓" if t["dir"] == "in" else "↑"
            fila.append(lbl(f"{flecha} {t['titulo']}", "nx-txt", xalign=0, hexpand=True,
                            ellipsize=Pango.EllipsizeMode.MIDDLE))
            cls = "nx-tag" if t["estado"] in ("hecha", "activa") else "nx-tag warn"
            fila.append(lbl(ESTADOS.get(t["estado"], t["estado"]), cls))
            if t["estado"] in VIVOS:
                tid = t["tid"]
                fila.append(boton("✕", "nx-btn cerrar", lambda tid=tid: self.mando.orden(c="cancelar", tid=tid), "Cancelar"))
            card.append(fila)
            info = lbl("", "nx-code", xalign=0, ellipsize=Pango.EllipsizeMode.END)
            barra = None
            if t["estado"] in ("activa", "ofrecida"):
                barra = Gtk.ProgressBar()
                barra.add_css_class("nx-prog")
                card.append(barra)
            card.append(info)
            self.barras[t["tid"]] = (barra, info)
            self.c_trans.append(card)

    def _progreso(self, t):
        barra, info = self.barras[t["tid"]]
        frac = t["hecho"] / t["total"] if t["total"] else 1
        if barra:
            barra.set_fraction(min(1, frac))
        p = [t["peer"], f"{t['n']} archivo(s)"]
        if t["estado"] == "activa":
            p.append(f"{tam_humano(t['hecho'])} / {tam_humano(t['total'])} · {frac * 100:.0f}%")
            if not t["enlace"]:
                p.append("CONEXIÓN PERDIDA: se reanudará sola")
            elif t["bps"] > 1024:
                p.append(f"{tam_humano(t['bps'])}/s · {duracion((t['total'] - t['hecho']) / t['bps'])}")
                p.append("POR BLUETOOTH" if t["enlace"] == "bt" else "")
            elif frac >= 1:
                p.append("verificando…")
        else:
            p.append(tam_humano(t["total"]))
        if t["error"]:
            p.append(t["error"])
        texto = " · ".join(x for x in p if x)
        if info.get_text() != texto:
            info.set_text(texto)

    # ---- acciones ----
    def _ip(self):
        v = self.e_ip.get_text().strip()
        if v and self.mando.orden(c="ip", v=v):
            self.e_ip.set_text("")
            self.aviso(f"Probando {v}…")

    def _carpeta(self):
        if self.est:
            d = self.est["yo"]["recibidos"]
            Path(d).mkdir(parents=True, exist_ok=True)
            Gio.AppInfo.launch_default_for_uri(Path(d).as_uri(), None)

    def enviar(self, pid, rutas):
        if self.mando.orden(c="enviar", id=pid, rutas=rutas):
            self.aviso("Enviando…")

    def _elegir(self, pid):
        if self.por_enviar:
            rutas, self.por_enviar = self.por_enviar, []
            self.enviar(pid, rutas)
            return
        Gtk.FileDialog(title="Elige archivos para enviar").open_multiple(self, None, self._elegidos, pid)

    def _elegidos(self, dlg, res, pid):
        try:
            lista = dlg.open_multiple_finish(res)
        except GLib.Error:
            return
        self.enviar(pid, [lista.get_item(i).get_path() for i in range(lista.get_n_items())])

    def _soltar(self, _t, valor, _x, _y, pid):
        rutas = [f.get_path() for f in valor.get_files() if f.get_path()]
        if rutas:
            self.enviar(pid, rutas)
        return True


class App(AppNeural):
    def __init__(self):
        super().__init__(APP_ID)

    def css_extra(self, t):
        return f".nx-sas {{ font-family: monospace; font-size: 34px; letter-spacing: 6px; color: {css_color(t['alt'])}; }}\n"

    def crear_ventana(self, args, cwd):
        rutas = [str(Path(cwd or ".", a).resolve()) for a in args]
        v = self.get_windows()
        if v:
            if rutas:
                v[0].por_enviar = rutas
                v[0].f_alerta = None
            v[0].present()
            return
        Ventana(self, rutas).present()


if __name__ == "__main__":
    nombrar_proceso("neural-nexo-gui")
    GLib.set_prgname(APP_ID)
    sys.exit(App().run(sys.argv))
