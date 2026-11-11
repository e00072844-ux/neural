#!/usr/bin/env python3
"""NEURAL//AUTH: agente de autenticación de polkit con el estilo NEURAL.

Cuando una app sin privilegios pide algo que necesita permiso (pkexec,
systemd, NEURAL//AJUSTES con su ayudante, la tienda de Flatpak…), polkitd
pregunta al agente registrado para esta sesión. El agente muestra la ventana
y le pasa la contraseña a polkit-agent-helper-1 (setuid). La contraseña nunca
la comprueba este programa ni la guarda: va directa del campo a PolkitAgent.Session.

- Capa OVERLAY a pantalla completa con el teclado en exclusiva mientras está
  abierta (ninguna otra ventana recibe lo que escribes).
- Enseña qué se pide (mensaje + id de la acción) y QUIÉN lo pide (el programa
  que hizo la petición), para no dar permiso a ciegas.
- Enter = autorizar · Esc o CANCELAR = denegar. Contraseña mal = otro intento.
- Si el programa que pedía se cierra, polkit cancela y la ventana se va sola.
- Una petición a la vez; las demás esperan en cola.

Proceso "neural-auth", instancia única (Gtk.Application). Se arranca desde
[autostart] de wayfire.ini. Log en ~/.cache/neural-auth.log.
Probar:  pkexec /usr/bin/true      (o  pkexec id)"""
from __future__ import annotations

import ctypes
import os
import re
import signal
import sys
import warnings
from pathlib import Path

PROC = "neural-auth"
OBJ_PATH = "/org/local/neural/AuthAgent"

from layer import _shell, ensure_preload  # noqa: E402

ensure_preload()

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Polkit", "1.0")
gi.require_version("PolkitAgent", "1.0")
from gi.repository import Gdk, Gio, GLib, GLibUnix, Gtk, Polkit, PolkitAgent  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from board import load_board  # noqa: E402
from estilo import MONO, PAD, SANS, Frame, css_color, load_themes, register_fonts  # noqa: E402

_MODIFICADORES = {Gdk.KEY_Caps_Lock, Gdk.KEY_Shift_L, Gdk.KEY_Shift_R, Gdk.KEY_Control_L, Gdk.KEY_Control_R,
                  Gdk.KEY_Alt_L, Gdk.KEY_Alt_R, Gdk.KEY_Super_L, Gdk.KEY_Super_R, Gdk.KEY_Num_Lock,
                  Gdk.KEY_ISO_Level3_Shift, Gdk.KEY_Meta_L, Gdk.KEY_Meta_R}

# mensajes de polkit/pkexec que vienen en inglés (no hay traducciones instaladas)
_TRADUCCIONES = [
    (re.compile(r"Authentication is needed to run `(.+)' as the super user", re.S),
     r"Hace falta tu contraseña para ejecutar «\1» como superusuario."),
    (re.compile(r"Authentication is needed to run `(.+)' as user (.+)", re.S),
     r"Hace falta tu contraseña para ejecutar «\1» como el usuario \2."),
    (re.compile(r"^Password:\s*$"), "CONTRASEÑA"),
    (re.compile(r"^Contraseña:\s*$"), "CONTRASEÑA"),
]


def traducir(texto: str) -> str:
    for patron, nuevo in _TRADUCCIONES:
        if patron.search(texto):
            return patron.sub(nuevo, texto)
    return texto


def _proceso(pid: str | None) -> str:
    """'bash (pid 1234)' a partir del pid que manda polkit; '' si no se sabe."""
    if not pid or not pid.isdigit():
        return ""
    try:
        cmd = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
        cmd = [c.decode(errors="replace") for c in cmd if c]
    except OSError:
        return f"pid {pid}"
    if not cmd:
        return f"pid {pid}"
    linea = " ".join(cmd)
    if len(linea) > 90:
        linea = linea[:87] + "…"
    return f"{linea}  (pid {pid})"


def _setup_layer(window) -> bool:
    ls = _shell()
    if ls is None:
        return False
    ls.init_for_window(window)
    ls.set_namespace(window, "neural-auth")
    ls.set_layer(window, ls.Layer.OVERLAY)
    for edge in (ls.Edge.LEFT, ls.Edge.RIGHT, ls.Edge.TOP, ls.Edge.BOTTOM):
        ls.set_anchor(window, edge, True)
    ls.set_exclusive_zone(window, -1)  # encima de todo, también de la barra
    ls.set_keyboard_mode(window, ls.KeyboardMode.EXCLUSIVE)
    return True


