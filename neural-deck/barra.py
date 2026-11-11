#!/usr/bin/env python3
"""NEURAL//BAR: barra de arriba para Wayfire, mismo estilo que NEURAL//DOCK.

Capa OVERLAY de wlr-layer-shell pegada arriba al centro (ver layer.py): NO va
de punta a punta, es del ancho de lo que muestra, y NO reserva espacio.
Como el dock: se esconde sola (se desliza hacia arriba y queda una franja de
2 px; al llevar el mouse ahi vuelve), o se deja fija con el boton ◇/◆.
`pkill -USR2 -x neural-bar` la muestra / esconde (para una tecla en
wayfire.ini). Sigue el tema del wallpaper (board.json).

Muestra: hora y fecha, WiFi, volumen, micro, brillo, bateria y apagado.
- Rueda sobre volumen / micro / brillo: sube o baja 5 %.
- Clic sobre volumen / micro: silenciar.
- Se refresca cada 3 s y al instante con SIGUSR1 (lo manda sistema.py
  despues de usar las teclas del teclado).
- Clic en el boton de apagado: panel rapido (ver panel.py)."""
from __future__ import annotations

import ctypes
import json
import signal
import subprocess
import sys
import time

from layer import ensure_preload, setup_bar, SeguirTty
from senales import segura

ensure_preload()  # antes de cargar Gtk (ver layer.py)

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, GLibUnix, Gtk  # noqa: E402

import sistema  # noqa: E402
from board import CONFIG_DIR, load_board  # noqa: E402
from panel import PanelRapido, notif_pendientes, notif_state, panel_css  # noqa: E402
from estilo import MONO, PAD, SANS, Frame, css_color, draw_icon, load_themes, register_fonts  # noqa: E402

APP_ID = "org.local.NeuralBar"
STATE_PATH = CONFIG_DIR / "bar-state.json"
STRIP_H = 2  # alto de la franja que queda al esconderse
HIDE_AFTER_MS = 650  # tiempo con el mouse fuera antes de esconderse
INNER_H = 30  # alto del contenido, sin el margen del brillo
BAR_H = INNER_H + 2 * PAD
REFRESH_S = 3
STEP = 5  # % por paso de rueda

DIAS = ("LUN", "MAR", "MIE", "JUE", "VIE", "SAB", "DOM")
MESES = ("ENE", "FEB", "MAR", "ABR", "MAY", "JUN", "JUL", "AGO", "SEP", "OCT", "NOV", "DIC")


def _set_comm(name: str) -> None:
    """Nombre del proceso (lo que ve `pkill -x`), para que sistema.py lo encuentre."""
    try:
        ctypes.CDLL(None).prctl(15, name.encode()[:15], 0, 0, 0)  # PR_SET_NAME
    except (OSError, AttributeError):
        pass


def _theme_css(t: dict) -> str:
    acc, alt, warn, text, muted = t["accent"], t["alt"], t["warn"], t["text"], t["muted"]
    return f"""
window.neural-bar {{ background: transparent; }}
.nb-clock {{ font-family: "{SANS}"; font-weight: 700; font-size: 17px; letter-spacing: 2px; color: {css_color(acc)}; }}
.nb-date {{ font-family: "{MONO}"; font-size: 11px; letter-spacing: 1px; color: {css_color(muted)}; }}
.nb-sep {{ background: {css_color(acc, 0.35)}; min-width: 1px; margin: 4px 6px; }}
.nb-ind {{ padding: 0 5px; border-bottom: 1px solid transparent; }}
.nb-ind:hover {{ background: {css_color(acc, 0.14)}; border-bottom-color: {css_color(acc)}; }}
.nb-val {{ font-family: "{MONO}"; font-size: 12px; color: {css_color(text)}; }}
.nb-val.off {{ color: {css_color(muted)}; }}
.nb-val.warn {{ color: {css_color(warn)}; }}
.nb-tool {{ all: unset; min-width: 22px; min-height: 18px; background: rgba(0, 0, 0, 0.55);
           border: 1px solid {css_color(acc, 0.6)}; color: {css_color(text)}; font-size: 11px; }}
.nb-tool:hover {{ background: {css_color(acc)}; color: #0a0204; }}
.nb-tool.on {{ background: {css_color(alt)}; border-color: {css_color(alt)}; color: #001014; }}
"""


