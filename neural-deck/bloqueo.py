#!/usr/bin/env python3
"""NEURAL//LOCK: pantalla de bloqueo con el estilo de NEURAL//DOCK.

Usa el protocolo ext-session-lock-v1 de Wayland (plugin session-lock de
Wayfire) a traves de Gtk4SessionLock (viene con gtk4-layer-shell): mientras
esta bloqueado el compositor no muestra NADA mas ni pasa el teclado a
ninguna otra app, y si este programa se cae la sesion SIGUE bloqueada
(el compositor pinta la pantalla de un color fijo). Para volver entonces:
desde tty2 relanzarlo con
    WAYLAND_DISPLAY=wayland-1 python3 ~/neural-deck/bloqueo.py
o cerrar la sesion con `pkill -x wayfire`.

La contraseña se comprueba con PAM, servicio "neural-lock"
(/etc/pam.d/neural-lock). Sin ese archivo NO bloquea (PAM usaria "other",
que lo deniega todo y no se podria desbloquear).
    bloqueo.py            bloquear
    bloqueo.py -f         bloquear y volver en cuanto este bloqueado (el
                          bloqueo sigue en segundo plano): para swayidle,
                          que asi espera al bloqueo antes de suspender
    bloqueo.py --probar   comprobar PAM en la terminal sin bloquear"""
from __future__ import annotations

import ctypes
import ctypes.util
import getpass
import os
import pwd
import signal
import sys
import threading
import time
from pathlib import Path

PAM_SERVICE = "neural-lock"
PROC = "neural-lock"


# ---------- PAM (libpam por ctypes) ----------
class _PamMessage(ctypes.Structure):
    _fields_ = [("msg_style", ctypes.c_int), ("msg", ctypes.c_char_p)]


class _PamResponse(ctypes.Structure):
    _fields_ = [("resp", ctypes.c_void_p), ("resp_retcode", ctypes.c_int)]


_CONV = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.POINTER(_PamMessage)),
                         ctypes.POINTER(ctypes.POINTER(_PamResponse)), ctypes.c_void_p)


class _PamConv(ctypes.Structure):
    _fields_ = [("conv", _CONV), ("appdata_ptr", ctypes.c_void_p)]


def pam_ok(user: str, password: str) -> bool:
    """True si PAM acepta la contraseña de `user` (pam_unix, como usuario
    normal, usa unix_chkpwd: solo vale para la contraseña propia)."""
    libpam = ctypes.CDLL(ctypes.util.find_library("pam") or "libpam.so.0")
    libc = ctypes.CDLL(None)
    libc.calloc.restype = ctypes.c_void_p
    libc.calloc.argtypes = (ctypes.c_size_t, ctypes.c_size_t)
    libc.strdup.restype = ctypes.c_void_p
    libc.strdup.argtypes = (ctypes.c_char_p,)
    secreto = password.encode()

    @_CONV
    def conv(n, msgs, resp, _data):
        # PAM libera estas respuestas: tienen que venir de malloc
        arr = libc.calloc(n, ctypes.sizeof(_PamResponse))
        if not arr:
            return 5  # PAM_BUF_ERR
        resp[0] = ctypes.cast(arr, ctypes.POINTER(_PamResponse))
        for i in range(n):
            if msgs[i].contents.msg_style in (1, 2):  # PROMPT_ECHO_OFF / ON
                resp[0][i].resp = libc.strdup(secreto)
        return 0

    handle = ctypes.c_void_p()
    c = _PamConv(conv, None)
    libpam.pam_start.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.POINTER(_PamConv),
                                 ctypes.POINTER(ctypes.c_void_p))
    libpam.pam_authenticate.argtypes = (ctypes.c_void_p, ctypes.c_int)
    libpam.pam_end.argtypes = (ctypes.c_void_p, ctypes.c_int)
    if libpam.pam_start(PAM_SERVICE.encode(), user.encode(), ctypes.byref(c), ctypes.byref(handle)) != 0:
        return False
    ret = libpam.pam_authenticate(handle, 0)
    libpam.pam_end(handle, ret)
    return ret == 0


def pam_configurado() -> bool:
    return Path("/etc/pam.d", PAM_SERVICE).exists()


def usuario() -> str:
    return pwd.getpwuid(os.getuid()).pw_name


def _probar() -> int:
    if not pam_configurado():
        print(f"Falta /etc/pam.d/{PAM_SERVICE}")
        return 1
    ok = pam_ok(usuario(), getpass.getpass(f"Contraseña de {usuario()}: "))
    print("PAM: contraseña CORRECTA" if ok else "PAM: contraseña rechazada")
    return 0 if ok else 1


