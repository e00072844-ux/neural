#!/usr/bin/env python3
"""Fondo de pantalla interactivo estilo HUD cyberpunk: ventana GTK4 sin
decoración que renderiza web/index.html con WebKit, y le empuja el tablero
guardado (paneles + tema), las apps de cada panel y las métricas del sistema.
Encima de la pagina va una terminal real (VTE, ver terminal.py) que se coloca
donde la pagina dibuja el panel de terminal.

Version para el LFS con Wayfire: la ventana es una capa BACKGROUND de
wlr-layer-shell (ver layer.py), asi queda detras de todo, estirada a la
pantalla, fuera de la lista de ventanas y del dock. Sin el protocolo queda
fullscreen."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from layer import ensure_preload, setup_background, SeguirTty

ensure_preload()  # antes de cargar Gtk (ver layer.py)

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("WebKit", "6.0")
from gi.repository import Gio, Gtk, WebKit, GLib, Gdk  # noqa: E402

from metrics import read_metrics
from apps import launch_app, list_installable_apps, resolve_apps, seed_app_ids
from board import CONFIG_DIR, CONFIG_PATH, app_ids_in, load_board, save_board
from terminal import TerminalPane
import decoracion  # noqa: E402
import iconos  # noqa: E402
import temas_apps  # noqa: E402
from wayfire_ipc import WayfireIPC  # noqa: E402

WEB_DIR = Path(__file__).parent / "web"
WM_CLASS = "live-wallpaper"
APP_ID = "org.local.LiveWallpaper"


FOOT_COLORS = Path.home() / ".config" / "foot" / "neural-colores.ini"


def _hex(css: str, fallback: str = "000000") -> str:
    c = Gdk.RGBA()
    if not c.parse(css or ""):
        return fallback
    return f"{round(c.red * 255):02x}{round(c.green * 255):02x}{round(c.blue * 255):02x}"


TERM_PALETTE = CONFIG_DIR / "term-palette.json"


def _write_term_palette(pal: dict) -> None:
    """La paleta para NEURAL//TERM (term.py la vigila). Atómico: tmp + rename."""
    text = json.dumps(pal, indent=1) + "\n"
    try:
        if TERM_PALETTE.exists() and TERM_PALETTE.read_text() == text:
            return
        tmp = TERM_PALETTE.with_suffix(".json.tmp")
        tmp.write_text(text)
        os.replace(tmp, TERM_PALETTE)
    except OSError:
        pass


def _write_foot_colors(pal: dict) -> None:
    """La misma paleta de la terminal del fondo, para foot (las ventanas
    nuevas la toman). Fondo = el color sobre el que se lee la terminal del
    panel (cursorText), casi opaco."""
    ansi = pal.get("ansi") or []
    if len(ansi) < 16:
        return
    bg = _hex(pal.get("cursorText"), "050507")
    lines = ["# lo escribe ~/neural-deck/app.py con el tema activo: no editar a mano", "[colors-dark]",
             "alpha=0.94", f"background={bg}", f"foreground={_hex(pal.get('fg'), 'e0e0e0')}",
             f"cursor={bg} {_hex(pal.get('cursor'), 'ffffff')}",
             f"selection-foreground={bg}", f"selection-background={_hex(ansi[6])}"]
    lines += [f"regular{i}={_hex(ansi[i])}" for i in range(8)]
    lines += [f"bright{i}={_hex(ansi[i + 8])}" for i in range(8)]
    text = "\n".join(lines) + "\n"
    try:
        if FOOT_COLORS.exists() and FOOT_COLORS.read_text() == text:
            return
        FOOT_COLORS.parent.mkdir(parents=True, exist_ok=True)
        FOOT_COLORS.write_text(text)
    except OSError:
        pass