def _theme_css(t: dict) -> str:
    acc, alt, warn, text, muted = t["accent"], t["alt"], t["warn"], t["text"], t["muted"]
    return f"""
window.neural-auth {{
  background-color: rgba(0, 0, 0, 0.78);
  background-image: radial-gradient(circle at 50% 45%, {css_color(acc, 0.14)}, transparent 55%),
                    repeating-linear-gradient(0deg, {css_color(acc, 0.03)} 0px, {css_color(acc, 0.03)} 1px,
                                              transparent 1px, transparent 4px);
}}
.au-code {{ font-family: "{MONO}"; font-size: 11px; letter-spacing: 2px; color: {css_color(muted)}; }}
.au-title {{ font-family: "{SANS}"; font-weight: 700; font-size: 24px; letter-spacing: 3px; color: {css_color(acc)};
            text-shadow: 0 0 14px {css_color(acc, 0.5)}; }}
.au-msg {{ font-family: "{SANS}"; font-weight: 600; font-size: 16px; color: {css_color(text)}; }}
.au-key {{ font-family: "{MONO}"; font-size: 11px; letter-spacing: 1px; color: {css_color(muted)}; }}
.au-val {{ font-family: "{MONO}"; font-size: 12px; color: {css_color(text)}; }}
.au-user {{ font-family: "{SANS}"; font-weight: 700; font-size: 18px; letter-spacing: 2px; color: {css_color(alt)}; }}
.au-pass {{ background: rgba(0, 0, 0, 0.7); border: 1px solid {css_color(acc, 0.7)}; border-radius: 0;
           color: {css_color(text)}; font-family: "{MONO}"; font-size: 16px; min-height: 34px;
           caret-color: {css_color(alt)}; }}
.au-pass:focus-within {{ border-color: {css_color(alt)}; box-shadow: 0 0 10px {css_color(alt, 0.5)}; }}
.au-state {{ font-family: "{MONO}"; font-size: 12px; letter-spacing: 2px; color: {css_color(muted)}; }}
.au-state.warn {{ color: {css_color(warn)}; }}
.au-state.ok {{ color: {css_color(alt)}; }}
.au-btn {{ all: unset; padding: 7px 18px; background: rgba(0, 0, 0, 0.55); border: 1px solid {css_color(acc, 0.6)};
          color: {css_color(text)}; font-family: "{SANS}"; font-weight: 700; font-size: 14px; letter-spacing: 2px; }}
.au-btn:hover {{ background: {css_color(acc)}; color: #0a0204; }}
.au-btn.main {{ border-color: {css_color(alt)}; color: {css_color(alt)}; }}
.au-btn.main:hover {{ background: {css_color(alt)}; color: #000; }}
.au-btn:disabled {{ opacity: 0.4; }}
scrolledwindow.au-scroll, scrolledwindow.au-scroll > viewport {{ background: transparent; }}
dropdown.au-who > button {{ all: unset; padding: 3px 8px; border: 1px solid {css_color(acc, 0.6)};
  color: {css_color(alt)}; font-family: "{SANS}"; font-weight: 700; font-size: 16px; }}
"""


def _identidades(lista) -> list:
    """a(sa{sv}) de polkitd -> [Polkit.UnixUser | Polkit.UnixGroup]"""
    out = []
    for kind, datos in lista:
        if kind == "unix-user" and "uid" in datos:
            out.append(Polkit.UnixUser.new(int(datos["uid"])))
        elif kind == "unix-group" and "gid" in datos:
            out.append(Polkit.UnixGroup.new(int(datos["gid"])))
    return out


class Peticion:
    """Una petición BeginAuthentication de polkitd: la llamada D-Bus que hay
    que contestar (cuando termine) y sus datos."""

    def __init__(self, action_id, message, details: dict, cookie, identities, invocation):
        self.action_id, self.cookie, self.invocation = action_id, cookie, invocation
        self.message = traducir(message or "")
        self.details = dict(details)
        uid = os.getuid()
        propias = [i for i in identities if isinstance(i, Polkit.UnixUser) and i.get_uid() == uid]
        otras = [i for i in identities if i not in propias]
        self.identities = propias + otras  # la tuya primero
        self.terminada = False
        self.al_cancelar = None  # lo pone la ventana: CancelAuthentication de polkitd

    def terminar(self, ok: bool):
        """Contesta a polkitd. ok=False = el usuario no quiso (descartada)."""
        if self.terminada:
            return
        self.terminada = True
        if ok:
            self.invocation.return_value(None)
        else:
            self.invocation.return_dbus_error("org.freedesktop.PolicyKit1.Error.Cancelled",
                                              "Cancelado por el usuario")


