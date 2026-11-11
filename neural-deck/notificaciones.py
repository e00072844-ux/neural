#!/usr/bin/env python3
"""NEURAL//NOTIFY: servidor de notificaciones (org.freedesktop.Notifications)
con el estilo de NEURAL//DOCK, para Wayfire.

- Tarjetas arriba a la derecha (capa OVERLAY de wlr-layer-shell, sin teclado
  ni espacio reservado), como mucho MAX_VISIBLES a la vez.
- Clic en la tarjeta = accion "default" (si la app la ofrece) y se cierra;
  ✕ = cerrar; botones para las demas acciones. Pasar el raton por encima
  pausa la cuenta atras.
- Urgencia critica (urgency=2): color de aviso y no se va sola.
- No molestar: ~/.config/neural-deck/notif-state.json {"dnd": true} (lo
  cambia el panel rapido); con no molestar no se muestran (salvo las
  criticas) pero se guardan en el historial.
- Historial (las ultimas HIST_MAX) en ~/.cache/neural-deck/notif-history.json,
  que lee el panel rapido. Este proceso es el unico que lo escribe.
Sigue el tema del wallpaper (board.json)."""
from __future__ import annotations

import ctypes
import json
import re
import signal
import sys
import time
from pathlib import Path

from layer import ensure_preload

ensure_preload()  # antes de cargar Gtk (ver layer.py)

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gdk, Gio, GLib, GLibUnix, Gtk, Pango  # noqa: E402

from board import CONFIG_DIR, load_board  # noqa: E402
from estilo import MONO, PAD, SANS, Frame, css_color, load_themes, register_fonts  # noqa: E402
import notif_texto  # noqa: E402

APP_ID = "org.local.NeuralNotify"
PROC = "neural-notify"
STATE_PATH = CONFIG_DIR / "notif-state.json"
HIST_PATH = Path.home() / ".cache" / "neural-deck" / "notif-history.json"
HIST_MAX = 50
MAX_VISIBLES = 4
DEFAULT_MS = 6000
CARD_W = 360

IFACE_XML = """
<node><interface name="org.freedesktop.Notifications">
  <method name="GetCapabilities"><arg type="as" direction="out"/></method>
  <method name="Notify">
    <arg type="s" direction="in"/><arg type="u" direction="in"/><arg type="s" direction="in"/>
    <arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="as" direction="in"/>
    <arg type="a{sv}" direction="in"/><arg type="i" direction="in"/><arg type="u" direction="out"/>
  </method>
  <method name="CloseNotification"><arg type="u" direction="in"/></method>
  <method name="GetServerInformation">
    <arg type="s" direction="out"/><arg type="s" direction="out"/><arg type="s" direction="out"/><arg type="s" direction="out"/>
  </method>
  <signal name="NotificationClosed"><arg type="u"/><arg type="u"/></signal>
  <signal name="ActionInvoked"><arg type="u"/><arg type="s"/></signal>
</interface></node>"""

# motivos de cierre (especificacion)
EXPIRED, DISMISSED, BY_CALL = 1, 2, 3


def _set_comm(name: str) -> None:
    try:
        ctypes.CDLL(None).prctl(15, name.encode()[:15], 0, 0, 0)  # PR_SET_NAME
    except (OSError, AttributeError):
        pass


_markup = notif_texto.markup  # compartida con el historial del panel


def _theme_css(t: dict) -> str:
    acc, alt, warn, text, muted = t["accent"], t["alt"], t["warn"], t["text"], t["muted"]
    return f"""
window.neural-notify {{ background: transparent; }}
.nn-app {{ font-family: "{MONO}"; font-size: 10px; letter-spacing: 1px; color: {css_color(muted)}; }}
.nn-sum {{ font-family: "{SANS}"; font-weight: 700; font-size: 16px; letter-spacing: 1px; color: {css_color(acc)}; }}
.nn-card.critica .nn-sum {{ color: {css_color(warn)}; }}
.nn-body {{ font-family: "{SANS}"; font-weight: 500; font-size: 14px; color: {css_color(text)}; }}
.nn-x {{ all: unset; color: {css_color(muted)}; padding: 0 4px; font-size: 14px; }}
.nn-x:hover {{ color: {css_color(warn)}; }}
.nn-act {{ all: unset; padding: 2px 10px; border: 1px solid {css_color(acc, 0.6)}; background: rgba(0, 0, 0, 0.55);
          color: {css_color(text)}; font-family: "{SANS}"; font-weight: 700; font-size: 12px; letter-spacing: 1px; }}
.nn-act:hover {{ background: {css_color(acc)}; color: #0a0204; }}
.nn-bar trough {{ min-height: 2px; background: transparent; }}
.nn-bar progress {{ min-height: 2px; background: {css_color(alt, 0.8)}; }}
"""


