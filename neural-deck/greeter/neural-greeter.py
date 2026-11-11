#!/usr/bin/env python3
"""NEURAL//LOGIN: pantalla de inicio de sesión (greeter de greetd) con el estilo
de NEURAL//DECK.

Corre como el usuario «greeter» dentro de un Wayfire mínimo
(/etc/greetd/wayfire-greeter.ini) que lanza greetd. Habla con greetd por su
socket ($GREETD_SOCK, JSON con longitud de 4 bytes delante). Al iniciar bien la
sesión, sale y cierra su Wayfire; greetd arranca entonces la sesión del usuario
(Wayfire + NEURAL//DECK, vía «bash -l» para que cargue ~/.bash_profile).

Sin $GREETD_SOCK (o con --probar) es solo una vista previa: no inicia nada y
Esc la cierra.

Instalación: greeter/instalar.sh (copia esto a /usr/local/share/neural-greeter).
"""
from __future__ import annotations

import json
import os
import pwd
import shlex
import socket
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

import faulthandler  # noqa: E402
import signal  # noqa: E402

os.environ.setdefault("GTK_A11Y", "none")  # sin bus de accesibilidad
if os.environ.get("GREETD_SOCK") and "--probar" not in sys.argv:
    # El greeter no necesita bus de sesión (login = socket de greetd; apagar =
    # bus del sistema). El del usuario «greeter» intentaba activar
    # xdg-desktop-portal (falla: "sesión inactiva") y GTK se quedaba esperando:
    # ventana nunca creada = pantalla negra.
    os.environ["DBUS_SESSION_BUS_ADDRESS"] = "disabled:"
    os.environ["GDK_DEBUG"] = "no-portals"
    os.environ["GTK_USE_PORTAL"] = "0"