if __name__ == "__main__" and "--probar" in sys.argv:
    sys.exit(_probar())

_FD_ENV = "NEURAL_LOCK_FD"  # tuberia hacia el proceso que espera (-f)


def _modo_fondo() -> None:
    """-f: el padre espera (max. 10 s) a que el hijo diga "bloqueado" y sale;
    el hijo sigue con el bloqueo. El fd pasa por el entorno porque
    ensure_preload() relanza el proceso con execve."""
    if _FD_ENV in os.environ or "-f" not in sys.argv:
        return
    r, w = os.pipe()
    if os.fork() > 0:
        os.close(w)
        import select
        listo = select.select([r], [], [], 10)[0] and os.read(r, 1) == b"1"
        os._exit(0 if listo else 1)
    os.close(r)
    os.setsid()
    os.set_inheritable(w, True)
    os.environ[_FD_ENV] = str(w)


def _salir_desbloqueado() -> None:
    """unlock() solo deja la peticion unlock_and_destroy en el bufer de Wayland
    (y gtk4-layer-shell puede mandarla un poco DESPUES de emitir "unlocked"):
    si salimos ya, el compositor ve morir al cliente CON la sesion bloqueada y
    se queda para siempre en la pantalla de "bloqueo caido" (💥). Por eso NO se
    sale dentro de la señal: se deja girar el bucle de GTK 1,5 s (para que la
    peticion salga de verdad), luego sync() = ida y vuelta con Wayfire, y fuera."""
    print("bloqueo: unlocked recibido; espero a que Wayfire lo procese", file=sys.stderr, flush=True)
    fd = os.environ.pop(_OK_ENV, None)  # avisar al guardian: si ahora me caigo, NO relanzar
    if fd is not None:
        try:
            os.write(int(fd), b"U")
        except (OSError, ValueError):
            pass

    def _fin():
        try:
            Gdk.Display.get_default().flush()
            Gdk.Display.get_default().sync()
        except Exception as e:  # noqa: BLE001
            print(f"bloqueo: sync fallo: {e}", file=sys.stderr)
        print("bloqueo: desbloqueado", file=sys.stderr)
        sys.stderr.flush()
        os._exit(0)

    GLib.timeout_add(1500, _fin)


def _avisar_bloqueado() -> None:
    fd = os.environ.pop(_FD_ENV, None)
    if fd is not None:
        try:
            os.write(int(fd), b"1")
            os.close(int(fd))
        except (OSError, ValueError):
            pass


# Codigos de salida del hijo que NO se reintentan (decision, no caida)
SALIR_RECHAZADO = 2   # el compositor rechazo el bloqueo (p. ej. ya hay otro)
SALIR_SIN_PAM = 3     # falta /etc/pam.d/neural-lock o ext-session-lock
_HIJO_ENV = "NEURAL_LOCK_HIJO"
_OK_ENV = "NEURAL_LOCK_OK_FD"  # el hijo escribe "U" aqui al desbloquearse


def _sello_compositor():
    """Identifica el socket de Wayland de ESTA sesion (inodo + fecha): si
    Wayfire se cierra y arranca otro con el mismo nombre, el sello cambia."""
    try:
        st = os.stat(Path(os.environ["XDG_RUNTIME_DIR"], os.environ.get("WAYLAND_DISPLAY", "wayland-0")))
        return (st.st_ino, st.st_ctime_ns)
    except (KeyError, OSError):
        return None


