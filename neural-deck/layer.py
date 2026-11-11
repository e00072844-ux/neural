"""Capas de Wayland (protocolo wlr-layer-shell) para el wallpaper, el dock y la barra.

En Wayfire (y en cualquier compositor wlroots) el wallpaper va en la capa
BACKGROUND, detras de todas las ventanas, y el dock en la capa TOP, encima de
ellas. Lo hace gtk4-layer-shell, que tiene que cargarse antes que
libwayland-client: por eso ensure_preload() relanza el proceso con LD_PRELOAD
(y ya relanzado lo quita del entorno, para que las apps y la terminal que se
abran desde aqui no lo hereden). Sin la biblioteca o sin el protocolo (GNOME)
setup_*() devuelve False y la ventana sigue como una ventana normal."""
from __future__ import annotations

import os
import sys
from pathlib import Path

LIB = "libgtk4-layer-shell.so"
LIB_DIRS = ("/usr/lib", "/usr/lib64", "/usr/local/lib")
_MARK = "NEURAL_DECK_PRELOAD"  # el LD_PRELOAD de antes de relanzar ("" si no habia)


def ensure_preload() -> None:
    """Llamar antes de importar Gtk. Puede no volver (os.execve)."""
    if _MARK in os.environ:
        # ya relanzado: la biblioteca esta cargada, dejar el entorno como estaba
        old = os.environ.pop(_MARK)
        if old:
            os.environ["LD_PRELOAD"] = old
        else:
            os.environ.pop("LD_PRELOAD", None)
        return
    if not os.environ.get("WAYLAND_DISPLAY"):
        return
    lib = next((Path(d) / LIB for d in LIB_DIRS if (Path(d) / LIB).exists()), None)
    if lib is None:
        return
    old = os.environ.get("LD_PRELOAD", "")
    env = {**os.environ, _MARK: old, "LD_PRELOAD": f"{lib} {old}".strip()}
    os.execve(sys.executable, [sys.executable, *sys.argv], env)


def _shell():
    try:
        import gi

        gi.require_version("Gtk4LayerShell", "1.0")
        from gi.repository import Gtk4LayerShell
    except (ImportError, ValueError):
        return None
    return Gtk4LayerShell if Gtk4LayerShell.is_supported() else None


def setup_background(window) -> bool:
    """Wallpaper: detras de todo, estirado a la pantalla (menos lo que
    reserven las barras, p. ej. yambar), con teclado solo al hacerle clic."""
    ls = _shell()
    if ls is None:
        return False
    ls.init_for_window(window)
    ls.set_namespace(window, "neural-deck")
    ls.set_layer(window, ls.Layer.BACKGROUND)
    for edge in (ls.Edge.LEFT, ls.Edge.RIGHT, ls.Edge.TOP, ls.Edge.BOTTOM):
        ls.set_anchor(window, edge, True)
    ls.set_keyboard_mode(window, ls.KeyboardMode.ON_DEMAND)
    return True


def setup_dock(window) -> bool:
    """Dock: encima de las ventanas, abajo al centro, sin reservar espacio y
    sin quitarle nunca el teclado a la app activa (asi el "activated" de la
    IPC de Wayfire sigue diciendo cual es)."""
    ls = _shell()
    if ls is None:
        return False
    ls.init_for_window(window)
    ls.set_namespace(window, "neural-dock")
    ls.set_layer(window, ls.Layer.TOP)
    ls.set_anchor(window, ls.Edge.BOTTOM, True)
    ls.set_exclusive_zone(window, 0)
    ls.set_keyboard_mode(window, ls.KeyboardMode.NONE)
    return True


def setup_bar(window) -> bool:
    """Barra: arriba al centro, del ancho de su contenido (no de punta a
    punta), sin reservar espacio (se esconde como el dock y se pone encima
    de las ventanas), en la capa OVERLAY para que tambien se pueda sacar con
    apps en pantalla completa, y sin quitar el teclado."""
    ls = _shell()
    if ls is None:
        return False
    ls.init_for_window(window)
    ls.set_namespace(window, "neural-bar")
    ls.set_layer(window, ls.Layer.OVERLAY)
    ls.set_anchor(window, ls.Edge.TOP, True)
    ls.set_exclusive_zone(window, 0)
    ls.set_keyboard_mode(window, ls.KeyboardMode.NONE)
    return True


