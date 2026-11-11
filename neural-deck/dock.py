#!/usr/bin/env python3
"""Dock NEURAL//DECK para Wayfire, nativo en GTK4 (sin WebKit).

Mismo estilo que el wallpaper: marco con esquinas cortadas y brillo,
pestaña TASK//, un segmento por ventana bajo cada icono, escuadras en las
esquinas de abajo. Todo se dibuja con cairo (ver Frame y AppTile) con los
colores de web/themes.js, y sigue al tema del wallpaper (board.json).

Es una capa TOP de wlr-layer-shell pegada abajo al centro (ver layer.py), del
tamaño justo de lo dibujado. La lista de ventanas y las acciones (activar,
minimizar, cerrar) van por la IPC de Wayfire (ver wayfire_ipc.py; plugins
ipc, ipc-rules y wm-actions). Esconderse = el contenido se desliza hacia
abajo (Gtk.Revealer) y la ventana queda en una franja de 2 px; al llevar el
mouse ahi vuelve.

Apps: primero las FIJADAS (dock-state.json "pinned", ids .desktop; sin
ventana = clic las abre) y detras las abiertas que no estan fijadas. Clic
derecho = ventanas + fijar / quitar / nueva ventana. El boton de rejilla (o
Super+D = SIGUSR1 a neural-dock) abre NEURAL//APPS (lanzador.py); Super sola = SIGWINCH
abre NEURAL//CINTA (cinta.py)."""
from __future__ import annotations

import ctypes
import json
import signal
import sys

from layer import ensure_preload, setup_dock, SeguirTty
from senales import segura

ensure_preload()  # antes de cargar Gtk (ver layer.py)

import cairo  # noqa: E402
import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Graphene", "1.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gdk, Gio, GLib, GLibUnix, Graphene, Gtk, Pango  # noqa: E402

from board import CONFIG_DIR, load_board  # noqa: E402
from estilo import (MONO, PAD, SANS, Frame, css_color, draw_icon, load_themes, register_fonts,  # noqa: E402
                    shape, snapshot_children, source)
from wayfire_ipc import WayfireIPC  # noqa: E402
from lanzador import Lanzador, lanzador_css, lanzar  # noqa: E402
from cinta import VistaCinta, cinta_css  # noqa: E402

APP_ID = "org.local.NeuralDock"
OWN_APP_IDS = (APP_ID, "org.local.LiveWallpaper", "live-wallpaper", "neural-dock")
STATE_PATH = CONFIG_DIR / "dock-state.json"
STRIP_H = 2  # alto de la franja que queda al esconderse
HIDE_AFTER_MS = 650  # tiempo con el mouse fuera del dock antes de esconderlo
TILE_W, TILE_H, ICON = 58, 62, 40
WINLIST_W = 380
MAX_SEGMENTS = 4
CLOSE_SZ = 15  # cuadrito de la ✕ (esquina de arriba a la derecha de cada icono)
# eventos de Wayfire que cambian lo que muestra el dock (los de geometria no)
# fijadas la primera vez (las que esten instaladas)
PIN_SEED = ("org.mozilla.firefox.desktop", "foot.desktop", "org.codeberg.dnkl.foot.desktop")
REFRESH_EVENTS = {"view-mapped", "view-unmapped", "view-focused", "view-title-changed",
                  "view-app-id-changed", "view-minimized", "view-fullscreen"}


