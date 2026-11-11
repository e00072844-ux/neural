#!/usr/bin/env python3
"""NEURAL//TERM: terminal propia (VTE + GTK4) con el estilo NEURAL//DECK.

- Ventana sin decoración: marco HUD con esquinas cortadas, brillo y escuadras
  (estilo.Frame); el margen del brillo sirve para redimensionar con el ratón.
- Pestañas TTY//01, TTY//02… con el programa en primer plano o la carpeta.
- Colores = la misma paleta que la terminal del fondo (term-palette.json, la
  escribe app.py con el tema activo) y el marco sigue board.json; cambian en vivo.
- Una sola instancia (Gtk.Application): volver a lanzarla abre otra ventana.

Atajos:
  Ctrl+Shift+T nueva pestaña · Ctrl+Shift+W cerrar pestaña · Ctrl+Shift+N nueva ventana
  Ctrl+RePág/AvPág (o Ctrl+Tab) cambiar pestaña · Ctrl+Shift+RePág/AvPág mover pestaña · Alt+1…9 ir
  Ctrl+Shift+C/V copiar/pegar · Ctrl+Shift+F buscar · Ctrl +/−/0 zoom · F11 pantalla completa
  Ctrl+clic abre enlaces · clic derecho = menú
  term.py [--dir CARPETA] [-e PROGRAMA ARGS…]
"""
from __future__ import annotations

import ctypes
import json
import os
import pwd
import sys
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Vte", "3.91")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango, Vte  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from board import CONFIG_DIR, CONFIG_PATH, load_board  # noqa: E402
from estilo import MONO, PAD, SANS, Frame, css_color, load_themes, register_fonts, rgba  # noqa: E402

APP_ID = "org.local.NeuralTerm"
PROC = "neural-term"
PALETA = CONFIG_DIR / "term-palette.json"
FUENTE = f"{MONO} 12"
SCROLLBACK = 10000
ESCALAS = (0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.35, 1.5, 1.75, 2.0)
BORDE = PAD + 4  # zona para redimensionar (el brillo del marco)
PCRE2_CASELESS, PCRE2_MULTILINE = 0x00000008, 0x00000400
URL_RE = r"(?:https?|ftp|file)://[^\s<>\"'`]*[^\s<>\"'`.,;:!?)\]}]"


def _shell() -> str:
    sh = os.environ.get("SHELL")
    if not sh:
        try:
            sh = pwd.getpwuid(os.getuid()).pw_shell
        except KeyError:
            sh = ""
    return sh if sh and os.path.exists(sh) else "/bin/bash"