def setup_popup(window) -> bool:
    """Panel rapido de la barra: cubre toda el area libre (debajo de la
    barra) para que un clic fuera del panel lo cierre, y se queda el
    teclado mientras esta abierto (Esc)."""
    ls = _shell()
    if ls is None:
        return False
    ls.init_for_window(window)
    ls.set_namespace(window, "neural-bar-panel")
    ls.set_layer(window, ls.Layer.OVERLAY)
    for edge in (ls.Edge.LEFT, ls.Edge.RIGHT, ls.Edge.TOP, ls.Edge.BOTTOM):
        ls.set_anchor(window, edge, True)
    ls.set_exclusive_zone(window, 0)
    ls.set_keyboard_mode(window, ls.KeyboardMode.EXCLUSIVE)
    return True


class SeguirTty:
    """Al cambiar de tty, Wayfire cambia la pantalla eDP-1 por una falsa
    (NOOP-1, 1280x720, sin vsync) y al volver la quita: las capas que
    estaban en ella se cierran (dock y barra desaparecian), WebKit pintaba
    sin freno hasta llenar la RAM, y volver a mapear la misma ventana dejaba
    GTK con medidas viejas (todo con zoom). Remedio (31-oct):
    - sesion de logind inactiva -> ocultar la ventana (nada se pinta);
    - sesion activa otra vez -> esperar a que vuelva la pantalla buena y
      RELANZAR el proceso entero (os.execv, mismo PID): arranca limpio.
    Si Wayfire cierra la capa por su cuenta, close-request -> ocultar y
    relanzar igual (estas ventanas se cierran siempre con app.quit())."""

    def __init__(self, window, nombre: str):
        from gi.repository import Gio, GLib

        self.w, self.nombre, self._GLib = window, nombre, GLib
        self.activa, self._timer, self._intentos = True, 0, 0
        window.connect("close-request", self._on_close)
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
            ruta = bus.call_sync(
                "org.freedesktop.login1", "/org/freedesktop/login1",
                "org.freedesktop.login1.Manager", "GetSessionByPID",
                GLib.Variant("(u)", (os.getpid(),)), GLib.VariantType("(o)"),
                Gio.DBusCallFlags.NONE, -1, None).unpack()[0]
            self.sesion = Gio.DBusProxy.new_sync(
                bus, Gio.DBusProxyFlags.NONE, None, "org.freedesktop.login1",
                ruta, "org.freedesktop.login1.Session", None)
        except Exception as e:  # sin logind: no se vigila
            self._log(f"sin logind ({e}); no se vigila la tty")
            return
        self.sesion.connect("g-properties-changed", self._on_cambia)
        self._log(f"vigilando {ruta}")

    def _log(self, msg: str) -> None:
        print(f"vt[{self.nombre}]: {msg}", flush=True)

    def _on_close(self, _w):
        self._log("Wayfire cerro la capa -> oculto y relanzare")
        self.w.set_visible(False)
        if self.activa:
            self._programar()
        return True  # no destruir la ventana

    def _on_cambia(self, proxy, cambios, _inv) -> None:
        activa = cambios.unpack().get("Active")
        if activa is None:
            v = proxy.get_cached_property("Active")
            activa = v.unpack() if v is not None else True
        if activa == self.activa:
            return
        self.activa = activa
        if self._timer:
            self._GLib.source_remove(self._timer)
            self._timer = 0
        if not activa:
            self._log("sesion inactiva -> oculto")
            self.w.set_visible(False)
        else:
            self._programar()

    def _programar(self) -> None:
        if not self._timer:
            self._intentos = 0
            self._timer = self._GLib.timeout_add(400, self._quizas_relanzar)

    def _pantalla_buena(self) -> bool:
        from gi.repository import Gdk

        mons = Gdk.Display.get_default().get_monitors()
        nombres = [mons.get_item(i).get_connector() or "" for i in range(mons.get_n_items())]
        return bool(nombres) and not any(n.startswith("NOOP") for n in nombres)

    def _quizas_relanzar(self) -> bool:
        if not self.activa:
            self._timer = 0
            return False
        self._intentos += 1
        # pantalla buena y sin NOOP, o 8 s como mucho
        if not self._pantalla_buena() and self._intentos < 20:
            return True
        self._timer = 0
        self._log("sesion activa y pantalla lista -> me relanzo")
        try:
            self.w.get_display().flush()
        except Exception:
            pass
        sys.stdout.flush()
        sys.stderr.flush()
        os.execv(sys.executable, [sys.executable, *sys.argv])
        return False