# Interfaz que polkitd llama en el agente. Se implementa a mano por D-Bus
# porque PolkitAgent.Listener desde Python pierde el user_data del callback
# asíncrono y libpolkit-agent se cae (segfault en auth_cb).
AGENT_XML = """<node>
 <interface name="org.freedesktop.PolicyKit1.AuthenticationAgent">
  <method name="BeginAuthentication">
   <arg type="s" name="action_id" direction="in"/>
   <arg type="s" name="message" direction="in"/>
   <arg type="s" name="icon_name" direction="in"/>
   <arg type="a{ss}" name="details" direction="in"/>
   <arg type="s" name="cookie" direction="in"/>
   <arg type="a(sa{sv})" name="identities" direction="in"/>
  </method>
  <method name="CancelAuthentication">
   <arg type="s" name="cookie" direction="in"/>
  </method>
 </interface>
</node>"""
POLKIT_NAME = "org.freedesktop.PolicyKit1"


class AuthWindow(Gtk.Window):
    def __init__(self, app: "AuthApp", p: Peticion, numero: int):
        super().__init__(application=app, title="Neural Auth")
        self.app, self.p, self.theme = app, p, app.theme
        self.sesion: PolkitAgent.Session | None = None
        self.fallos = 0
        self.esperando = False  # True = hay un "request" de PAM pendiente
        self.set_decorated(False)
        if not _setup_layer(self):
            self.fullscreen()
        self.add_css_class("neural-auth")

        frame = Frame(self, Gtk.Orientation.VERTICAL, 14, ("tl", "br"), True, f"AUTH// {numero:02d}")
        frame.set_halign(Gtk.Align.CENTER)
        frame.set_valign(Gtk.Align.CENTER)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_start=PAD + 26,
                      margin_end=PAD + 26, margin_top=PAD + 28, margin_bottom=PAD + 20)
        box.set_size_request(520, -1)
        box.append(self._lbl("SYS//POLKIT  ·  AUTORIZACIÓN REQUERIDA", "au-code"))
        box.append(self._lbl("PERMISO DE ADMINISTRADOR", "au-title"))
        msg = self._lbl(p.message or "Una aplicación pide permisos especiales.", "au-msg")
        msg.set_wrap(True)
        msg.set_max_width_chars(60)
        box.append(msg)

        info = Gtk.Grid(column_spacing=12, row_spacing=3, margin_top=4)
        filas = [("ACCIÓN", p.action_id)]
        quien = _proceso(p.details.get("polkit.subject-pid"))
        if quien:
            filas.append(("PIDE", quien))
        for i, (k, v) in enumerate(filas):
            info.attach(self._lbl(k, "au-key"), 0, i, 1, 1)
            val = self._lbl(v, "au-val")
            val.set_wrap(True)
            val.set_selectable(False)
            info.attach(val, 1, i, 1, 1)
        box.append(info)

        fila = Gtk.Box(spacing=10, margin_top=10)
        fila.append(self._lbl("USUARIO", "au-key"))
        if len(p.identities) > 1:
            nombres = Gtk.StringList.new([self._nombre(i) for i in p.identities])
            self.quien = Gtk.DropDown(model=nombres)
            self.quien.add_css_class("au-who")
            self.quien.connect("notify::selected", lambda *_: self._empezar())
            fila.append(self.quien)
        else:
            self.quien = None
            fila.append(self._lbl(self._nombre(p.identities[0]).upper(), "au-user"))
        box.append(fila)

        self.prompt = self._lbl("CONTRASEÑA", "au-key")
        box.append(self.prompt)
        self.entry = Gtk.PasswordEntry(placeholder_text="contraseña", show_peek_icon=False, sensitive=False)
        self.entry.add_css_class("au-pass")
        self.entry.connect("activate", lambda *_: self._enviar())
        box.append(self.entry)
        self.estado = self._lbl("CONECTANDO…", "au-state")
        box.append(self.estado)

        botones = Gtk.Box(spacing=10, halign=Gtk.Align.END, margin_top=8)
        cancelar = Gtk.Button(label="CANCELAR")
        cancelar.add_css_class("au-btn")
        cancelar.connect("clicked", lambda *_: self.cancelar())
        self.ok = Gtk.Button(label="AUTORIZAR", sensitive=False)
        self.ok.add_css_class("au-btn")
        self.ok.add_css_class("main")
        self.ok.connect("clicked", lambda *_: self._enviar())
        botones.append(cancelar)
        botones.append(self.ok)
        box.append(botones)
        box.append(self._lbl("ENTER AUTORIZAR  ·  ESC CANCELAR", "au-code"))
        frame.append(box)
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)  # puede encogerse sin avisos
        scroll.add_css_class("au-scroll")
        scroll.set_child(frame)
        self.set_child(scroll)

        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)
        p.al_cancelar = self._cancelado_fuera  # el que pedía se fue → polkitd cancela
        self._empezar()

    @staticmethod
    def _lbl(text, cls, **kw):
        lb = Gtk.Label(label=text, xalign=0, **kw)
        lb.add_css_class(cls)
        return lb

    @staticmethod
    def _nombre(ident) -> str:
        if isinstance(ident, Polkit.UnixUser):
            return ident.get_name() or str(ident.get_uid())
        return ident.to_string()

    def _identidad(self):
        if self.quien is None:
            return self.p.identities[0]
        return self.p.identities[self.quien.get_selected()]

    # ---------- sesión con polkit-agent-helper-1 ----------
    def _empezar(self):
        if self.sesion is not None:
            s, self.sesion = self.sesion, None
            s.cancel()
        self.esperando = False
        s = PolkitAgent.Session.new(self._identidad(), self.p.cookie)
        s.connect("request", self._on_request)
        s.connect("show-error", lambda _s, t: self._estado(traducir(t).upper(), "warn"))
        s.connect("show-info", lambda _s, t: self._estado(traducir(t).upper()))
        s.connect("completed", self._on_completed)
        self.sesion = s
        s.initiate()

    def _on_request(self, s, request, echo_on):
        if s is not self.sesion:
            return
        self.esperando = True
        self.prompt.set_text(traducir(request.strip()).upper().rstrip(":"))
        self.entry.set_show_peek_icon(False)
        self.entry.set_sensitive(True)
        self.ok.set_sensitive(True)
        if not self.fallos:
            self._estado("ESPERANDO CONTRASEÑA")
        self._enfocar()

    def _enviar(self):
        if not self.esperando or self.sesion is None:
            return
        texto = self.entry.get_text()
        if not texto:
            return
        self.esperando = False
        self.entry.set_sensitive(False)
        self.ok.set_sensitive(False)
        self._estado("VERIFICANDO…")
        self.sesion.response(texto)
        self.entry.set_text("")

    def _on_completed(self, s, ganado):
        if s is not self.sesion:
            return
        self.sesion = None
        if ganado:
            self._estado("ACCESO CONCEDIDO", "ok")
            self.p.terminar(True)
            GLib.timeout_add(350, self._cerrar)
            return
        if self.p.terminada:
            return
        self.fallos += 1
        self._estado(f"ACCESO DENEGADO  ·  INTENTO {self.fallos}", "warn")
        self._empezar()

    # ---------- cierre ----------
    def cancelar(self):
        if self.sesion is not None:
            s, self.sesion = self.sesion, None
            s.cancel()
        self.p.terminar(False)
        self._cerrar()

    def _cancelado_fuera(self):
        if not self.p.terminada:
            print("auth: el programa que pedía canceló", file=sys.stderr, flush=True)
            self.cancelar()

    def _cerrar(self):
        self.p.al_cancelar = None
        self.destroy()
        self.app.siguiente()
        return GLib.SOURCE_REMOVE

    # ---------- teclado ----------
    def _on_key(self, _c, keyval, *_):
        if keyval == Gdk.KEY_Escape:
            self.cancelar()
            return True
        if keyval in _MODIFICADORES or not self.entry.get_sensitive():
            return False
        foco = self.get_focus()
        if foco is None or not (foco == self.entry or foco.is_ancestor(self.entry)):
            self._enfocar()
        return False

    def _enfocar(self):
        texto = self.entry.get_delegate()
        if texto is not None and hasattr(texto, "grab_focus_without_selecting"):
            texto.grab_focus_without_selecting()
        else:
            self.entry.grab_focus()

    def _estado(self, text: str, cls: str = ""):
        self.estado.set_text(text)
        for c in ("warn", "ok"):
            (self.estado.add_css_class if c == cls else self.estado.remove_css_class)(c)


class AuthApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="org.local.NeuralAuth")
        self.cola: list[Peticion] = []
        self.actual: AuthWindow | None = None
        self.contador = 0
        self.theme = None
        self.css = None
        self.bus = None
        self.reg_id = 0
        self.authority = None
        self.subject = None

    def do_startup(self):
        Gtk.Application.do_startup(self)
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            GLibUnix.signal_add(GLib.PRIORITY_DEFAULT, sig, lambda *_: (self._salir(), GLib.SOURCE_REMOVE)[1])
        self.hold()
        try:
            # Polkit.Authority usa la misma conexión compartida al bus del sistema,
            # así polkitd llama a este objeto desde el nombre con el que se registró
            self.bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
            info = Gio.DBusNodeInfo.new_for_xml(AGENT_XML).interfaces[0]
            with warnings.catch_warnings():  # register_object "deprecated" en PyGObject nuevo; funciona
                warnings.simplefilter("ignore", DeprecationWarning)
                self.reg_id = self.bus.register_object(OBJ_PATH, info, self._on_llamada, None, None)
            self.authority = Polkit.Authority.get_sync(None)
            self.subject = Polkit.UnixSession.new_for_process_sync(os.getpid(), None)
            locale = os.environ.get("LC_ALL") or os.environ.get("LC_MESSAGES") or os.environ.get("LANG") or "C"
            self.authority.register_authentication_agent_sync(self.subject, locale, OBJ_PATH, None)
        except GLib.Error as e:
            print(f"auth: no pude registrar el agente: {e.message}", file=sys.stderr, flush=True)
            GLib.idle_add(self.quit)
            return
        print("auth: agente registrado para esta sesión", file=sys.stderr, flush=True)

    def _dueno_polkit(self) -> str | None:
        try:
            r = self.bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                                   "GetNameOwner", GLib.Variant("(s)", (POLKIT_NAME,)),
                                   GLib.VariantType("(s)"), Gio.DBusCallFlags.NONE, 2000, None)
            return r.unpack()[0]
        except GLib.Error:
            return None

    def _on_llamada(self, _bus, sender, _path, _iface, metodo, params, invocation):
        # solo polkitd puede pedirnos cosas
        if sender != self._dueno_polkit():
            print(f"auth: llamada {metodo} RECHAZADA de {sender} (no es polkitd)", file=sys.stderr, flush=True)
            invocation.return_dbus_error("org.freedesktop.DBus.Error.AccessDenied", "Solo polkitd")
            return
        if metodo == "BeginAuthentication":
            action_id, message, _icon, details, cookie, identities = params.unpack()
            p = Peticion(action_id, message, details, cookie, _identidades(identities), invocation)
            print(f"auth: petición {action_id}", file=sys.stderr, flush=True)
            if not p.identities:
                p.terminar(False)
                return
            self.encolar(p)
        elif metodo == "CancelAuthentication":
            (cookie,) = params.unpack()
            for p in list(self.cola):
                if p.cookie == cookie:
                    self.cola.remove(p)
                    p.terminar(False)
            if self.actual is not None and self.actual.p.cookie == cookie and self.actual.p.al_cancelar:
                self.actual.p.al_cancelar()
            invocation.return_value(None)
        else:
            invocation.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", metodo)

    def do_activate(self):
        pass  # sin ventana hasta que llegue una petición

    def _aplicar_tema(self):
        themes = load_themes()
        self.theme = themes.get(load_board().get("theme", "red"), next(iter(themes.values())))
        display = Gdk.Display.get_default()
        if self.css is not None:
            Gtk.StyleContext.remove_provider_for_display(display, self.css)
        self.css = Gtk.CssProvider()
        self.css.load_from_string(_theme_css(self.theme))
        Gtk.StyleContext.add_provider_for_display(display, self.css, Gtk.STYLE_PROVIDER_PRIORITY_USER + 1)

    def encolar(self, p: Peticion):
        self.cola.append(p)
        if self.actual is None:
            self.siguiente()

    def siguiente(self):
        self.actual = None
        while self.cola:
            p = self.cola.pop(0)
            if p.terminada:
                continue
            self._aplicar_tema()  # el tema actual del fondo, cada vez
            self.contador += 1
            self.actual = AuthWindow(self, p, self.contador)
            self.actual.present()
            return

    def _salir(self):
        if self.actual is not None:
            self.actual.cancelar()
        for p in self.cola:
            p.terminar(False)
        self.cola.clear()
        if self.authority is not None and self.subject is not None:
            try:
                self.authority.unregister_authentication_agent_sync(self.subject, OBJ_PATH, None)
            except GLib.Error:
                pass
        self.quit()


if __name__ == "__main__":
    try:
        ctypes.CDLL(None).prctl(15, PROC.encode(), 0, 0, 0)  # PR_SET_NAME
    except (OSError, AttributeError):
        pass
    GLib.set_prgname(PROC)
    register_fonts()
    sys.exit(AuthApp().run(sys.argv[:1]))