def _leer_paleta() -> dict:
    try:
        d = json.loads(PALETA.read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _css(t: dict) -> str:
    acc, alt, warn, text, muted = t["accent"], t["alt"], t["warn"], t["text"], t["muted"]
    return f"""
window.neural-term {{ background: transparent; }}
.nt-code {{ font-family: "{MONO}"; font-size: 10px; letter-spacing: 1px; color: {css_color(muted)}; }}
.nt-title {{ font-family: "{SANS}"; font-weight: 700; font-size: 15px; letter-spacing: 2px; color: {css_color(acc)}; }}
.nt-head {{ border-bottom: 1px solid {css_color(acc, 0.45)}; padding-bottom: 4px; }}
.nt-tab {{ all: unset; padding: 3px 6px 3px 10px; background: rgba(0, 0, 0, 0.45);
          border-bottom: 2px solid {css_color(acc, 0.25)}; }}
.nt-tab:hover {{ background: {css_color(acc, 0.12)}; }}
.nt-tab.on {{ background: {css_color(acc, 0.18)}; border-bottom-color: {css_color(acc)}; }}
.nt-tab-num {{ font-family: "{MONO}"; font-size: 11px; color: {css_color(acc)}; }}
.nt-tab-txt {{ font-family: "{SANS}"; font-weight: 600; font-size: 13px; color: {css_color(muted)}; }}
.nt-tab.on .nt-tab-txt {{ color: {css_color(text)}; }}
.nt-tab.ocupada .nt-tab-num {{ color: {css_color(alt)}; }}
.nt-x {{ all: unset; padding: 0 4px; font-size: 12px; color: transparent; }}
.nt-tab:hover .nt-x, .nt-tab.on .nt-x {{ color: {css_color(muted)}; }}
.nt-x:hover {{ color: {css_color(warn)}; }}
.nt-x.armed {{ color: #000; background: {css_color(warn)}; }}
.nt-btn {{ all: unset; min-width: 26px; padding: 1px 4px; font-family: "{MONO}"; font-size: 13px;
          color: {css_color(muted)}; border: 1px solid transparent; }}
.nt-btn:hover {{ color: {css_color(text)}; border-color: {css_color(acc, 0.6)}; background: {css_color(acc, 0.14)}; }}
.nt-btn.cerrar:hover {{ color: #000; background: {css_color(warn)}; border-color: {css_color(warn)}; }}
.nt-btn.armed {{ color: #000; background: {css_color(warn)}; }}
.nt-foot {{ border-top: 1px solid {css_color(acc, 0.25)}; padding-top: 3px; }}
.nt-foot label {{ font-family: "{MONO}"; font-size: 10px; letter-spacing: 1px; color: {css_color(muted)}; }}
.nt-foot label.on {{ color: {css_color(alt)}; }}
.nt-foot label.warn {{ color: {css_color(warn)}; }}
vte-terminal {{ padding: 6px 8px; background: transparent; }}
scrolledwindow.nt-pane, scrolledwindow.nt-pane > viewport {{ background: transparent; }}
scrolledwindow.nt-pane scrollbar {{ background: transparent; border: none; }}
scrolledwindow.nt-pane scrollbar slider {{ background: {css_color(acc, 0.55)}; border-radius: 0; min-width: 4px; }}
.nt-buscar {{ border-top: 1px solid {css_color(acc, 0.25)}; padding: 4px 0; }}
.nt-buscar entry {{ background: rgba(0, 0, 0, 0.6); border: 1px solid {css_color(acc, 0.6)}; border-radius: 0;
                   color: {css_color(text)}; font-family: "{MONO}"; min-height: 22px; box-shadow: none; }}
.nt-buscar entry.nada {{ border-color: {css_color(warn)}; }}
popover.nt-menu > contents {{ background: rgba(5, 5, 8, 0.96); border: 1px solid {css_color(acc, 0.7)};
                             border-radius: 0; padding: 4px; box-shadow: 0 0 10px {css_color(acc, 0.35)}; }}
popover.nt-menu > arrow {{ background: transparent; border: none; }}
.nt-item {{ all: unset; padding: 4px 14px 4px 8px; font-family: "{SANS}"; font-weight: 600; font-size: 13px;
           letter-spacing: 1px; color: {css_color(text)}; border-left: 2px solid transparent; }}
.nt-item:hover {{ background: {css_color(acc, 0.16)}; border-left-color: {css_color(acc)}; }}
.nt-item-k {{ font-family: "{MONO}"; font-size: 10px; color: {css_color(muted)}; }}
"""


def _lbl(text: str, cls: str, **kw) -> Gtk.Label:
    lb = Gtk.Label(label=text, **kw)
    lb.add_css_class(cls)
    return lb


def _on(w: Gtk.Widget, cls: str, activo: bool) -> None:
    (w.add_css_class if activo else w.remove_css_class)(cls)


# ---------------------------------------------------------------- pestaña
class Pestana:
    def __init__(self, win: "Ventana", cwd: str | None, cmd: list[str] | None = None):
        self.win = win
        self.pid: int | None = None
        self.nombre = "…"
        self.ocupada = False
        self.cwd = cwd or os.path.expanduser("~")
        self._escala = ESCALAS.index(1.0)
        self.armada = 0

        t = self.term = Vte.Terminal()
        t.set_font(Pango.FontDescription.from_string(FUENTE))
        t.set_scrollback_lines(SCROLLBACK)
        t.set_cursor_shape(Vte.CursorShape.BLOCK)
        t.set_cursor_blink_mode(Vte.CursorBlinkMode.ON)
        t.set_mouse_autohide(True)
        t.set_audible_bell(False)
        t.set_bold_is_bright(True)
        t.set_allow_hyperlink(True)
        t.set_scroll_on_keystroke(True)
        t.set_scroll_on_output(False)
        t.set_hexpand(True)
        t.set_vexpand(True)
        try:
            rx = Vte.Regex.new_for_match(URL_RE, -1, PCRE2_MULTILINE)
            tag = t.match_add_regex(rx, 0)
            t.match_set_cursor_name(tag, "pointer")
        except GLib.Error:
            pass
        self.aplicar_paleta(win.app.paleta)

        clic = Gtk.GestureClick(button=0)
        clic.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        clic.connect("pressed", self._clic)
        t.add_controller(clic)
        t.connect("child-exited", lambda *_: GLib.idle_add(lambda: (win.cerrar_pestana(self, True), False)[1]))
        t.connect("bell", lambda *_: win.timbre())

        self.panel = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                        vscrollbar_policy=Gtk.PolicyType.AUTOMATIC)
        self.panel.add_css_class("nt-pane")
        self.panel.set_overlay_scrolling(True)
        self.panel.set_child(t)

        # botón de pestaña
        self.boton = Gtk.Button()
        self.boton.add_css_class("nt-tab")
        caja = Gtk.Box(spacing=7)
        self.lb_num = _lbl("TTY//01", "nt-tab-num")
        self.lb_txt = _lbl("…", "nt-tab-txt", ellipsize=Pango.EllipsizeMode.END, max_width_chars=18)
        self.bx = Gtk.Button(label="✕", tooltip_text="Cerrar pestaña (Ctrl+Shift+W)")
        self.bx.add_css_class("nt-x")
        self.bx.connect("clicked", lambda *_: win.cerrar_pestana(self))
        for w in (self.lb_num, self.lb_txt, self.bx):
            caja.append(w)
        self.boton.set_child(caja)
        self.boton.connect("clicked", lambda *_: win.ir(self))
        medio = Gtk.GestureClick(button=2)
        medio.connect("pressed", lambda *_: win.cerrar_pestana(self))
        self.boton.add_controller(medio)

        t.spawn_async(Vte.PtyFlags.DEFAULT, self.cwd, cmd or [_shell()],
                      ["NEURAL_TERM=1", "COLORTERM=truecolor", "TERM_PROGRAM=neural-term"],
                      GLib.SpawnFlags.DEFAULT, None, None, -1, None, self._lanzado)

    def _lanzado(self, _t, pid, error, *_):
        if error is not None:
            self.term.feed(f"\r\n[no se pudo iniciar {_shell()}: {error.message}]\r\n".encode())
            return
        self.pid = pid
        if "-GNUTLS" in Vte.get_features():
            # VTE sin GnuTLS escribe un aviso al lanzar; se borra antes de que el shell pinte el prompt
            self.term.feed(b"\x1b[2J\x1b[3J\x1b[H")
        self.actualizar()

    # ---- estado (programa en primer plano, carpeta) ----
    def actualizar(self) -> bool:
        """True si cambió algo visible."""
        antes = (self.nombre, self.ocupada, self.cwd)
        if self.pid:
            try:
                self.cwd = os.readlink(f"/proc/{self.pid}/cwd")
            except OSError:
                pass
            grupo = None
            pty = self.term.get_pty()
            if pty is not None:
                try:
                    grupo = os.tcgetpgrp(pty.get_fd())
                except OSError:
                    grupo = None
            self.ocupada = bool(grupo and grupo != self.pid)
            if self.ocupada:
                try:
                    self.nombre = Path(f"/proc/{grupo}/comm").read_text().strip()
                except OSError:
                    self.nombre = "?"
            else:
                casa = os.path.expanduser("~")
                self.nombre = "~" if self.cwd == casa else (os.path.basename(self.cwd) or "/")
        self.lb_txt.set_text(self.nombre)
        _on(self.boton, "ocupada", self.ocupada)
        return antes != (self.nombre, self.ocupada, self.cwd)

    def cwd_corto(self) -> str:
        casa = os.path.expanduser("~")
        return "~" + self.cwd[len(casa):] if self.cwd.startswith(casa) else self.cwd

    # ---- colores ----
    def aplicar_paleta(self, pal: dict) -> None:
        t = self.term
        ansi = [rgba(c) for c in pal.get("ansi", [])][:16]
        t.set_colors(rgba(pal.get("fg", "#e0e0e0")), rgba("rgba(0,0,0,0)"), ansi if len(ansi) == 16 else None)
        if pal.get("cursor"):
            t.set_color_cursor(rgba(pal["cursor"]))
            t.set_color_cursor_foreground(rgba(pal.get("cursorText", "#000000")))
        if pal.get("selection"):
            t.set_color_highlight(rgba(pal["selection"]))
            t.set_color_highlight_foreground(rgba(pal.get("selectionText", pal.get("fg", "#ffffff"))))

    def zoom(self, paso: int) -> None:
        self._escala = ESCALAS.index(1.0) if paso == 0 else max(0, min(len(ESCALAS) - 1, self._escala + paso))
        self.term.set_font_scale(ESCALAS[self._escala])

    # ---- ratón ----
    def _clic(self, gesto, _n, x, y):
        boton = gesto.get_current_button()
        mods = gesto.get_current_event_state() & Gtk.accelerator_get_default_mod_mask()
        if boton == 1 and mods == Gdk.ModifierType.CONTROL_MASK:
            uri = self.term.check_hyperlink_at(x, y) or self.term.check_match_at(x, y)[0]
            if uri:
                gesto.set_state(Gtk.EventSequenceState.CLAIMED)
                try:
                    Gio.AppInfo.launch_default_for_uri(uri, None)
                except GLib.Error as e:
                    self.win.aviso(f"NO SE PUDO ABRIR: {e.message}", True)
        elif boton == 3:
            gesto.set_state(Gtk.EventSequenceState.CLAIMED)
            self.win.menu(self, x, y)


