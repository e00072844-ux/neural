"""Panel rapido de NEURAL//BAR (se abre con el boton de apagado de la barra).

Ventana de capa OVERLAY que cubre la pantalla entera, transparente salvo el
marco del panel (ver layer.setup_popup): un clic fuera del marco o Esc lo
cierran. Dentro:
- barras para arrastrar: volumen, micro y brillo (clic en el icono = silenciar);
- elegir la salida y la entrada de audio (altavoces, jack, HDMI, Bluetooth);
- WiFi: estado y boton REDES: buscar, conectar a guardadas, pedir la
  contraseña de las nuevas (se guardan solo si conecta), olvidar (NetworkManager, ver red.py);
- Bluetooth: encender/apagar y EQUIPOS: buscar, emparejar, conectar,
  desconectar, olvidar (ver bt.py); bateria;
- avisos: no molestar e historial de NEURAL//NOTIFY (ver notificaciones.py);
- sesion: salir / reiniciar / apagar, con confirmacion (primer clic arma,
  el segundo ejecuta; se desarma solo a los 3 s).
Lo crea barra.py; se importa despues de ensure_preload()."""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

import mosaico  # noqa: E402
import sistema  # noqa: E402
import red  # noqa: E402
import notif_texto  # noqa: E402
from wayfire_ipc import WayfireIPC  # noqa: E402
from bt import Bluetooth, BtError  # noqa: E402
from board import CONFIG_DIR  # noqa: E402
from estilo import MONO, PAD, SANS, Frame, css_color, draw_icon  # noqa: E402
from layer import setup_popup  # noqa: E402

PANEL_W = 340  # ancho del contenido, sin el margen del brillo
ARM_MS = 3000
MAX_REDES = 8
MAX_EQUIPOS = 8
MAX_AVISOS = 5
NOTIF_STATE = CONFIG_DIR / "notif-state.json"  # la misma carpeta que board.json
NOTIF_HIST = Path.home() / ".cache" / "neural-deck" / "notif-history.json"


