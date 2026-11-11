#!/usr/bin/env python3
"""NEURAL//WEB v0.1: navegador propio sobre WebKitGTK 6.0 (GTK4), estilo NEURAL//DECK.

Va en la carpeta de neural-deck porque usa estilo.py (marcos HUD, temas, fuentes)
y board.py (el tema activo, board.json). Sigue al tema del wallpaper en vivo.

v0.1: ventana con marco HUD propio (sin barra de GTK), pestañas, barra de
direcciones (URL o búsqueda en DuckDuckGo), atrás/adelante/recargar, barra de
progreso, línea de estado con el enlace bajo el ratón, página de inicio con el
tema y atajos de teclado.
Datos (cookies, caché) en ~/.local/share/neural-web y ~/.cache/neural-web.

Atajos: Ctrl+T pestaña nueva · Ctrl+W cerrar · Ctrl+Tab / Ctrl+Shift+Tab cambiar
        Ctrl+L barra · F5 / Ctrl+R recargar · Alt+← / Alt+→ · Ctrl+ +/-/0 zoom
        F12 inspector · F11 pantalla completa
"""
from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import quote_plus

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("WebKit", "6.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango, WebKit  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from board import CONFIG_PATH, load_board  # noqa: E402
from estilo import MONO, PAD, SANS, Frame, css_color, load_themes, register_fonts  # noqa: E402

APP_ID = "org.local.NeuralWeb"
VERSION = "0.1"
HOME = "neural:inicio"  # lo que se ve en la barra en la página de inicio
SEARCH = "https://duckduckgo.com/?q={}"
DATA_DIR = Path(GLib.get_user_data_dir()) / "neural-web"
CACHE_DIR = Path(GLib.get_user_cache_dir()) / "neural-web"


# ---------- CSS ----------
def theme_css(t: dict) -> str:
    acc, alt, warn, text, muted = t["accent"], t["alt"], t["warn"], t["text"], t["muted"]
    dark = "#0a0204"
    return f"""
window.neural-web, window.neural-web.csd {{ background: transparent; box-shadow: none;
    border-radius: 0; border: none; outline: none; }}
.nw-titlebar {{ min-height: 0; background: none; border: none; box-shadow: none; }}
.nw-brand {{ font-family: "{SANS}"; font-weight: 700; font-size: 15px; letter-spacing: 2px;
    color: {css_color(acc)}; }}
.nw-ver {{ font-family: "{MONO}"; font-size: 10px; color: {css_color(muted)}; }}
.nw-btn {{ all: unset; min-width: 26px; min-height: 24px; background: rgba(0, 0, 0, 0.55);
    border: 1px solid {css_color(acc, 0.6)}; color: {css_color(text)}; font-family: "{MONO}";
    font-size: 13px; }}
.nw-btn:hover {{ background: {css_color(acc)}; color: {dark}; }}
.nw-btn:disabled {{ color: {css_color(muted, 0.6)}; border-color: {css_color(muted, 0.35)}; background: none; }}
.nw-btn.close:hover {{ background: {css_color(warn)}; border-color: {css_color(warn)}; color: #000; }}
.nw-url {{ background: rgba(0, 0, 0, 0.6); color: {css_color(text)}; caret-color: {css_color(alt)};
    border: 1px solid {css_color(acc, 0.55)}; border-radius: 0; box-shadow: none; min-height: 24px;
    font-family: "{MONO}"; font-size: 13px; padding: 0 8px; outline: none; }}
.nw-url:focus-within {{ border-color: {css_color(alt)}; }}
.nw-url selection {{ background: {css_color(acc)}; color: {dark}; }}
.nw-url.secure {{ border-left: 3px solid {css_color(alt)}; }}
.nw-url.insecure {{ border-left: 3px solid {css_color(warn)}; }}
.nw-tab {{ all: unset; padding: 3px 4px 3px 10px; min-height: 20px; background: rgba(0, 0, 0, 0.45);
    border: 1px solid {css_color(acc, 0.3)}; border-bottom: 2px solid transparent; }}
.nw-tab:hover {{ background: {css_color(acc, 0.14)}; }}
.nw-tab.on {{ border-color: {css_color(acc, 0.8)}; border-bottom-color: {css_color(acc)};
    background: {css_color(acc, 0.18)}; }}
.nw-tab-num {{ font-family: "{MONO}"; font-size: 10px; color: {css_color(muted)}; }}
.nw-tab.on .nw-tab-num {{ color: {css_color(alt)}; }}
.nw-tab-title {{ font-family: "{SANS}"; font-weight: 600; font-size: 13px; color: {css_color(text)}; }}
.nw-x {{ all: unset; color: {css_color(muted)}; padding: 0 4px; font-size: 13px; }}
.nw-x:hover {{ color: {css_color(warn)}; }}
.nw-progress trough {{ min-height: 2px; background: none; border: none; }}
.nw-progress progress {{ min-height: 2px; background: {css_color(alt)}; border: none; border-radius: 0; }}
.nw-view {{ border: 1px solid {css_color(acc, 0.35)}; }}
.nw-status {{ font-family: "{MONO}"; font-size: 10px; color: {css_color(muted)}; letter-spacing: 1px; }}
.nw-status .ok {{ color: {css_color(alt)}; }}
"""


def home_html(t: dict) -> str:
    acc, alt, text, muted = (css_color(t[k]) for k in ("accent", "alt", "text", "muted"))
    links = [("DuckDuckGo", "https://duckduckgo.com"), ("YouTube", "https://www.youtube.com"),
             ("Wikipedia", "https://es.wikipedia.org"), ("BLFS", "https://www.linuxfromscratch.org/blfs/"),
             ("GitHub", "https://github.com"), ("Arch Wiki", "https://wiki.archlinux.org")]
    tiles = "".join(f'<a href="{u}"><b>{i + 1:02d}</b>{n}</a>' for i, (n, u) in enumerate(links))
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Inicio</title>
<style>
:root {{ color-scheme: dark; }}
body {{ margin: 0; height: 100vh; display: flex; flex-direction: column; align-items: center;
  justify-content: center; background: #050507; color: {text}; font-family: "{SANS}", sans-serif;
  background-image: repeating-linear-gradient(0deg, rgba(255,255,255,0.025) 0 1px, transparent 1px 4px); }}
h1 {{ font-size: 64px; letter-spacing: 8px; margin: 0; color: {acc};
  text-shadow: 0 0 18px {acc}; font-weight: 700; }}
h1 span {{ color: {alt}; text-shadow: 0 0 18px {alt}; }}
.sub {{ font-family: "{MONO}", monospace; color: {muted}; letter-spacing: 3px; font-size: 12px; margin: 6px 0 34px; }}
form {{ width: min(640px, 86vw); }}
input {{ width: 100%; box-sizing: border-box; background: rgba(0,0,0,0.6); color: {text};
  border: 1px solid {acc}; padding: 12px 16px; font: 18px "{MONO}", monospace; outline: none;
  clip-path: polygon(14px 0, 100% 0, 100% calc(100% - 14px), calc(100% - 14px) 100%, 0 100%, 0 14px); }}
input:focus {{ border-color: {alt}; box-shadow: 0 0 14px {alt}; }}
.grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; width: min(640px, 86vw); margin-top: 28px; }}
a {{ display: block; padding: 12px 14px; border: 1px solid {acc}; color: {text}; text-decoration: none;
  font-weight: 600; letter-spacing: 1px; background: rgba(0,0,0,0.45);
  clip-path: polygon(10px 0, 100% 0, 100% calc(100% - 10px), calc(100% - 10px) 100%, 0 100%, 0 10px); }}