# ---------------------------------------------------------------- ventana
class Ventana(Gtk.ApplicationWindow):
    def __init__(self, app: "App", cwd: str | None = None, cmd: list[str] | None = None):
        super().__init__(application=app, title="NEURAL//TERM")
        self.app = app
        self.theme = app.theme
        self.pestanas: list[Pestana] = []
        self.actual: Pestana | None = None
        self.armada_cierre = 0
        self.set_decorated(False)
        self.add_css_class("neural-term")
        self.set_default_size(1000, 640)

        self.marco = Frame(self, Gtk.Orientation.VERTICAL, 14, ("tl", "br"), True, None)
        caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_start=PAD + 10,
                       margin_end=PAD + 10, margin_top=PAD + 6, margin_bottom=PAD + 6, vexpand=True)
        self.marco.append(caja)

        # cabecera (arrastrar = mover, doble clic = maximizar)
        cab = Gtk.Box(spacing=10)
        cab.add_css_class("nt-head")
        tit = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
        tit.append(_lbl("SYS//TTY", "nt-code", xalign=0))
        tit.append(_lbl("NEURAL//TERM", "nt-title", xalign=0))
        cab.append(tit)
        self.barra_tabs = Gtk.Box(spacing=4, valign=Gtk.Align.END)
        scroll_tabs = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.EXTERNAL,
                                         vscrollbar_policy=Gtk.PolicyType.NEVER, hexpand=True)
        scroll_tabs.set_child(self.barra_tabs)
        scroll_tabs.set_propagate_natural_height(True)
        cab.append(scroll_tabs)
        mas = self._boton("+", "Nueva pestaña (Ctrl+Shift+T)", lambda: self.nueva_pestana())
        mas.set_valign(Gtk.Align.END)
        cab.append(mas)
        botones = Gtk.Box(spacing=2, valign=Gtk.Align.START)
        botones.append(self._boton("─", "Minimizar", self.minimize))
        botones.append(self._boton("□", "Maximizar", self._max))
        self.b_cerrar = self._boton("✕", "Cerrar ventana", self.close)
        self.b_cerrar.add_css_class("cerrar")
        botones.append(self.b_cerrar)
        cab.append(botones)
        asa = Gtk.WindowHandle(child=cab)
        caja.append(asa)

        self.pila = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=110,
                              vexpand=True, hexpand=True)
        caja.append(self.pila)

        # buscar
        self.buscar = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_UP, transition_duration=120)
        fila = Gtk.Box(spacing=6)
        fila.add_css_class("nt-buscar")
        fila.append(_lbl("BUSCAR//", "nt-code"))
        self.entrada = Gtk.Entry(hexpand=True, placeholder_text="texto (sin distinguir mayúsculas)")
        self.entrada.connect("changed", lambda *_: self._regex())
        self.entrada.connect("activate", lambda *_: self._buscar(-1))
        fila.append(self.entrada)
        fila.append(self._boton("↑", "Anterior (Enter)", lambda: self._buscar(-1)))
        fila.append(self._boton("↓", "Siguiente (Shift+Enter)", lambda: self._buscar(1)))
        fila.append(self._boton("✕", "Cerrar (Esc)", self._cerrar_buscar))
        self.buscar.set_child(fila)
        caja.append(self.buscar)

        # pie
        pie = Gtk.Box(spacing=14)
        pie.add_css_class("nt-foot")
        self.p_proc = _lbl("", "nt-code", xalign=0)
        self.p_cwd = _lbl("", "nt-code", xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.START)
        self.p_msg = _lbl("", "nt-code", xalign=1)
        self.p_tam = _lbl("", "nt-code", xalign=1)
        self.p_hora = _lbl("", "nt-code", xalign=1)
        for w in (self.p_proc, self.p_cwd, self.p_msg, self.p_tam, self.p_hora):
            pie.append(w)
        caja.append(pie)
        self.set_child(self.marco)

        # redimensionar desde el borde (margen del brillo)
        clic = Gtk.GestureClick(button=1)
        clic.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        clic.connect("pressed", self._borde_pulsado)
        self.add_controller(clic)
        mov = Gtk.EventControllerMotion()
        mov.connect("motion", lambda _c, x, y: self.set_cursor_from_name(self._cursor_borde(x, y)))
        mov.connect("leave", lambda *_: self.set_cursor_from_name(None))
        self.add_controller(mov)

        teclas = Gtk.EventControllerKey()
        teclas.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        teclas.connect("key-pressed", self._tecla)
        self.add_controller(teclas)
        self.connect("close-request", self._al_cerrar)

        self.nueva_pestana(cwd, cmd)
        self.reloj = GLib.timeout_add(1000, self._tick)
        self._tick()

    # ---- piezas ----
    def _boton(self, texto, tip, accion) -> Gtk.Button:
        b = Gtk.Button(label=texto, tooltip_text=tip)
        b.add_css_class("nt-btn")
        b.connect("clicked", lambda *_: accion())
        return b

    def _max(self):
        self.unmaximize() if self.is_maximized() else self.maximize()

    # ---- pestañas ----
    def nueva_pestana(self, cwd: str | None = None, cmd: list[str] | None = None):
        if cwd is None and self.actual is not None:
            cwd = self.actual.cwd
        p = Pestana(self, cwd, cmd)
        self.pestanas.append(p)
        self.barra_tabs.append(p.boton)
        self.pila.add_child(p.panel)
        self._numerar()
        self.ir(p)

    def ir(self, p: Pestana):
        self.actual = p
        self.pila.set_visible_child(p.panel)
        for q in self.pestanas:
            _on(q.boton, "on", q is p)
        p.term.grab_focus()
        self._pie()

    def _numerar(self):
        for i, p in enumerate(self.pestanas, 1):
            p.lb_num.set_text(f"TTY//{i:02d}")

    def mover(self, paso: int):
        if self.actual is None or len(self.pestanas) < 2:
            return
        i = self.pestanas.index(self.actual)
        j = (i + paso) % len(self.pestanas)
        self.pestanas.insert(j, self.pestanas.pop(i))
        self.barra_tabs.reorder_child_after(self.actual.boton,
                                            self.pestanas[j - 1].boton if j > 0 else None)
        self._numerar()

    def saltar(self, paso: int):
        if self.actual is not None and self.pestanas:
            i = self.pestanas.index(self.actual)
            self.ir(self.pestanas[(i + paso) % len(self.pestanas)])

    def cerrar_pestana(self, p: Pestana, forzar: bool = False):
        if p not in self.pestanas:
            return
        p.actualizar()
        if p.ocupada and not forzar and time.monotonic() > p.armada:
            p.armada = time.monotonic() + 3
            p.bx.add_css_class("armed")
            self.ir(p)
            self.aviso(f"{p.nombre.upper()} SIGUE EN MARCHA · ✕ OTRA VEZ PARA CERRAR", True)
            GLib.timeout_add(3000, lambda: (p.bx.remove_css_class("armed"), False)[1])
            return
        i = self.pestanas.index(p)
        self.pestanas.remove(p)
        self.barra_tabs.remove(p.boton)
        self.pila.remove(p.panel)
        if p.pid and not forzar:
            try:
                os.kill(p.pid, 1)  # SIGHUP, como al cerrar una terminal
            except OSError:
                pass
        if not self.pestanas:
            self.destroy()
            return
        self._numerar()
        if self.actual is p:
            self.ir(self.pestanas[min(i, len(self.pestanas) - 1)])

    def _al_cerrar(self, *_):
        ocupadas = [p for p in self.pestanas if (p.actualizar(), p.ocupada)[1]]
        if ocupadas and time.monotonic() > self.armada_cierre:
            self.armada_cierre = time.monotonic() + 3
            self.b_cerrar.add_css_class("armed")
            nombres = ", ".join(p.nombre for p in ocupadas[:3])
            self.aviso(f"EN MARCHA: {nombres.upper()} · CIERRA OTRA VEZ PARA TERMINAR", True)
            GLib.timeout_add(3000, lambda: (self.b_cerrar.remove_css_class("armed"), False)[1])
            return True
        for p in self.pestanas:
            if p.pid:
                try:
                    os.kill(p.pid, 1)
                except OSError:
                    pass
        GLib.source_remove(self.reloj)
        return False

    # ---- pie / avisos ----
    def _tick(self):
        for p in self.pestanas:
            p.actualizar()
        self._pie()  # también refresca columnas×filas tras redimensionar
        self.p_hora.set_text(time.strftime("%H:%M:%S"))
        return GLib.SOURCE_CONTINUE

    def _pie(self):
        p = self.actual
        if p is None:
            return
        self.p_proc.set_text(("▶ " if p.ocupada else "◇ ") + p.nombre.upper())
        _on(self.p_proc, "on", p.ocupada)
        self.p_cwd.set_text(p.cwd_corto())
        self.p_tam.set_text(f"{p.term.get_column_count()}×{p.term.get_row_count()}  ·  {len(self.pestanas)} TTY")
        self.set_title(f"{p.nombre} · NEURAL//TERM")

    def aviso(self, texto: str, warn: bool = False, ms: int = 3000):
        self.p_msg.set_text(texto)
        _on(self.p_msg, "warn", warn)
        if getattr(self, "_aviso_id", 0):
            GLib.source_remove(self._aviso_id)
        self._aviso_id = GLib.timeout_add(ms, lambda: (self.p_msg.set_text(""), setattr(self, "_aviso_id", 0),
                                                       False)[2])

    def timbre(self):
        """Campana visual: el marco destella con el color de aviso."""
        base = self.app.theme
        self.theme = dict(base, accent=base["warn"])
        self.marco.queue_draw()

        def volver():
            self.theme = self.app.theme
            self.marco.queue_draw()
            return False

        GLib.timeout_add(160, volver)

    def nuevo_tema(self):
        self.theme = self.app.theme
        self.marco.queue_draw()
        for p in self.pestanas:
            p.aplicar_paleta(self.app.paleta)

    # ---- buscar ----
    def abrir_buscar(self):
        self.buscar.set_reveal_child(True)
        sel = self.actual.term.get_text_selected(Vte.Format.TEXT) if self.actual else None
        if sel and "\n" not in sel:
            self.entrada.set_text(sel)
        self.entrada.grab_focus()

    def _cerrar_buscar(self):
        self.buscar.set_reveal_child(False)
        if self.actual:
            self.actual.term.search_set_regex(None, 0)
            self.actual.term.grab_focus()

    def _regex(self):
        if self.actual is None:
            return
        texto = self.entrada.get_text()
        self.entrada.remove_css_class("nada")
        if not texto:
            self.actual.term.search_set_regex(None, 0)
            return
        try:
            rx = Vte.Regex.new_for_search(GLib.Regex.escape_string(texto, -1), -1,
                                          PCRE2_CASELESS | PCRE2_MULTILINE)
        except GLib.Error:
            return
        self.actual.term.search_set_wrap_around(True)
        self.actual.term.search_set_regex(rx, 0)
        self._buscar(-1)

    def _buscar(self, dir_: int):
        if self.actual is None or not self.entrada.get_text():
            return
        t = self.actual.term
        hallado = t.search_find_previous() if dir_ < 0 else t.search_find_next()
        _on(self.entrada, "nada", not hallado)

    # ---- menú contextual ----
    def menu(self, p: Pestana, x: float, y: float):
        pop = Gtk.Popover(has_arrow=False)
        pop.add_css_class("nt-menu")
        caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        uri = p.term.check_hyperlink_at(x, y) or p.term.check_match_at(x, y)[0]
        hay_sel = p.term.get_has_selection()
        items = []
        if uri:
            items.append(("ABRIR ENLACE", "CTRL+CLIC", lambda: Gio.AppInfo.launch_default_for_uri(uri, None)))
            items.append(("COPIAR ENLACE", "", lambda: self.get_clipboard().set(uri)))
        if hay_sel:
            items.append(("COPIAR", "CTRL+SHIFT+C", lambda: p.term.copy_clipboard_format(Vte.Format.TEXT)))
        items += [("PEGAR", "CTRL+SHIFT+V", p.term.paste_clipboard),
                  ("SELECCIONAR TODO", "", p.term.select_all),
                  ("BUSCAR", "CTRL+SHIFT+F", self.abrir_buscar),
                  ("NUEVA PESTAÑA", "CTRL+SHIFT+T", lambda: self.nueva_pestana()),
                  ("NUEVA VENTANA", "CTRL+SHIFT+N", lambda: self.app.nueva_ventana(p.cwd)),
                  ("LIMPIAR", "", lambda: p.term.reset(True, True)),
                  ("CERRAR PESTAÑA", "CTRL+SHIFT+W", lambda: self.cerrar_pestana(p))]
        for texto, atajo, accion in items:
            b = Gtk.Button()
            b.add_css_class("nt-item")
            fila = Gtk.Box(spacing=18)
            fila.append(_lbl(texto, "nt-item-t", xalign=0, hexpand=True))
            fila.append(_lbl(atajo, "nt-item-k", xalign=1))
            b.set_child(fila)
            b.connect("clicked", lambda _b, a=accion: (pop.popdown(), a()))
            caja.append(b)
        pop.set_child(caja)
        pop.set_parent(p.term)
        r = Gdk.Rectangle()
        r.x, r.y, r.width, r.height = int(x), int(y), 1, 1
        pop.set_pointing_to(r)
        pop.connect("closed", lambda *_: GLib.idle_add(lambda: (pop.unparent(), False)[1]))
        pop.popup()

    # ---- teclado ----
    def _tecla(self, _c, kv, _code, estado):
        mods = estado & Gtk.accelerator_get_default_mod_mask()
        C, S, A = Gdk.ModifierType.CONTROL_MASK, Gdk.ModifierType.SHIFT_MASK, Gdk.ModifierType.ALT_MASK
        k = Gdk.keyval_to_lower(kv)
        p = self.actual
        if self.buscar.get_reveal_child() and self.entrada.has_focus():
            if kv == Gdk.KEY_Escape:
                self._cerrar_buscar()
                return True
            if kv in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and mods == S:
                self._buscar(1)
                return True
        if mods == C | S:
            acciones = {Gdk.KEY_t: lambda: self.nueva_pestana(), Gdk.KEY_w: lambda: self.cerrar_pestana(p),
                        Gdk.KEY_n: lambda: self.app.nueva_ventana(p.cwd if p else None),
                        Gdk.KEY_c: lambda: p.term.copy_clipboard_format(Vte.Format.TEXT),
                        Gdk.KEY_v: lambda: p.term.paste_clipboard(), Gdk.KEY_f: self.abrir_buscar,
                        Gdk.KEY_Page_Up: lambda: self.mover(-1), Gdk.KEY_Page_Down: lambda: self.mover(1),
                        Gdk.KEY_ISO_Left_Tab: lambda: self.saltar(-1), Gdk.KEY_Tab: lambda: self.saltar(-1)}
            if k in acciones and p is not None:
                acciones[k]()
                return True
        if mods == C:
            acciones = {Gdk.KEY_Page_Up: -1, Gdk.KEY_Page_Down: 1, Gdk.KEY_Tab: 1}
            if k in acciones:
                self.saltar(acciones[k])
                return True
        if mods in (C, C | S) and p is not None:
            if k in (Gdk.KEY_plus, Gdk.KEY_equal, Gdk.KEY_KP_Add):
                p.zoom(1)
                return True
            if k in (Gdk.KEY_minus, Gdk.KEY_underscore, Gdk.KEY_KP_Subtract):
                p.zoom(-1)
                return True
            if k in (Gdk.KEY_0, Gdk.KEY_KP_0, Gdk.KEY_parenright):
                p.zoom(0)
                return True
        if mods == A and Gdk.KEY_1 <= kv <= Gdk.KEY_9:
            i = kv - Gdk.KEY_1
            if i < len(self.pestanas):
                self.ir(self.pestanas[i])
            return True
        if kv == Gdk.KEY_F11 and not mods:
            self.unfullscreen() if self.is_fullscreen() else self.fullscreen()
            return True
        return False

    # ---- redimensionar ----
    def _borde(self, x, y):
        if self.is_maximized() or self.is_fullscreen():
            return None
        w, h = self.get_width(), self.get_height()
        n, s, o, e = y < BORDE, y > h - BORDE, x < BORDE, x > w - BORDE
        E = Gdk.SurfaceEdge
        return {(1, 0, 1, 0): E.NORTH_WEST, (1, 0, 0, 1): E.NORTH_EAST, (0, 1, 1, 0): E.SOUTH_WEST,
                (0, 1, 0, 1): E.SOUTH_EAST, (1, 0, 0, 0): E.NORTH, (0, 1, 0, 0): E.SOUTH,
                (0, 0, 1, 0): E.WEST, (0, 0, 0, 1): E.EAST}.get((int(n), int(s), int(o), int(e)))

    def _cursor_borde(self, x, y):
        b = self._borde(x, y)
        if b is None:
            return None
        E = Gdk.SurfaceEdge
        return {E.NORTH_WEST: "nw-resize", E.NORTH_EAST: "ne-resize", E.SOUTH_WEST: "sw-resize",
                E.SOUTH_EAST: "se-resize", E.NORTH: "n-resize", E.SOUTH: "s-resize",
                E.WEST: "w-resize", E.EAST: "e-resize"}[b]

    def _borde_pulsado(self, gesto, _n, x, y):
        b = self._borde(x, y)
        if b is None:
            return
        gesto.set_state(Gtk.EventSequenceState.CLAIMED)
        superficie = self.get_surface()
        if isinstance(superficie, Gdk.Toplevel):
            superficie.begin_resize(b, gesto.get_device(), 1, x, y, gesto.get_current_event_time())