class WallpaperWindow(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application, windowed_size: tuple[int, int] | None = None):
        super().__init__(application=app, title="Live Wallpaper")
        self.set_decorated(False)
        if windowed_size:
            self.set_default_size(*windowed_size)  # modo pruebas
        elif not setup_background(self):
            self.fullscreen()

        self._watch_apps()
        # si se edita wayfire.ini, Wayfire recarga y vuelve a su decoracion
        self._wf_monitor = Gio.File.new_for_path(str(Path.home() / ".config" / "wayfire.ini")).monitor_file(
            Gio.FileMonitorFlags.NONE, None)
        self._wf_monitor.connect("changed", lambda *_: GLib.timeout_add(800, lambda: (decoracion.aplicar(), False)[1]))
        # tema elegido desde fuera (NEURAL//AJUSTES > APARIENCIA escribe board.json)
        self._tema_visto = load_board().get("theme")
        self._board_monitor = Gio.File.new_for_path(str(CONFIG_PATH)).monitor_file(Gio.FileMonitorFlags.NONE, None)
        self._board_monitor.connect("changed", lambda *_: GLib.timeout_add(300, lambda: (self._tema_externo(), False)[1]))
        # animaciones del fondo on/off (panel rápido > FONDO): sección "fondo" de ajustes.json
        self._ajustes_monitor = Gio.File.new_for_path(str(CONFIG_DIR / "ajustes.json")).monitor_file(Gio.FileMonitorFlags.NONE, None)
        self._ajustes_monitor.connect("changed", lambda *_: GLib.timeout_add(200, lambda: (self._push_animaciones(), False)[1]))
        content_manager = WebKit.UserContentManager()
        for name, handler in (
            ("launchApp", self._on_launch_app),
            ("requestAllApps", self._on_request_all_apps),
            ("boardUpdate", self._on_board_update),
            ("quitApp", self._on_quit_app),
            ("termRect", self._on_term_rect),
            ("termTheme", self._on_term_theme),
            ("termFocus", self._on_term_focus),
        ):
            content_manager.register_script_message_handler(name)
            content_manager.connect(f"script-message-received::{name}", handler)

        self.webview = WebKit.WebView(user_content_manager=content_manager)
        settings = self.webview.get_settings()
        settings.set_enable_developer_extras(False)  # sin inspector (auditoria 3-nov-2026)
        # el fondo SOLO carga sus propios archivos: una pagina de fuera (p. ej. un enlace
        # arrastrado al escritorio) heredaria el puente con Python (launchApp, boardUpdate...)
        self.webview.connect("decide-policy", self._on_decide_policy)
        self.webview.set_background_color(Gdk.RGBA())  # transparente

        # La terminal va en una capa encima de la WebView; la pagina le dice
        # donde y de que tamaño (termRect) y con que colores (termTheme).
        self.terminal = TerminalPane()
        overlay = Gtk.Overlay()
        overlay.set_child(self.webview)
        overlay.add_overlay(self.terminal)
        self.set_child(overlay)

        self.webview.connect("load-changed", self._on_load_changed)
        self.webview.load_uri(f"file://{WEB_DIR / 'index.html'}")

        GLib.timeout_add_seconds(1, self._push_metrics)
        self._tapado = False   # una ventana cubre el fondo: la página se pausa
        self._n_metricas = 0
        self._cobertura_pend = False
        self._vigilar_ventanas()
        self._tty = SeguirTty(self, "fondo")

        # Ctrl+Q cierra el wallpaper. (Escape ya no: ahora lo usa la pagina
        # para cerrar el selector de apps / salir del modo edicion.)
        key_controller = Gtk.EventControllerKey()
        key_controller.connect("key-pressed", self._on_key_pressed)
        self.add_controller(key_controller)

        # F12 pliega/despliega la terminal aunque ella tenga el foco (por eso
        # en fase de captura: si no, se lo comeria la propia terminal).
        toggle_controller = Gtk.EventControllerKey()
        toggle_controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        toggle_controller.connect("key-pressed", self._on_toggle_key)
        self.add_controller(toggle_controller)

    @staticmethod
    def _uri_propia(uri: str) -> bool:
        if not uri.startswith("file://"):
            return False
        try:
            ruta = Path(GLib.filename_from_uri(uri.split("#", 1)[0].split("?", 1)[0])[0]).resolve()
        except GLib.Error:
            return False
        return ruta.is_relative_to(WEB_DIR.resolve())

    def _on_decide_policy(self, _webview, decision, tipo):
        if tipo in (WebKit.PolicyDecisionType.NAVIGATION_ACTION, WebKit.PolicyDecisionType.NEW_WINDOW_ACTION):
            uri = decision.get_navigation_action().get_request().get_uri() or ""
            if not self._uri_propia(uri):
                print(f"fondo: navegacion BLOQUEADA a {uri[:120]}", file=sys.stderr, flush=True)
                decision.ignore()
                return True
        return False

    def run_js(self, code: str) -> None:
        self.webview.evaluate_javascript(code, -1, None, None, None)

    def _on_key_pressed(self, _controller, keyval, _keycode, state):
        if keyval in (Gdk.KEY_q, Gdk.KEY_Q) and state & Gdk.ModifierType.CONTROL_MASK:
            # guardar cualquier cambio pendiente del tablero antes de salir
            self.run_js("flushSave();")
            GLib.timeout_add(200, lambda: self.get_application().quit())
            return True
        return False

    def _on_toggle_key(self, _controller, keyval, _keycode, state):
        if keyval == Gdk.KEY_F12 and not state & Gtk.accelerator_get_default_mod_mask():
            self.run_js("window.__termToggle && window.__termToggle();")
            return True
        return False

    def _on_quit_app(self, _manager, _js_value):
        self.get_application().quit()

    def _on_term_rect(self, _manager, js_value):
        r = json.loads(js_value.to_string())
        was_visible = self.terminal.get_visible()
        self.terminal.place(r.get("visible", False), r.get("x", 0), r.get("y", 0), r.get("w", 0), r.get("h", 0))
        if r.get("focus") and self.terminal.get_visible():
            self.terminal.focus_terminal()
        elif was_visible and not self.terminal.get_visible():
            self.webview.grab_focus()  # que E/Esc/T vuelvan a llegar a la pagina

    def _on_term_theme(self, _manager, js_value):
        pal = json.loads(js_value.to_string())
        self.terminal.apply_palette(pal)
        _write_foot_colors(pal)
        _write_term_palette(pal)
        decoracion.aplicar()  # barras de titulo de las ventanas con el tema
        temas_apps.aplicar()  # apps GTK3/GTK4 (gtk.css) con el tema
        try:
            iconos.generar()  # iconos de las apps NEURAL con los colores del tema
        except OSError as e:
            print(f"iconos: {e}", file=sys.stderr, flush=True)

    def _on_term_focus(self, _manager, js_value):
        if js_value.to_boolean() and self.terminal.get_visible():
            self.terminal.focus_terminal()
        else:
            self.webview.grab_focus()

    # ---------- apps instaladas: avisar al instalar / desinstalar ----------
    def _watch_apps(self):
        """GIO vigila las carpetas applications/ de XDG_DATA_DIRS (Flatpak
        incluido); al cambiar, manda la lista nueva a la ruleta (con 1.5 s de
        espera: una instalacion toca varios archivos seguidos)."""
        self._apps_pending = 0
        self._apps_monitor = Gio.AppInfoMonitor.get()
        self._apps_monitor.connect("changed", lambda *_: self._apps_changed())

    def _apps_changed(self):
        if self._apps_pending:
            GLib.source_remove(self._apps_pending)
        self._apps_pending = GLib.timeout_add(1500, self._push_all_apps)

    def _push_all_apps(self):
        self._apps_pending = 0
        self.run_js(f"window.__setAllApps({json.dumps(list_installable_apps())});")
        return GLib.SOURCE_REMOVE

    def _on_launch_app(self, _manager, js_value):
        launch_app(js_value.to_string())

    def _on_request_all_apps(self, _manager, _js_value):
        self._push_all_apps()

    def _on_board_update(self, _manager, js_value):
        board = json.loads(js_value.to_string())
        self._tema_visto = board.get("theme")
        save_board(board)

    def _tema_externo(self):
        """board.json cambió: si el tema no es el que tiene la página, aplicarlo
        (applyTheme avisa luego por termTheme y se re-tiñe todo lo demás)."""
        tema = load_board().get("theme")
        if isinstance(tema, str) and tema != self._tema_visto:
            self._tema_visto = tema
            self.run_js(f"window.applyTheme ? applyTheme({json.dumps(tema)}, {{flash: true}}) : null;")

    def _on_load_changed(self, _webview, event):
        if event != WebKit.LoadEvent.FINISHED:
            return
        board = load_board()
        seed = [] if board["panels"] is not None else seed_app_ids()
        apps = resolve_apps(app_ids_in(board) + seed)
        payload = {"board": board, "apps": apps, "seedApps": seed}
        self.run_js(f"window.__init({json.dumps(payload)});")
        self._push_animaciones()
        self._push_metrics()

    def _push_metrics(self) -> bool:
        # tapado: métricas cada 5 s (solo para saber si se enchufa la batería)
        self._n_metricas += 1
        if self._tapado and self._n_metricas % 5:
            return True
        self.run_js(f"window.__updateMetrics({json.dumps(read_metrics())});")
        return True  # sigue el timer

    def _push_animaciones(self):
        try:
            on = json.loads((CONFIG_DIR / "ajustes.json").read_text()).get("fondo", {}).get("animaciones", True)
        except (OSError, ValueError, AttributeError):
            on = True
        self.run_js(f"window.__animaciones && window.__animaciones({json.dumps(bool(on))});")

    # ---- pausa cuando no se ve el fondo (2026-11-07) ----
    def _vigilar_ventanas(self):
        """Eventos de Wayfire -> ¿hay ventanas tapando toda la pantalla?"""
        try:
            self._ipc = WayfireIPC()
            self._ipc_ev = WayfireIPC(self._ipc.path)
            self._ipc_ev.watch(self._on_evento_wf)
        except (OSError, RuntimeError) as e:
            print(f"pausa: sin IPC de Wayfire ({e}); el fondo no se pausa", file=sys.stderr)
            self._ipc = None
            return
        self._calcular_cobertura()

    def _on_evento_wf(self, ev):
        if ev is None:  # Wayfire se reinició: volver a intentarlo
            GLib.timeout_add_seconds(3, lambda: (self._vigilar_ventanas(), False)[1])
            return
        if not self._cobertura_pend:  # agrupar ráfagas de eventos
            self._cobertura_pend = True
            GLib.timeout_add(150, self._calcular_cobertura)

    def _calcular_cobertura(self) -> bool:
        self._cobertura_pend = False
        try:
            vistas = self._ipc.call("window-rules/list-views")
            salidas = self._ipc.call("window-rules/list-outputs")
        except (OSError, RuntimeError, ValueError):
            return False
        tapado = bool(salidas) and all(self._salida_tapada(o, vistas) for o in salidas)
        if tapado != self._tapado:
            self._tapado = tapado
            self.run_js(f"window.__pausa && window.__pausa({json.dumps(tapado)});")
            if not tapado:
                self._push_metrics()
        return False

    @staticmethod
    def _salida_tapada(salida: dict, vistas: list) -> bool:
        """True si las ventanas visibles cubren ≥97% de esa pantalla."""
        g = salida["geometry"]
        W, H = g["width"], g["height"]
        rects = []
        for v in vistas:
            if (v.get("role") != "toplevel" or not v.get("mapped") or v.get("minimized")
                    or v.get("layer") != "workspace" or v.get("output-id") != salida["id"]):
                continue
            r = v["geometry"]  # relativa al escritorio visible de su pantalla
            if v.get("fullscreen") or v.get("tiled-edges") == 15:
                if r["x"] < W and r["x"] + r["width"] > 0 and r["y"] < H and r["y"] + r["height"] > 0:
                    return True
            rects.append((r["x"], r["y"], r["x"] + r["width"], r["y"] + r["height"]))
        if not rects:
            return False
        # muestreo en rejilla 64x36: suficiente para "¿queda fondo a la vista?"
        n = tot = 0
        for i in range(64):
            x = (i + 0.5) * W / 64
            for j in range(36):
                y = (j + 0.5) * H / 36
                tot += 1
                n += any(a <= x < c and b <= y < d for a, b, c, d in rects)
        return n / tot >= 0.97


class WallpaperApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID)
        self.connect("shutdown", self._on_shutdown)

    def _on_shutdown(self, _app):
        for win in self.get_windows():
            if isinstance(win, WallpaperWindow):
                win.terminal.shutdown()

    def do_activate(self):
        win = self.props.active_window
        if not win:
            win = WallpaperWindow(self)
        win.present()


if __name__ == "__main__":
    import sys

    GLib.set_prgname(WM_CLASS)
    app = WallpaperApp()
    sys.exit(app.run(sys.argv))