a:hover {{ background: {acc}; color: #0a0204; }}
a b {{ font-family: "{MONO}", monospace; font-weight: 400; color: {muted}; margin-right: 10px; font-size: 12px; }}
a:hover b {{ color: #0a0204; }}
</style></head><body>
<h1>NEURAL<span>//</span>WEB</h1>
<div class="sub">ENLACE NEURAL ESTABLE · v{VERSION}</div>
<form action="https://duckduckgo.com/" method="get"><input name="q" placeholder="&gt; buscar o escribir dirección" autofocus></form>
<div class="grid">{tiles}</div>
</body></html>"""


def to_uri(text: str) -> str:
    """URL tal cual, dominio sin esquema → https://, lo demás → búsqueda."""
    s = text.strip()
    if not s:
        return ""
    if s == HOME:
        return HOME
    if "://" in s or s.startswith(("about:", "file:", "data:")):
        return s
    if " " not in s and ("." in s or s.startswith("localhost")):
        return "https://" + s
    return SEARCH.format(quote_plus(s))


# ---------- una pestaña ----------
class Tab:
    def __init__(self, win: "Browser", view: WebKit.WebView):
        self.win, self.view = win, view
        view.add_css_class("nw-view")
        view.set_vexpand(True)
        view.set_hexpand(True)
        s = view.get_settings()
        s.set_enable_developer_extras(True)
        s.set_enable_back_forward_navigation_gestures(True)
        s.set_javascript_can_open_windows_automatically(False)

        # botón de la pestaña: número · título · ✕
        self.button = Gtk.Button()
        self.button.add_css_class("nw-tab")
        box = Gtk.Box(spacing=6)
        self.num = Gtk.Label()
        self.num.add_css_class("nw-tab-num")
        self.title = Gtk.Label(label="NUEVA", xalign=0, max_width_chars=22,
                               ellipsize=Pango.EllipsizeMode.END)
        self.title.add_css_class("nw-tab-title")
        close = Gtk.Button(label="✕")
        close.add_css_class("nw-x")
        close.connect("clicked", lambda *_: win.close_tab(self))
        for w in (self.num, self.title, close):
            box.append(w)
        self.button.set_child(box)
        self.button.connect("clicked", lambda *_: win.select(self))
        middle = Gtk.GestureClick(button=2)  # clic central cierra
        middle.connect("released", lambda *_: win.close_tab(self))
        self.button.add_controller(middle)

        view.connect("notify::title", self.on_title)
        view.connect("notify::uri", lambda *_: win.sync(self))
        view.connect("notify::estimated-load-progress", lambda *_: win.sync(self))
        view.connect("notify::is-loading", lambda *_: win.sync(self))
        view.connect("load-changed", lambda *_: win.sync(self))
        view.connect("create", self.on_create)
        view.connect("mouse-target-changed", self.on_hover)
        view.connect("close", lambda *_: win.close_tab(self))

    def on_title(self, *_):
        t = self.view.get_title() or self.view.get_uri() or "SIN TÍTULO"
        self.title.set_label(t)
        self.button.set_tooltip_text(t)
        self.win.sync(self)

    def on_create(self, view, action):
        """window.open / target=_blank → pestaña nueva relacionada (comparte sesión)."""
        new = WebKit.WebView(related_view=view)
        self.win.add_tab(new, select=True)
        return new

    def on_hover(self, view, hit, modifiers):
        if self.win.current is self:
            self.win.hover(hit.get_link_uri() if hit.context_is_link() else None)

    def show_home(self):
        self.view.load_html(home_html(self.win.theme), None)

    def is_home(self) -> bool:
        return (self.view.get_uri() or "about:blank") == "about:blank"


# ---------- ventana ----------
class Browser(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application, session: WebKit.NetworkSession, home: bool = True):
        super().__init__(application=app, title="NEURAL//WEB")
        self.session = session
        self.themes = load_themes()
        self.theme = self.themes.get(load_board().get("theme"), next(iter(self.themes.values())))
        self.tabs: list[Tab] = []
        self.current: Tab | None = None
        self.css = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), self.css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_USER + 1)
        self.add_css_class("neural-web")
        self.set_default_size(1280, 800)
        # barra de título vacía: GTK sigue dando bordes para redimensionar, pero
        # lo que se ve es todo nuestro (el marco HUD)
        empty = Gtk.Box()
        empty.add_css_class("nw-titlebar")
        self.set_titlebar(empty)

        frame = Frame(self, Gtk.Orientation.VERTICAL, 16, ("tl", "br"), True, tab="NET//WEB")
        for side in ("start", "end", "top", "bottom"):
            getattr(frame, f"set_margin_{side}")(0)
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        inner.set_margin_top(PAD + 20)
        inner.set_margin_bottom(PAD + 8)
        inner.set_margin_start(PAD + 12)
        inner.set_margin_end(PAD + 12)
        inner.set_hexpand(True)
        inner.set_vexpand(True)
        frame.append(inner)
        self.set_child(frame)

        # fila 1 (se arrastra la ventana desde aquí): marca · pestañas · + · ventana
        handle = Gtk.WindowHandle()
        row1 = Gtk.Box(spacing=8)
        handle.set_child(row1)
        brand = Gtk.Box(spacing=6)
        lb = Gtk.Label(label="NEURAL//WEB")
        lb.add_css_class("nw-brand")
        lv = Gtk.Label(label=f"v{VERSION}")
        lv.add_css_class("nw-ver")
        brand.append(lb)
        brand.append(lv)
        row1.append(brand)
        self.tabbar = Gtk.Box(spacing=4)
        tabscroll = Gtk.ScrolledWindow(hexpand=True, vscrollbar_policy=Gtk.PolicyType.NEVER,
                                       hscrollbar_policy=Gtk.PolicyType.EXTERNAL)
        tabscroll.set_child(self.tabbar)
        row1.append(tabscroll)
        row1.append(self.btn("+", "Pestaña nueva (Ctrl+T)", lambda *_: self.new_tab()))
        sep = Gtk.Box()
        sep.set_size_request(10, 1)
        row1.append(sep)
        row1.append(self.btn("_", "Minimizar", lambda *_: self.minimize()))
        row1.append(self.btn("□", "Maximizar", lambda *_: self.unmaximize() if self.is_maximized()
                             else self.maximize()))
        row1.append(self.btn("✕", "Cerrar", lambda *_: self.close(), "close"))
        inner.append(handle)

        # fila 2: ← → ⟳ ⌂ · barra de direcciones
        row2 = Gtk.Box(spacing=4)
        self.b_back = self.btn("◀", "Atrás (Alt+←)", lambda *_: self.current.view.go_back())
        self.b_fwd = self.btn("▶", "Adelante (Alt+→)", lambda *_: self.current.view.go_forward())
        self.b_reload = self.btn("⟳", "Recargar (F5)", lambda *_: self.reload_or_stop())
        for b in (self.b_back, self.b_fwd, self.b_reload,
                  self.btn("⌂", "Inicio", lambda *_: self.current.show_home())):
            row2.append(b)
        self.url = Gtk.Entry(hexpand=True, placeholder_text="> buscar o escribir dirección")
        self.url.add_css_class("nw-url")
        self.url.connect("activate", self.on_url)
        row2.append(self.url)
        inner.append(row2)

        self.progress = Gtk.ProgressBar()
        self.progress.add_css_class("nw-progress")
        inner.append(self.progress)

        self.stack = Gtk.Stack(hexpand=True, vexpand=True)
        inner.append(self.stack)

        status = Gtk.Box(spacing=8)
        status.add_css_class("nw-status")
        ok = Gtk.Label(label="ENLACE NEURAL ESTABLE")
        ok.add_css_class("ok")
        self.status = Gtk.Label(xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.MIDDLE)
        status.append(ok)
        status.append(Gtk.Label(label="·"))
        status.append(self.status)
        inner.append(status)

        self.shortcuts()
        self.apply_theme()
        self.watch_board()
        if home:
            self.new_tab()

    # --- piezas ---
    def btn(self, label, tip, cb, extra=None) -> Gtk.Button:
        b = Gtk.Button(label=label, tooltip_text=tip)
        b.add_css_class("nw-btn")
        if extra:
            b.add_css_class(extra)
        b.connect("clicked", cb)
        return b

    def shortcuts(self):
        sc = Gtk.ShortcutController(scope=Gtk.ShortcutScope.GLOBAL)
        cur = lambda: self.current.view  # noqa: E731
        table = {
            "<Control>t": lambda: self.new_tab(),
            "<Control>w": lambda: self.close_tab(self.current),
            "<Control>Tab": lambda: self.cycle(1),
            "<Control><Shift>Tab": lambda: self.cycle(-1),
            "<Control><Shift>ISO_Left_Tab": lambda: self.cycle(-1),
            "<Control>l": lambda: (self.url.grab_focus(), self.url.select_region(0, -1)),
            "F6": lambda: (self.url.grab_focus(), self.url.select_region(0, -1)),
            "F5": lambda: cur().reload(),
            "<Control>r": lambda: cur().reload(),
            "<Control><Shift>r": lambda: cur().reload_bypass_cache(),
            "<Alt>Left": lambda: cur().go_back(),
            "<Alt>Right": lambda: cur().go_forward(),
            "<Control>plus": lambda: cur().set_zoom_level(cur().get_zoom_level() + 0.1),
            "<Control>equal": lambda: cur().set_zoom_level(cur().get_zoom_level() + 0.1),
            "<Control>minus": lambda: cur().set_zoom_level(max(0.3, cur().get_zoom_level() - 0.1)),
            "<Control>0": lambda: cur().set_zoom_level(1.0),
            "F12": lambda: cur().get_inspector().show(),
            "F11": lambda: self.unfullscreen() if self.is_fullscreen() else self.fullscreen(),
            "Escape": lambda: cur().stop_loading(),
        }
        for key, fn in table.items():
            def run(_w, _a, fn=fn):
                if self.current:
                    fn()
                return True
            sc.add_shortcut(Gtk.Shortcut(trigger=Gtk.ShortcutTrigger.parse_string(key),
                                         action=Gtk.CallbackAction.new(run)))
        self.add_controller(sc)

    # --- tema (sigue a board.json del wallpaper) ---
    def apply_theme(self):
        self.css.load_from_string(theme_css(self.theme))
        self.queue_draw()
        for tab in self.tabs:
            tab.button.queue_draw()
            if tab.is_home():
                tab.show_home()

    def watch_board(self):
        self.monitor = Gio.File.new_for_path(str(CONFIG_PATH)).monitor_file(Gio.FileMonitorFlags.NONE, None)

        def changed(*_):
            t = self.themes.get(load_board().get("theme"))
            if t is not None and t is not self.theme:
                self.theme = t
                self.apply_theme()
        self.monitor.connect("changed", changed)

    # --- pestañas ---
    def new_tab(self, uri: str | None = None):
        view = WebKit.WebView(network_session=self.session)
        tab = self.add_tab(view, select=True)
        if uri:
            view.load_uri(uri)
        else:
            tab.show_home()
            self.url.grab_focus()

    def add_tab(self, view: WebKit.WebView, select: bool) -> Tab:
        tab = Tab(self, view)
        self.tabs.append(tab)
        self.tabbar.append(tab.button)
        self.stack.add_child(view)
        self.renumber()
        if select:
            self.select(tab)
        return tab

    def close_tab(self, tab: Tab | None):
        if tab is None or tab not in self.tabs:
            return
        i = self.tabs.index(tab)
        self.tabs.remove(tab)
        self.tabbar.remove(tab.button)
        self.stack.remove(tab.view)
        if not self.tabs:
            self.close()
            return
        if self.current is tab:
            self.current = None
            self.select(self.tabs[min(i, len(self.tabs) - 1)])
        self.renumber()

    def select(self, tab: Tab):
        if self.current is not None:
            self.current.button.remove_css_class("on")
        self.current = tab
        tab.button.add_css_class("on")
        self.stack.set_visible_child(tab.view)
        self.sync(tab)
        self.hover(None)

    def cycle(self, step: int):
        if self.tabs:
            self.select(self.tabs[(self.tabs.index(self.current) + step) % len(self.tabs)])

    def renumber(self):
        for i, tab in enumerate(self.tabs):
            tab.num.set_label(f"{i + 1:02d}")

    # --- estado de la pestaña actual ---
    def sync(self, tab: Tab):
        if tab is not self.current:
            return
        v = tab.view
        if not self.url.has_focus():
            self.url.set_text(HOME if tab.is_home() else (v.get_uri() or ""))
        uri = v.get_uri() or ""
        self.url.remove_css_class("secure")
        self.url.remove_css_class("insecure")
        if uri.startswith("https://"):
            ok, _cert, errors = v.get_tls_info()
            self.url.add_css_class("secure" if ok and not errors else "insecure")
        elif uri.startswith("http://"):
            self.url.add_css_class("insecure")
        self.b_back.set_sensitive(v.can_go_back())
        self.b_fwd.set_sensitive(v.can_go_forward())
        loading = v.is_loading()
        self.b_reload.set_label("✕" if loading else "⟳")
        p = v.get_estimated_load_progress()
        self.progress.set_fraction(p if loading else 0)
        self.set_title(f"{v.get_title() or 'NEURAL//WEB'} — NEURAL//WEB")

    def hover(self, link: str | None):
        self.status.set_label(link or (self.current.view.get_uri() or "" if self.current else ""))

    def reload_or_stop(self):
        v = self.current.view
        v.stop_loading() if v.is_loading() else v.reload()

    def on_url(self, entry):
        uri = to_uri(entry.get_text())
        if not uri:
            return
        if uri == HOME:
            self.current.show_home()
        else:
            self.current.view.load_uri(uri)
        self.current.view.grab_focus()


class App(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_OPEN)
        self.session = None

    def do_startup(self):
        Gtk.Application.do_startup(self)
        register_fonts()
        Gtk.Settings.get_default().set_property("gtk-application-prefer-dark-theme", True)
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self.session = WebKit.NetworkSession.new(str(DATA_DIR), str(CACHE_DIR))
        # cookies persistentes (si no, se pierden al cerrar)
        self.session.get_cookie_manager().set_persistent_storage(
            str(DATA_DIR / "cookies.sqlite"), WebKit.CookiePersistentStorage.SQLITE)

    def do_activate(self):
        win = self.get_active_window() or Browser(self, self.session)
        win.present()

    def do_open(self, files, n, hint):
        win = self.get_active_window() or Browser(self, self.session, home=False)
        for f in files:
            win.new_tab(f.get_uri())
        win.present()


if __name__ == "__main__":
    sys.exit(App().run(sys.argv))