# ---------------------------------------------------------------- app
class App(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.theme = None
        self.paleta: dict = {}
        self.css = None

    def do_startup(self):
        Gtk.Application.do_startup(self)
        self._tema()
        self.mons = []
        for ruta in (CONFIG_PATH, PALETA):
            m = Gio.File.new_for_path(str(ruta)).monitor_file(Gio.FileMonitorFlags.NONE, None)
            m.connect("changed", lambda *_: GLib.timeout_add(300, lambda: (self._tema(), False)[1]))
            self.mons.append(m)

    def _tema(self):
        themes = load_themes()
        theme = themes.get(load_board().get("theme", "red"), next(iter(themes.values())))
        paleta = _leer_paleta() or {"fg": css_color(theme["text"]), "cursor": css_color(theme["alt"])}
        if theme == self.theme and paleta == self.paleta:
            return
        self.theme, self.paleta = theme, paleta
        display = Gdk.Display.get_default()
        if self.css is not None:
            Gtk.StyleContext.remove_provider_for_display(display, self.css)
        self.css = Gtk.CssProvider()
        self.css.load_from_string(_css(theme))
        Gtk.StyleContext.add_provider_for_display(display, self.css, Gtk.STYLE_PROVIDER_PRIORITY_USER + 1)
        for w in self.get_windows():
            if isinstance(w, Ventana):
                w.nuevo_tema()

    def nueva_ventana(self, cwd: str | None = None, cmd: list[str] | None = None):
        Ventana(self, cwd, cmd).present()

    def do_command_line(self, linea):
        args = linea.get_arguments()[1:]
        cmd = None
        if "-e" in args:  # -e PROGRAMA ARGS…: ejecutarlo en la pestaña (se cierra al salir)
            i = args.index("-e")
            cmd, args = args[i + 1:] or None, args[:i]
        cwd = linea.get_cwd()
        if "--dir" in args and args.index("--dir") + 1 < len(args):
            cwd = args[args.index("--dir") + 1]
        self.nueva_ventana(cwd if cwd and os.path.isdir(cwd) else None, cmd)
        return 0


if __name__ == "__main__":
    try:
        ctypes.CDLL(None).prctl(15, PROC.encode(), 0, 0, 0)
    except (OSError, AttributeError):
        pass
    GLib.set_prgname(APP_ID)
    register_fonts()
    sys.exit(App().run(sys.argv))