faulthandler.enable()
faulthandler.register(signal.SIGUSR1)  # `pkill -USR1 -f neural-greeter` = traza al log
print("greeter: inicio", time.strftime("%H:%M:%S"), flush=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(1, str(Path(__file__).resolve().parent.parent))  # vista previa desde ~/neural-deck/greeter

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from estilo import MONO, PAD, SANS, Frame, css_color, load_themes, register_fonts  # noqa: E402

AQUI = Path(__file__).resolve().parent
CONF = Path("/etc/greetd/neural")          # tema elegido (lo escribe instalar.sh)
ESTADO = Path("/var/lib/greetd/neural-ultimo")  # último usuario (el HOME de «greeter» en Arch es «/»)
MARCA_OK = Path("/var/lib/greetd/neural-login-ok")  # la lee /usr/local/bin/neural-login
SESION_ULTIMA = Path("/var/lib/greetd/neural-sesion")  # id de la última sesión elegida
SESIONES_DIR = Path("/usr/share/wayland-sessions")
SOCK = os.environ.get("GREETD_SOCK")
PRUEBA = "--probar" in sys.argv or not SOCK

SESION_CMD = ["/bin/bash", "-l", "-c", 'export GTK_A11Y=none; exec wayfire > "$HOME/wayfire.log" 2>&1']
SESION_ENV = ["XDG_SESSION_TYPE=wayland", "XDG_SESSION_DESKTOP=wayfire", "XDG_CURRENT_DESKTOP=Wayfire"]

DIAS = ("LUNES", "MARTES", "MIÉRCOLES", "JUEVES", "VIERNES", "SÁBADO", "DOMINGO")
MESES = ("ENERO", "FEBRERO", "MARZO", "ABRIL", "MAYO", "JUNIO", "JULIO", "AGOSTO", "SEPTIEMBRE", "OCTUBRE",
         "NOVIEMBRE", "DICIEMBRE")
_MODIFICADORES = {Gdk.KEY_Caps_Lock, Gdk.KEY_Shift_L, Gdk.KEY_Shift_R, Gdk.KEY_Control_L, Gdk.KEY_Control_R,
                  Gdk.KEY_Alt_L, Gdk.KEY_Alt_R, Gdk.KEY_Super_L, Gdk.KEY_Super_R, Gdk.KEY_Num_Lock,
                  Gdk.KEY_ISO_Level3_Shift, Gdk.KEY_Meta_L, Gdk.KEY_Meta_R}


# ---------------------------------------------------------------- greetd IPC
class Greetd:
    def __init__(self, ruta: str):
        self.s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.s.connect(ruta)

    def pedir(self, msg: dict) -> dict:
        datos = json.dumps(msg).encode()
        self.s.sendall(struct.pack("=I", len(datos)) + datos)
        n = struct.unpack("=I", self._leer(4))[0]
        return json.loads(self._leer(n))

    def _leer(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            parte = self.s.recv(n - len(buf))
            if not parte:
                raise ConnectionError("greetd cerró el socket")
            buf += parte
        return buf


def sesiones() -> list[dict]:
    """NEURAL primero (comando propio, siempre disponible) y luego las de wayland-sessions.
    wayfire.desktop y neural.desktop se omiten: serían NEURAL arrancado de otra forma."""
    out = [{"id": "neural", "nombre": "NEURAL", "cmd": SESION_CMD, "env": SESION_ENV}]
    for f in sorted(SESIONES_DIR.glob("*.desktop")):
        if f.stem in ("neural", "wayfire"):
            continue
        k = GLib.KeyFile()
        try:
            k.load_from_file(str(f), GLib.KeyFileFlags.NONE)
            g = "Desktop Entry"

            def leer(fn, clave, defecto):
                try:
                    return fn(g, clave)
                except GLib.Error:  # clave ausente
                    return defecto
            if leer(k.get_boolean, "Hidden", False) or leer(k.get_boolean, "NoDisplay", False):
                continue
            nombre = k.get_locale_string(g, "Name", None)
            ejecutar = k.get_string(g, "Exec")
            nombres = [n for n in leer(k.get_string_list, "DesktopNames", []) if n] or [f.stem]
        except GLib.Error:
            continue
        ejecutar = " ".join(t for t in ejecutar.split() if not (len(t) == 2 and t[0] == "%"))
        if not ejecutar:
            continue
        out.append({"id": f.stem, "nombre": nombre.upper(),
                    "cmd": ["/bin/bash", "-l", "-c", f"exec {ejecutar}"],
                    "env": ["XDG_SESSION_TYPE=wayland", f"XDG_SESSION_DESKTOP={nombres[0].lower()}",
                            f"XDG_CURRENT_DESKTOP={':'.join(nombres)}"]})
    return out


def entrar(usuario: str, clave: str, sesion: dict | None = None) -> tuple[bool, str]:
    """Autentica e inicia la sesión. Devuelve (ok, mensaje)."""
    g = Greetd(SOCK)
    r = g.pedir({"type": "create_session", "username": usuario})
    avisos = []
    while r.get("type") == "auth_message":
        tipo = r.get("auth_message_type")
        if tipo in ("secret", "visible"):
            r = g.pedir({"type": "post_auth_message_response", "response": clave})
        else:  # info / error de PAM: anotarlo y seguir
            if r.get("auth_message"):
                avisos.append(r["auth_message"].strip())
            r = g.pedir({"type": "post_auth_message_response"})
    if r.get("type") != "success":
        g.pedir({"type": "cancel_session"})
        if r.get("error_type") == "auth_error":
            return False, "CONTRASEÑA INCORRECTA"
        return False, (r.get("description") or " · ".join(avisos) or "ERROR").upper()[:90]
    # greetd NO hace execve del vector: lo une con espacios y lo pasa a
    # «/bin/sh -c "exec …"» (greetd/src/session/worker.rs), así que va citado.
    cmd, env = (sesion["cmd"], sesion["env"]) if sesion else (SESION_CMD, SESION_ENV)
    r = g.pedir({"type": "start_session", "cmd": [shlex.join(cmd)], "env": env})
    if r.get("type") != "success":
        g.pedir({"type": "cancel_session"})
        return False, (r.get("description") or "NO SE PUDO INICIAR LA SESIÓN").upper()[:90]
    try:  # para neural-login: sesión aceptada, no pasar a tuigreet
        MARCA_OK.touch()
    except OSError:
        pass
    return True, ""


# ---------------------------------------------------------------- utilidades
def usuarios() -> list[str]:
    out = []
    for p in pwd.getpwall():
        if 1000 <= p.pw_uid < 60000 and not p.pw_shell.endswith(("nologin", "false")):
            out.append(p.pw_name)
    return sorted(out)


def tema() -> dict:
    temas = load_themes()
    try:
        tid = (CONF / "tema").read_text().strip()
    except OSError:
        try:  # vista previa: el tema del escritorio
            tid = json.loads((Path.home() / ".config/live-wallpaper/board.json").read_text()).get("theme", "red")
        except (OSError, ValueError):
            tid = "red"
    return temas.get(tid, next(iter(temas.values())))


def bateria() -> str:
    for b in Path("/sys/class/power_supply").glob("BAT*"):
        try:
            cap = (b / "capacity").read_text().strip()
            st = (b / "status").read_text().strip()
        except OSError:
            continue
        return f"BAT {cap}%" + (" · CARGANDO" if st == "Charging" else "")
    return ""


def logind(metodo: str):
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM)
        bus.call_sync("org.freedesktop.login1", "/org/freedesktop/login1", "org.freedesktop.login1.Manager",
                      metodo, GLib.Variant("(b)", (True,)), None, Gio.DBusCallFlags.NONE, 10000, None)
        return None
    except GLib.Error as e:
        return e.message


def css(t: dict) -> str:
    acc, alt, warn, text, muted = t["accent"], t["alt"], t["warn"], t["text"], t["muted"]
    return f"""
window.neural-login {{
  background-color: #000;
  background-image: radial-gradient(circle at 50% 42%, {css_color(acc, 0.16)}, transparent 55%),
                    repeating-linear-gradient(0deg, {css_color(acc, 0.035)} 0px, {css_color(acc, 0.035)} 1px,
                                              transparent 1px, transparent 4px);
}}
.ng-clock {{ font-family: "{SANS}"; font-weight: 700; font-size: 120px; letter-spacing: 6px; color: {css_color(acc)};
            text-shadow: 0 0 24px {css_color(acc, 0.55)}; }}
.ng-date {{ font-family: "{MONO}"; font-size: 15px; letter-spacing: 4px; color: {css_color(text)}; }}
.ng-code {{ font-family: "{MONO}"; font-size: 11px; letter-spacing: 2px; color: {css_color(muted)}; }}
.ng-user {{ all: unset; font-family: "{SANS}"; font-weight: 700; font-size: 20px; letter-spacing: 3px;
           color: {css_color(muted)}; padding: 2px 10px; border-bottom: 2px solid transparent; }}
.ng-user:hover {{ color: {css_color(text)}; }}
.ng-user.on {{ color: {css_color(alt)}; border-bottom-color: {css_color(alt)}; }}
.ng-pass {{ background: rgba(0, 0, 0, 0.7); border: 1px solid {css_color(acc, 0.7)}; border-radius: 0;
           color: {css_color(text)}; font-family: "{MONO}"; font-size: 16px; min-height: 34px; caret-color: {css_color(alt)}; }}
.ng-pass:focus-within {{ border-color: {css_color(alt)}; box-shadow: 0 0 10px {css_color(alt, 0.5)}; }}
.ng-msg {{ font-family: "{MONO}"; font-size: 12px; letter-spacing: 2px; color: {css_color(muted)}; }}
.ng-msg.warn {{ color: {css_color(warn)}; }}
.ng-msg.ok {{ color: {css_color(alt)}; }}
.ng-btn {{ all: unset; font-family: "{MONO}"; font-size: 12px; letter-spacing: 2px; color: {css_color(muted)};
          padding: 6px 14px; border: 1px solid {css_color(acc, 0.35)}; }}
.ng-btn:hover {{ color: {css_color(text)}; background: {css_color(acc, 0.12)}; border-color: {css_color(acc)}; }}
.ng-btn.armado {{ color: {css_color(warn)}; border-color: {css_color(warn)}; }}
"""


def lbl(texto, cls, **kw):
    lb = Gtk.Label(label=texto, **kw)
    lb.add_css_class(cls)
    return lb


# ---------------------------------------------------------------- ventana
class Login(Gtk.ApplicationWindow):
    def __init__(self, app: "App"):
        super().__init__(application=app, title="NEURAL//LOGIN")
        self.app, self.theme = app, app.theme
        self.add_css_class("neural-login")
        self.ocupado = False
        self.armado: dict[str, float] = {}

        raiz = Gtk.Overlay()
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, halign=Gtk.Align.CENTER,
                      valign=Gtk.Align.CENTER)
        host = socket.gethostname().upper()
        col.append(lbl(f"NEURAL//DECK  ·  SYS//LOGIN  ·  {host}", "ng-code"))
        self.clock = lbl("", "ng-clock")
        self.date = lbl("", "ng-date")
        col.append(self.clock)
        col.append(self.date)

        frame = Frame(self, Gtk.Orientation.VERTICAL, 14, ("tl", "br"), True, "AUTH// 00")
        frame.set_margin_top(28)
        caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_start=PAD + 22,
                       margin_end=PAD + 22, margin_top=PAD + 26, margin_bottom=PAD + 18)
        caja.set_size_request(360, -1)
        self.fila_users = Gtk.Box(spacing=6)
        self.botones_user: dict[str, Gtk.Button] = {}
        lista = usuarios() or ["root"]
        try:
            ultimo = ESTADO.read_text().strip()
        except OSError:
            ultimo = ""
        self.usuario = ultimo if ultimo in lista else lista[0]
        for u in lista:
            b = Gtk.Button(label=u.upper())
            b.add_css_class("ng-user")
            b.connect("clicked", lambda _b, u=u: self.elegir(u))
            self.botones_user[u] = b
            self.fila_users.append(b)
        caja.append(self.fila_users)
        self.entry = Gtk.PasswordEntry(placeholder_text="contraseña", show_peek_icon=False)
        self.entry.add_css_class("ng-pass")
        self.entry.connect("activate", lambda e: self.intentar(e.get_text()))
        caja.append(self.entry)
        self.msg = lbl("ENTER = ENTRAR", "ng-msg", xalign=0)
        caja.append(self.msg)
        self.sesiones = sesiones()
        try:
            ultima = SESION_ULTIMA.read_text().strip()
        except OSError:
            ultima = ""
        self.i_sesion = next((i for i, x in enumerate(self.sesiones) if x["id"] == ultima), 0)
        self.b_sesion = Gtk.Button(halign=Gtk.Align.START, focusable=False)
        self.b_sesion.add_css_class("ng-btn")
        self.b_sesion.connect("clicked", lambda _b: self.cambiar_sesion(1))
        self.b_sesion.set_visible(len(self.sesiones) > 1)
        self._pintar_sesion()
        caja.append(self.b_sesion)
        frame.append(caja)
        col.append(frame)
        self.extra = lbl("", "ng-code")
        self.extra.set_margin_top(18)
        col.append(self.extra)
        raiz.set_child(col)

        abajo = Gtk.Box(spacing=10, halign=Gtk.Align.END, valign=Gtk.Align.END, margin_end=28, margin_bottom=24)
        self.b_rei = Gtk.Button(label="↻ REINICIAR")
        self.b_apa = Gtk.Button(label="⏻ APAGAR")
        for b, m in ((self.b_rei, "Reboot"), (self.b_apa, "PowerOff")):
            b.add_css_class("ng-btn")
            b.connect("clicked", lambda _b, m=m: self.energia(m, _b))
            abajo.append(b)
        raiz.add_overlay(abajo)
        izq = lbl("Ctrl+Alt+Retroceso = login en modo texto  ·  Ctrl+Alt+F1 = consola de rescate", "ng-code", halign=Gtk.Align.START,
                  valign=Gtk.Align.END, margin_start=28, margin_bottom=28)
        raiz.add_overlay(izq)
        if PRUEBA:
            raiz.add_overlay(lbl("VISTA PREVIA · Esc = cerrar", "ng-code", halign=Gtk.Align.CENTER,
                                 valign=Gtk.Align.START, margin_top=20))
        self.set_child(raiz)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._tecla)
        self.add_controller(keys)
        tab = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        tab.connect("key-pressed", self._tab)
        self.add_controller(tab)
        self.elegir(self.usuario)
        self.tick()
        self._reloj = GLib.timeout_add_seconds(1, lambda: (self.tick(), True)[1])
        self.connect("destroy", lambda *_: GLib.source_remove(self._reloj))

    # ---------- interacción ----------
    def elegir(self, u: str):
        self.usuario = u
        for nombre, b in self.botones_user.items():
            (b.add_css_class if nombre == u else b.remove_css_class)("on")
        self.entry.set_text("")
        GLib.idle_add(lambda: (self._enfocar(), False)[1])

    def _tecla(self, _c, keyval, *_):
        if keyval == Gdk.KEY_Escape:
            if PRUEBA:
                self.app.quit()
            else:
                self.entry.set_text("")
            return True
        if keyval in _MODIFICADORES:
            return False
        foco = self.get_focus()
        if foco is None or not (foco == self.entry or foco.is_ancestor(self.entry)):
            self._enfocar()
        return False

    def _tab(self, _c, keyval, *_):
        if keyval in (Gdk.KEY_Tab, Gdk.KEY_ISO_Left_Tab, Gdk.KEY_KP_Tab):
            self.cambiar_sesion(-1 if keyval == Gdk.KEY_ISO_Left_Tab else 1)
            return True
        return False

    def cambiar_sesion(self, d: int):
        if len(self.sesiones) > 1 and not self.ocupado:
            self.i_sesion = (self.i_sesion + d) % len(self.sesiones)
            self._pintar_sesion()

    def _pintar_sesion(self):
        self.b_sesion.set_label(f"SESIÓN  ◂ {self.sesiones[self.i_sesion]['nombre']} ▸   (Tab)")

    def _enfocar(self):
        texto = self.entry.get_delegate()
        if texto is not None and hasattr(texto, "grab_focus_without_selecting"):
            texto.grab_focus_without_selecting()
        else:
            self.entry.grab_focus()

    def mensaje(self, texto: str, cls: str = ""):
        self.msg.set_text(texto)
        for c in ("warn", "ok"):
            (self.msg.add_css_class if c == cls else self.msg.remove_css_class)(c)

    def tick(self):
        now = time.localtime()
        self.clock.set_text(time.strftime("%H:%M", now))
        self.date.set_text(f"{DIAS[now.tm_wday]} {now.tm_mday:02d} {MESES[now.tm_mon - 1]} {now.tm_year}")
        self.extra.set_text(bateria())

    def intentar(self, clave: str):
        if self.ocupado or not clave:
            return
        if PRUEBA:
            self.entry.set_text("")
            return self.mensaje("VISTA PREVIA: NO INICIA NADA", "warn")
        self.ocupado = True
        self.entry.set_sensitive(False)
        self.mensaje("VERIFICANDO…")
        usuario = self.usuario
        sesion = self.sesiones[self.i_sesion]

        def trabajo():
            try:
                ok, texto = entrar(usuario, clave, sesion)
                if ok:
                    try:
                        SESION_ULTIMA.write_text(sesion["id"] + "\n")
                    except OSError:
                        pass
            except (OSError, ValueError, ConnectionError) as e:
                ok, texto = False, f"GREETD: {e}"[:90]
            GLib.idle_add(self._resultado, ok, texto, usuario)
        threading.Thread(target=trabajo, daemon=True).start()

    def _resultado(self, ok, texto, usuario):
        if ok:
            self.mensaje(f"BIENVENIDO, {usuario.upper()}", "ok")
            try:
                ESTADO.write_text(usuario + "\n")
            except OSError:
                pass
            GLib.timeout_add(400, self.app.terminar)
            return False
        self.ocupado = False
        self.entry.set_sensitive(True)
        self.entry.set_text("")
        self.mensaje(texto, "warn")
        self._enfocar()
        return False

    def energia(self, metodo: str, boton: Gtk.Button):
        """Doble clic en 3 s para confirmar (como el panel rápido)."""
        ahora = time.monotonic()
        if ahora - self.armado.get(metodo, 0) > 3:
            self.armado[metodo] = ahora
            boton.add_css_class("armado")
            texto = boton.get_label()
            boton.set_label("¿SEGURO? CLIC OTRA VEZ")
            GLib.timeout_add(3000, lambda: (boton.remove_css_class("armado"), boton.set_label(texto), False)[2])
            return
        if PRUEBA:
            return self.mensaje(f"VISTA PREVIA: {metodo.upper()} NO SE EJECUTA", "warn")
        err = logind(metodo)
        if err:
            self.mensaje(err.upper()[:90], "warn")