def notif_state() -> dict:
    """{dnd, visto_hasta, borrado_hasta}: lo escribe el panel; notificaciones.py solo lee dnd."""
    try:
        return json.loads(NOTIF_STATE.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def notif_set(**kw) -> dict:
    st = {**notif_state(), **kw}
    try:
        NOTIF_STATE.parent.mkdir(parents=True, exist_ok=True)
        NOTIF_STATE.write_text(json.dumps(st))
    except OSError:
        pass
    return st


def notif_hist() -> list[dict]:
    """Historial sin lo borrado desde el panel, lo mas nuevo primero."""
    try:
        hist = json.loads(NOTIF_HIST.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    desde = notif_state().get("borrado_hasta", 0)
    return [h for h in reversed(hist) if h.get("t", 0) > desde]


def notif_pendientes() -> int:
    """Avisos que no se mostraron (no molestar) y aun no se vieron en el panel."""
    visto = notif_state().get("visto_hasta", 0)
    return sum(1 for h in notif_hist() if h.get("oculta") and h.get("t", 0) > visto)
SESION = (("salir", "SALIR", "¿SALIR?"), ("suspender", "SUSPENDER", "¿SUSPENDER?"),
          ("reiniciar", "REINICIAR", "¿REINICIAR?"), ("apagar", "APAGAR", "¿APAGAR?"))


def panel_css(t: dict) -> str:
    acc, alt, warn, text, muted = t["accent"], t["alt"], t["warn"], t["text"], t["muted"]
    return f"""
window.neural-bar-panel {{ background: transparent; }}
.np-title {{ font-family: "{SANS}"; font-weight: 700; font-size: 16px; letter-spacing: 2px; color: {css_color(acc)}; }}
.np-code {{ font-family: "{MONO}"; font-size: 10px; color: {css_color(muted)}; }}
.np-head {{ border-bottom: 1px solid {css_color(acc, 0.45)}; padding-bottom: 5px; }}
.np-sec {{ font-family: "{MONO}"; font-size: 10px; letter-spacing: 1px; color: {css_color(muted)}; margin-top: 4px; }}
.np-val {{ font-family: "{MONO}"; font-size: 12px; color: {css_color(text)}; min-width: 38px; }}
.np-val.off, .np-dim {{ color: {css_color(muted)}; }}
.np-dim {{ font-family: "{MONO}"; font-size: 11px; }}
.np-txt {{ font-family: "{SANS}"; font-weight: 600; font-size: 14px; color: {css_color(text)}; }}
.np-icon {{ all: unset; padding: 2px; border: 1px solid transparent; }}
.np-icon:hover {{ border-color: {css_color(acc, 0.6)}; background: {css_color(acc, 0.14)}; }}
.np-scale trough {{ min-height: 4px; background: {css_color(acc, 0.18)}; border-radius: 0; }}
.np-scale highlight {{ background: {css_color(acc)}; border-radius: 0; }}
.np-scale slider {{ min-width: 10px; min-height: 16px; margin: -7px 0; border-radius: 0;
                    background: {css_color(alt)}; border: none; box-shadow: 0 0 6px {css_color(alt, 0.7)}; }}
.np-scale.off highlight {{ background: {css_color(muted)}; }}
.np-btn {{ all: unset; padding: 5px 0; background: rgba(0, 0, 0, 0.55); border: 1px solid {css_color(acc, 0.6)};
          color: {css_color(text)}; font-family: "{SANS}"; font-weight: 700; font-size: 13px; letter-spacing: 1.5px; }}
.np-btn:hover {{ background: {css_color(acc)}; color: #0a0204; }}
.np-mini {{ all: unset; padding: 1px 8px; border: 1px solid {css_color(acc, 0.6)}; color: {css_color(text)};
           font-family: "{MONO}"; font-size: 10px; letter-spacing: 1px; background: rgba(0, 0, 0, 0.55); }}
.np-mini:hover, .np-mini.on {{ background: {css_color(alt)}; border-color: {css_color(alt)}; color: #001014; }}
.np-redes {{ border-left: 1px solid {css_color(acc, 0.45)}; margin-left: 8px; padding-left: 8px; }}
.np-net {{ all: unset; padding: 3px 6px; border-left: 2px solid transparent; }}
.np-net:hover {{ background: {css_color(acc, 0.14)}; border-left-color: {css_color(acc)}; }}
.np-net.current {{ border-left-color: {css_color(alt)}; }}
.np-net-name {{ font-family: "{SANS}"; font-weight: 600; font-size: 14px; color: {css_color(text)}; }}
.np-tag {{ font-family: "{MONO}"; font-size: 10px; color: {css_color(muted)}; }}
.np-tag.on {{ color: {css_color(alt)}; }}
.np-x {{ all: unset; color: {css_color(muted)}; padding: 0 4px; font-size: 13px; }}
.np-x:hover {{ color: {css_color(warn)}; }}
.np-x.armed {{ color: #000; background: {css_color(warn)}; font-family: "{MONO}"; font-size: 10px; }}
.np-msg {{ font-family: "{MONO}"; font-size: 11px; color: {css_color(muted)}; }}
.np-msg.warn {{ color: {css_color(warn)}; }}
.np-pass {{ background: rgba(0, 0, 0, 0.6); border: 1px solid {css_color(acc, 0.6)}; border-radius: 0;
           color: {css_color(text)}; font-family: "{MONO}"; min-height: 24px; }}
.np-aviso {{ padding: 2px 6px; border-left: 2px solid {css_color(acc, 0.5)}; }}
.np-aviso.oculta {{ border-left-color: {css_color(alt)}; }}
.np-aviso.critica {{ border-left-color: {css_color(warn)}; }}
.np-btn.armed {{ background: {css_color(warn)}; border-color: {css_color(warn)}; color: #000; }}
"""


class Icono(Gtk.DrawingArea):
    def __init__(self, panel: "PanelRapido", name: str, size: int = 18):
        super().__init__(content_width=size, content_height=size, valign=Gtk.Align.CENTER)
        self.panel, self.name = panel, name
        self.level: float | None = None
        self.off = False
        self.set_draw_func(lambda _a, cr, w, h: draw_icon(cr, self.name, w, h, self.panel.bar.theme["alt"],
                                                          self.level, self.off))

    def set(self, level=None, off=False):
        self.level, self.off = level, off
        self.queue_draw()


class Deslizador(Gtk.Box):
    """Icono (clic = silenciar, si se puede) + barra 0..100 + valor."""

    def __init__(self, panel: "PanelRapido", icon: str, on_set, on_mute=None, tooltip=""):
        super().__init__(spacing=8)
        self.panel, self.on_set = panel, on_set
        self.icon = Icono(panel, icon)
        btn = Gtk.Button(child=self.icon, tooltip_text=tooltip)
        btn.add_css_class("np-icon")
        if on_mute:
            btn.connect("clicked", lambda _b: (on_mute(), self.panel.refresh()))
        else:
            btn.set_sensitive(False)
        self.scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        self.scale.set_draw_value(False)
        self.scale.set_hexpand(True)
        self.scale.add_css_class("np-scale")
        self.scale.connect("value-changed", self._changed)
        self.val = Gtk.Label(xalign=1)
        self.val.add_css_class("np-val")
        self.append(btn)
        self.append(self.scale)
        self.append(self.val)
        self._busy = False  # refresco desde el sistema: no reenviar
        self._pending = 0

    def show_value(self, frac: float, off: bool = False):
        self._busy = True
        self.scale.set_value(round(frac * 100))
        self._busy = False
        self.val.set_text(f"{round(frac * 100)}%")
        for w in (self.scale, self.val):
            (w.add_css_class if off else w.remove_css_class)("off")
        self.icon.set(frac, off)

    def _changed(self, scale):
        v = scale.get_value()
        self.val.set_text(f"{round(v)}%")
        self.icon.set(v / 100, self.icon.off)
        if self._busy or self._pending:
            return
        # arrastrando llegan muchos cambios: mandar uno cada 60 ms
        def send():
            self._pending = 0
            self.on_set(self.scale.get_value() / 100)
            return GLib.SOURCE_REMOVE

        self._pending = GLib.timeout_add(60, send)


def _popen(cmd) -> None:
    if cmd:
        try:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            pass


def _label(text: str, cls: str, **kw) -> Gtk.Label:
    lb = Gtk.Label(label=text, **kw)
    lb.add_css_class(cls)
    return lb


class Selector(Gtk.Box):
    """Dispositivo de audio en uso; clic = lista para cambiarlo (wpctl set-default)."""

    def __init__(self, panel: "PanelRapido", salida: bool):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=2, margin_start=30)
        self.panel, self.salida = panel, salida
        self.items: list[dict] = []
        self.firma = None
        self.head = Gtk.Button()
        self.head.add_css_class("np-net")
        hb = Gtk.Box(spacing=8)
        self.actual = _label("", "np-dim", xalign=0, hexpand=True, ellipsize=3)
        self.flecha = _label("▾", "np-tag")
        hb.append(_label("SALIDA" if salida else "ENTRADA", "np-tag"))
        hb.append(self.actual)
        hb.append(self.flecha)
        self.head.set_child(hb)
        self.head.connect("clicked", lambda _b: self.toggle())
        self.lista = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        self.lista.add_css_class("np-redes")
        self.rev = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN, transition_duration=150)
        self.rev.set_child(self.lista)
        self.append(self.head)
        self.append(self.rev)

    def toggle(self):
        if len(self.items) > 1 or self.rev.get_reveal_child():
            self.rev.set_reveal_child(not self.rev.get_reveal_child())
            self.flecha.set_text("▴" if self.rev.get_reveal_child() else "▾")

    def cerrar(self):
        self.rev.set_reveal_child(False)
        self.flecha.set_text("▾")

    def update(self, items: list[dict]):
        self.items = items
        firma = tuple((d["id"], d["nombre"], d["default"], d["disponible"]) for d in items)
        if firma == self.firma:
            return
        self.firma = firma
        actual = next((d for d in items if d["default"]), None)
        self.actual.set_text(("BT · " if actual and actual["bt"] else "") + (actual["nombre"] if actual else "ninguna"))
        self.flecha.set_visible(len(items) > 1)
        self.set_visible(bool(items))
        while (c := self.lista.get_first_child()) is not None:
            self.lista.remove(c)
        for d in items:
            b = Gtk.Button()
            b.add_css_class("np-net")
            row = Gtk.Box(spacing=8)
            row.append(_label(d["nombre"], "np-net-name", xalign=0, hexpand=True, ellipsize=3))
            tag = _label("EN USO" if d["default"] else ("SIN CONECTAR" if not d["disponible"] else
                                                        ("BLUETOOTH" if d["bt"] else "")), "np-tag")
            if not d["disponible"] and not d["default"]:
                b.set_sensitive(False)
                b.set_tooltip_text("Conector vacío: enchufa algo para poder usarlo")
            if d["default"]:
                b.add_css_class("current")
                tag.add_css_class("on")
            row.append(tag)
            b.set_child(row)
            b.connect("clicked", lambda _b, d=d: self._elegir(d))
            self.lista.append(b)
        if len(items) <= 1:
            self.cerrar()

    def _elegir(self, d: dict):
        if not d["default"]:
            _popen(sistema.cmd_set_default(d["id"]))
        self.cerrar()
        GLib.timeout_add(300, lambda: (self.panel.refresh(), GLib.SOURCE_REMOVE)[1])