class Tarjeta(Frame):
    def __init__(self, win: "NotifyWindow", n: dict):
        super().__init__(win, Gtk.Orientation.VERTICAL, 12, ("tl", "br"), False, n["app"].upper()[:18] or "AVISO")
        self.win, self.n = win, n
        self.add_css_class("nn-card")
        if n["urgencia"] == 2:
            self.add_css_class("critica")
        self.set_size_request(CARD_W + 2 * PAD, -1)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_start=PAD + 14,
                       margin_end=PAD + 10, margin_top=PAD + 20, margin_bottom=PAD + 10)
        self.append(body)

        head = Gtk.Box(spacing=8)
        icono = self._icono(n)
        if icono is not None:
            head.append(icono)
        textos = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        self.sum = Gtk.Label(xalign=0, wrap=True, wrap_mode=2, max_width_chars=34, lines=2, ellipsize=3)
        self.sum.add_css_class("nn-sum")
        self.cuerpo = Gtk.Label(xalign=0, wrap=True, wrap_mode=2, max_width_chars=40, lines=4, ellipsize=3)
        # enlaces del cuerpo (los manda cualquier app): solo web, nada de file:// u otros esquemas
        self.cuerpo.connect("activate-link", lambda _l, uri: notif_texto.abrir_web(uri) or True)  # nunca el de GTK (Error 71 en layer-shell)
        self.cuerpo.add_css_class("nn-body")
        textos.append(self.sum)
        textos.append(self.cuerpo)
        head.append(textos)
        x = Gtk.Button(label="✕", valign=Gtk.Align.START, tooltip_text="Cerrar")
        x.add_css_class("nn-x")
        x.connect("clicked", lambda _b: self.win.cerrar(self.n["id"], DISMISSED))
        head.append(x)
        body.append(head)

        self.acciones = Gtk.Box(spacing=6, halign=Gtk.Align.END)
        body.append(self.acciones)
        self.barra = Gtk.ProgressBar(fraction=1.0)
        self.barra.add_css_class("nn-bar")
        body.append(self.barra)
        self.rellenar(n)

        click = Gtk.GestureClick()
        click.connect("released", self._on_click)
        self.add_controller(click)
        motion = Gtk.EventControllerMotion()
        motion.connect("enter", lambda *_: self.win.pausar(self.n["id"], True))
        motion.connect("leave", lambda *_: self.win.pausar(self.n["id"], False))
        self.add_controller(motion)

    def _icono(self, n: dict) -> Gtk.Widget | None:
        icon = n.get("icono") or ""
        if not icon:
            return None
        img = Gtk.Image(pixel_size=36, valign=Gtk.Align.START)
        if icon.startswith("file://"):
            icon = GLib.filename_from_uri(icon)[0]
        if icon.startswith("/"):
            if not Path(icon).exists():
                return None
            img.set_from_file(icon)
        else:
            theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
            if not theme.has_icon(icon):
                return None
            img.set_from_icon_name(icon)
        return img

    def rellenar(self, n: dict):
        self.n = n
        self.sum.set_text(n["resumen"])
        self.cuerpo.set_markup(_markup(n["cuerpo"]))
        self.cuerpo.set_visible(bool(n["cuerpo"]))
        while (c := self.acciones.get_first_child()) is not None:
            self.acciones.remove(c)
        for key, label in n["acciones"]:
            if key == "default":
                continue
            b = Gtk.Button(label=label)
            b.add_css_class("nn-act")
            b.connect("clicked", lambda _b, k=key: self.win.accion(self.n["id"], k))
            self.acciones.append(b)
        self.acciones.set_visible(self.acciones.get_first_child() is not None)
        self.barra.set_visible(n["ms"] > 0)

    def _on_click(self, gesture, _n, x, y):
        w = self.pick(x, y, Gtk.PickFlags.DEFAULT)
        while w is not None and w is not self:  # clic en un boton: ya lo atiende el boton
            if isinstance(w, Gtk.Button):
                return
            w = w.get_parent()
        if any(k == "default" for k, _ in self.n["acciones"]):
            self.win.accion(self.n["id"], "default")
        else:
            self.win.cerrar(self.n["id"], DISMISSED)