class Indicador(Gtk.Box):
    """Icono de linea (estilo.draw_icon) + texto corto."""

    def __init__(self, bar: "BarWindow", icon: str, tooltip: str = ""):
        super().__init__(spacing=4, valign=Gtk.Align.CENTER, tooltip_text=tooltip)
        self.add_css_class("nb-ind")
        self.bar, self.icon = bar, icon
        self.level: float | None = None
        self.off = False
        self.extra = None
        self.area = Gtk.DrawingArea(content_width=18, content_height=18, valign=Gtk.Align.CENTER)
        self.area.set_draw_func(self._draw)
        self.label = Gtk.Label()
        self.label.add_css_class("nb-val")
        self.append(self.area)
        self.append(self.label)

    def _draw(self, _area, cr, w, h):
        t = self.bar.theme
        color = t["warn"] if "warn" in self.label.get_css_classes() else t["alt"]
        draw_icon(cr, self.icon, w, h, color, self.level, self.off, self.extra)

    def set(self, text: str, level: float | None = None, off: bool = False, warn: bool = False, extra=None):
        self.label.set_text(text)
        self.level, self.off, self.extra = level, off, extra
        for cls, on in (("off", off), ("warn", warn)):
            (self.label.add_css_class if on else self.label.remove_css_class)(cls)
        self.area.queue_draw()

    def on_scroll(self, handler) -> None:
        ctrl = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL)
        ctrl.connect("scroll", lambda _c, _dx, dy: (handler(-STEP if dy > 0 else STEP), True)[1])
        self.add_controller(ctrl)

    def on_click(self, handler) -> None:
        click = Gtk.GestureClick()
        click.connect("released", lambda *_: handler())
        self.add_controller(click)


def _load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return {"mode": "autohide"}


def _save_state(state: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state))


def _sep() -> Gtk.Widget:
    s = Gtk.Box()
    s.add_css_class("nb-sep")
    return s