def _guardian() -> None:
    """El proceso que se ejecuta NO bloquea: vigila a un hijo que si lo hace.
    Si el hijo se cae (segfault de GTK al cambiar de monitor, etc.) con la
    sesion bloqueada, Wayfire se queda en su pantalla de "bloqueo caido" (💥)
    sin forma de volver; el guardian lanza otro bloqueo al momento y Wayfire
    lo acepta, asi que se puede seguir escribiendo la contraseña."""
    if _HIJO_ENV in os.environ:
        return
    try:
        ctypes.CDLL(None).prctl(15, b"neural-lock-g", 0, 0, 0)
    except (OSError, AttributeError):
        pass
    import subprocess
    ok_r, ok_w = os.pipe()
    os.set_blocking(ok_r, False)
    env = dict(os.environ, **{_HIJO_ENV: "1", _OK_ENV: str(ok_w)})
    fds = (int(env[_FD_ENV]),) if _FD_ENV in env else ()
    caidas: list[float] = []
    sello = _sello_compositor()
    while True:
        if _sello_compositor() != sello or sello is None:
            # el Wayfire de esta sesion ya no existe (se cerro la sesion): NO
            # relanzar, o el bloqueo caeria en la sesion NUEVA (mismo wayland-1)
            print("bloqueo: el compositor de mi sesion ya no esta; salgo", file=sys.stderr, flush=True)
            os._exit(0)
        r = subprocess.call([sys.executable, os.path.abspath(__file__)], env=env, pass_fds=fds + (ok_w,))
        try:
            desbloqueado = b"U" in os.read(ok_r, 16)
        except BlockingIOError:
            desbloqueado = False
        if r in (0, SALIR_RECHAZADO, SALIR_SIN_PAM) or desbloqueado:
            if r != 0 and desbloqueado:
                print(f"bloqueo: el hijo salio con {r} DESPUES de desbloquear; no relanzo", file=sys.stderr)
            os._exit(0)
        ahora = time.monotonic()
        caidas = [t for t in caidas if ahora - t < 60] + [ahora]
        print(f"bloqueo: el bloqueo se cayo (codigo {r}); relanzo ({len(caidas)}/6 en 60 s)",
              file=sys.stderr, flush=True)
        if len(caidas) >= 6:
            print("bloqueo: demasiadas caidas; me rindo (desde otra tty: "
                  "WAYLAND_DISPLAY=wayland-N python3 ~/neural-deck/bloqueo.py)", file=sys.stderr)
            os._exit(1)
        if fds:  # el aviso "bloqueado" solo lo da el primer hijo
            env.pop(_FD_ENV, None)
            os.close(fds[0])
            fds = ()
        time.sleep(0.3)


if __name__ == "__main__":
    _modo_fondo()
    _guardian()

# ---------- interfaz ----------
from layer import ensure_preload  # noqa: E402

ensure_preload()  # Gtk4SessionLock esta en la misma biblioteca que layer-shell

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gtk4SessionLock", "1.0")
from gi.repository import Gdk, GLib, GLibUnix, Gtk, Gtk4SessionLock  # noqa: E402

import sistema  # noqa: E402
from board import load_board  # noqa: E402
from estilo import MONO, PAD, SANS, Frame, css_color, load_themes, register_fonts  # noqa: E402

_MODIFICADORES = {Gdk.KEY_Caps_Lock, Gdk.KEY_Shift_L, Gdk.KEY_Shift_R, Gdk.KEY_Control_L, Gdk.KEY_Control_R,
                  Gdk.KEY_Alt_L, Gdk.KEY_Alt_R, Gdk.KEY_Super_L, Gdk.KEY_Super_R, Gdk.KEY_Num_Lock,
                  Gdk.KEY_ISO_Level3_Shift, Gdk.KEY_Meta_L, Gdk.KEY_Meta_R}
DIAS = ("LUNES", "MARTES", "MIÉRCOLES", "JUEVES", "VIERNES", "SÁBADO", "DOMINGO")
MESES = ("ENERO", "FEBRERO", "MARZO", "ABRIL", "MAYO", "JUNIO", "JULIO", "AGOSTO", "SEPTIEMBRE", "OCTUBRE",
         "NOVIEMBRE", "DICIEMBRE")


def _theme_css(t: dict) -> str:
    acc, alt, warn, text, muted = t["accent"], t["alt"], t["warn"], t["text"], t["muted"]
    return f"""
window.neural-lock {{
  background-color: #000;
  background-image: radial-gradient(circle at 50% 42%, {css_color(acc, 0.16)}, transparent 55%),
                    repeating-linear-gradient(0deg, {css_color(acc, 0.035)} 0px, {css_color(acc, 0.035)} 1px,
                                              transparent 1px, transparent 4px);
}}
.nl-clock {{ font-family: "{SANS}"; font-weight: 700; font-size: 120px; letter-spacing: 6px; color: {css_color(acc)};
            text-shadow: 0 0 24px {css_color(acc, 0.55)}; }}
.nl-date {{ font-family: "{MONO}"; font-size: 15px; letter-spacing: 4px; color: {css_color(text)}; }}
.nl-code {{ font-family: "{MONO}"; font-size: 11px; letter-spacing: 2px; color: {css_color(muted)}; }}
.nl-user {{ font-family: "{SANS}"; font-weight: 700; font-size: 20px; letter-spacing: 3px; color: {css_color(alt)}; }}
.nl-pass {{ background: rgba(0, 0, 0, 0.7); border: 1px solid {css_color(acc, 0.7)}; border-radius: 0;
           color: {css_color(text)}; font-family: "{MONO}"; font-size: 16px; min-height: 34px; caret-color: {css_color(alt)}; }}
.nl-pass:focus-within {{ border-color: {css_color(alt)}; box-shadow: 0 0 10px {css_color(alt, 0.5)}; }}
.nl-msg {{ font-family: "{MONO}"; font-size: 12px; letter-spacing: 2px; color: {css_color(muted)}; }}
.nl-msg.warn {{ color: {css_color(warn)}; }}
.nl-msg.ok {{ color: {css_color(alt)}; }}
"""