class App(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="org.local.NeuralLogin", flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.theme = None

    def do_startup(self):
        Gtk.Application.do_startup(self)
        register_fonts()
        self.theme = tema()
        prov = Gtk.CssProvider()
        prov.load_from_string(css(self.theme))
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), prov,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_USER + 1)

    def do_activate(self):
        if PRUEBA and "--pantalla-completa" not in sys.argv:
            w = Login(self)
            w.set_default_size(1280, 760)
            w.present()
            return
        # Si greetd arranca con la VT inactiva, Wayfire solo tiene la salida falsa
        # NOOP-1; al pasar a la VT aparece eDP-1 y NOOP-1 desaparece con la
        # ventana dentro (pantalla negra). Por eso la ventana se crea (y se
        # rehace) siempre en un monitor real.
        self.win = None
        self._rehacer_id = 0
        mons = Gdk.Display.get_default().get_monitors()
        mons.connect("items-changed", lambda *_: self._programar())
        self._rehacer()

    @staticmethod
    def _monitor_real():
        mons = Gdk.Display.get_default().get_monitors()
        for i in range(mons.get_n_items()):
            m = mons.get_item(i)
            if not (m.get_connector() or "").startswith(("NOOP", "HEADLESS")):
                return m
        return None

    def _programar(self):
        if self._rehacer_id:
            GLib.source_remove(self._rehacer_id)
        self._rehacer_id = GLib.timeout_add(800, self._rehacer)

    def _rehacer(self):
        self._rehacer_id = 0
        m = self._monitor_real()
        print("greeter: monitor", m.get_connector() if m else None, flush=True)
        if m is None:
            self.hold()  # sin ventana: que la app no se cierre mientras espera
            self._esperando = True
            return False
        viejo = self.win
        if viejo is not None and getattr(viejo, "_monitor", None) is m:
            return False
        w = Login(self)
        w._monitor = m
        w.fullscreen_on_monitor(m)
        w.present()
        self.win = w
        if viejo is not None:
            viejo.destroy()
        if getattr(self, "_esperando", False):
            self._esperando = False
            self.release()
        return False

    def terminar(self):
        """Sesión aceptada: salir y cerrar el Wayfire del greeter (greetd lanza la sesión)."""
        self.quit()
        if not PRUEBA:
            subprocess.run(["pkill", "-x", "-U", str(os.getuid()), "wayfire"], check=False)
        return False


if __name__ == "__main__":
    sys.exit(App().run([sys.argv[0]]))