class PanelRapido(Gtk.Window):
    def __init__(self, bar):
        super().__init__(application=bar.get_application(), title="Neural Bar Panel")
        self.bar = bar
        self.set_decorated(False)
        setup_popup(self)
        self.add_css_class("neural-bar-panel")
        self.arm_ids: dict[str, int] = {}
        self.refresh_id = 0

        self.frame = Frame(bar, Gtk.Orientation.VERTICAL, 12, ("tl", "br"), True, "CTRL// 01")
        self.frame.set_halign(Gtk.Align.START)
        self.frame.set_valign(Gtk.Align.START)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_start=PAD + 14,
                       margin_end=PAD + 14, margin_top=PAD + 22, margin_bottom=PAD + 14)
        body.set_size_request(PANEL_W, -1)
        self.frame.append(body)

        head = Gtk.Box(spacing=8)
        head.add_css_class("np-head")
        head.append(_label("CONTROL RÁPIDO", "np-title", hexpand=True, xalign=0))
        self.uptime = _label("", "np-code")
        head.append(self.uptime)
        body.append(head)

        body.append(_label("AUDIO // LUZ", "np-sec", xalign=0))
        self.vol = Deslizador(self, "vol", lambda f: _popen(sistema.cmd_set_volume(sistema.SINK, f)),
                              lambda: _popen(sistema.cmd_toggle_mute(sistema.SINK)), "Silenciar el volumen")
        self.mic = Deslizador(self, "mic", lambda f: _popen(sistema.cmd_set_volume(sistema.SOURCE, f)),
                              lambda: _popen(sistema.cmd_toggle_mute(sistema.SOURCE)), "Silenciar el micrófono")
        self.brillo = Deslizador(self, "sol", lambda f: _popen(sistema.cmd_set_brillo(f)))
        self.sel_salida = Selector(self, True)
        self.sel_entrada = Selector(self, False)
        for w in (self.vol, self.sel_salida, self.mic, self.sel_entrada, self.brillo):
            body.append(w)

        body.append(_label("RED // ENERGÍA", "np-sec", xalign=0))
        self.wifi_icon, self.wifi_txt, self.wifi_dim = self._fila(body, "wifi")
        self.redes_btn = Gtk.Button(label="REDES", valign=Gtk.Align.CENTER, tooltip_text="Buscar y elegir red")
        self.redes_btn.add_css_class("np-mini")
        self.redes_btn.connect("clicked", lambda _b: self.toggle_redes())
        self.wifi_txt.get_parent().append(self.redes_btn)
        self.wifi_pow = Gtk.Button(label="ON", valign=Gtk.Align.CENTER, tooltip_text="Encender / apagar el WiFi")
        self.wifi_pow.add_css_class("np-mini")
        self.wifi_pow.connect("clicked", lambda _b: self._wifi_power())
        self.wifi_txt.get_parent().append(self.wifi_pow)
        self.redes_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.redes_box.add_css_class("np-redes")
        self.redes = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN, transition_duration=150)
        self.redes.set_child(self.redes_box)
        body.append(self.redes)
        self.vistas: list[dict] = []
        self.guardadas: dict[str, str] = {}  # SSID → UUID de NetworkManager
        self.buscando = False
        self.conectando = False
        self.msg = ("", False)
        self.pedir_clave: str | None = None  # SSID al que se le esta pidiendo la contraseña
        self.olvidar_armado: str | None = None
        self.bt_icon, self.bt_txt, self.bt_dim = self._fila(body, "bt")
        bt_row = self.bt_txt.get_parent()
        self.bt_pow = Gtk.Button(label="ON", valign=Gtk.Align.CENTER, tooltip_text="Encender / apagar el Bluetooth")
        self.bt_pow.add_css_class("np-mini")
        self.bt_pow.connect("clicked", lambda _b: self._bt_power())
        self.bt_btn = Gtk.Button(label="EQUIPOS", valign=Gtk.Align.CENTER, tooltip_text="Buscar y conectar dispositivos")
        self.bt_btn.add_css_class("np-mini")
        self.bt_btn.connect("clicked", lambda _b: self.toggle_bt())
        bt_row.append(self.bt_pow)
        bt_row.append(self.bt_btn)
        self.bt_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.bt_box.add_css_class("np-redes")
        self.bt_lista = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN, transition_duration=150)
        self.bt_lista.set_child(self.bt_box)
        body.append(self.bt_lista)
        self.bt: Bluetooth | None = None
        self.bt_equipos: list[dict] = []
        self.bt_firma = None  # para repintar la lista solo si cambia
        self.bt_msg = ("", False)
        self.bt_ocupado: str | None = None  # path del equipo con una accion en curso
        self.bt_olvidar: str | None = None
        self.bt_pregunta = None  # (texto, responder) de una confirmacion de emparejamiento
        self.bt_reintento = 0.0  # tras un fallo, no volver a preguntar a BlueZ hasta esta hora
        self.bat_icon, self.bat_txt, self.bat_dim = self._fila(body, "bat")
        # luz del teclado: OFF / BAJO / ALTO (Fn+Espacio tambien vale; la barra recuerda el nivel)
        self.kbd_icon, self.kbd_txt, self.kbd_dim = self._fila(body, "kbd")
        self.kbd_txt.set_text("Luz del teclado")
        self.kbd_btns: list[Gtk.Button] = []
        for n, texto in enumerate(("OFF", "BAJO", "ALTO")):
            b = Gtk.Button(label=texto, valign=Gtk.Align.CENTER)
            b.add_css_class("np-mini")
            b.connect("clicked", lambda _b, n=n: self._teclado(n))
            self.kbd_txt.get_parent().append(b)
            self.kbd_btns.append(b)
        self.kbd_dim.set_visible(False)

        body.append(_label("MOSAICO  ·  SUPER+0…4", "np-sec", xalign=0))
        mos = Gtk.Box(spacing=6, homogeneous=True)
        self.mos_btns: dict[int, Gtk.Button] = {}
        for m, texto, tip in ((0, "LIBRE", "Ventanas flotantes"), (1, "1", "Una ventana a pantalla completa"),
                              (2, "2", "Dos ventanas a mitades"), (3, "3", "Una grande y dos pequeñas"),
                              (4, "4", "Cuatro en cuadrícula")):
            b = Gtk.Button(label=texto, tooltip_text=tip)
            b.add_css_class("np-mini")
            b.connect("clicked", lambda _b, m=m: (mosaico.cambiar_modo(m), self._refresh_mosaico()))
            self.mos_btns[m] = b
            mos.append(b)
        body.append(mos)

        body.append(_label("FONDO", "np-sec", xalign=0))
        fo = Gtk.Box(spacing=10)
        self.fo_icon = Icono(self, "desk")
        self.fo_txt = _label("", "np-txt", xalign=0, hexpand=True)
        self.fo_btn = Gtk.Button(label="ANIMACIONES", valign=Gtk.Align.CENTER,
                                 tooltip_text="Hologramas del fondo en movimiento (apágalo cuando uses mucho la laptop)")
        self.fo_btn.add_css_class("np-mini")
        self.fo_btn.connect("clicked", lambda _b: self._toggle_animaciones())
        for w in (self.fo_icon, self.fo_txt, self.fo_btn):
            fo.append(w)
        body.append(fo)

        body.append(_label("AVISOS", "np-sec", xalign=0))
        av = Gtk.Box(spacing=10)
        self.av_icon = Icono(self, "bell")
        self.av_txt = _label("", "np-txt", xalign=0, hexpand=True)
        self.dnd_btn = Gtk.Button(label="NO MOLESTAR", valign=Gtk.Align.CENTER,
                                  tooltip_text="No mostrar avisos (se guardan aquí; los críticos sí salen)")
        self.dnd_btn.add_css_class("np-mini")
        self.dnd_btn.connect("clicked", lambda _b: self._toggle_dnd())
        self.av_borrar = Gtk.Button(label="BORRAR", valign=Gtk.Align.CENTER, tooltip_text="Vaciar la lista")
        self.av_borrar.add_css_class("np-mini")
        self.av_borrar.connect("clicked", lambda _b: (notif_set(borrado_hasta=time.time()), self._refresh_avisos()))
        for w in (self.av_icon, self.av_txt, self.dnd_btn, self.av_borrar):
            av.append(w)
        body.append(av)
        self.av_lista = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        body.append(self.av_lista)
        self.av_firma = None

        aj = Gtk.Button(label="AJUSTES", tooltip_text="NEURAL//AJUSTES: todo lo configurable")
        aj.add_css_class("np-btn")
        aj.set_margin_top(6)
        aj.connect("clicked", lambda _b: (self.close_panel(),
                                          _popen(["python3", str(Path(__file__).resolve().parent / "ajustes.py")])))
        body.append(aj)

        body.append(_label("SESIÓN", "np-sec", xalign=0))
        ses = Gtk.Box(spacing=6, homogeneous=True)
        self.ses_btns: dict[str, Gtk.Button] = {}
        for accion, texto, _ in SESION:
            b = Gtk.Button(label=texto)
            b.add_css_class("np-btn")
            b.connect("clicked", self._on_sesion, accion)
            self.ses_btns[accion] = b
            ses.append(b)
        body.append(ses)

        self.set_child(self.frame)

        click = Gtk.GestureClick()
        click.connect("pressed", self._on_click)
        self.add_controller(click)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", lambda _c, kv, *_: (self.close_panel(), True)[1] if kv == Gdk.KEY_Escape else False)
        self.add_controller(keys)
        self.connect("close-request", lambda *_: (self.close_panel(), True)[1])

    def _fila(self, body, icon):
        row = Gtk.Box(spacing=10)
        ic = Icono(self, icon)
        txt = _label("", "np-txt", xalign=0, hexpand=True, ellipsize=3)
        dim = _label("", "np-dim", xalign=1)
        for w in (ic, txt, dim):
            row.append(w)
        body.append(row)
        return ic, txt, dim

    # ---------- abrir / cerrar ----------
    def open_at(self, right_x: int, top_y: int):
        """El borde derecho del marco en right_x (el de la barra), debajo de ella."""
        self.frame.set_margin_start(max(0, right_x - (PANEL_W + 2 * (PAD + 14))))
        self.frame.set_margin_top(top_y)
        self.refresh()
        self.present()
        if not self.refresh_id:
            self.refresh_id = GLib.timeout_add_seconds(2, lambda: (self.refresh(), GLib.SOURCE_CONTINUE)[1])

    def close_panel(self):
        if self.refresh_id:
            GLib.source_remove(self.refresh_id)
            self.refresh_id = 0
        for accion in list(self.arm_ids):
            self._disarm(accion)
        self.set_visible(False)
        self._cerrar_redes()
        self._cerrar_bt()
        self.sel_salida.cerrar()
        self.sel_entrada.cerrar()
        self.bar.panel_closed()

    def _on_click(self, gesture, _n, x, y):
        w = self.pick(x, y, Gtk.PickFlags.DEFAULT)
        if w is None or not (w == self.frame or w.is_ancestor(self.frame)):
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            self.close_panel()

    # ---------- WiFi: lista de redes (NetworkManager, red.py) ----------
    def _hilo(self, trabajo, fin):
        """nmcli puede tardar segundos: fuera de GTK, y el resultado de vuelta en GTK."""
        def correr():
            try:
                res, err = trabajo(), None
            except (red.RedError, OSError) as e:
                res, err = None, e
            GLib.idle_add(lambda: (fin(res, err), GLib.SOURCE_REMOVE)[1])
        threading.Thread(target=correr, daemon=True).start()

    def toggle_redes(self):
        if self.redes.get_reveal_child():
            self._cerrar_redes()
            return
        self.redes_btn.add_css_class("on")
        self.redes.set_reveal_child(True)
        self.buscar(rapido=True)

    def _cerrar_redes(self):
        self.redes.set_reveal_child(False)
        self.redes_btn.remove_css_class("on")
        self.pedir_clave = None  # y con ello se borra lo escrito en la contraseña
        self.olvidar_armado = None
        self._pintar()

    def buscar(self, rapido: bool = False):
        """rapido = lo que NetworkManager ya conoce (al momento) y luego una búsqueda nueva."""
        if self.buscando:
            return
        self.buscando = True
        if not self.conectando:
            self.msg = ("Buscando redes…", False)
        self._pintar()

        def fin(res, err):
            self.buscando = False
            if err:
                self.msg = (f"WiFi: {red.texto_error(err)}", True)
            else:
                self.vistas, self.guardadas = res
                if not self.conectando and (not rapido or self.msg[0] == "Buscando redes…"):
                    self.msg = ("", False)
            self._pintar()
            if rapido and not err:
                self.buscar()

        self._hilo(lambda: (red.wifi_lista(not rapido), red.guardadas_wifi()), fin)

    def _pintar(self):
        box = self.redes_box
        while (c := box.get_first_child()) is not None:
            box.remove(c)
        if not self.redes.get_reveal_child():
            return
        head = Gtk.Box(spacing=6)
        msg = _label(self.msg[0] or f"{len(self.vistas)} redes", "np-msg", xalign=0, hexpand=True,
                     ellipsize=3, wrap=False)
        if self.msg[1]:
            msg.add_css_class("warn")
        again = Gtk.Button(label="↻", tooltip_text="Buscar otra vez")
        again.add_css_class("np-x")
        again.connect("clicked", lambda _b: self.buscar())
        head.append(msg)
        head.append(again)
        box.append(head)
        for r in self.vistas[:MAX_REDES]:
            box.append(self._fila_red(r, self.guardadas.get(r["ssid"])))
            if self.pedir_clave == r["ssid"]:
                box.append(self._fila_clave(r["ssid"]))

    def _fila_red(self, r: dict, uuid: str | None) -> Gtk.Widget:
        row = Gtk.Box(spacing=6)
        btn = Gtk.Button(hexpand=True)
        btn.add_css_class("np-net")
        inner = Gtk.Box(spacing=8)
        ic = Icono(self, "wifi", 16)
        ic.set(r["señal"] / 100)
        name = _label(r["ssid"], "np-net-name", xalign=0, hexpand=True, ellipsize=3)
        if r["en_uso"]:
            btn.add_css_class("current")
            tag = _label("CONECTADA", "np-tag")
            tag.add_css_class("on")
        else:
            tag = _label("GUARDADA" if uuid else ("🔒" if r["seguridad"] else "ABIERTA"), "np-tag")
        inner.append(ic)
        inner.append(name)
        inner.append(tag)
        inner.append(_label(f"{r['señal']}%", "np-tag"))
        btn.set_child(inner)
        btn.set_tooltip_text(f"{r['ssid']} · {r['señal']} % · {r['banda']} · canal {r['canal']} · "
                             f"{r['seguridad'] or 'abierta'}")
        btn.connect("clicked", lambda _b: self._elegir(r, uuid))
        row.append(btn)
        if r["en_uso"]:
            d = Gtk.Button(label="DESCONECTAR", valign=Gtk.Align.CENTER, tooltip_text="Desconectar de esta red")
            d.add_css_class("np-mini")
            d.connect("clicked", lambda _b: self._desconectar())
            row.append(d)
        if uuid:
            armed = self.olvidar_armado == r["ssid"]
            x = Gtk.Button(label="¿OLVIDAR?" if armed else "✕", valign=Gtk.Align.CENTER,
                           tooltip_text="Olvidar esta red")
            x.add_css_class("np-x")
            if armed:
                x.add_css_class("armed")
            x.connect("clicked", lambda _b: self._olvidar(r["ssid"], uuid))
            row.append(x)
        return row

    def _radio_on(self) -> bool | None:
        """WiFi encendido = sin bloqueo rfkill y con el interruptor de NetworkManager en ON."""
        rf = sistema.wifi_radio_on()
        if rf is None or rf is False:
            return rf
        try:
            return red.wifi_radio_on()
        except red.RedError:
            return rf

    def _wifi_power(self):
        encender = not self._radio_on()
        try:
            if encender:
                sistema.wifi_radio(True)  # quita el bloqueo rfkill (p. ej. modo avión)
            red.wifi_radio(encender)      # NetworkManager vuelve a conectar solo a la guardada
        except (OSError, red.RedError) as e:
            self.msg = (f"WiFi: {getattr(e, 'strerror', None) or e}", True)
        GLib.timeout_add(400, lambda: (self.refresh(), GLib.SOURCE_REMOVE)[1])

    def _desconectar(self):
        def fin(_r, err):
            self.msg = (f"WiFi: {red.texto_error(err)}", True) if err else \
                ("Desconectado (elige una red para volver)", False)
            self.refresh()
            self.buscar(rapido=True)

        self._hilo(lambda: red.desconectar(red.wifi_dev() or ""), fin)

    def _teclado(self, nivel: int):
        if sistema.set_teclado(nivel):
            sistema.guardar_teclado(nivel)
        GLib.timeout_add(150, lambda: (self._refresh_teclado(), GLib.SOURCE_REMOVE)[1])

    def _refresh_teclado(self):
        k = sistema.get_teclado()
        self.kbd_icon.get_parent().set_visible(k is not None)
        if k is None:
            return
        nivel, maximo = k
        self.kbd_icon.set(nivel / maximo if maximo else 0, off=nivel == 0)
        for n, b in enumerate(self.kbd_btns):
            b.set_visible(n <= maximo)
            (b.add_css_class if n == nivel else b.remove_css_class)("on")

    def _fila_clave(self, ssid: str) -> Gtk.Widget:
        row = Gtk.Box(spacing=6, margin_start=24)
        entry = Gtk.PasswordEntry(show_peek_icon=True, hexpand=True, placeholder_text="contraseña")
        entry.add_css_class("np-pass")
        ok = Gtk.Button(label="CONECTAR", valign=Gtk.Align.CENTER)
        ok.add_css_class("np-mini")
        go = lambda *_: self._conectar(ssid, entry.get_text())  # noqa: E731
        entry.connect("activate", go)
        ok.connect("clicked", go)
        row.append(entry)
        row.append(ok)
        GLib.idle_add(lambda: (entry.grab_focus(), GLib.SOURCE_REMOVE)[1])
        return row

    def _elegir(self, r: dict, uuid: str | None):
        self.olvidar_armado = None
        if r["en_uso"] or self.conectando:
            return
        if uuid or not r["seguridad"]:
            self._conectar(r["ssid"], None)
        else:
            self.pedir_clave = None if self.pedir_clave == r["ssid"] else r["ssid"]
            self._pintar()

    def _conectar(self, ssid: str, clave: str | None):
        """Guardada o abierta: clave=None. Nueva con contraseña: red.py solo la guarda si conecta."""
        if self.conectando or (clave is not None and not clave):
            return
        seguridad = next((r["seguridad"] for r in self.vistas if r["ssid"] == ssid), "")
        self.conectando = True
        self.pedir_clave = None
        self.msg = (f"Conectando a {ssid}…", False)
        self._pintar()

        def fin(_r, err):
            self.conectando = False
            if isinstance(err, red.NecesitaClave):
                self.msg = (f"{ssid}: escribe la contraseña", False)
                self.pedir_clave = ssid
            elif err:
                self.msg = (f"No conectó a {ssid}: {red.texto_error(err)}", True)
            else:
                self.msg = (f"Conectado a {ssid}", False)
            self.refresh()
            self.buscar(rapido=True)

        self._hilo(lambda: red.conectar_wifi(ssid, seguridad, clave), fin)

    def _olvidar(self, ssid: str, uuid: str):
        if self.olvidar_armado != ssid:
            self.olvidar_armado = ssid
            self._pintar()
            return
        self.olvidar_armado = None

        def fin(_r, err):
            self.msg = (f"WiFi: {red.texto_error(err)}", True) if err else (f"Olvidada: {ssid}", False)
            self.buscar(rapido=True)

        self._hilo(lambda: red.olvidar(uuid), fin)

    # ---------- avisos ----------
    def _refresh_mosaico(self):
        actual = mosaico.leer()["modo"]
        for m, b in self.mos_btns.items():
            (b.add_css_class if m == actual else b.remove_css_class)("on")

    # ---------- fondo: animaciones on/off ----------
    @staticmethod
    def _animaciones_on() -> bool:
        import energia
        return bool(energia.leer_todo().get("fondo", {}).get("animaciones", True))

    def _toggle_animaciones(self):
        import energia
        fondo = dict(energia.leer_todo().get("fondo", {}))
        fondo["animaciones"] = not self._animaciones_on()
        energia.guardar_seccion("fondo", fondo)  # app.py lo ve al momento
        self._refresh_fondo()

    def _refresh_fondo(self):
        on = self._animaciones_on()
        (self.fo_btn.add_css_class if on else self.fo_btn.remove_css_class)("on")
        self.fo_icon.set(off=not on)
        self.fo_txt.set_text("Hologramas en movimiento" if on else "Hologramas quietos (ahorro)")

    def _toggle_dnd(self):
        notif_set(dnd=not notif_state().get("dnd", False))
        self._refresh_avisos()
        self.bar._update()

    def _refresh_avisos(self):
        st = notif_state()
        dnd = bool(st.get("dnd"))
        hist = notif_hist()
        (self.dnd_btn.add_css_class if dnd else self.dnd_btn.remove_css_class)("on")
        self.av_icon.set(off=dnd)
        self.av_txt.set_text(f"{len(hist)} avisos" if hist else "Sin avisos")
        self.av_borrar.set_visible(bool(hist))
        firma = tuple(h.get("t") for h in hist[:MAX_AVISOS])
        if firma != self.av_firma:
            self.av_firma = firma
            while (c := self.av_lista.get_first_child()) is not None:
                self.av_lista.remove(c)
            for h in hist[:MAX_AVISOS]:
                row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
                row.add_css_class("np-aviso")
                if h.get("urgencia") == 2:
                    row.add_css_class("critica")
                elif h.get("oculta"):
                    row.add_css_class("oculta")
                top = Gtk.Box(spacing=6)
                top.append(_label(h.get("resumen", ""), "np-net-name", xalign=0, hexpand=True, ellipsize=3))
                hora = time.strftime("%H:%M", time.localtime(h.get("t", 0)))
                top.append(_label(f"{(h.get('app') or '').upper()[:12]} {hora}", "np-tag"))
                if h.get("desktop"):  # botón aparte: así los enlaces del texto no chocan con él
                    ab = Gtk.Button(label="ABRIR", valign=Gtk.Align.CENTER,
                                    tooltip_text="Abrir o traer al frente la app que mandó el aviso")
                    ab.add_css_class("np-mini")
                    ab.connect("clicked", lambda _b, d=h["desktop"]: self._aviso_abrir(d))
                    top.append(ab)
                row.append(top)
                if h.get("markup") or h.get("cuerpo"):
                    cuerpo = _label("", "np-dim", xalign=0, ellipsize=3)
                    if h.get("markup"):  # enlaces pulsables (solo web)
                        cuerpo.set_markup(notif_texto.markup(h["markup"].replace("\n", " ")))
                        cuerpo.connect("activate-link", self._aviso_enlace)
                    else:
                        cuerpo.set_text(h["cuerpo"].replace("\n", " "))
                    row.append(cuerpo)
                self.av_lista.append(row)
        # abrir el panel = los avisos guardados por no molestar ya estan vistos
        if hist and self.get_visible():
            notif_set(visto_hasta=time.time())

    def _aviso_enlace(self, _label_w, uri: str) -> bool:
        """Enlace del historial: solo web. NO dejar que GTK lo abra: su UriLauncher intenta
        exportar la ventana (xdg-foreign) y en una ventana layer-shell Wayfire corta la
        conexion (Error 71) y el panel se cae. Se abre con Gio, sin ventana padre."""
        if not notif_texto.es_web(uri):
            print(f"panel: enlace BLOQUEADO (no es web): {uri[:100]}", file=sys.stderr, flush=True)
            return True
        print(f"panel: abriendo enlace {uri[:100]}", file=sys.stderr, flush=True)
        notif_texto.abrir_web(uri)
        GLib.idle_add(lambda: (self.close_panel(), GLib.SOURCE_REMOVE)[1])
        return True

    def _aviso_abrir(self, desktop: str):
        self.close_panel()
        cortos = {desktop.lower(), desktop.lower().rsplit(".", 1)[-1]}  # org.mozilla.firefox -> firefox
        try:  # si ya tiene ventana, traerla al frente en vez de abrir otra
            ipc = WayfireIPC()
            for v in sorted(ipc.call("window-rules/list-views") or [], key=lambda v: -v.get("last-focus-timestamp", 0)):
                if v.get("role") == "toplevel" and (v.get("app-id") or "").lower() in cortos:
                    if v.get("minimized"):
                        ipc.call("wm-actions/set-minimized", {"view_id": v["id"], "state": False})
                    ipc.call("window-rules/focus-view", {"id": v["id"]})
                    return
        except Exception:  # noqa: BLE001 (sin IPC: abrirla)
            pass
        info = Gio.DesktopAppInfo.new(f"{desktop}.desktop")
        if info is not None:
            try:
                info.launch([], None)
            except GLib.Error:
                pass

    # ---------- Bluetooth ----------
    def _bt(self) -> Bluetooth:
        if self.bt is None:
            self.bt = Bluetooth(self._bt_codigo, self._bt_confirmar)
        return self.bt

    def _bt_codigo(self, texto: str):
        self.bt_msg = (texto, False)
        self.bt_firma = None
        self._pintar_bt()

    def _bt_confirmar(self, texto: str, responder):
        """bt.py pregunta SI/NO (codigo de emparejamiento): nunca se acepta solo."""
        self.bt_pregunta = (texto, responder)
        self.bt_firma = None
        self._pintar_bt()

    def _bt_responder(self, si: bool):
        if self.bt_pregunta is None:
            return
        _texto, responder = self.bt_pregunta
        self.bt_pregunta = None
        responder(si)
        self.bt_msg = ("Código aceptado, emparejando…" if si else "Emparejamiento rechazado", not si)
        self.bt_firma = None
        self._pintar_bt()

    def _refresh_bt(self):
        self.bt_txt.set_text("Bluetooth")
        if not sistema.bluez_instalado():
            self.bt_dim.set_text("falta BlueZ")
            self.bt_icon.set(off=True)
            for w in (self.bt_pow, self.bt_btn):
                w.set_visible(False)
            return
        if time.monotonic() < self.bt_reintento:  # fallo hace poco: no bloquear el panel otra vez
            return
        try:
            bt = self._bt()
            on = bt.encendido()
            self.bt_equipos = bt.dispositivos() if on else []
        except BtError as e:
            self.bt_reintento = time.monotonic() + 10
            self.bt = None
            self.bt_dim.set_text("sin servicio")
            self.bt_icon.set(off=True)
            self.bt_msg = (str(e), True)
            return
        self.bt_pow.set_label("ON" if on else "OFF")
        (self.bt_pow.add_css_class if on else self.bt_pow.remove_css_class)("on")
        self.bt_btn.set_sensitive(on)
        conectados = [d["nombre"] for d in self.bt_equipos if d["connected"]]
        self.bt_txt.set_text(", ".join(conectados) if conectados else "Bluetooth")
        self.bt_dim.set_text("" if conectados else ("encendido" if on else "apagado"))
        self.bt_icon.set(1.0 if conectados else 0.5, off=not on)
        if not on and self.bt_lista.get_reveal_child():
            self._cerrar_bt()
        self._pintar_bt()

    def _bt_power(self):
        try:
            bt = self._bt()
            bt.encender(not bt.encendido())
        except BtError as e:
            self.bt_msg = (str(e), True)
        GLib.timeout_add(400, lambda: (self._refresh_bt(), GLib.SOURCE_REMOVE)[1])

    def toggle_bt(self):
        if self.bt_lista.get_reveal_child():
            self._cerrar_bt()
            return
        self.bt_btn.add_css_class("on")
        self.bt_lista.set_reveal_child(True)
        try:
            self._bt().buscar(True)
            self.bt_msg = ("Buscando… (pon el otro en modo emparejar)", False)
        except BtError as e:
            self.bt_msg = (str(e), True)
        self.bt_firma = None
        self._refresh_bt()

    def _cerrar_bt(self):
        if self.bt_lista.get_reveal_child() and self.bt is not None:
            try:
                self.bt.buscar(False)
            except BtError:
                pass
        self.bt_lista.set_reveal_child(False)
        self.bt_btn.remove_css_class("on")
        self.bt_olvidar = None
        if self.bt_pregunta is not None:  # cerrar la lista = NO
            self._bt_responder(False)
        self.bt_firma = None
        self._pintar_bt()

    def _pintar_bt(self):
        box = self.bt_box
        if not self.bt_lista.get_reveal_child():
            while (c := box.get_first_child()) is not None:
                box.remove(c)
            return
        firma = (self.bt_msg, self.bt_ocupado, self.bt_olvidar, self.bt_pregunta and self.bt_pregunta[0],
                 tuple((d["path"], d["nombre"], d["paired"], d["connected"], d["bateria"])
                       for d in self.bt_equipos[:MAX_EQUIPOS]))
        if firma == self.bt_firma:  # nada cambio: no rehacer (asi no se pierden clics)
            return
        self.bt_firma = firma
        while (c := box.get_first_child()) is not None:
            box.remove(c)
        msg = _label(self.bt_msg[0] or f"{len(self.bt_equipos)} equipos", "np-msg", xalign=0, ellipsize=3)
        if self.bt_msg[1]:
            msg.add_css_class("warn")
        box.append(msg)
        if self.bt_pregunta is not None:
            q = _label(self.bt_pregunta[0], "np-msg", xalign=0, wrap=True)
            q.add_css_class("warn")
            box.append(q)
            fila = Gtk.Box(spacing=6, homogeneous=True)
            for texto, si in (("SÍ, COINCIDE", True), ("NO", False)):
                b = Gtk.Button(label=texto)
                b.add_css_class("np-btn")
                b.connect("clicked", lambda _b, si=si: self._bt_responder(si))
                fila.append(b)
            box.append(fila)
        for d in self.bt_equipos[:MAX_EQUIPOS]:
            box.append(self._fila_equipo(d))

    def _fila_equipo(self, d: dict) -> Gtk.Widget:
        row = Gtk.Box(spacing=6)
        btn = Gtk.Button(hexpand=True)
        btn.add_css_class("np-net")
        inner = Gtk.Box(spacing=8)
        ic = Icono(self, "bt", 16)
        ic.set(off=not d["paired"])
        name = _label(d["nombre"], "np-net-name", xalign=0, hexpand=True, ellipsize=3)
        if self.bt_ocupado == d["path"]:
            tag = _label("…", "np-tag")
        elif d["connected"]:
            btn.add_css_class("current")
            tag = _label("CONECTADO", "np-tag")
            tag.add_css_class("on")
        else:
            tag = _label("EMPAREJADO" if d["paired"] else (d["tipo"] or "NUEVO"), "np-tag")
        inner.append(ic)
        inner.append(name)
        if d["bateria"] is not None:
            inner.append(_label(f"{d['bateria']}%", "np-tag"))
        inner.append(tag)
        btn.set_child(inner)
        accion = "desconectar" if d["connected"] else ("conectar" if d["paired"] else "emparejar")
        btn.set_tooltip_text(f"{d['nombre']} · {d['addr']} · clic = {accion}")
        btn.connect("clicked", lambda _b: self._bt_elegir(d))
        row.append(btn)
        if d["paired"]:
            armed = self.bt_olvidar == d["path"]
            x = Gtk.Button(label="¿OLVIDAR?" if armed else "✕", valign=Gtk.Align.CENTER, tooltip_text="Olvidar")
            x.add_css_class("np-x")
            if armed:
                x.add_css_class("armed")
            x.connect("clicked", lambda _b: self._bt_olvidar_click(d))
            row.append(x)
        return row

    def _bt_elegir(self, d: dict):
        if self.bt_ocupado:
            return
        self.bt_olvidar = None
        self.bt_ocupado = d["path"]

        def done(err):
            self.bt_ocupado = None
            if err and any(k in err for k in ("Protocol not available", "profile-unavailable",
                                              "br-connection-profile")):
                err = "emparejado, pero no hay perfil para conectar (¿falta audio Bluetooth en PipeWire?)"
            self.bt_msg = (f"{d['nombre']}: {err}", True) if err else (f"{d['nombre']}: conectado" if not
                           d["connected"] else f"{d['nombre']}: desconectado", False)
            self.bt_firma = None
            self._refresh_bt()

        try:
            if d["connected"]:
                self.bt_msg = (f"Desconectando {d['nombre']}…", False)
                self._bt().desconectar(d["path"], done)
            elif d["paired"]:
                self.bt_msg = (f"Conectando {d['nombre']}…", False)
                self._bt().conectar(d["path"], done)
            else:
                self.bt_msg = (f"Emparejando {d['nombre']}…", False)
                self._bt().emparejar_y_conectar(d["path"], done)
        except BtError as e:
            done(str(e))
            return
        self.bt_firma = None
        self._pintar_bt()

    def _bt_olvidar_click(self, d: dict):
        if self.bt_olvidar != d["path"]:
            self.bt_olvidar = d["path"]
            self._pintar_bt()
            return
        self.bt_olvidar = None
        try:
            self._bt().olvidar(d["path"])
            self.bt_msg = (f"Olvidado: {d['nombre']}", False)
        except BtError as e:
            self.bt_msg = (str(e), True)
        self._refresh_bt()

    # ---------- sesion ----------
    def _on_sesion(self, b, accion):
        if accion in self.arm_ids:
            self._disarm(accion)
            self.close_panel()
            _popen(sistema.cmd_sesion(accion))
            return
        for other in list(self.arm_ids):
            self._disarm(other)
        b.add_css_class("armed")
        b.set_label(next(c for a, _, c in SESION if a == accion))
        self.arm_ids[accion] = GLib.timeout_add(ARM_MS, lambda: (self._disarm(accion), GLib.SOURCE_REMOVE)[1])

    def _disarm(self, accion):
        sid = self.arm_ids.pop(accion, 0)
        if sid:
            GLib.source_remove(sid)
        b = self.ses_btns[accion]
        b.remove_css_class("armed")
        b.set_label(next(t for a, t, _ in SESION if a == accion))

    # ---------- datos ----------
    def refresh(self):
        self.uptime.set_text(f"UP {sistema.uptime()}")
        for sl, dev in ((self.vol, sistema.SINK), (self.mic, sistema.SOURCE)):
            a = sistema.get_audio(dev)
            sl.set_visible(a is not None)
            if a and not sl._pending:
                sl.show_value(a[0], a[1])
        self.sel_salida.update(sistema.get_dispositivos(True))
        self.sel_entrada.update(sistema.get_dispositivos(False))
        b = sistema.get_brillo()
        self.brillo.set_visible(b is not None)
        if b is not None and not self.brillo._pending:
            self.brillo.show_value(b)

        radio = self._radio_on()
        self.wifi_pow.set_visible(radio is not None)
        self.wifi_pow.set_label("ON" if radio else "OFF")
        (self.wifi_pow.add_css_class if radio else self.wifi_pow.remove_css_class)("on")
        self.redes_btn.set_sensitive(bool(radio))
        w = sistema.get_wifi()
        if radio is False:
            self.wifi_txt.set_text("WiFi apagado")
            self.wifi_dim.set_text("")
            self.wifi_icon.set(0, True)
            if self.redes.get_reveal_child():
                self._cerrar_redes()
        elif w is None:
            self.wifi_txt.set_text("Sin tarjeta WiFi")
            self.wifi_dim.set_text("")
            self.wifi_icon.set(0, True)
        elif w["up"] and w["ssid"]:
            self.wifi_txt.set_text(w["ssid"])
            self.wifi_dim.set_text(w["ip"] or "sin IP")
            self.wifi_icon.set(1.0)
        else:
            self.wifi_txt.set_text("Desconectado")
            self.wifi_dim.set_text(w["iface"])
            self.wifi_icon.set(0, True)

        self._refresh_bt()
        self._refresh_teclado()
        self._refresh_avisos()
        self._refresh_mosaico()
        self._refresh_fondo()

        bat = sistema.get_bateria()
        if bat:
            estados = {"Charging": "cargando", "Discharging": "descargando", "Full": "llena",
                       "Not charging": "sin cargar"}
            self.bat_txt.set_text(f"Batería {bat['capacity']}%")
            estado = bat["status"]
            if estado == "Not charging" and bat["ac"] and bat["capacity"] >= 95:
                estado = "Full"  # el portatil para de cargar al llegar arriba
            self.bat_dim.set_text(estados.get(estado, estado.lower())
                                  + (" · cargador" if bat["ac"] else ""))
            self.bat_icon.set(bat["capacity"] / 100)
        else:
            self.bat_txt.set_text("Sin batería")
            self.bat_dim.set_text("")
            self.bat_icon.set(0, True)