class LockWindow(Gtk.Window):
    def __init__(self, app: "LockApp", principal: bool):
        # SIN application=app a proposito: al quitar una ventana de la app, GTK
        # 4.22 llama a gtk_application_impl_wayland_window_forget ->
        # gdk_wayland_toplevel_remove_from_session(superficie YA destruida)
        # -> segfault (al desbloquear o al desaparecer un monitor, p. ej. al
        # cambiar de tty). La app se mantiene viva con hold().
        super().__init__()
        self.app, self.theme = app, app.theme
        self.add_css_class("neural-lock")
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, halign=Gtk.Align.CENTER,
                      valign=Gtk.Align.CENTER)
        col.append(self._lbl("SYS//LOCK  ·  SESIÓN BLOQUEADA", "nl-code"))
        self.clock = self._lbl("", "nl-clock")
        self.date = self._lbl("", "nl-date")
        col.append(self.clock)
        col.append(self.date)
        self.entry: Gtk.PasswordEntry | None = None
        if principal:
            frame = Frame(self, Gtk.Orientation.VERTICAL, 14, ("tl", "br"), True, "AUTH// 01")
            frame.set_margin_top(28)
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_start=PAD + 22,
                          margin_end=PAD + 22, margin_top=PAD + 26, margin_bottom=PAD + 18)
            box.set_size_request(340, -1)
            box.append(self._lbl(app.user.upper(), "nl-user", xalign=0))
            self.entry = Gtk.PasswordEntry(placeholder_text="contraseña", show_peek_icon=False)
            self.entry.add_css_class("nl-pass")
            self.entry.connect("activate", lambda e: app.intentar(e.get_text()))
            box.append(self.entry)
            self.msg = self._lbl("ENTER = DESBLOQUEAR", "nl-msg", xalign=0)
            box.append(self.msg)
            frame.append(box)
            col.append(frame)
            keys = Gtk.EventControllerKey()
            keys.connect("key-pressed", self._on_key)
            self.add_controller(keys)
        self.extra = self._lbl("", "nl-code")
        self.extra.set_margin_top(18)
        col.append(self.extra)
        self.set_child(col)
        self.tick()

    @staticmethod
    def _lbl(text, cls, **kw):
        lb = Gtk.Label(label=text, **kw)
        lb.add_css_class(cls)
        return lb

    def _on_key(self, _c, keyval, *_):
        if keyval == Gdk.KEY_Escape and self.entry is not None:
            self.entry.set_text("")
            return True
        if self.entry is None or keyval in _MODIFICADORES:
            return False  # Bloq Mayús, Shift, Ctrl...: nada que hacer
        foco = self.get_focus()
        if foco is None or not (foco == self.entry or foco.is_ancestor(self.entry)):
            self._enfocar()  # una tecla fuera del campo: al campo de contraseña
        return False

    def _enfocar(self):
        """Foco al campo SIN seleccionar lo escrito (grab_focus lo selecciona
        todo y la siguiente letra lo borraba)."""
        texto = self.entry.get_delegate()  # el Gtk.Text de dentro
        if texto is not None and hasattr(texto, "grab_focus_without_selecting"):
            texto.grab_focus_without_selecting()
        else:
            self.entry.grab_focus()
            self.entry.select_region(-1, -1)

    def tick(self):
        now = time.localtime()
        self.clock.set_text(time.strftime("%H:%M", now))
        self.date.set_text(f"{DIAS[now.tm_wday]} {now.tm_mday:02d} {MESES[now.tm_mon - 1]} {now.tm_year}")
        bat = sistema.get_bateria()
        partes = []
        if bat:
            partes.append(f"BAT {bat['capacity']}%" + (" · CARGANDO" if bat["status"] == "Charging" else ""))
        partes.append(f"UP {sistema.uptime()}")
        self.extra.set_text("  ·  ".join(partes))

    def mensaje(self, text: str, cls: str = ""):
        if self.entry is None:
            return
        self.msg.set_text(text)
        for c in ("warn", "ok"):
            (self.msg.add_css_class if c == cls else self.msg.remove_css_class)(c)


class LockApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="org.local.NeuralLock")
        self.user = usuario()
        self.windows: list[LockWindow] = []
        self.ocupado = False
        self.fallos = 0
        self.lock = None
        self.theme = None
        self.codigo = 0

    def do_startup(self):
        Gtk.Application.do_startup(self)
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):  # NO desbloquea: solo sale
            GLibUnix.signal_add(GLib.PRIORITY_DEFAULT, sig, lambda *_: (self.quit(), GLib.SOURCE_REMOVE)[1])
        themes = load_themes()
        self.theme = themes.get(load_board().get("theme", "red"), next(iter(themes.values())))
        css = Gtk.CssProvider()
        css.load_from_string(_theme_css(self.theme))
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_USER + 1)
        self.hold()

    def do_activate(self):
        if self.lock is not None:  # ya bloqueado (segunda llamada): nada
            return
        if not pam_configurado():
            print(f"bloqueo: falta /etc/pam.d/{PAM_SERVICE}; no bloqueo (no se podria desbloquear)",
                  file=sys.stderr)
            self.codigo = SALIR_SIN_PAM
            self.quit()
            return
        if not Gtk4SessionLock.is_supported():
            print("bloqueo: el compositor no tiene ext-session-lock (plugin session-lock)", file=sys.stderr)
            self.codigo = SALIR_SIN_PAM
            self.quit()
            return
        self.lock = Gtk4SessionLock.Instance.new()
        self.lock.connect("monitor", self._on_monitor)
        self.lock.connect("failed", lambda *_: (print("bloqueo: el compositor rechazo el bloqueo",
                                                      file=sys.stderr), self._rechazado()))
        # ya desbloqueado: salir sin que GTK destruya las superficies del bloqueo
        # (las soltó el compositor y GTK se caía con segfault al limpiar)
        self.lock.connect("unlocked", lambda *_: _salir_desbloqueado())
        self.lock.connect("locked", lambda *_: _avisar_bloqueado())
        self.lock.lock()
        GLib.timeout_add_seconds(1, self._tick)

    def _rechazado(self):
        self.codigo = SALIR_RECHAZADO
        self.quit()

    def _on_monitor(self, lock, monitor):
        w = LockWindow(self, principal=self._principal() is None)
        self.windows.append(w)
        lock.assign_window_to_monitor(w, monitor)
        # monitor que desaparece (cambio de tty, pantalla externa): olvidar su
        # ventana; si era la de la contraseña, el siguiente monitor la tendra
        monitor.connect("invalidate", lambda *_: w in self.windows and self.windows.remove(w))
        if w.entry is not None:
            GLib.idle_add(lambda: (w._enfocar(), GLib.SOURCE_REMOVE)[1])

    def _tick(self):
        for w in self.windows:
            w.tick()
        return GLib.SOURCE_CONTINUE

    def _principal(self) -> LockWindow | None:
        return next((w for w in self.windows if w.entry is not None), None)

    def intentar(self, password: str):
        w = self._principal()
        if self.ocupado or w is None or not password:
            return
        self.ocupado = True
        w.entry.set_sensitive(False)
        w.mensaje("VERIFICANDO…")

        def trabajo():
            ok = pam_ok(self.user, password)
            GLib.idle_add(self._resultado, ok)

        threading.Thread(target=trabajo, daemon=True).start()

    def _resultado(self, ok: bool):
        w = self._principal()
        self.ocupado = False
        if ok:
            w.mensaje("ACCESO CONCEDIDO", "ok")
            self.lock.unlock()  # -> "unlocked" -> salir
            return GLib.SOURCE_REMOVE
        self.fallos += 1
        w.entry.set_text("")
        w.entry.set_sensitive(True)
        w._enfocar()
        w.mensaje(f"ACCESO DENEGADO  ·  INTENTO {self.fallos}", "warn")
        return GLib.SOURCE_REMOVE


if __name__ == "__main__":
    try:
        ctypes.CDLL(None).prctl(15, PROC.encode(), 0, 0, 0)  # PR_SET_NAME
    except (OSError, AttributeError):
        pass
    GLib.set_prgname(PROC)
    register_fonts()
    app = LockApp()
    ret = app.run(sys.argv[:1])
    if app.get_is_remote():  # ya habia otro bloqueo activo: tambien vale
        _avisar_bloqueado()
    sys.exit(app.codigo or ret)