# ---------- CSS del dock ----------
def _theme_css(t: dict) -> str:
    acc, alt, warn, text, muted = t["accent"], t["alt"], t["warn"], t["text"], t["muted"]
    return f"""
window.neural-dock {{ background: transparent; }}
.nd-tip {{ background: {css_color(acc)}; color: #0a0204; font-family: "{SANS}"; font-weight: 700;
          font-size: 14px; letter-spacing: 1.5px; padding: 3px 12px; }}
.nd-letter {{ border: 1px solid {css_color(acc)}; color: {css_color(acc)}; font-family: "{SANS}";
             font-weight: 700; font-size: 20px; min-width: 38px; min-height: 38px; }}
.nd-msg {{ font-family: "{MONO}"; font-size: 11px; color: {css_color(muted)}; letter-spacing: 1px; padding: 0 12px; }}
.nd-msg.warn {{ color: {css_color(warn)}; }}
.nd-tools {{ border-left: 1px solid {css_color(acc, 0.35)}; padding-left: 8px; margin-left: 8px; }}
.nd-dtool {{ all: unset; min-width: 22px; min-height: 18px; background: rgba(0, 0, 0, 0.55);
            border: 1px solid {css_color(acc, 0.6)}; color: {css_color(text)}; font-size: 11px; }}
.nd-dtool:hover {{ background: {css_color(acc)}; color: #0a0204; }}
.nd-dtool.on {{ background: {css_color(alt)}; border-color: {css_color(alt)}; color: #001014; }}
.nd-dtool.armed {{ background: {css_color(warn)}; border-color: {css_color(warn)}; color: #000;
                  font-weight: 700; font-size: 10px; padding: 0 5px; }}
.nd-wl-head {{ border-bottom: 1px solid {css_color(acc, 0.45)}; padding: 0 4px 5px; }}
.nd-wl-code {{ font-family: "{MONO}"; font-size: 10px; color: {css_color(muted)}; }}
.nd-wl-title {{ font-family: "{SANS}"; font-weight: 700; font-size: 15px; letter-spacing: 2px; color: {css_color(acc)}; }}
.nd-wl-item {{ padding: 4px 6px; border-left: 2px solid transparent; color: {css_color(text)};
              font-family: "{SANS}"; font-weight: 600; font-size: 14px; }}
.nd-wl-item:hover {{ background: {css_color(acc, 0.14)}; border-left-color: {css_color(acc)}; }}
.nd-wl-item.focused {{ border-left-color: {css_color(alt)}; }}
.nd-tag {{ font-family: "{MONO}"; font-size: 10px; color: {css_color(muted)}; }}
.nd-x {{ all: unset; color: {css_color(muted)}; padding: 0 4px; font-size: 15px; }}
.nd-x:hover {{ color: {css_color(warn)}; }}
.nd-gridbtn {{ all: unset; padding: 0 6px; border-right: 1px solid {css_color(acc, 0.35)}; }}
.nd-gridbtn:hover {{ background: {css_color(acc, 0.12)}; }}
.nd-wl-acts {{ border-top: 1px solid {css_color(acc, 0.3)}; padding-top: 4px; margin-top: 2px; }}
.nd-wl-act {{ all: unset; padding: 3px 6px; color: {css_color(muted)}; font-family: "{MONO}"; font-size: 11px;
             letter-spacing: 1px; }}
.nd-wl-act:hover {{ color: {css_color(alt)}; background: {css_color(acc, 0.12)}; }}
"""



class AppTile(Gtk.Box):
    """Una app abierta: icono, fondo al pasar / enfocada, y un segmento por ventana."""

    def __init__(self, dock: "DockWindow", app: dict):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.dock = dock
        self.set_size_request(TILE_W, TILE_H)
        self.hover = self.flash = self.over_x = False
        self.app: dict = {}
        self.icon: Gtk.Widget | None = None

        motion = Gtk.EventControllerMotion()
        motion.connect("enter", lambda *_: self._set_hover(True))
        motion.connect("leave", lambda *_: self._set_hover(False))
        motion.connect("motion", self._on_motion)
        self.add_controller(motion)
        click = Gtk.GestureClick(button=0)
        click.connect("released", self._on_click)
        self.add_controller(click)
        self.update(app)

    def update(self, app: dict) -> None:
        if self.icon is None or app.get("gicon") != self.app.get("gicon"):
            if self.icon is not None:
                self.remove(self.icon)
            if app.get("gicon") is not None:
                self.icon = Gtk.Image.new_from_gicon(app["gicon"])
                self.icon.set_pixel_size(ICON)
            else:
                self.icon = Gtk.Label(label=(app["name"] or "?")[:1].upper())
                self.icon.add_css_class("nd-letter")
            self.icon.set_halign(Gtk.Align.CENTER)
            self.icon.set_margin_top(7)
            self.append(self.icon)
        self.app = app
        self.icon.set_opacity(0.45 if self.minimized else 1.0)
        self.queue_draw()

    @property
    def focused(self) -> bool:
        return any(w["focused"] and not w["minimized"] for w in self.app["windows"])

    @property
    def minimized(self) -> bool:
        wins = self.app["windows"]
        return bool(wins) and all(w["minimized"] for w in wins)

    def _set_hover(self, on: bool) -> None:
        self.hover = on
        self.queue_draw()
        if on:
            self.dock.show_tip(self)
        else:
            self.over_x = False
            self.dock.hide_tip()

    def _en_x(self, x, y) -> bool:
        """El puntero esta sobre la ✕? (solo existe si la app tiene ventanas)"""
        return bool(self.app.get("windows")) and x >= self.get_width() - CLOSE_SZ - 2 and y <= CLOSE_SZ + 2

    def _on_motion(self, _ctrl, x, y):
        over = self._en_x(x, y)
        if over != self.over_x:
            self.over_x = over
            self.queue_draw()
            if self.hover:
                self.dock.show_tip(self)  # "CERRAR ..." encima

    def _on_click(self, gesture, _n, x, y):
        if gesture.get_current_button() == 1 and self._en_x(x, y):
            self.dock.close_app(self)
            return
        if gesture.get_current_button() == 3:
            self.dock.open_winlist(self)
        else:
            self.dock.tile_clicked(self)

    def blink(self) -> None:
        self.flash = True
        self.queue_draw()
        GLib.timeout_add(350, lambda: (setattr(self, "flash", False), self.queue_draw(), GLib.SOURCE_REMOVE)[2])

    def do_snapshot(self, snapshot):
        t = self.dock.theme
        w, h = self.get_width(), self.get_height()
        cr = snapshot.append_cairo(Graphene.Rect().init(0, 0, w, h))
        focused = self.focused
        bg = (t["alt"], 0.9) if self.flash else (t["accent"], 0.16) if focused else (t["accent"], 0.12) if self.hover else None
        if bg:
            shape(cr, 0, 0, w, h, 9, ("tr",))
            source(cr, *bg)
            cr.fill()
        if focused:
            source(cr, t["alt"])
            cr.rectangle(0, 0, 2, h)
            cr.fill()
        n = min(len(self.app["windows"]), MAX_SEGMENTS)
        x0, y0 = (w - (n * 10 + (n - 1) * 3)) / 2, h - 9
        color, alpha = (t["alt"], 1) if focused else (t["accent"], 0.45) if self.minimized else (t["accent"], 1)
        for i in range(n):
            if focused:  # brillo del segmento activo
                source(cr, color, 0.3)
                cr.rectangle(x0 + i * 13 - 2, y0 - 2, 14, 7)
                cr.fill()
            source(cr, color, alpha)
            cr.rectangle(x0 + i * 13, y0, 10, 3)
            cr.fill()
        if self.hover and self.app["windows"]:  # ✕ para cerrar la app
            x0, y0, s = w - CLOSE_SZ - 1, 1, CLOSE_SZ
            shape(cr, x0, y0, s, s, 4, ("bl",))
            if self.over_x:
                source(cr, t["warn"])
                cr.fill()
                cr.set_source_rgb(0, 0, 0)
            else:
                cr.set_source_rgba(0, 0, 0, 0.75)
                cr.fill_preserve()
                source(cr, t["warn"], 0.9)
                cr.set_line_width(1)
                cr.stroke()
                source(cr, t["warn"])
            cr.set_line_width(1.6)
            m = 4.5
            cr.move_to(x0 + m, y0 + m)
            cr.line_to(x0 + s - m, y0 + s - m)
            cr.move_to(x0 + s - m, y0 + m)
            cr.line_to(x0 + m, y0 + s - m)
            cr.stroke()
        del cr
        snapshot_children(self, snapshot)