class BarWindow(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application):
        super().__init__(application=app, title="Neural Bar")
        self.set_decorated(False)
        self.set_resizable(False)
        setup_bar(self)
        self._tty = SeguirTty(self, "barra")
        self.add_css_class("neural-bar")

        self.themes = load_themes()
        self.theme = next(iter(self.themes.values()))
        self.css = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), self.css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_USER + 1)

        self.state = _load_state()
        self.shown = True
        self.hide_id = 0

        self.frame = Frame(self, Gtk.Orientation.HORIZONTAL, 12, ("bl", "br"), False)
        self.frame.set_size_request(-1, BAR_H)
        row = Gtk.Box(spacing=2, valign=Gtk.Align.CENTER, margin_start=PAD + 16, margin_end=PAD + 16,
                      margin_top=PAD, margin_bottom=PAD)
        self.frame.append(row)

        self.clock = Gtk.Label()
        self.clock.add_css_class("nb-clock")
        self.date = Gtk.Label(margin_start=8)
        self.date.add_css_class("nb-date")
        self.wifi = Indicador(self, "wifi")
        self.vol = Indicador(self, "vol", "Volumen — rueda: subir/bajar · clic: silenciar")
        self.mic = Indicador(self, "mic", "Micrófono — rueda: subir/bajar · clic: silenciar")
        self.brillo = Indicador(self, "sol", "Brillo — rueda: subir/bajar")
        self.bat = Indicador(self, "bat")
        self.avisos = Indicador(self, "bell", "Avisos — clic: panel rápido")
        self.avisos.on_click(self.toggle_panel)
        self.power = Indicador(self, "power", "Panel rápido: volumen, brillo, red y sesión")
        self.power.on_click(self.toggle_panel)
        self.panel: PanelRapido | None = None

        self.vol.on_scroll(lambda p: self._do(sistema.step_volume, sistema.SINK, p))
        self.mic.on_scroll(lambda p: self._do(sistema.step_volume, sistema.SOURCE, p))
        self.brillo.on_scroll(lambda p: self._do(sistema.step_brillo, p))
        self.vol.on_click(lambda: self._do(subprocess.run, sistema.cmd_toggle_mute(sistema.SINK)))
        self.mic.on_click(lambda: self._do(subprocess.run, sistema.cmd_toggle_mute(sistema.SOURCE)))

        self.pin = Gtk.Button(valign=Gtk.Align.CENTER, margin_start=6)
        self.pin.add_css_class("nb-tool")
        self.pin.connect("clicked", self._on_pin)

        for w in (self.clock, self.date, _sep(), self.wifi, self.vol, self.mic, self.brillo, self.bat,
                  self.avisos, _sep(), self.power, self.pin):
            row.append(w)

        # franja de 2 px arriba + la barra que baja / sube
        self.frame.set_halign(Gtk.Align.CENTER)
        self.revealer = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN,
                                     transition_duration=180, reveal_child=True)
        self.revealer.set_child(self.frame)
        strip = Gtk.Box()
        strip.set_size_request(-1, STRIP_H)
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.append(strip)
        outer.append(self.revealer)
        self.set_child(outer)

        motion = Gtk.EventControllerMotion()
        motion.connect("notify::contains-pointer", self._on_pointer)
        self.add_controller(motion)
        self._sync_pin()

        # el tema sigue al del wallpaper (board.json)
        self.monitor = Gio.File.new_for_path(str(CONFIG_DIR / "board.json")).monitor_file(
            Gio.FileMonitorFlags.NONE, None)
        self.monitor.connect("changed", lambda *_: self._apply_theme())
        self._apply_theme()
        self._tick_clock()
        self._update()
        GLib.timeout_add_seconds(1, self._tick_clock)
        GLib.timeout_add_seconds(REFRESH_S, self._update)
        self._teclado_iniciar()
        self._schedule_hide()  # si el mouse no esta encima, se esconde

    # ---------- tema ----------
    def _apply_theme(self):
        self.theme = self.themes.get(load_board().get("theme", "red"), self.theme)
        self.css.load_from_string(_theme_css(self.theme) + panel_css(self.theme))
        self.frame.queue_draw()
        for ind in (self.wifi, self.vol, self.mic, self.brillo, self.bat, self.avisos, self.power):
            ind.area.queue_draw()
        if self.panel is not None:
            self.panel.frame.queue_draw()

    # ---------- acciones ----------
    def _do(self, fn, *args):
        try:
            fn(*args)
        except (OSError, subprocess.SubprocessError):
            pass
        self._update()

    # ---------- panel rapido ----------
    def panel_open(self) -> bool:
        return self.panel is not None and self.panel.get_visible()

    def toggle_panel(self):
        if self.panel_open():
            self.panel.close_panel()
            return
        if self.panel is None:
            self.panel = PanelRapido(self)
        if self.hide_id:
            GLib.source_remove(self.hide_id)
            self.hide_id = 0
        # la barra esta centrada: su borde derecho = (pantalla + barra) / 2
        mon = self.get_display().get_monitor_at_surface(self.get_surface())
        mon_w = mon.get_geometry().width if mon else 1920
        right = (mon_w + self.frame.get_width()) // 2
        self.panel.open_at(right, STRIP_H + self.frame.get_height() - PAD)

    def panel_closed(self):
        self._schedule_hide()

    # ---------- esconder / mostrar (igual que el dock) ----------
    def _sync_pin(self):
        pinned = self.state.get("mode") == "pinned"
        self.pin.set_label("◆" if pinned else "◇")
        self.pin.set_tooltip_text("Fija: siempre visible (clic = esconderse sola)" if pinned
                                  else "Se esconde sola (clic = dejarla fija)")
        (self.pin.add_css_class if pinned else self.pin.remove_css_class)("on")

    def _on_pin(self, _b):
        self.state["mode"] = "autohide" if self.state.get("mode") == "pinned" else "pinned"
        _save_state(self.state)
        self._sync_pin()

    def _on_pointer(self, ctrl, _pspec):
        if ctrl.get_property("contains-pointer"):
            if self.hide_id:
                GLib.source_remove(self.hide_id)
                self.hide_id = 0
            self._set_shown(True)
        else:
            self._schedule_hide()

    def _schedule_hide(self):
        if self.hide_id or not self.shown:
            return

        def done():
            self.hide_id = 0
            if self.state.get("mode") != "pinned" and not self.panel_open():
                self._set_shown(False)
            return GLib.SOURCE_REMOVE

        self.hide_id = GLib.timeout_add(HIDE_AFTER_MS, done)

    def _set_shown(self, on: bool):
        if on == self.shown:
            return
        self.shown = on
        self.revealer.set_reveal_child(on)
        if on:
            self._update()

    def toggle(self):
        """SIGUSR2 (tecla): mostrar unos segundos / esconder ya."""
        if self.shown:
            self._set_shown(False)
        else:
            self._set_shown(True)
            self._schedule_hide_later()

    def _schedule_hide_later(self, ms: int = 3000):
        if self.hide_id:
            GLib.source_remove(self.hide_id)
        self.hide_id = 0

        def later():
            self.hide_id = 0
            self._schedule_hide()
            return GLib.SOURCE_REMOVE

        self.hide_id = GLib.timeout_add(ms, later)

    # ---------- refresco ----------
    # ---------- luz del teclado: recordar el nivel (también si se cambia con Fn+Espacio) ----------
    def _teclado_iniciar(self):
        self.kbd_vigilar = False
        if sistema.get_teclado() is None:
            return
        self.kbd_visto = None
        try:  # al volver de suspender, poner otra vez el nivel guardado (y no guardar el 0 de la vuelta)
            bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
            bus.signal_subscribe("org.freedesktop.login1", "org.freedesktop.login1.Manager", "PrepareForSleep",
                                 "/org/freedesktop/login1", None, Gio.DBusSignalFlags.NONE,
                                 lambda _c, _s, _p, _i, _n, params: self._teclado_dormir(params.unpack()[0]))
        except GLib.Error:
            pass
        GLib.timeout_add(1500, lambda: (self._teclado_restaurar(), GLib.SOURCE_REMOVE)[1])
        GLib.timeout_add_seconds(3, self._teclado_vigilar)

    def _teclado_restaurar(self):
        guardado = sistema.teclado_guardado()
        if guardado is not None:
            sistema.set_teclado(guardado)
        k = sistema.get_teclado()
        self.kbd_visto = k[0] if k else None
        self.kbd_vigilar = True

    def _teclado_dormir(self, entrando: bool):
        if entrando:
            self.kbd_vigilar = False
        else:
            GLib.timeout_add(2000, lambda: (self._teclado_restaurar(), GLib.SOURCE_REMOVE)[1])

    def _teclado_vigilar(self):
        if self.kbd_vigilar:
            k = sistema.get_teclado()
            if k is not None and k[0] != self.kbd_visto:
                self.kbd_visto = k[0]
                sistema.guardar_teclado(k[0])
        return GLib.SOURCE_CONTINUE

    def _tick_clock(self):
        now = time.localtime()
        self.clock.set_text(time.strftime("%H:%M", now))
        self.date.set_text(f"{DIAS[now.tm_wday]} {now.tm_mday:02d} {MESES[now.tm_mon - 1]}")
        return GLib.SOURCE_CONTINUE

    def _update(self):
        if not self.shown:  # escondida: no gastar en leer el sistema
            return GLib.SOURCE_CONTINUE
        w = sistema.get_wifi()
        self.wifi.set_visible(w is not None)
        if w:
            if w["up"] and w["ssid"]:
                self.wifi.set(w["ssid"][:14], 1.0)
                self.wifi.set_tooltip_text(f"{w['iface']} · {w['ssid']} · {w['ip'] or 'sin IP'}")
            else:
                self.wifi.set("OFF", 0, off=True, warn=True)
                self.wifi.set_tooltip_text(f"{w['iface']}: desconectado")

        for ind, dev in ((self.vol, sistema.SINK), (self.mic, sistema.SOURCE)):
            a = sistema.get_audio(dev)
            ind.set_visible(a is not None)
            if a:
                ind.set(f"{round(a[0] * 100):3d}%", a[0], off=a[1])

        b = sistema.get_brillo()
        self.brillo.set_visible(b is not None)
        if b is not None:
            self.brillo.set(f"{round(b * 100):3d}%", b)

        bat = sistema.get_bateria()
        self.bat.set_visible(bat is not None)
        if bat:
            cap = bat["capacity"]
            charging = bat["status"] == "Charging"
            self.bat.set(f"{cap:3d}%", cap / 100, warn=cap <= 15 and not bat["ac"],
                         extra=self.theme["alt"] if charging else None)
            self.bat.set_tooltip_text(f"Batería {cap}% · {bat['status']}" + (" · cargador" if bat["ac"] else ""))

        # campana: solo con no molestar o con avisos guardados sin ver
        dnd, pend = bool(notif_state().get("dnd")), notif_pendientes()
        self.avisos.set_visible(dnd or pend > 0)
        self.avisos.set(str(pend) if pend else "", off=dnd)
        self.power.set(sistema.uptime())
        return GLib.SOURCE_CONTINUE


class BarApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID)

    def do_startup(self):
        Gtk.Application.do_startup(self)
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            GLibUnix.signal_add(GLib.PRIORITY_DEFAULT, sig, lambda *_: (self.quit(), GLib.SOURCE_REMOVE)[1])
        # SIGUSR1 = "algo cambio" (teclas de volumen / brillo via sistema.py)
        # 5-nov: la BARRA, no "la ventana activa" (con el panel rápido abierto era
        # PanelRapido, sin _update -> excepción -> GLib quitaba el manejador -> la
        # siguiente SIGUSR1 mataba el proceso). senales.segura nunca lo suelta.
        GLibUnix.signal_add(GLib.PRIORITY_DEFAULT, signal.SIGUSR1,
                            segura("barra SIGUSR1", lambda: self._barra() and self._barra()._update()))

    def _barra(self):
        return next((w for w in self.get_windows() if isinstance(w, BarWindow)), None)

    def do_activate(self):
        win = self._barra() or BarWindow(self)
        if not getattr(self, "_usr2_puesto", False):
            self._usr2_puesto = True
            GLibUnix.signal_add(GLib.PRIORITY_DEFAULT, signal.SIGUSR2,
                                segura("barra SIGUSR2", lambda: self._barra() and self._barra().toggle()))
        win.present()


if __name__ == "__main__":
    _set_comm(sistema.BAR_PROC)
    GLib.set_prgname(sistema.BAR_PROC)
    register_fonts()
    sys.exit(BarApp().run(sys.argv))