class NotifyWindow(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application):
        super().__init__(application=app, title="Neural Notify")
        self.set_decorated(False)
        self.set_resizable(False)
        self._layer()
        self.add_css_class("neural-notify")

        self.themes = load_themes()
        self.theme = next(iter(self.themes.values()))
        self.css = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), self.css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_USER + 1)
        self.pila = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=6, margin_end=6)
        self.set_child(self.pila)

        self.tarjetas: dict[int, Tarjeta] = {}
        self.timers: dict[int, dict] = {}  # id -> {fin, restante, pausa}
        self.next_id = 1
        self.hist: list[dict] = self._leer_hist()
        self.bus: Gio.DBusConnection | None = None

        self.mon_board = Gio.File.new_for_path(str(CONFIG_DIR / "board.json")).monitor_file(
            Gio.FileMonitorFlags.NONE, None)
        self.mon_board.connect("changed", lambda *_: self._apply_theme())
        self._apply_theme()
        GLib.timeout_add(100, self._tick)

    def _layer(self):
        try:
            gi.require_version("Gtk4LayerShell", "1.0")
            from gi.repository import Gtk4LayerShell as ls
        except (ImportError, ValueError):
            return
        if not ls.is_supported():
            return
        ls.init_for_window(self)
        ls.set_namespace(self, "neural-notify")
        ls.set_layer(self, ls.Layer.OVERLAY)
        ls.set_anchor(self, ls.Edge.TOP, True)
        ls.set_anchor(self, ls.Edge.RIGHT, True)
        ls.set_exclusive_zone(self, 0)
        ls.set_keyboard_mode(self, ls.KeyboardMode.NONE)

    def _apply_theme(self):
        self.theme = self.themes.get(load_board().get("theme", "red"), self.theme)
        self.css.load_from_string(_theme_css(self.theme))
        for t in self.tarjetas.values():
            t.queue_draw()

    # ---------- estado / historial ----------
    @staticmethod
    def dnd() -> bool:
        try:
            return bool(json.loads(STATE_PATH.read_text()).get("dnd"))
        except (OSError, json.JSONDecodeError):
            return False

    @staticmethod
    def _leer_hist() -> list[dict]:
        try:
            return json.loads(HIST_PATH.read_text())[-HIST_MAX:]
        except (OSError, json.JSONDecodeError):
            return []

    def _guardar_hist(self):
        try:
            HIST_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = HIST_PATH.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.hist[-HIST_MAX:], ensure_ascii=False))
            tmp.replace(HIST_PATH)
        except OSError:
            pass

    # ---------- D-Bus ----------
    def exportar(self, bus: Gio.DBusConnection):
        self.bus = bus
        info = Gio.DBusNodeInfo.new_for_xml(IFACE_XML).interfaces[0]
        bus.register_object("/org/freedesktop/Notifications", info, self._metodo, None, None)

    def _metodo(self, _conn, _sender, _path, _iface, method, params, inv):
        if method == "GetCapabilities":
            inv.return_value(GLib.Variant("(as)", (["body", "body-markup", "body-hyperlinks", "actions",
                                                     "icon-static", "persistence"],)))
        elif method == "GetServerInformation":
            inv.return_value(GLib.Variant("(ssss)", ("NEURAL//NOTIFY", "neural-deck", "1.0", "1.2")))
        elif method == "CloseNotification":
            self.cerrar(params.unpack()[0], BY_CALL)
            inv.return_value(None)
        elif method == "Notify":
            inv.return_value(GLib.Variant("(u)", (self.notify(*params.unpack()),)))
        else:
            inv.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", method)

    def _signal(self, name: str, args: GLib.Variant):
        if self.bus is not None:
            self.bus.emit_signal(None, "/org/freedesktop/Notifications", "org.freedesktop.Notifications", name, args)

    # ---------- notificaciones ----------
    def notify(self, app, replaces, icon, summary, body, actions, hints, timeout) -> int:
        urg = hints.get("urgency", 1)
        urg = urg[0] if isinstance(urg, (bytes, bytearray)) else int(urg)
        if not icon:
            icon = hints.get("image-path") or hints.get("image_path") or hints.get("desktop-entry") or ""
        nid = replaces if replaces in self.tarjetas else self.next_id
        if nid == self.next_id:
            self.next_id += 1
        ms = 0 if urg == 2 or timeout == 0 else (DEFAULT_MS if timeout < 0 else timeout)
        n = {"id": nid, "app": app or "", "icono": icon, "resumen": summary or "", "cuerpo": body or "",
             "acciones": list(zip(actions[0::2], actions[1::2])), "urgencia": urg, "ms": ms}
        oculta = self.dnd() and urg != 2
        if not hints.get("transient"):  # transient = aviso efímero (OSD): fuera del historial
            self.hist.append({"t": time.time(), "app": n["app"], "resumen": n["resumen"],
                              "cuerpo": re.sub(r"<[^>]+>", "", n["cuerpo"])[:300], "urgencia": urg, "oculta": oculta,
                              "markup": n["cuerpo"][:600],  # con enlaces, para el historial del panel
                              "desktop": notif_texto.desktop_valido(hints.get("desktop-entry"))})
            self.hist = self.hist[-HIST_MAX:]
            self._guardar_hist()
        if oculta:
            return nid
        if nid in self.tarjetas:
            self.tarjetas[nid].rellenar(n)
        else:
            t = Tarjeta(self, n)
            self.tarjetas[nid] = t
            self.pila.prepend(t)  # la nueva arriba
        if ms > 0:
            self.timers[nid] = {"fin": time.monotonic() + ms / 1000, "total": ms / 1000, "pausa": None}
        else:
            self.timers.pop(nid, None)
        # demasiadas: cerrar la mas vieja que no sea critica
        while len(self.tarjetas) > MAX_VISIBLES:
            vieja = next((i for i in self.tarjetas if self.tarjetas[i].n["urgencia"] != 2), None)
            if vieja is None:
                break
            self.cerrar(vieja, EXPIRED)
        self.set_visible(True)
        self.present()
        return nid

    def cerrar(self, nid: int, motivo: int):
        t = self.tarjetas.pop(nid, None)
        self.timers.pop(nid, None)
        if t is not None:
            self.pila.remove(t)
            self._signal("NotificationClosed", GLib.Variant("(uu)", (nid, motivo)))
        if not self.tarjetas:
            self.set_visible(False)

    def accion(self, nid: int, key: str):
        self._signal("ActionInvoked", GLib.Variant("(us)", (nid, key)))
        self.cerrar(nid, DISMISSED)

    def pausar(self, nid: int, on: bool):
        tm = self.timers.get(nid)
        if tm is None:
            return
        now = time.monotonic()
        if on and tm["pausa"] is None:
            tm["pausa"] = now
        elif not on and tm["pausa"] is not None:
            tm["fin"] += now - tm["pausa"]
            tm["pausa"] = None

    def _tick(self):
        now = time.monotonic()
        for nid, tm in list(self.timers.items()):
            if tm["pausa"] is not None:
                continue
            resta = tm["fin"] - now
            if resta <= 0:
                self.cerrar(nid, EXPIRED)
            elif nid in self.tarjetas:
                self.tarjetas[nid].barra.set_fraction(resta / tm["total"])
        return GLib.SOURCE_CONTINUE


class NotifyApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID)
        self.win: NotifyWindow | None = None

    def do_startup(self):
        Gtk.Application.do_startup(self)
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            GLibUnix.signal_add(GLib.PRIORITY_DEFAULT, sig, lambda *_: (self.quit(), GLib.SOURCE_REMOVE)[1])
        self.hold()  # sigue vivo sin ventanas visibles
        self.win = NotifyWindow(self)
        bus = self.get_dbus_connection()
        self.win.exportar(bus)
        Gio.bus_own_name_on_connection(bus, "org.freedesktop.Notifications", Gio.BusNameOwnerFlags.REPLACE,
                                       None, lambda *_: (print("otro servidor de notificaciones ya esta activo",
                                                              file=sys.stderr), self.quit()))

    def do_activate(self):
        pass


if __name__ == "__main__":
    _set_comm(PROC)
    GLib.set_prgname(PROC)
    register_fonts()
    sys.exit(NotifyApp().run(sys.argv))