def _load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return {"mode": "autohide"}


def _save_state(state: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state))


def _desktop_info(app_id: str) -> Gio.DesktopAppInfo | None:
    """El .desktop de un app-id de Wayland (foot, org.mozilla.firefox...)."""
    for cand in (app_id, app_id.lower()):
        try:
            info = Gio.DesktopAppInfo.new(f"{cand}.desktop")
        except TypeError:
            info = None
        if info is not None:
            return info
    for info in Gio.AppInfo.get_all():
        if isinstance(info, Gio.DesktopAppInfo) and (info.get_startup_wm_class() or "").lower() == app_id.lower():
            return info
    return None


def _tool(label: str, tooltip: str, handler) -> Gtk.Button:
    b = Gtk.Button(label=label, tooltip_text=tooltip)
    b.add_css_class("nd-dtool")
    b.connect("clicked", handler)
    return b


class DockWindow(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application):
        super().__init__(application=app, title="Neural Dock")
        self.set_decorated(False)
        self.set_resizable(False)
        setup_dock(self)
        self._tty = SeguirTty(self, "dock")
        self.add_css_class("neural-dock")

        self.themes = load_themes()
        self.theme = next(iter(self.themes.values()))
        self.css = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), self.css, Gtk.STYLE_PROVIDER_PRIORITY_USER + 1)

        self.state = _load_state()
        self.shown = True
        self.hide_id = 0
        self.refresh_id = 0
        self.arm_id = 0
        self.app_cache: dict[str, dict] = {}
        self.views: dict[int, dict] = {}
        self.order: list[str] = []
        self.apps: list[dict] = []
        self.tiles: dict[str, AppTile] = {}
        self.winlist_app: str | None = None
        self.ipc: WayfireIPC | None = None

        # arriba: nombre de la app o lista de ventanas; abajo: el dock
        self.tip = Gtk.Label(use_markup=True, halign=Gtk.Align.START, visible=False)
        self.tip.add_css_class("nd-tip")
        self.winlist = Frame(self, Gtk.Orientation.VERTICAL, 12, ("tr", "bl"), False)
        self.winlist.set_size_request(WINLIST_W + 2 * PAD, -1)
        self.winlist.set_halign(Gtk.Align.START)
        self.winlist.set_visible(False)
        self.top = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.top.append(self.tip)
        self.top.append(self.winlist)

        self.frame = Frame(self, Gtk.Orientation.HORIZONTAL, 14, ("tl", "tr"), True, "TASK// 00")
        self.frame.set_halign(Gtk.Align.CENTER)
        self.frame.set_size_request(-1, 86 + 2 * PAD)
        inner = self.frame  # margenes = PAD + el padding del dock original
        self.grid_btn = Gtk.Button(tooltip_text="Todas las apps (Super+D)", valign=Gtk.Align.CENTER,
                                   margin_start=PAD + 12, margin_top=PAD + 14, margin_bottom=PAD + 6)
        self.grid_btn.add_css_class("nd-gridbtn")
        grid_icon = Gtk.DrawingArea(content_width=34, content_height=TILE_H - 8)
        grid_icon.set_draw_func(lambda _a, cr, w, h: draw_icon(cr, "grid", w, min(h, 26), self.theme["alt"]))
        self.grid_btn.set_child(grid_icon)
        self.grid_btn.connect("clicked", lambda _b: self.toggle_apps())
        # ver escritorio (Super+H): minimiza todo y con el mismo clic lo devuelve
        self.escondidas: list[int] = []
        self.desk_btn = Gtk.Button(tooltip_text="Ver escritorio / volver (Super+H)", valign=Gtk.Align.CENTER,
                                   margin_start=4, margin_top=PAD + 14, margin_bottom=PAD + 6)
        self.desk_btn.add_css_class("nd-gridbtn")
        desk_icon = Gtk.DrawingArea(content_width=34, content_height=TILE_H - 8)
        desk_icon.set_draw_func(lambda _a, cr, w, h: draw_icon(
            cr, "desk", w, min(h, 26), self.theme["alt"], level=1.0 if self._hay_escondidas() else 0.0))
        self.desk_btn.set_child(desk_icon)
        self.desk_btn.connect("clicked", lambda _b: self.toggle_escritorio())
        self.lanzador: Lanzador | None = None
        self.cinta: VistaCinta | None = None
        self.pin_cache: dict[str, dict | None] = {}
        self.apps_box = Gtk.Box(spacing=4, valign=Gtk.Align.CENTER, margin_start=6, margin_top=PAD + 14,
                                margin_bottom=PAD + 6)
        self.msg = Gtk.Label(visible=False, valign=Gtk.Align.CENTER, margin_top=PAD + 8)
        self.msg.add_css_class("nd-msg")
        self.pin = _tool("", "", self._on_pin)
        self.hide_btn = _tool("▾", "Esconder ahora (vuelve al llevar el mouse abajo al centro)", self._on_hide)
        self.quit_btn = _tool("✕", "Cerrar este dock", self._on_quit)
        tools = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3, valign=Gtk.Align.CENTER,
                        margin_top=PAD + 8, margin_end=PAD + 10)
        tools.add_css_class("nd-tools")
        for b in (self.pin, self.hide_btn, self.quit_btn):
            tools.append(b)
        inner.append(self.grid_btn)
        inner.append(self.desk_btn)
        inner.append(self.apps_box)
        inner.append(self.msg)
        inner.append(tools)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.append(self.top)
        content.append(self.frame)
        self.revealer = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_UP,
                                     transition_duration=180, reveal_child=True)
        self.revealer.set_child(content)
        strip = Gtk.Box()
        strip.set_size_request(-1, STRIP_H)
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.append(self.revealer)
        outer.append(strip)
        self.set_child(outer)

        # el mouse entra / sale de la ventana (dock, lista o franja)
        motion = Gtk.EventControllerMotion()
        motion.connect("notify::contains-pointer", self._on_pointer)
        self.add_controller(motion)

        # el tema sigue al del wallpaper (board.json)
        self.monitor = Gio.File.new_for_path(str(CONFIG_DIR / "board.json")).monitor_file(Gio.FileMonitorFlags.NONE, None)
        self.monitor.connect("changed", lambda *_: self._apply_theme())
        self._apply_theme()
        self._sync_pin()
        self._render()
        GLib.idle_add(self._connect_ipc)
        self._schedule_hide()  # si el mouse no esta encima, se esconde como siempre

    # ---------- tema ----------
    def _apply_theme(self):
        self.theme = self.themes.get(load_board().get("theme", "red"), self.theme)
        self.css.load_from_string(_theme_css(self.theme) + lanzador_css(self.theme) + cinta_css(self.theme))
        for w in (self.frame, self.winlist, *self.tiles.values(), self.grid_btn.get_child(),
                  self.desk_btn.get_child()):
            w.queue_draw()

    # ---------- IPC de Wayfire ----------
    def _connect_ipc(self):
        try:
            ipc = WayfireIPC()
            ipc.call("window-rules/list-views")  # falla aqui si falta ipc-rules
            ipc.watch(self._on_event)
        except (OSError, RuntimeError, ValueError) as e:
            print("neural-dock: sin IPC de Wayfire:", e, file=sys.stderr)
            self._render()
            return GLib.SOURCE_REMOVE
        self.ipc = ipc
        self._refresh()
        return GLib.SOURCE_REMOVE

    def _reconectar(self):
        """Reintenta cada segundo hasta que la IPC vuelva."""
        self._connect_ipc()
        return GLib.SOURCE_REMOVE if self.ipc else GLib.SOURCE_CONTINUE

    def _ipc(self, method: str, data: dict | None = None):
        if not self.ipc:
            return None
        try:
            return self.ipc.call(method, data)
        except (OSError, RuntimeError, ValueError) as e:
            print("neural-dock:", e, file=sys.stderr)
            return None

    def _on_event(self, msg):
        if msg is None:
            # se cortó la IPC (Wayfire recargó plugins/config): sin esto el dock
            # seguía pintando las ventanas pero sus clics ya no hacían nada
            print("neural-dock: se cortó la IPC de Wayfire; reconectando", file=sys.stderr)
            self.ipc = None
            self._render()
            GLib.timeout_add(1000, self._reconectar)
            return
        # agrupado: muchas señales seguidas = un solo refresco
        if msg.get("event") in REFRESH_EVENTS and not self.refresh_id:
            self.refresh_id = GLib.timeout_add(80, self._refresh)

    def _is_listed(self, v: dict) -> bool:
        return v.get("role") == "toplevel" and v.get("mapped", True) and v.get("app-id") not in OWN_APP_IDS

    def _app_of(self, v: dict) -> dict:
        app_id = v.get("app-id") or ""
        if not app_id:
            return {"id": f"window:{v['id']}", "name": v.get("title") or "?", "gicon": None}
        if app_id not in self.app_cache:
            info = _desktop_info(app_id)
            if info is None:
                self.app_cache[app_id] = {"id": f"app:{app_id}", "name": app_id, "gicon": None}
            else:
                self.app_cache[app_id] = {"id": info.get_id(), "name": info.get_display_name() or app_id,
                                          "gicon": info.get_icon()}
        return self.app_cache[app_id]

    def _refresh(self):
        self.refresh_id = 0
        views = self._ipc("window-rules/list-views")
        if views is None:
            return GLib.SOURCE_REMOVE
        listed = [v for v in views if self._is_listed(v)]
        self.views = {v["id"]: v for v in listed}
        apps: dict[str, dict] = {}
        for v in sorted(listed, key=lambda v: -v.get("last-focus-timestamp", 0)):
            a = self._app_of(v)
            apps.setdefault(a["id"], {**a, "windows": []})["windows"].append({
                "id": v["id"],
                "title": v.get("title") or a["name"],
                "minimized": bool(v.get("minimized")),
                "focused": bool(v.get("activated")),
            })
        fijadas = []
        for pid in self.pinned():
            if pid in apps:
                fijadas.append(apps.pop(pid))
            else:
                info = self._pin_info(pid)
                if info is not None:
                    fijadas.append({**info, "windows": []})
        # orden estable: las apps nuevas van al final
        for app_id in apps:
            if app_id not in self.order:
                self.order.append(app_id)
        self.order = [i for i in self.order if i in apps]
        self.apps = fijadas + [apps[i] for i in self.order]
        self._render()
        self.desk_btn.get_child().queue_draw()
        return GLib.SOURCE_REMOVE

    # ---------- dibujar las apps ----------
    def _render(self):
        ids = [a["id"] for a in self.apps]
        for app_id in [i for i in self.tiles if i not in ids]:
            self.apps_box.remove(self.tiles.pop(app_id))
        prev = None
        for a in self.apps:
            tile = self.tiles.get(a["id"])
            if tile is None:
                tile = self.tiles[a["id"]] = AppTile(self, a)
                self.apps_box.insert_child_after(tile, prev)
            else:
                tile.update(a)
                self.apps_box.reorder_child_after(tile, prev)
            prev = tile
        self.frame.tab = f"TASK// {sum(1 for a in self.apps if a['windows']):02d}"
        if self.ipc is None:
            self.msg.set_text("SIN IPC DE WAYFIRE · AÑADE ipc ipc-rules A plugins")
            self.msg.add_css_class("warn")
        else:
            self.msg.set_text("SIN APPS ABIERTAS")
            self.msg.remove_css_class("warn")
        self.msg.set_visible(not self.apps)
        self.frame.queue_draw()
        if self.winlist_app:
            tile = self.tiles.get(self.winlist_app)
            if tile:
                self.open_winlist(tile)
            else:
                self.close_winlist()

    # ---------- fijadas ----------
    def pinned(self) -> list[str]:
        if "pinned" not in self.state:
            self.state["pinned"] = []
            for pid in PIN_SEED:
                info = self._pin_info(pid)
                # foot puede venir con dos nombres: solo uno
                if info is not None and not (pid.endswith("foot.desktop") and
                                             any(p.endswith("foot.desktop") for p in self.state["pinned"])):
                    self.state["pinned"].append(pid)
            _save_state(self.state)
        return list(self.state["pinned"])

    def _pin_info(self, pid: str) -> dict | None:
        if pid not in self.pin_cache:
            try:
                info = Gio.DesktopAppInfo.new(pid)
            except TypeError:
                info = None
            self.pin_cache[pid] = None if info is None else {
                "id": info.get_id(), "name": info.get_display_name() or pid, "gicon": info.get_icon()}
        return self.pin_cache[pid]

    def is_pinned(self, app_id: str) -> bool:
        return app_id in self.pinned()

    def toggle_pin(self, app_id: str):
        pins = self.pinned()
        if app_id in pins:
            pins.remove(app_id)
        elif app_id.endswith(".desktop"):
            pins.append(app_id)
        self.state["pinned"] = pins
        _save_state(self.state)
        self.pin_cache.clear()  # por si se desinstalo / reinstalo algo
        self._refresh()

    def _lanzar(self, app_id: str) -> bool:
        try:
            info = Gio.DesktopAppInfo.new(app_id)
        except TypeError:
            info = None
        return info is not None and lanzar(info)

    # ---------- todas las apps (NEURAL//APPS) ----------
    def toggle_apps(self):
        if self.lanzador is None:
            self.lanzador = Lanzador(self)
        self.hide_tip()
        self.close_winlist()
        self.lanzador.toggle()

    # ---------- ver escritorio (Super+H) ----------
    def _hay_escondidas(self) -> bool:
        """Quedan ventanas que escondimos nosotros y siguen minimizadas."""
        return any(v.get("minimized") for i in self.escondidas if (v := self.views.get(i)))

    def toggle_escritorio(self):
        self.hide_tip()
        self.close_winlist()
        views = self._ipc("window-rules/list-views")
        if views is None:
            return
        listed = {v["id"]: v for v in views if self._is_listed(v)}
        pendientes = [i for i in self.escondidas if listed.get(i, {}).get("minimized")]
        if pendientes:
            # volver: en el orden en que estaban; la que tenia el foco, la ultima (queda arriba)
            for vid in pendientes:
                self._ipc("wm-actions/set-minimized", {"view_id": vid, "state": False})
            self._ipc("window-rules/focus-view", {"id": pendientes[-1]})
            self.escondidas = []
        else:
            visibles = sorted((v for v in listed.values() if not v.get("minimized")),
                              key=lambda v: v.get("last-focus-timestamp", 0))
            self.escondidas = [v["id"] for v in visibles]
            for vid in self.escondidas:
                self._ipc("wm-actions/set-minimized", {"view_id": vid, "state": True})
        if not self.refresh_id:
            self.refresh_id = GLib.timeout_add(80, self._refresh)

    # ---------- vista de ventanas (NEURAL//CINTA, Super) ----------
    def toggle_cinta(self):
        if self.lanzador is not None and self.lanzador.get_visible():
            self.lanzador.cerrar()
        if self.cinta is None:
            self.cinta = VistaCinta(self, _desktop_info)
        self.hide_tip()
        self.close_winlist()
        self.cinta.toggle()

    # ---------- acciones ----------
    def tile_clicked(self, tile: AppTile):
        self.hide_tip()
        if not tile.app["windows"]:  # fijada sin abrir: abrirla
            self.close_winlist()
            tile.blink()
            self._lanzar(tile.app["id"])
            return
        if len(tile.app["windows"]) > 1 and tile.focused:
            self.open_winlist(tile)
            return
        self.close_winlist()
        tile.blink()
        self._click_app(tile.app)

    def close_app(self, tile: AppTile):
        """✕ del icono: cerrar todas las ventanas de la app (como su propia ✕:
        la app puede preguntar, p. ej. Brave con varias pestañas).
        Antes se des-minimiza y enfoca: si estaba minimizada, la pregunta
        quedaba escondida y la app parecia colgada (ni cerraba ni abria)."""
        self.hide_tip()
        self.close_winlist()
        for w in tile.app["windows"]:
            self._cerrar_ventana(w["id"])

    def _cerrar_ventana(self, view_id: int):
        self._activate(view_id)
        self._ipc("window-rules/close-view", {"id": view_id})

    def _click_app(self, app: dict):
        wins = [self.views[w["id"]] for w in app["windows"] if w["id"] in self.views]
        if not wins:
            return
        if any(w.get("activated") and not w.get("minimized") for w in wins):
            for w in wins:
                self._ipc("wm-actions/set-minimized", {"view_id": w["id"], "state": True})
        else:
            target = next((w for w in wins if not w.get("minimized")), wins[0])
            self._activate(target["id"])

    def _activate(self, view_id: int):
        v = self.views.get(view_id)
        if v and v.get("minimized"):
            self._ipc("wm-actions/set-minimized", {"view_id": view_id, "state": False})
        self._ipc("window-rules/focus-view", {"id": view_id})

    # ---------- nombre al pasar el mouse / lista de ventanas ----------
    def _place_above(self, widget: Gtk.Widget, tile: AppTile):
        """Centra `widget` (dentro de self.top) encima de `tile`."""
        def place():
            ok, pt = tile.compute_point(self.top, Graphene.Point().init(tile.get_width() / 2, 0))
            if not ok:
                return GLib.SOURCE_REMOVE
            nat = widget.measure(Gtk.Orientation.HORIZONTAL, -1)[1]
            limit = max(0, self.top.get_width() - nat)
            widget.set_margin_start(int(min(max(0, pt.x - nat / 2), limit)))
            return GLib.SOURCE_REMOVE
        place()
        GLib.timeout_add(40, place)  # otra vez cuando la ventana ya tomo su ancho nuevo

    def show_tip(self, tile: AppTile):
        if self.winlist.get_visible():
            return
        n = len(tile.app["windows"])
        name = GLib.markup_escape_text(tile.app["name"].upper())
        if tile.over_x:
            name = "CERRAR " + name
        extra = f'  <span font_family="{MONO}" size="small" alpha="75%">{n} VENTANAS</span>' if n > 1 else ""
        self.tip.set_markup(name + extra)
        self.tip.set_visible(True)
        self._place_above(self.tip, tile)

    def hide_tip(self):
        self.tip.set_visible(False)

    def open_winlist(self, tile: AppTile):
        self.hide_tip()
        app = tile.app
        self.winlist_app = app["id"]
        child = self.winlist.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.winlist.remove(child)
            child = nxt
        head = Gtk.Box(spacing=6, margin_start=PAD + 8, margin_end=PAD + 8, margin_top=PAD + 6)
        head.add_css_class("nd-wl-head")
        code = Gtk.Label(label="WIN//", valign=Gtk.Align.CENTER)
        code.add_css_class("nd-wl-code")
        title = Gtk.Label(label=app["name"].upper(), ellipsize=Pango.EllipsizeMode.END, xalign=0,
                          valign=Gtk.Align.CENTER, hexpand=True)
        title.add_css_class("nd-wl-title")
        head.append(code)
        head.append(title)
        self.winlist.append(head)
        items = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, margin_start=PAD + 8,
                        margin_end=PAD + 8, margin_top=4, margin_bottom=PAD + 8)
        for w in app["windows"]:
            row = Gtk.Box(spacing=8)
            row.add_css_class("nd-wl-item")
            if w["focused"]:
                row.add_css_class("focused")
            row.append(Gtk.Label(label=w["title"], ellipsize=Pango.EllipsizeMode.END, xalign=0, hexpand=True))
            if w["minimized"]:
                tag = Gtk.Label(label="MIN")
                tag.add_css_class("nd-tag")
                row.append(tag)
            x = Gtk.Button(label="×", tooltip_text="Cerrar esta ventana")
            x.add_css_class("nd-x")
            x.connect("clicked", lambda _b, vid=w["id"]: self._cerrar_ventana(vid))
            row.append(x)
            click = Gtk.GestureClick(button=1)
            click.connect("released", lambda *_a, vid=w["id"]: (self._activate(vid), self.close_winlist()))
            row.add_controller(click)
            items.append(row)
        self.winlist.append(items)
        acts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0, margin_start=PAD + 8, margin_end=PAD + 8,
                       margin_bottom=PAD + 8)
        acts.add_css_class("nd-wl-acts")
        if app["id"].endswith(".desktop"):
            if app["windows"]:
                nueva = Gtk.Button(label="＋ NUEVA VENTANA", halign=Gtk.Align.START)
                nueva.add_css_class("nd-wl-act")
                nueva.connect("clicked", lambda _b, i=app["id"]: (self._lanzar(i), self.close_winlist()))
                acts.append(nueva)
            fij = self.is_pinned(app["id"])
            pin = Gtk.Button(label="◇ QUITAR DEL DOCK" if fij else "◆ FIJAR EN EL DOCK", halign=Gtk.Align.START)
            pin.add_css_class("nd-wl-act")
            pin.connect("clicked", lambda _b, i=app["id"]: (self.close_winlist(), self.toggle_pin(i)))
            acts.append(pin)
        if acts.get_first_child() is not None:
            self.winlist.append(acts)
        self.winlist.set_visible(True)
        self._place_above(self.winlist, tile)

    def close_winlist(self):
        self.winlist_app = None
        self.winlist.set_visible(False)

    # ---------- botones ----------
    def _sync_pin(self):
        pinned = self.state.get("mode") == "pinned"
        self.pin.set_label("◆" if pinned else "◇")
        self.pin.set_tooltip_text("Fijo: siempre visible (clic = esconder solo)" if pinned
                                  else "Se esconde solo (clic = dejarlo fijo)")
        if pinned:
            self.pin.add_css_class("on")
        else:
            self.pin.remove_css_class("on")

    def _on_pin(self, _b):
        self.state["mode"] = "autohide" if self.state.get("mode") == "pinned" else "pinned"
        _save_state(self.state)
        self._sync_pin()

    def _on_hide(self, _b):
        if self.state.get("mode") == "pinned":
            self.state["mode"] = "autohide"
            _save_state(self.state)
            self._sync_pin()
        self._set_shown(False)

    def _on_quit(self, b):
        if b.has_css_class("armed"):
            self.get_application().quit()
            return
        b.add_css_class("armed")
        b.set_label("¿CERRAR?")

        def disarm():
            self.arm_id = 0
            b.remove_css_class("armed")
            b.set_label("✕")
            return GLib.SOURCE_REMOVE

        self.arm_id = GLib.timeout_add(2500, disarm)

    # ---------- esconder / mostrar ----------
    def _on_pointer(self, ctrl, _pspec):
        if ctrl.get_property("contains-pointer"):
            if self.hide_id:
                GLib.source_remove(self.hide_id)
                self.hide_id = 0
            self._reveal()
        else:
            self.hide_tip()
            self._schedule_hide()

    def _schedule_hide(self):
        if self.hide_id or not self.shown:
            return

        def done():
            self.hide_id = 0
            self.close_winlist()  # sin teclado no hay Esc ni "clic fuera": se cierra al irse el mouse
            if self.state.get("mode") != "pinned":
                self._set_shown(False)
            return GLib.SOURCE_REMOVE

        self.hide_id = GLib.timeout_add(HIDE_AFTER_MS, done)

    def _reveal(self):
        if self.shown:
            return
        # no molestar a juegos / videos en pantalla completa
        if any(v.get("activated") and v.get("fullscreen") for v in self.views.values()):
            return
        self._set_shown(True)

    def _set_shown(self, on: bool):
        if on == self.shown:
            return
        self.shown = on
        if not on:
            self.hide_tip()
            self.close_winlist()
        self.revealer.set_reveal_child(on)


class DockApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID)

    def do_startup(self):
        Gtk.Application.do_startup(self)
        # pkill / cierre de sesion: salir ordenado
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            GLibUnix.signal_add(GLib.PRIORITY_DEFAULT, sig, lambda *_: (self.quit(), GLib.SOURCE_REMOVE)[1])
        # SIGUSR1 (Super+D): todas las apps
        GLibUnix.signal_add(GLib.PRIORITY_DEFAULT, signal.SIGUSR1, segura("dock SIGUSR1", self._usr1))
        # SIGWINCH (Super sola): NEURAL//CINTA
        GLibUnix.signal_add(GLib.PRIORITY_DEFAULT, signal.SIGWINCH, segura("dock SIGWINCH", self._winch))
        # SIGUSR2 (Super+H): ver escritorio / volver
        GLibUnix.signal_add(GLib.PRIORITY_DEFAULT, signal.SIGUSR2, segura("dock SIGUSR2", self._usr2))

    def _winch(self, *_):
        win = next((w for w in self.get_windows() if isinstance(w, DockWindow)), None)
        if win is not None:
            win.toggle_cinta()
        return GLib.SOURCE_CONTINUE

    def _usr2(self, *_):
        win = next((w for w in self.get_windows() if isinstance(w, DockWindow)), None)
        if win is not None:
            win.toggle_escritorio()
        return GLib.SOURCE_CONTINUE

    def _usr1(self, *_):
        win = next((w for w in self.get_windows() if isinstance(w, DockWindow)), None)
        if win is not None:
            win.toggle_apps()
        return GLib.SOURCE_CONTINUE

    def do_activate(self):
        win = next((w for w in self.get_windows() if isinstance(w, DockWindow)), None) or DockWindow(self)
        win.present()


if __name__ == "__main__":
    try:
        ctypes.CDLL(None).prctl(15, b"neural-dock", 0, 0, 0)  # PR_SET_NAME: para pkill -x (Super+D)
    except (OSError, AttributeError):
        pass
    GLib.set_prgname("neural-dock")
    register_fonts()
    sys.exit(DockApp().run(sys.argv))
