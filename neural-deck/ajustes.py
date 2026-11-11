#!/usr/bin/env python3
"""NEURAL//AJUSTES: configuración de NEURAL//DECK, en una ventana NEURAL normal
(ventana.py: marco HUD, minimizar / maximizar / cerrar, redimensionar).

Escribe ~/.config/live-wallpaper/ajustes.json (una sección por pieza) y cada
pieza vigila el archivo y se aplica sola:
    inactividad -> inactividad.py (neural-idle)
    energia     -> energia.py     (neural-power: powerprofilesctl / neural-energia)
    mosaico     -> mosaico.py     (neural-tile)
    pantalla    -> pantalla.py    (reinicia wlsunset: protector de la vista)
    apariencia  -> decoracion.py  (barra de título y borde de las demás ventanas)
Otras páginas escriben en su sitio:
    animaciones, teclado/ratón -> wayfire.ini [animate]/[neural-holo]/[input] (+ IPC)
    tema        -> board.json (lo siguen el fondo y todas las apps NEURAL)
    red         -> NetworkManager (red.py, nmcli)
    sonido      -> PipeWire (wpctl)
Páginas: INACTIVIDAD · ENERGÍA · BATERÍA · RED · PANTALLA · SONIDO · TECLADO Y RATÓN ·
APARIENCIA · MOSAICO · ANIMACIONES · SISTEMA. Esc o ✕ cierra.
`ajustes.py --pagina NOMBRE` abre (o lleva) a esa página.
Instancia única: abrirla otra vez la trae al frente."""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import animaciones  # noqa: E402
import decoracion  # noqa: E402
import energia  # noqa: E402
import entrada  # noqa: E402
import iconos  # noqa: E402
import inactividad  # noqa: E402
import mosaico  # noqa: E402
import pantalla  # noqa: E402
import red  # noqa: E402
import servicios  # noqa: E402
import sistema  # noqa: E402
import temas_apps  # noqa: E402
from board import CONFIG_PATH, load_board, save_board  # noqa: E402
from estilo import MONO, PAD, SANS, WEB_DIR, Frame, css_color, load_themes  # noqa: E402
from ventana import AppNeural, VentanaNeural, nombrar_proceso  # noqa: E402

PROC = "neural-ajustes"
APP_ID = "org.local.NeuralAjustes"
MINUTOS = (0, 1, 2, 3, 5, 10, 15, 30, 60)
MINUTOS_SUSP = (0, 10, 15, 20, 30, 45, 60, 120)
NOMBRE_PERFIL = {"ahorro": "AHORRO", "normal": "NORMAL", "rendimiento": "RENDIMIENTO"}
DESC_PERFIL = {
    "ahorro": "Firmware low-power · CPU en preferencia «power». Más batería, menos calor y ruido.",
    "normal": "Firmware balanced · CPU «balance_performance». Lo de siempre.",
    "rendimiento": "Firmware performance · gobernador performance. Todo el potencial de la T14; más consumo.",
}
UMBRALES = [  # (inicio, fin, título, descripción)
    (75, 80, "MÁXIMA VIDA", "Carga hasta 80 %. Para cuando pasa casi siempre enchufada."),
    (85, 90, "EQUILIBRIO", "Carga hasta 90 %. Buena autonomía cuidando la batería."),
    (95, 100, "CARGA COMPLETA", "Hasta 100 %. Para un viaje o un día sin enchufe."),
]
ESTADOS_BAT = {"Charging": "CARGANDO", "Discharging": "DESCARGANDO", "Full": "LLENA",
               "Not charging": "EN PAUSA (UMBRAL)", "Unknown": "DESCONOCIDO"}


def _css(t: dict) -> str:
    acc, alt, warn, text, muted = t["accent"], t["alt"], t["warn"], t["text"], t["muted"]
    return f"""
.aj-code {{ font-family: "{MONO}"; font-size: 11px; letter-spacing: 2px; color: {css_color(muted)}; }}
.aj-h {{ font-family: "{SANS}"; font-weight: 700; font-size: 18px; letter-spacing: 3px; color: {css_color(alt)}; }}
.aj-txt {{ font-family: "{SANS}"; font-weight: 600; font-size: 14px; color: {css_color(text)}; }}
.aj-small {{ font-family: "{SANS}"; font-weight: 500; font-size: 13px; color: {css_color(muted)}; }}
.aj-key {{ font-family: "{MONO}"; font-size: 11px; letter-spacing: 1px; color: {css_color(muted)}; }}
.aj-val {{ font-family: "{SANS}"; font-weight: 700; font-size: 20px; color: {css_color(text)}; }}
.aj-val.ok {{ color: {css_color(alt)}; }}
.aj-val.warn {{ color: {css_color(warn)}; }}
.aj-nav {{ all: unset; padding: 9px 16px; border-left: 2px solid transparent; color: {css_color(text)};
          font-family: "{SANS}"; font-weight: 700; font-size: 15px; letter-spacing: 2px; }}
.aj-nav:hover {{ background: {css_color(acc, 0.12)}; }}
.aj-nav.on {{ border-left-color: {css_color(alt)}; color: {css_color(alt)}; background: {css_color(acc, 0.16)}; }}
.aj-chip {{ all: unset; padding: 5px 12px; border: 1px solid {css_color(acc, 0.55)}; background: rgba(0,0,0,0.55);
           color: {css_color(text)}; font-family: "{MONO}"; font-size: 12px; letter-spacing: 1px; }}
.aj-chip:hover {{ background: {css_color(acc, 0.25)}; }}
.aj-chip.on {{ background: {css_color(acc)}; border-color: {css_color(acc)}; color: #0a0204; }}
.aj-card {{ all: unset; padding: 12px 14px; border: 1px solid {css_color(acc, 0.45)}; background: rgba(0,0,0,0.55); }}
.aj-card:hover {{ background: {css_color(acc, 0.14)}; }}
.aj-card.on {{ border-color: {css_color(alt)}; background: {css_color(alt, 0.12)};
              box-shadow: 0 0 12px {css_color(alt, 0.35)}; }}
.aj-card .aj-ct {{ font-family: "{SANS}"; font-weight: 700; font-size: 16px; letter-spacing: 2px; color: {css_color(text)}; }}
.aj-card.on .aj-ct {{ color: {css_color(alt)}; }}
.aj-close {{ all: unset; padding: 4px 12px; border: 1px solid {css_color(acc, 0.6)}; color: {css_color(text)};
            font-family: "{SANS}"; font-weight: 700; font-size: 16px; }}
.aj-close:hover {{ background: {css_color(warn)}; border-color: {css_color(warn)}; color: #000; }}
.aj-warn {{ font-family: "{MONO}"; font-size: 12px; color: {css_color(warn)}; }}
scrolledwindow.aj-scroll, scrolledwindow.aj-scroll > viewport {{ background: transparent; }}
.aj-entry {{ background: rgba(0,0,0,0.6); border: 1px solid {css_color(acc, 0.55)}; border-radius: 0;
            color: {css_color(text)}; font-family: "{MONO}"; font-size: 13px; padding: 2px 8px; min-height: 28px;
            box-shadow: none; outline: none; }}
.aj-entry:focus-within {{ border-color: {css_color(alt)}; }}
.aj-fila {{ padding: 8px 12px; border: 1px solid {css_color(acc, 0.25)}; background: rgba(0,0,0,0.45); }}
.aj-fila.on {{ border-color: {css_color(alt)}; background: {css_color(alt, 0.10)}; }}
.aj-fila.on .aj-txt {{ color: {css_color(alt)}; }}
.aj-ok {{ font-family: "{MONO}"; font-size: 12px; color: {css_color(alt)}; }}
scale.aj-scale trough {{ min-height: 6px; background: {css_color(acc, 0.22)}; border: none; border-radius: 0; }}
scale.aj-scale highlight {{ background: {css_color(acc)}; border-radius: 0; }}
scale.aj-scale slider {{ min-width: 16px; min-height: 16px; margin: -6px; background: {css_color(alt)};
                        border: none; border-radius: 0; box-shadow: 0 0 8px {css_color(alt, 0.5)}; }}
"""


def _lbl(text: str, cls: str, **kw) -> Gtk.Label:
    lb = Gtk.Label(label=text, xalign=0, **kw)
    lb.add_css_class(cls)
    return lb


def _on(widget: Gtk.Widget, activo: bool) -> None:
    (widget.add_css_class if activo else widget.remove_css_class)("on")


def _boton(texto: str, accion, cls: str = "aj-chip") -> Gtk.Button:
    b = Gtk.Button(label=texto, valign=Gtk.Align.CENTER)
    b.add_css_class(cls)
    b.connect("clicked", lambda *_: accion())
    return b


def _entrada(pista: str, ancho: int = 22, clave: bool = False) -> Gtk.Widget:
    e = Gtk.PasswordEntry(show_peek_icon=True) if clave else Gtk.Entry(placeholder_text=pista)
    if clave:
        e.set_property("placeholder-text", pista)
    e.set_size_request(ancho * 9, -1)
    e.add_css_class("aj-entry")
    return e


def _vaciar(box: Gtk.Box) -> None:
    c = box.get_first_child()
    while c is not None:
        sig = c.get_next_sibling()
        box.remove(c)
        c = sig


def _en_hilo(trabajo, al_terminar) -> None:
    """Corre `trabajo()` fuera de GTK (nmcli puede tardar segundos) y entrega
    (resultado, error) a `al_terminar` ya en el hilo de GTK."""
    def correr():
        try:
            res, err = trabajo(), None
        except (red.RedError, OSError, ValueError) as e:
            res, err = None, e
        GLib.idle_add(lambda: (al_terminar(res, err), False)[1])
    threading.Thread(target=correr, daemon=True).start()


def _texto_error(err: Exception) -> str:
    if isinstance(err, red.RedError):
        return red.texto_error(err).upper()
    return f"ERROR: {str(err).strip() or err.__class__.__name__}"[:140]


class Chips(Gtk.FlowBox):
    """Fila de opciones exclusivas (como botones de radio); salta de línea si no cabe."""

    def __init__(self, opciones: list[tuple[object, str]], al_elegir):
        super().__init__(column_spacing=6, row_spacing=6, selection_mode=Gtk.SelectionMode.NONE,
                         max_children_per_line=12, homogeneous=False)
        self.botones = {}
        for valor, texto in opciones:
            b = Gtk.Button(label=texto)
            b.add_css_class("aj-chip")
            b.connect("clicked", lambda _b, v=valor: al_elegir(v))
            self.append(b)
            self.botones[valor] = b

    def marcar(self, valor):
        for v, b in self.botones.items():
            _on(b, v == valor)


class ChipsMulti(Chips):
    """Como Chips, pero cada opción se enciende / apaga por separado."""

    def marcar(self, valores):
        for v, b in self.botones.items():
            _on(b, v in valores)


SI_NO = [(True, "SÍ"), (False, "NO")]


class Deslizador(Gtk.Box):
    """Escala con su valor al lado. Manda `al_cambiar(valor)` como mucho cada 60 ms
    y no se deja pisar por refrescar() mientras la estás moviendo."""

    def __init__(self, minimo, maximo, paso, al_cambiar, formato=lambda v: f"{v:.0f} %"):
        super().__init__(spacing=16)
        self.al_cambiar, self.formato = al_cambiar, formato
        self.tocado = 0
        self.pendiente = 0
        self.sin_eco = False
        self.escala = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, minimo, maximo, paso)
        self.escala.set_hexpand(True)
        self.escala.set_draw_value(False)
        self.escala.add_css_class("aj-scale")
        self.escala.connect("value-changed", self._cambio)
        self.append(self.escala)
        self.valor = _lbl("—", "aj-val")
        self.valor.set_size_request(90, -1)
        self.append(self.valor)

    def _cambio(self, escala):
        self.valor.set_text(self.formato(escala.get_value()))
        if self.sin_eco:
            return
        self.tocado = GLib.get_monotonic_time()
        if not self.pendiente:
            self.pendiente = GLib.timeout_add(60, self._mandar)

    def _mandar(self):
        self.pendiente = 0
        self.al_cambiar(self.escala.get_value())
        return False

    def poner(self, v):
        """Valor leído del sistema (no lo vuelve a mandar)."""
        if v is None or GLib.get_monotonic_time() - self.tocado < 1_500_000:
            return
        self.sin_eco = True
        self.escala.set_value(v)
        self.sin_eco = False
        self.valor.set_text(self.formato(v))


class Tarjeta(Gtk.Button):
    def __init__(self, titulo: str, desc: str, al_pulsar):
        super().__init__()
        self.add_css_class("aj-card")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        t = _lbl(titulo, "aj-ct")
        box.append(t)
        d = _lbl(desc, "aj-small", wrap=True)
        d.set_max_width_chars(34)
        box.append(d)
        self.set_child(box)
        self.connect("clicked", lambda *_: al_pulsar())


class Seccion(Frame):
    """Un bloque con marco HUD, título y contenido."""

    def __init__(self, owner, tab: str, titulo: str, sub: str = ""):
        super().__init__(owner, Gtk.Orientation.VERTICAL, 12, ("tl", "br"), True, tab)
        self.cuerpo = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_start=PAD + 22,
                              margin_end=PAD + 22, margin_top=PAD + 26, margin_bottom=PAD + 18)
        self.cuerpo.append(_lbl(titulo, "aj-h"))
        if sub:
            self.cuerpo.append(_lbl(sub, "aj-small", wrap=True))
        super().append(self.cuerpo)

    def add(self, w: Gtk.Widget):
        self.cuerpo.append(w)


# ---------- páginas ----------
class PaginaInactividad(Gtk.Box):
    def __init__(self, win):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        s = Seccion(win, "IDLE// 01", "INACTIVIDAD", "Qué pasa cuando dejas de usar la laptop. "
                    "La tapa siempre bloquea. Los vídeos que estén sonando frenan el apagado y la suspensión.")
        opts = [(m, "NUNCA" if m == 0 else f"{m} MIN") for m in MINUTOS]
        s.add(_lbl("BLOQUEAR LA SESIÓN TRAS", "aj-key"))
        self.bloqueo = Chips(opts, lambda v: self._poner("bloqueo_min", v))
        s.add(self.bloqueo)
        s.add(_lbl("APAGAR LA PANTALLA TRAS", "aj-key"))
        self.pantalla = Chips(opts, lambda v: self._poner("pantalla_min", v))
        s.add(self.pantalla)
        s.add(_lbl("SUSPENDER TRAS", "aj-key"))
        self.suspender = Chips([(m, "NUNCA" if m == 0 else (f"{m // 60} H" if m >= 60 else f"{m} MIN"))
                                for m in MINUTOS_SUSP], lambda v: self._poner("suspender_min", v))
        s.add(self.suspender)
        s.add(_lbl("BLOQUEAR ANTES DE SUSPENDER", "aj-key"))
        self.al_suspender = Chips(SI_NO, lambda v: self._poner("bloquear_al_suspender", v))
        s.add(self.al_suspender)
        s.add(_lbl("Con NO, al despertar la sesión queda abierta. La tapa siempre bloquea.", "aj-small", wrap=True))
        fila = Gtk.Box(spacing=12, margin_top=8)
        fila.append(_boton("🔒 BLOQUEAR AHORA", lambda: subprocess.Popen(
            ["python3", str(Path(__file__).with_name("bloqueo.py"))], start_new_session=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)))
        self.estado = _lbl("", "aj-key", hexpand=True)
        fila.append(self.estado)
        s.add(fila)
        self.aviso = _lbl("", "aj-warn", wrap=True)
        s.add(self.aviso)
        self.append(s)
        self.refrescar()

    def _poner(self, clave, valor):
        cfg = inactividad.leer()
        cfg[clave] = valor
        energia.guardar_seccion("inactividad", cfg)
        self.refrescar()

    def refrescar(self):
        cfg = inactividad.leer()
        self.bloqueo.marcar(cfg["bloqueo_min"])
        self.pantalla.marcar(cfg["pantalla_min"])
        self.suspender.marcar(cfg["suspender_min"])
        self.al_suspender.marcar(cfg["bloquear_al_suspender"])
        vivo = sistema_proceso("neural-idle")
        self.estado.set_text("NEURAL//IDLE: " + ("ACTIVO" if vivo else "DETENIDO (reinícialo en SISTEMA)"))
        b, p = cfg["bloqueo_min"], cfg["pantalla_min"]
        self.aviso.set_text("AVISO: sin bloqueo automático, cualquiera puede usar la sesión si te vas."
                            if b == 0 else ("NOTA: la pantalla se apaga antes de bloquear." if p and p < b else ""))


class PaginaEnergia(Gtk.Box):
    def __init__(self, win):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        s = Seccion(win, "PWR// 02", "PERFIL DE ENERGÍA",
                    "FIJO = siempre el mismo. AUTOMÁTICO = uno enchufada y otro con batería.")
        s.add(_lbl("MODO", "aj-key"))
        self.modo = Chips([("fijo", "FIJO"), ("auto", "AUTOMÁTICO")], lambda v: self._poner(modo=v))
        s.add(self.modo)

        self.fijo = Gtk.Box(spacing=10, homogeneous=True, margin_top=6)
        self.tarjetas = {}
        for p in energia.PERFILES:
            t = Tarjeta(NOMBRE_PERFIL[p], DESC_PERFIL[p], lambda p=p: self._poner(perfil=p))
            self.fijo.append(t)
            self.tarjetas[p] = t
        s.add(self.fijo)

        self.auto = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=6)
        opts = [(p, NOMBRE_PERFIL[p]) for p in energia.PERFILES]
        self.auto.append(_lbl("ENCHUFADA", "aj-key"))
        self.corr = Chips(opts, lambda v: self._poner(perfil_corriente=v))
        self.auto.append(self.corr)
        self.auto.append(_lbl("CON BATERÍA", "aj-key"))
        self.bat = Chips(opts, lambda v: self._poner(perfil_bateria=v))
        self.auto.append(self.bat)
        s.add(self.auto)

        self.ahora = _lbl("", "aj-key")
        self.ahora.set_margin_top(8)
        s.add(self.ahora)
        self.aviso = _lbl("", "aj-warn", wrap=True)
        s.add(self.aviso)
        self.append(s)
        self.refrescar()

    def _poner(self, **cambios):
        cfg = energia.leer()
        cfg.update(cambios)
        energia.guardar_seccion("energia", cfg)
        self.refrescar()

    def refrescar(self):
        cfg, est = energia.leer(), energia.estado()
        self.modo.marcar(cfg["modo"])
        self.fijo.set_visible(cfg["modo"] == "fijo")
        self.auto.set_visible(cfg["modo"] == "auto")
        for p, t in self.tarjetas.items():
            _on(t, p == cfg["perfil"])
        self.corr.marcar(cfg["perfil_corriente"])
        self.bat.marcar(cfg["perfil_bateria"])
        self.ahora.set_text(
            f"AHORA: {NOMBRE_PERFIL.get(est['perfil'], est['firmware'].upper() or '?')}  ·  firmware {est['firmware']}"
            f"  ·  {est['governor']} / {est['epp']}  ·  {'ENCHUFADA' if est['corriente'] else 'CON BATERÍA'}"
            + ("  ·  vía power-profiles-daemon" if est["ppd"] else ""))
        avisos = []
        if not est["ppd"] and not est["ayudante"]:
            avisos.append("No hay power-profiles-daemon ni /usr/libexec/neural-energia: no se puede aplicar.")
        elif not sistema_proceso("neural-power"):
            avisos.append("NEURAL//POWER (energia.py) no está corriendo: se guardará pero no se aplicará "
                          "hasta que arranque.")
        elif energia.perfil_deseado(cfg) != est["perfil"]:
            avisos.append("APLICANDO…")
        self.aviso.set_text("  ".join(avisos))


class PaginaBateria(Gtk.Box):
    def __init__(self, win):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        s = Seccion(win, "BAT// 03", "BATERÍA")
        self.grid = Gtk.Grid(column_spacing=36, row_spacing=12)
        self.vals = {}
        campos = [("carga", "CARGA"), ("estado", "ESTADO"), ("consumo", "CONSUMO"), ("tiempo", "TIEMPO"),
                  ("salud", "SALUD"), ("ciclos", "CICLOS"), ("cap", "CAPACIDAD"), ("modelo", "MODELO")]
        for i, (k, nombre) in enumerate(campos):
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            box.append(_lbl(nombre, "aj-key"))
            v = _lbl("—", "aj-val")
            box.append(v)
            self.vals[k] = v
            self.grid.attach(box, i % 4, i // 4, 1, 1)
        s.add(self.grid)
        self.append(s)

        u = Seccion(win, "CHG// 04", "UMBRAL DE CARGA",
                    "Cargar siempre al 100 % desgasta la batería. Con un umbral, deja de cargar al llegar al "
                    "límite y solo vuelve a cargar cuando baja del inicio.")
        fila = Gtk.Box(spacing=10, homogeneous=True, margin_top=6)
        self.umb = {}
        for ini, fin, tit, desc in UMBRALES:
            t = Tarjeta(f"{tit}  {ini}→{fin}", desc, lambda i=ini, f=fin: self._umbral(i, f))
            fila.append(t)
            self.umb[(ini, fin)] = t
        u.add(fila)
        self.umb_txt = _lbl("", "aj-key")
        u.add(self.umb_txt)
        self.umb_aviso = _lbl("", "aj-warn", wrap=True, selectable=True)
        u.add(self.umb_aviso)
        self.append(u)
        self.refrescar()

    def _umbral(self, ini, fin):
        cfg = energia.leer()
        cfg["carga_inicio"], cfg["carga_fin"] = ini, fin
        energia.guardar_seccion("energia", cfg)
        self.refrescar()

    def refrescar(self):
        b, est, cfg = energia.bateria(), energia.estado(), energia.leer()
        if b:
            self.vals["carga"].set_text(f"{b['capacidad']} %")
            self.vals["estado"].set_text(ESTADOS_BAT.get(b["estado"], b["estado"].upper()))
            self.vals["consumo"].set_text(f"{b['vatios']:.1f} W" if b["vatios"] else "—")
            if b["horas"]:
                h = int(b["horas"])
                m = int((b["horas"] - h) * 60)
                self.vals["tiempo"].set_text(f"{h} h {m:02d} min" + (" PARA LLENAR" if b["estado"] == "Charging"
                                                                     else ""))
            else:
                self.vals["tiempo"].set_text("—")
            salud = b["salud"]
            self.vals["salud"].set_text(f"{salud} %" if salud else "—")
            for c in ("ok", "warn"):
                self.vals["salud"].remove_css_class(c)
            if salud:
                self.vals["salud"].add_css_class("ok" if salud >= 80 else "warn")
            self.vals["ciclos"].set_text(str(b["ciclos"]) if b["ciclos"] is not None else "—")
            self.vals["cap"].set_text(f"{b['wh']:.1f} / {b['wh_diseno']:.1f} Wh" if b["wh"] and b["wh_diseno"]
                                      else "—")
            self.vals["modelo"].set_text(b["modelo"] or "—")
        actual = est["umbral"]
        elegido = (cfg["carga_inicio"], cfg["carga_fin"]) if cfg["carga_fin"] is not None else actual
        for k, t in self.umb.items():
            _on(t, k == elegido)
        self.umb_txt.set_text(f"AHORA EN EL FIRMWARE: {actual[0]} → {actual[1]} %" if actual
                              else "ESTA BATERÍA NO ADMITE UMBRALES")
        pendiente = actual is not None and cfg["carga_fin"] is not None and elegido != actual
        self.umb_aviso.set_text("Para cambiar el umbral falta el ayudante con permisos. Una vez:  "
                                "sudo ~/neural-deck/root/instalar.sh" if pendiente and not est["ayudante"] else "")


def _leer(p: Path) -> str:
    try:
        return p.read_text().strip()
    except OSError:
        return ""


def _temperaturas() -> list[tuple[str, str]]:
    """[(nombre, '52 °C')] de los sensores conocidos (CPU, GPU, disco)."""
    nombres = {"k10temp": "CPU", "coretemp": "CPU", "amdgpu": "GPU", "nvme": "DISCO NVMe", "thinkpad": "PLACA"}
    out = []
    for h in sorted(Path("/sys/class/hwmon").glob("hwmon*")):
        n = _leer(h / "name")
        if n not in nombres:
            continue
        t = _leer(h / "temp1_input")
        if t.isdigit():
            out.append((nombres[n], f"{int(t) / 1000:.0f} °C"))
    return out


def _gpu() -> str:
    try:
        for linea in subprocess.run(["lspci"], capture_output=True, text=True, timeout=3).stdout.splitlines():
            if " VGA " in linea or "Display controller" in linea or "3D controller" in linea:
                nombre = linea.split(": ", 1)[1]
                return nombre.replace("Advanced Micro Devices, Inc. ", "").replace("[AMD/ATI] ", "")
    except (OSError, subprocess.SubprocessError):
        pass
    return "—"


def _disco(ruta: str) -> str:
    try:
        u = shutil.disk_usage(ruta)
    except OSError:
        return "—"
    g = 1024 ** 3
    return f"{u.used / g:.0f} / {u.total / g:.0f} GiB  ({100 * u.used / u.total:.0f} %)"


class PaginaSistema(Gtk.Box):
    CAMPOS = [("os", "SISTEMA"), ("kernel", "KERNEL"), ("escritorio", "ESCRITORIO"), ("host", "EQUIPO"),
              ("cpu", "CPU"), ("gpu", "GRÁFICA"), ("mem", "MEMORIA"), ("raiz", "DISCO /"), ("casa", "DISCO /home"),
              ("temp", "TEMPERATURAS"), ("up", "ENCENDIDO"), ("tema", "TEMA")]

    def __init__(self, win):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        s = Seccion(win, "SYS// 23", "SISTEMA")
        self.grid = Gtk.Grid(column_spacing=24, row_spacing=8)
        s.add(self.grid)
        self.append(s)
        self.vals: dict[str, Gtk.Label] = {}
        for i, (k, nombre) in enumerate(self.CAMPOS):
            self.grid.attach(_lbl(nombre, "aj-key"), 0, i, 1, 1)
            v = _lbl("—", "aj-txt", selectable=True, wrap=True)
            self.grid.attach(v, 1, i, 1, 1)
            self.vals[k] = v
        self.fijos = False

        sv = Seccion(win, "PROC// 24", "PROCESOS DE NEURAL",
                     "Lo que arranca Wayfire al entrar. Si algo deja de responder, REINICIAR lo vuelve a "
                     "lanzar sin cerrar la sesión.")
        self.lista = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        sv.add(self.lista)
        self.msg = _lbl("", "aj-small", wrap=True)
        sv.add(self.msg)
        self.append(sv)
        self.estados: dict[str, Gtk.Label] = {}
        self.filas: dict[str, Gtk.Box] = {}
        for sv_ in servicios.lista():
            fila = Gtk.Box(spacing=12)
            fila.add_css_class("aj-fila")
            txt = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
            txt.append(_lbl(sv_["nombre"], "aj-txt"))
            txt.append(_lbl(sv_["desc"], "aj-small", wrap=True))
            fila.append(txt)
            est = _lbl("", "aj-key")
            fila.append(est)
            fila.append(_boton("⟳ REINICIAR", lambda x=sv_: self._reiniciar(x)))
            self.lista.append(fila)
            self.estados[sv_["clave"]] = est
            self.filas[sv_["clave"]] = fila
        self.refrescar()

    def _reiniciar(self, sv_):
        self.msg.set_text(f"REINICIANDO {sv_['nombre']}…")

        def fin(_r, err):
            self.msg.set_text(_texto_error(err) if err else f"{sv_['nombre']}: REINICIADO")
            GLib.timeout_add(800, lambda: (self.refrescar(), False)[1])

        _en_hilo(lambda: servicios.reiniciar(sv_), fin)

    def refrescar(self):
        if not self.fijos:  # lo que no cambia: una vez
            self.fijos = True
            nombre_os = "Linux"
            for linea in _leer(Path("/etc/os-release")).splitlines():
                if linea.startswith("PRETTY_NAME="):
                    nombre_os = linea.split("=", 1)[1].strip().strip('"') or nombre_os
            self.vals["os"].set_text(nombre_os)
            self.vals["kernel"].set_text(platform.release())
            self.vals["host"].set_text(platform.node())
            try:
                wf = subprocess.run(["wayfire", "--version"], capture_output=True, text=True, timeout=3).stdout
                self.vals["escritorio"].set_text(f"Wayfire {wf.split()[0]}  ·  NEURAL//DECK" if wf else "Wayfire")
            except (OSError, subprocess.SubprocessError, IndexError):
                self.vals["escritorio"].set_text("Wayfire")
            cpu = "—"
            for line in _leer(Path("/proc/cpuinfo")).splitlines():
                if line.startswith("model name"):
                    cpu = f"{line.split(':', 1)[1].strip()}  ·  {os.cpu_count()} hilos"
                    break
            self.vals["cpu"].set_text(cpu)
            self.vals["gpu"].set_text(_gpu())
        try:
            info = dict(l.split(":", 1) for l in _leer(Path("/proc/meminfo")).splitlines() if ":" in l)
            tot = int(info["MemTotal"].split()[0]) / 1048576
            dis = int(info["MemAvailable"].split()[0]) / 1048576
            self.vals["mem"].set_text(f"{tot - dis:.1f} / {tot:.1f} GiB en uso")
        except (KeyError, ValueError):
            pass
        self.vals["raiz"].set_text(_disco("/"))
        self.vals["casa"].set_text(_disco(str(Path.home())))
        self.vals["temp"].set_text("  ·  ".join(f"{n} {t}" for n, t in _temperaturas()) or "—")
        self.vals["up"].set_text(sistema.uptime())
        tema = load_board().get("theme", "red")
        self.vals["tema"].set_text(NOMBRES_TEMA.get(tema, (tema, ""))[0])
        for sv_ in servicios.lista():
            est = self.estados.get(sv_["clave"])
            if est is not None:
                vivo = servicios.vivo(sv_)
                est.set_text("● ACTIVO" if vivo else "○ DETENIDO")
                est.remove_css_class("aj-warn" if vivo else "aj-key")
                est.add_css_class("aj-key" if vivo else "aj-warn")


# ---------- RED ----------
NOMBRE_TIPO = {"wifi": "WIFI", "ethernet": "CABLE"}


class PaginaRed(Gtk.Box):
    """Estado de WiFi/Ethernet, radios, redes WiFi, conexiones guardadas y opciones avanzadas."""

    def __init__(self, win):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        self.ocupado = False
        self.cargando = False
        self.ultimo_estado = None
        self.ultimas_guardadas = None
        self.wifi_cargada = False
        self.form: dict | None = None

        # --- estado + radios
        e = Seccion(win, "NET// 05", "ESTADO DE LA RED", "Cada tarjeta de red del equipo y su conexión actual.")
        self.estado_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        e.add(self.estado_box)
        radios = Gtk.Box(spacing=28, margin_top=6)
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        col.append(_lbl("WIFI", "aj-key"))
        self.radio_wifi = Chips([(True, "ENCENDIDO"), (False, "APAGADO")], self._radio_wifi)
        col.append(self.radio_wifi)
        radios.append(col)
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        col.append(_lbl("MODO AVIÓN  (WIFI + BLUETOOTH)", "aj-key"))
        self.avion = Chips([(False, "NO"), (True, "SÍ")], self._avion)
        col.append(self.avion)
        radios.append(col)
        e.add(radios)
        self.append(e)

        # --- WiFi
        w = Seccion(win, "WLAN// 06", "REDES WIFI",
                    "Las redes nuevas solo se guardan si conectan bien. La contraseña no sale en la lista de procesos.")
        barra = Gtk.Box(spacing=12)
        self.btn_buscar = _boton("⟳ BUSCAR REDES", lambda: self.cargar_wifi(True))
        barra.append(self.btn_buscar)
        self.wifi_msg = _lbl("", "aj-small", wrap=True, hexpand=True)
        barra.append(self.wifi_msg)
        w.add(barra)
        self.lista_wifi = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        w.add(self.lista_wifi)
        self.append(w)

        # --- guardadas
        g = Seccion(win, "CON// 07", "CONEXIONES GUARDADAS",
                    "WiFi y cable. OPCIONES abre los ajustes avanzados de cada conexión.")
        self.lista_guardadas = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        g.add(self.lista_guardadas)
        self.guard_msg = _lbl("", "aj-small", wrap=True)
        g.add(self.guard_msg)
        self.append(g)

        # --- avanzado
        self.av = Seccion(win, "ADV// 08", "AVANZADO",
                          "Cambios por conexión. Si está activa se reconecta para aplicarlos (corte de 1-2 s).")
        self.av_titulo = _lbl("", "aj-txt")
        self.av.add(self.av_titulo)
        self.av_chips = {}

        def fila_chips(clave, titulo, opciones, nota=""):
            self.av.add(_lbl(titulo, "aj-key"))
            ch = Chips(opciones, lambda v, k=clave: self._form(k, v))
            self.av.add(ch)
            if nota:
                self.av.add(_lbl(nota, "aj-small", wrap=True))
            self.av_chips[clave] = ch

        fila_chips("auto", "CONECTAR SOLA", [(True, "SÍ"), (False, "NO")])
        fila_chips("medida", "CONEXIÓN MEDIDA", [("auto", "AUTO"), ("si", "SÍ"), ("no", "NO")],
                   "SÍ = datos limitados (hotspot del teléfono): las apps evitan descargas grandes.")
        fila_chips("ipv4", "DIRECCIÓN IPv4", [("auto", "AUTOMÁTICA (DHCP)"), ("manual", "MANUAL")])
        self.manual = Gtk.Box(spacing=10)
        self.e_dir = _entrada("192.168.1.50/24")
        self.e_puerta = _entrada("192.168.1.1", 16)
        for t, en in (("IP/PREFIJO", self.e_dir), ("PUERTA", self.e_puerta)):
            self.manual.append(_lbl(t, "aj-key"))
            self.manual.append(en)
        self.av.add(self.manual)
        fila_chips("dns", "DNS", [("auto", "DEL ROUTER"), ("cloudflare", "CLOUDFLARE"), ("quad9", "QUAD9"),
                                  ("google", "GOOGLE"), ("propio", "PROPIO")],
                   "QUAD9 bloquea dominios maliciosos. CLOUDFLARE suele ser el más rápido.")
        self.e_dns = _entrada("1.1.1.1 9.9.9.9", 30)
        self.av.add(self.e_dns)
        fila_chips("ipv6", "IPv6", [("auto", "AUTOMÁTICO"), ("off", "DESACTIVADO")])
        fila_chips("mac", "DIRECCIÓN MAC", [("permanente", "LA REAL"), ("stable", "FALSA FIJA POR RED"),
                                            ("random", "ALEATORIA CADA VEZ")],
                   "Una MAC falsa evita que las redes públicas te sigan entre visitas.")
        acc = Gtk.Box(spacing=10, margin_top=8)
        self.btn_guardar = _boton("✓ GUARDAR Y APLICAR", self._guardar_form)
        acc.append(self.btn_guardar)
        acc.append(_boton("CERRAR", self._cerrar_form))
        self.av_msg = _lbl("", "aj-small", wrap=True, hexpand=True)
        acc.append(self.av_msg)
        self.av.add(acc)
        self.av.set_visible(False)
        self.append(self.av)

    # ----- estado (cada 2 s) -----
    def refrescar(self):
        if self.cargando:
            return
        self.cargando = True

        def trabajo():
            devs = [red.detalle(d["dev"]) for d in red.dispositivos()]
            return devs, red.wifi_radio_on(), red.modo_avion(), red.guardadas()

        _en_hilo(trabajo, self._estado)
        if not self.wifi_cargada:
            self.wifi_cargada = True
            self.cargar_wifi(False)

    def _estado(self, res, err):
        self.cargando = False
        if err:
            _vaciar(self.estado_box)
            self.estado_box.append(_lbl(_texto_error(err), "aj-warn", wrap=True))
            self.ultimo_estado = None
            return
        devs, wifi_on, avion, guard = res
        self.radio_wifi.marcar(wifi_on)
        self.avion.marcar(bool(avion))
        if devs != self.ultimo_estado:
            self.ultimo_estado = devs
            _vaciar(self.estado_box)
            for d in devs:
                caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
                cab = _lbl(f"{NOMBRE_TIPO.get(d['tipo'], d['tipo'].upper())}  ·  {d['dev']}  ·  {d['estado']}",
                           "aj-txt")
                caja.append(cab)
                filas = [("CONEXIÓN", d["con"]), ("IPv4", d["ip"]), ("PUERTA", d["puerta"]), ("DNS", d["dns"]),
                         ("IPv6", d["ip6"]), ("VELOCIDAD", d["velocidad"]), ("MAC", d["mac"]),
                         ("CONTROLADOR", d["driver"])]
                grid = Gtk.Grid(column_spacing=24, row_spacing=4, margin_start=12)
                i = 0
                for k, v in filas:
                    if not v:
                        continue
                    grid.attach(_lbl(k, "aj-key"), 0, i, 1, 1)
                    grid.attach(_lbl(v, "aj-txt", selectable=True, wrap=True), 1, i, 1, 1)
                    i += 1
                if i:
                    caja.append(grid)
                self.estado_box.append(caja)
            if not devs:
                self.estado_box.append(_lbl("NO HAY TARJETAS DE RED", "aj-warn"))
        firma = [(x["uuid"], x["nombre"], x["auto"], x["dev"]) for x in guard]
        if firma != self.ultimas_guardadas:
            self.ultimas_guardadas = firma
            self._pintar_guardadas(guard)

    def _radio_wifi(self, encender):
        _en_hilo(lambda: red.wifi_radio(encender), lambda _r, err: self._tras_radio(err, encender))

    def _avion(self, activar):
        _en_hilo(lambda: red.poner_modo_avion(activar), lambda _r, err: self._tras_radio(err, True))

    def _tras_radio(self, err, recargar):
        if err:
            self.wifi_msg.set_text(_texto_error(err))
        self.refrescar()
        if recargar:
            GLib.timeout_add_seconds(3, lambda: (self.cargar_wifi(False), False)[1])

    # ----- WiFi -----
    def cargar_wifi(self, buscar: bool):
        self.btn_buscar.set_sensitive(False)
        self.wifi_msg.set_text("BUSCANDO REDES…" if buscar else "")
        _en_hilo(lambda: red.wifi_lista(buscar), self._pintar_wifi)

    def _pintar_wifi(self, redes, err):
        self.btn_buscar.set_sensitive(True)
        _vaciar(self.lista_wifi)
        if err:
            self.wifi_msg.set_text(_texto_error(err))
            return
        self.wifi_msg.set_text(f"{len(redes)} REDES" if redes else "NO SE VE NINGUNA RED (¿WIFI APAGADO?)")
        for r in redes:
            self.lista_wifi.append(self._fila_wifi(r))

    def _fila_wifi(self, r: dict) -> Gtk.Widget:
        caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        caja.add_css_class("aj-fila")
        _on(caja, r["en_uso"])
        fila = Gtk.Box(spacing=12)
        fila.append(_lbl(red.barras(r["señal"]), "aj-key"))
        txt = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        txt.append(_lbl(r["ssid"], "aj-txt"))
        detalles = [r["seguridad"] or "ABIERTA", r["banda"], f"CANAL {r['canal']}", f"{r['señal']} %"]
        txt.append(_lbl("  ·  ".join(detalles), "aj-small"))
        fila.append(txt)
        clave_box = Gtk.Box(spacing=10)
        clave_box.set_visible(False)
        if r["en_uso"]:
            fila.append(_boton("DESCONECTAR", lambda: self._accion(
                lambda: red.desconectar(red.wifi_dev() or ""), "DESCONECTANDO…", self.wifi_msg, wifi=True)))
        else:
            fila.append(_boton("CONECTAR", lambda: self._conectar(r, None, clave_box)))
        caja.append(fila)
        entrada = _entrada("contraseña", 26, clave=True)
        entrada.connect("activate", lambda e: self._conectar(r, e.get_text(), clave_box))
        clave_box.append(_lbl("CONTRASEÑA", "aj-key"))
        clave_box.append(entrada)
        clave_box.append(_boton("CONECTAR", lambda: self._conectar(r, entrada.get_text(), clave_box)))
        clave_box.append(_boton("CANCELAR", lambda: clave_box.set_visible(False)))
        caja.append(clave_box)
        return caja

    def _conectar(self, r: dict, clave, clave_box: Gtk.Box):
        if self.ocupado:
            return
        if clave is not None and not clave:
            return
        self.ocupado = True
        self.wifi_msg.set_text(f"CONECTANDO A {r['ssid']}…")

        def fin(texto, err):
            self.ocupado = False
            if isinstance(err, red.NecesitaClave):
                self.wifi_msg.set_text(f"{r['ssid']}: ESCRIBE LA CONTRASEÑA")
                clave_box.set_visible(True)
                ent = clave_box.get_first_child().get_next_sibling()
                ent.grab_focus()
                return
            if err:
                self.wifi_msg.set_text(_texto_error(err))
                return
            self.wifi_msg.set_text(texto)
            self.ultimas_guardadas = None
            self.refrescar()
            self.cargar_wifi(False)

        _en_hilo(lambda: red.conectar_wifi(r["ssid"], r["seguridad"], clave), fin)

    def _accion(self, trabajo, texto: str, etiqueta: Gtk.Label, wifi: bool = False):
        """Acción de red genérica con mensaje mientras corre."""
        if self.ocupado:
            return
        self.ocupado = True
        etiqueta.set_text(texto)

        def fin(_res, err):
            self.ocupado = False
            etiqueta.set_text(_texto_error(err) if err else "LISTO")
            self.ultimas_guardadas = None
            self.refrescar()
            if wifi:
                self.cargar_wifi(False)

        _en_hilo(trabajo, fin)

    # ----- guardadas -----
    def _pintar_guardadas(self, guard: list[dict]):
        _vaciar(self.lista_guardadas)
        if not guard:
            self.lista_guardadas.append(_lbl("NO HAY CONEXIONES GUARDADAS", "aj-small"))
        for x in guard:
            fila = Gtk.Box(spacing=12)
            fila.add_css_class("aj-fila")
            _on(fila, bool(x["dev"]))
            fila.append(_lbl(NOMBRE_TIPO.get(x["tipo"], "?").ljust(5), "aj-key"))
            txt = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
            txt.append(_lbl(x["nombre"], "aj-txt"))
            est = [f"ACTIVA EN {x['dev']}" if x["dev"] else "INACTIVA",
                   "CONECTA SOLA" if x["auto"] else "SOLO A MANO"]
            txt.append(_lbl("  ·  ".join(est), "aj-small"))
            fila.append(txt)
            if x["dev"]:
                fila.append(_boton("DESCONECTAR", lambda d=x["dev"]: self._accion(
                    lambda: red.desconectar(d), "DESCONECTANDO…", self.guard_msg, wifi=True)))
            else:
                fila.append(_boton("ACTIVAR", lambda u=x["uuid"]: self._accion(
                    lambda: red.activar(u), "CONECTANDO…", self.guard_msg, wifi=True)))
            fila.append(_boton("OPCIONES", lambda u=x["uuid"]: self._abrir_form(u)))
            olvidar = Gtk.Button(label="OLVIDAR", valign=Gtk.Align.CENTER)
            olvidar.add_css_class("aj-chip")
            olvidar.connect("clicked", lambda b, u=x["uuid"]: self._olvidar(b, u))
            fila.append(olvidar)
            self.lista_guardadas.append(fila)

    def _olvidar(self, boton: Gtk.Button, uuid: str):
        if boton.get_label() != "¿SEGURO?":  # dos clics: no borrar una red por accidente
            boton.set_label("¿SEGURO?")
            GLib.timeout_add_seconds(3, lambda: (boton.set_label("OLVIDAR"), False)[1])
            return
        if self.form and self.form["uuid"] == uuid:
            self._cerrar_form()
        self._accion(lambda: red.olvidar(uuid), "OLVIDANDO…", self.guard_msg, wifi=True)

    # ----- avanzado -----
    def _abrir_form(self, uuid: str):
        self.av_msg.set_text("CARGANDO…")
        self.av.set_visible(True)

        def fin(o, err):
            if err:
                self.av_msg.set_text(_texto_error(err))
                return
            self.form = o
            self.av_titulo.set_text(f"{NOMBRE_TIPO.get(o['tipo'], '')}  ·  {o['nombre']}")
            self.e_dir.set_text(o["direccion"])
            self.e_puerta.set_text(o["puerta"])
            self.e_dns.set_text(o["dns_propio"])
            self.av_msg.set_text("")
            self._marcar_form()

        _en_hilo(lambda: red.opciones(uuid), fin)

    def _form(self, clave, valor):
        if self.form is None:
            return
        self.form[clave] = valor
        self._marcar_form()

    def _marcar_form(self):
        for k, ch in self.av_chips.items():
            ch.marcar(self.form[k])
        self.manual.set_visible(self.form["ipv4"] == "manual")
        self.e_dns.set_visible(self.form["dns"] == "propio")

    def _cerrar_form(self):
        self.form = None
        self.av.set_visible(False)

    def _guardar_form(self):
        if self.form is None or self.ocupado:
            return
        o = dict(self.form, direccion=self.e_dir.get_text(), puerta=self.e_puerta.get_text(),
                 dns_propio=self.e_dns.get_text())
        self.ocupado = True
        self.btn_guardar.set_sensitive(False)
        self.av_msg.set_text("APLICANDO…")

        def fin(texto, err):
            self.ocupado = False
            self.btn_guardar.set_sensitive(True)
            self.av_msg.set_text(_texto_error(err) if err else texto)
            self.ultimas_guardadas = None
            self.refrescar()

        _en_hilo(lambda: red.guardar_opciones(o), fin)


# ---------- PANTALLA ----------
TEMPS_NOCHE = [(5500, "5500 K · SUAVE"), (4500, "4500 K"), (4000, "4000 K"), (3500, "3500 K"),
               (3000, "3000 K"), (2500, "2500 K · FUERTE")]
TEMPS_DIA = [(6500, "6500 K · NEUTRO"), (6000, "6000 K"), (5500, "5500 K")]
ATARDECERES = ["17:00", "18:00", "18:30", "19:00", "19:30", "20:00", "21:00", "22:00"]
AMANECERES = ["05:00", "06:00", "06:30", "07:00", "07:30", "08:00", "09:00"]
TRANSICIONES = [(1, "INMEDIATA"), (15, "15 MIN"), (30, "30 MIN"), (60, "1 H"), (120, "2 H")]
GAMMAS = [(0.9, "0.9 · MÁS CLARO"), (1.0, "1.0 · NORMAL"), (1.1, "1.1 · MÁS CONTRASTE")]


class PaginaPantalla(Gtk.Box):
    def __init__(self, win):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        self.tocado = 0.0
        self.brillo_pendiente = None

        # --- brillo
        b = Seccion(win, "DSP// 09", "BRILLO", "Por logind: sin root. Nunca baja del 2 % para no dejarla negra.")
        fila = Gtk.Box(spacing=16)
        self.escala = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 2, 100, 1)
        self.escala.set_hexpand(True)
        self.escala.set_draw_value(False)
        self.escala.add_css_class("aj-scale")
        self.escala.connect("value-changed", self._brillo)
        fila.append(self.escala)
        self.brillo_val = _lbl("—", "aj-val")
        self.brillo_val.set_size_request(70, -1)
        fila.append(self.brillo_val)
        b.add(fila)
        b.add(_lbl("NIVELES RÁPIDOS", "aj-key"))
        b.add(Chips([(p, f"{p} %") for p in (10, 25, 50, 75, 100)], lambda v: self.escala.set_value(v)))
        self.teclado_titulo = _lbl("LUZ DEL TECLADO", "aj-key")
        b.add(self.teclado_titulo)
        tk = sistema.get_teclado()
        self.teclado = None
        if tk:
            self.teclado = Chips([(i, "APAGADA" if i == 0 else f"NIVEL {i}") for i in range(tk[1] + 1)],
                                 self._teclado)
            b.add(self.teclado)
        else:
            self.teclado_titulo.set_text("LUZ DEL TECLADO: NO HAY")
        self.append(b)

        # --- protector
        s = Seccion(win, "EYE// 10", "PROTECTOR DE LA VISTA",
                    "Quita luz azul por la noche (wlsunset). Cuanto más baja la temperatura, más naranja la "
                    "pantalla y menos le cuesta al cuerpo dormir después.")
        s.add(_lbl("MODO", "aj-key"))
        self.modo = Chips([("apagado", "APAGADO"), ("horario", "CON HORARIO"), ("siempre", "SIEMPRE")],
                          lambda v: self._poner(protector=v))
        s.add(self.modo)
        s.add(_lbl("INTENSIDAD (TEMPERATURA DE NOCHE)", "aj-key"))
        self.noche = Chips(TEMPS_NOCHE, lambda v: self._poner(temp_noche=v))
        s.add(self.noche)

        self.horario = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.horario.append(_lbl("SE ACTIVA A LAS", "aj-key"))
        self.atardecer = Chips([(h, h) for h in ATARDECERES], lambda v: self._poner(atardecer=v))
        self.horario.append(self.atardecer)
        self.horario.append(_lbl("SE QUITA A LAS", "aj-key"))
        self.amanecer = Chips([(h, h) for h in AMANECERES], lambda v: self._poner(amanecer=v))
        self.horario.append(self.amanecer)
        s.add(self.horario)
        self.estado = _lbl("", "aj-key")
        self.estado.set_margin_top(6)
        s.add(self.estado)
        self.aviso = _lbl("", "aj-warn", wrap=True)
        s.add(self.aviso)
        self.append(s)

        # --- avanzado
        a = Seccion(win, "ADV// 11", "AVANZADO",
                    "Ajuste fino del filtro. Los cambios reinician wlsunset al momento.")
        a.add(_lbl("TEMPERATURA DE DÍA", "aj-key"))
        self.dia = Chips(TEMPS_DIA, lambda v: self._poner(temp_dia=v))
        a.add(self.dia)
        a.add(_lbl("TRANSICIÓN DÍA ↔ NOCHE", "aj-key"))
        self.trans = Chips(TRANSICIONES, lambda v: self._poner(transicion=v))
        a.add(self.trans)
        a.add(_lbl("GAMMA", "aj-key"))
        self.gamma = Chips(GAMMAS, lambda v: self._poner(gamma=v))
        a.add(self.gamma)
        a.add(_lbl("Si la pantalla se ve lavada o muy oscura con el filtro, prueba otra gamma.", "aj-small",
                   wrap=True))
        self.append(a)
        self.refrescar()

    # brillo: la escala manda cada 60 ms como mucho (busctl por cada píxel sería demasiado)
    def _brillo(self, escala):
        v = escala.get_value()
        self.brillo_val.set_text(f"{v:.0f} %")
        if getattr(self, "_sin_eco", False):
            return
        self.tocado = GLib.get_monotonic_time()
        if self.brillo_pendiente is None:
            self.brillo_pendiente = GLib.timeout_add(60, self._mandar_brillo)

    def _mandar_brillo(self):
        self.brillo_pendiente = None
        cmd = sistema.cmd_set_brillo(self.escala.get_value() / 100)
        if cmd:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            sistema.avisar_barra()
        return False

    def _teclado(self, nivel):
        if sistema.set_teclado(nivel):
            sistema.guardar_teclado(nivel)
        self.refrescar()

    def _poner(self, **cambios):
        cfg = pantalla.leer()
        cfg.update(cambios)
        pantalla.guardar(cfg)
        err = pantalla.aplicar(cfg)
        self.aviso.set_text(err or "")
        GLib.timeout_add(400, lambda: (self.refrescar(), False)[1])

    def refrescar(self):
        actual = sistema.get_brillo()
        if actual is not None and GLib.get_monotonic_time() - self.tocado > 1_500_000:
            self._sin_eco = True
            self.escala.set_value(round(actual * 100))
            self._sin_eco = False
        tk = sistema.get_teclado()
        if self.teclado and tk:
            self.teclado.marcar(tk[0])
        cfg = pantalla.leer()
        self.modo.marcar(cfg["protector"])
        self.noche.marcar(cfg["temp_noche"])
        self.atardecer.marcar(cfg["atardecer"])
        self.amanecer.marcar(cfg["amanecer"])
        self.horario.set_visible(cfg["protector"] == "horario")
        self.dia.marcar(cfg["temp_dia"])
        self.trans.marcar(cfg["transicion"])
        self.gamma.marcar(cfg["gamma"])
        activo = pantalla.corriendo()
        self.estado.set_text(f"WLSUNSET: {'ACTIVO' if activo else 'DETENIDO'}"
                             + (f"  ·  FILTRO DE {cfg['atardecer']} A {cfg['amanecer']}"
                                if cfg["protector"] == "horario" else ""))
        if cfg["protector"] != "apagado" and not activo and not self.aviso.get_text():
            self.aviso.set_text("wlsunset no está corriendo: elige el modo otra vez para arrancarlo.")
        elif activo:
            self.aviso.set_text("")


# ---------- SONIDO ----------
class BloqueAudio(Seccion):
    """Salida o entrada de PipeWire: dispositivo, volumen y silencio."""

    def __init__(self, win, salida: bool):
        tab, tit = ("SND// 12", "SALIDA DE SONIDO") if salida else ("MIC// 13", "MICRÓFONO")
        sub = ("Altavoces, audífonos, HDMI o Bluetooth. Lo que ya suena se pasa solo a la nueva salida."
               if salida else "Entrada para llamadas y grabaciones.")
        super().__init__(win, tab, tit, sub)
        self.salida = salida
        self.dev = sistema.SINK if salida else sistema.SOURCE
        self.firma = None
        self.add(_lbl("DISPOSITIVO", "aj-key"))
        self.lista = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.add(self.lista)
        self.add(_lbl("VOLUMEN", "aj-key"))
        self.vol = Deslizador(0, 100, 1, self._volumen)
        self.add(self.vol)
        self.add(_lbl("SILENCIAR", "aj-key"))
        self.mudo = Chips([(False, "CON SONIDO"), (True, "SILENCIADO")], self._mudo)
        self.add(self.mudo)
        self.cargando = False

    def _volumen(self, v):
        subprocess.Popen(sistema.cmd_set_volume(self.dev, v / 100), stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
        sistema.avisar_barra()

    def _mudo(self, silenciar):
        actual = sistema.get_audio(self.dev)
        if actual is not None and actual[1] != silenciar:
            subprocess.run(sistema.cmd_toggle_mute(self.dev), check=False)
            sistema.avisar_barra()
        self.mudo.marcar(silenciar)

    def _elegir(self, d):
        subprocess.run(sistema.cmd_set_default(d["id"]), check=False)
        self.firma = None
        GLib.timeout_add(300, lambda: (self.refrescar(), False)[1])

    def refrescar(self):
        if self.cargando:
            return
        self.cargando = True
        _en_hilo(lambda: (sistema.get_dispositivos(self.salida), sistema.get_audio(self.dev)), self._pintar)

    def _pintar(self, res, err):
        self.cargando = False
        if err or res is None:
            return
        devs, audio = res
        if audio:
            self.vol.poner(round(audio[0] * 100))
            self.mudo.marcar(audio[1])
        firma = [(d["id"], d["default"], d["disponible"]) for d in devs]
        if firma == self.firma:
            return
        self.firma = firma
        _vaciar(self.lista)
        if not devs:
            self.lista.append(_lbl("NO HAY DISPOSITIVOS", "aj-warn"))
        for d in devs:
            fila = Gtk.Box(spacing=12)
            fila.add_css_class("aj-fila")
            _on(fila, d["default"])
            fila.append(_lbl("BT" if d["bt"] else ("●" if d["default"] else "○"), "aj-key"))
            txt = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
            txt.append(_lbl(d["nombre"], "aj-txt"))
            if not d["disponible"]:
                txt.append(_lbl("NADA ENCHUFADO", "aj-small"))
            fila.append(txt)
            if d["default"]:
                fila.append(_lbl("EN USO", "aj-ok"))
            elif d["disponible"]:
                fila.append(_boton("USAR", lambda d=d: self._elegir(d)))
            self.lista.append(fila)


class PaginaSonido(Gtk.Box):
    def __init__(self, win):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        self.bloques = [BloqueAudio(win, True), BloqueAudio(win, False)]
        for b in self.bloques:
            self.append(b)
        fila = Gtk.Box(spacing=12)
        fila.append(_boton("▶ PROBAR SONIDO", self._probar))
        fila.append(_lbl("Suena un aviso corto por la salida elegida.", "aj-small"))
        self.bloques[0].add(fila)
        self.refrescar()

    def _probar(self):
        for f in ("/usr/share/sounds/freedesktop/stereo/complete.oga",
                  "/usr/share/sounds/freedesktop/stereo/bell.oga"):
            if Path(f).exists() and shutil.which("pw-play"):
                subprocess.Popen(["pw-play", f], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return

    def refrescar(self):
        for b in self.bloques:
            b.refrescar()


# ---------- TECLADO Y RATÓN ----------
ACEL = [("default", "NORMAL"), ("adaptive", "ADAPTATIVA"), ("flat", "PLANA (SIN ACELERAR)")]
VEL_SCROLL = [(0.5, "LENTO"), (1.0, "NORMAL"), (1.5, "RÁPIDO"), (2.0, "MUY RÁPIDO")]


class PaginaEntrada(Gtk.Box):
    """[input] de wayfire.ini: teclado, ratón, touchpad y cursor."""

    def __init__(self, win):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        self.chips: dict[str, Chips] = {}
        self.barras: dict[str, Deslizador] = {}

        def chips(sec, clave, titulo, opciones, nota=""):
            sec.add(_lbl(titulo, "aj-key"))
            ch = Chips(opciones, lambda v, k=clave: self._poner(**{k: v}))
            sec.add(ch)
            if nota:
                sec.add(_lbl(nota, "aj-small", wrap=True))
            self.chips[clave] = ch

        def velocidad(sec, clave, titulo):
            sec.add(_lbl(titulo, "aj-key"))
            d = Deslizador(-100, 100, 5, lambda v, k=clave: self._poner(**{k: round(v / 100, 2)}),
                           lambda v: "NORMAL" if abs(v) < 1 else f"{v:+.0f}")
            sec.add(d)
            self.barras[clave] = d

        # --- teclado
        t = Seccion(win, "KBD// 14", "TECLADO", "Se aplica al momento a todas las ventanas.")
        t.add(_lbl("DISTRIBUCIÓN", "aj-key"))
        self.distro = Chips([(f"{l}:{v}", n) for l, v, n in entrada.DISTRIBUCIONES], self._distro)
        t.add(self.distro)
        t.add(_lbl("TECLAS ESPECIALES", "aj-key"))
        self.xkb = ChipsMulti(entrada.OPCIONES_XKB, self._xkb)
        t.add(self.xkb)
        chips(t, "kb_repeat_delay", "ESPERA ANTES DE REPETIR UNA TECLA",
              [(200, "200 MS"), (300, "300 MS"), (400, "400 MS"), (500, "500 MS"), (660, "660 MS")])
        chips(t, "kb_repeat_rate", "VELOCIDAD DE REPETICIÓN",
              [(20, "20/S"), (30, "30/S"), (40, "40/S"), (50, "50/S"), (60, "60/S")])
        chips(t, "kb_numlock_default_state", "BLOQ NUM ENCENDIDO AL ENTRAR", SI_NO)
        t.add(_lbl("PRUEBA AQUÍ", "aj-key"))
        t.add(_entrada("escribe para probar la distribución y la repetición…", 40))
        self.append(t)

        # --- ratón
        r = Seccion(win, "PTR// 15", "RATÓN")
        velocidad(r, "mouse_cursor_speed", "VELOCIDAD DEL PUNTERO")
        chips(r, "mouse_accel_profile", "ACELERACIÓN", ACEL,
              "PLANA = la misma distancia siempre, sin importar lo rápido que muevas (para juegos y diseño).")
        chips(r, "mouse_scroll_speed", "VELOCIDAD DE LA RUEDA", VEL_SCROLL)
        chips(r, "mouse_natural_scroll", "DESPLAZAMIENTO NATURAL (COMO EN EL MÓVIL)", SI_NO)
        chips(r, "left_handed_mode", "MODO ZURDO (CAMBIA LOS BOTONES)", SI_NO)
        chips(r, "middle_emulation", "CLIC CENTRAL CON LOS DOS BOTONES A LA VEZ", SI_NO)
        self.append(r)

        # --- touchpad
        if entrada.hay_touchpad():
            tp = Seccion(win, "TPD// 16", "TOUCHPAD")
            velocidad(tp, "touchpad_cursor_speed", "VELOCIDAD DEL PUNTERO")
            chips(tp, "touchpad_accel_profile", "ACELERACIÓN", ACEL)
            chips(tp, "tap_to_click", "TOCAR PARA HACER CLIC", SI_NO)
            chips(tp, "tap_and_drag", "TOCAR DOS VECES Y ARRASTRAR", SI_NO)
            chips(tp, "natural_scroll", "DESPLAZAMIENTO NATURAL", SI_NO)
            chips(tp, "scroll_method", "CÓMO DESPLAZAR",
                  [("default", "AUTOMÁTICO"), ("two-finger", "DOS DEDOS"), ("edge", "BORDE DERECHO")])
            chips(tp, "touchpad_scroll_speed", "VELOCIDAD DEL DESPLAZAMIENTO", VEL_SCROLL)
            chips(tp, "click_method", "CLIC DERECHO",
                  [("default", "AUTOMÁTICO"), ("clickfinger", "PULSAR CON DOS DEDOS"),
                   ("button-areas", "ESQUINA INFERIOR DERECHA")])
            chips(tp, "disable_touchpad_while_typing", "APAGARLO MIENTRAS ESCRIBES", SI_NO)
            chips(tp, "disable_touchpad_while_mouse", "APAGARLO SI HAY UN RATÓN CONECTADO", SI_NO)
            self.append(tp)

        # --- cursor
        c = Seccion(win, "CUR// 17", "CURSOR", "Tema y tamaño del puntero (Wayfire y apps GTK).")
        temas = ["default"] + [x for x in entrada.temas_cursor() if x != "default"]
        chips(c, "cursor_theme", "TEMA", [(x, "DEL SISTEMA" if x == "default" else x.upper()) for x in temas])
        chips(c, "cursor_size", "TAMAÑO", [(16, "PEQUEÑO"), (24, "NORMAL"), (32, "GRANDE"), (48, "ENORME")])
        self.append(c)
        self.refrescar()

    def _poner(self, **cambios):
        entrada.guardar(**cambios)
        if ("cursor_theme" in cambios or "cursor_size" in cambios) and shutil.which("gsettings"):
            cfg = entrada.leer()
            tema = "Adwaita" if cfg["cursor_theme"] == "default" else cfg["cursor_theme"]
            for k, v in (("cursor-theme", tema), ("cursor-size", str(cfg["cursor_size"]))):
                subprocess.run(["gsettings", "set", "org.gnome.desktop.interface", k, v],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        self.refrescar()

    def _distro(self, valor):
        layout, variante = valor.split(":", 1)
        self._poner(xkb_layout=layout, xkb_variant=variante)

    def _xkb(self, opcion):
        actuales = [o for o in entrada.leer()["xkb_options"].split(",") if o]
        if opcion in actuales:
            actuales.remove(opcion)
        else:
            # dos opciones para Bloq Mayús a la vez no tienen sentido
            familia = opcion.split(":")[0]
            actuales = [o for o in actuales if o.split(":")[0] != familia] + [opcion]
        self._poner(xkb_options=",".join(actuales))

    def refrescar(self):
        cfg = entrada.leer()
        self.distro.marcar(f"{cfg['xkb_layout']}:{cfg['xkb_variant']}")
        self.xkb.marcar(set(o for o in cfg["xkb_options"].split(",") if o))
        for k, ch in self.chips.items():
            ch.marcar(cfg[k])
        for k, d in self.barras.items():
            d.poner(round(cfg[k] * 100))


# ---------- APARIENCIA ----------
def _nombres_tema() -> dict[str, tuple[str, str]]:
    """{id: (nombre, descripción)} de web/themes.js."""
    import re
    out = {}
    try:
        texto = (WEB_DIR / "themes.js").read_text()
    except OSError:
        return out
    for m in re.finditer(r'id:\s*"([^"]+)",\s*name:\s*"([^"]+)",\s*desc:\s*"([^"]*)"', texto):
        out[m.group(1)] = (m.group(2), m.group(3))
    return out


NOMBRES_TEMA = _nombres_tema()


class TarjetaTema(Gtk.Button):
    def __init__(self, win, tid: str, tema: dict, al_pulsar):
        super().__init__()
        self.add_css_class("aj-card")
        caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        muestra = Gtk.DrawingArea(content_height=14, hexpand=True)
        colores = [tema[k] for k in ("accent", "alt", "warn", "text") if k in tema]

        def dibujar(_a, cr, w, h):
            ancho = w / max(1, len(colores))
            for i, c in enumerate(colores):
                cr.set_source_rgba(c.red, c.green, c.blue, 1)
                cr.rectangle(i * ancho, 0, ancho - 3, h)
                cr.fill()

        muestra.set_draw_func(dibujar)
        caja.append(muestra)
        nombre, desc = NOMBRES_TEMA.get(tid, (tid.upper(), ""))
        caja.append(_lbl(nombre, "aj-ct"))
        d = _lbl(desc, "aj-small", wrap=True)
        d.set_max_width_chars(26)
        caja.append(d)
        self.set_child(caja)
        self.connect("clicked", lambda *_: al_pulsar(tid))


class PaginaApariencia(Gtk.Box):
    def __init__(self, win):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        s = Seccion(win, "THM// 18", "TEMA DE COLOR",
                    "Lo siguen el fondo, la barra, el dock, las terminales, estas ventanas y las barras de "
                    "título de las demás apps.")
        rejilla = Gtk.FlowBox(column_spacing=10, row_spacing=10, selection_mode=Gtk.SelectionMode.NONE,
                              max_children_per_line=4, min_children_per_line=2, homogeneous=True)
        self.tarjetas = {}
        for tid, tema in load_themes().items():
            t = TarjetaTema(win, tid, tema, self._tema)
            rejilla.append(t)
            self.tarjetas[tid] = t
        s.add(rejilla)
        self.aviso = _lbl("", "aj-small", wrap=True)
        s.add(self.aviso)
        self.append(s)

        d = Seccion(win, "WIN// 19", "VENTANAS DE OTRAS APPS",
                    "Barra de título y borde que pone Wayfire a las apps que no son de NEURAL (Firefox, etc.).")
        d.add(_lbl("ALTO DE LA BARRA DE TÍTULO", "aj-key"))
        self.alto = Chips([(20, "FINA"), (26, "NORMAL"), (32, "ALTA"), (40, "MUY ALTA")],
                          lambda v: self._deco(titulo_alto=v))
        d.add(self.alto)
        d.add(_lbl("GROSOR DEL BORDE", "aj-key"))
        self.borde = Chips([(0, "SIN BORDE"), (1, "1 PX"), (2, "2 PX"), (4, "4 PX")], lambda v: self._deco(borde=v))
        d.add(self.borde)
        self.append(d)

        a = Seccion(win, "APP// 20", "APPS INSTALADAS",
                    "Colores NEURAL en las apps GTK, Qt y Flatpak (las de pacman y las de Flathub). "
                    "Firefox, Chrome y las apps Electron solo se ponen en modo oscuro.")
        a.add(_lbl("INTENSIDAD", "aj-key"))
        self.intensidad = Chips([("suave", "SUAVE"), ("completo", "COMPLETO")], self._intensidad)
        a.add(self.intensidad)
        self.int_desc = _lbl("", "aj-small", wrap=True)
        a.add(self.int_desc)
        a.add(_lbl("Las apps que ya estén abiertas cambian al volver a abrirlas.", "aj-small", wrap=True))
        self.append(a)
        self.refrescar()

    def _intensidad(self, modo):
        energia.guardar_seccion("apps", {"intensidad": modo})
        _en_hilo(temas_apps.aplicar, lambda *_: None)
        self.refrescar()

    def _tema(self, tid):
        try:
            board = json.loads(CONFIG_PATH.read_text())
        except (OSError, json.JSONDecodeError):
            board = None
        if not isinstance(board, dict) or not isinstance(board.get("panels"), list):
            self.aviso.set_text("No encuentro board.json del fondo: elige el tema desde el fondo (barra de temas).")
            return
        board["theme"] = tid
        save_board(board)
        _en_hilo(lambda: iconos.generar(tid), lambda *_: None)
        self.aviso.set_text("Si el fondo no cambia, reinicia FONDO / ESCRITORIO en SISTEMA (una vez, para que "
                            "cargue la versión que escucha este cambio).")
        GLib.timeout_add(400, lambda: (self.refrescar(), False)[1])

    def _deco(self, **cambios):
        cfg = dict(zip(("titulo_alto", "borde"), decoracion.medidas()))
        cfg.update(cambios)
        energia.guardar_seccion("apariencia", cfg)
        decoracion.aplicar()
        self.refrescar()

    def refrescar(self):
        actual = load_board().get("theme", "red")
        for tid, t in self.tarjetas.items():
            _on(t, tid == actual)
        alto, borde = decoracion.medidas()
        self.alto.marcar(alto)
        self.borde.marcar(borde)
        modo = temas_apps.intensidad()
        self.intensidad.marcar(modo)
        self.int_desc.set_text(
            "SUAVE: cada app conserva su aspecto oscuro y toma el color del tema en la selección, el botón "
            "principal, el foco, las casillas y las barras de progreso." if modo == "suave" else
            "COMPLETO: fondo negro puro, bordes y botones con el color del tema, como las ventanas de NEURAL.")


MODOS_MOSAICO = [  # (modo, título, descripción)
    (0, "LIBRE", "Ventanas flotantes, cada una donde la dejes."),
    (1, "1 VENTANA", "Una ventana a pantalla completa; las demás siguen en la cinta."),
    (2, "2 VENTANAS", "Dos mitades, una al lado de la otra."),
    (3, "3 VENTANAS", "Una grande a la izquierda y dos apiladas a la derecha."),
    (4, "4 VENTANAS", "Cuadrícula de 2 × 2."),
]
HUECOS = (0, 4, 10, 16, 24, 32)
ATAJOS_MOSAICO = [
    ("SUPER", "NEURAL//CINTA: todas las ventanas en fila (← → recorrer, Enter quedarse, Esc volver)"),
    ("SUPER + ALT + ← / →", "Recorrer la cinta sin abrir la vista"),
    ("SUPER + SHIFT + ← / →", "Llevar la ventana activa a la izquierda / derecha en la cinta"),
    ("SUPER + 0…4", "Cambiar de modo (0 = libre)"),
    ("SUPER + T", "Siguiente modo"),
]


class PaginaMosaico(Gtk.Box):
    def __init__(self, win):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        s = Seccion(win, "TILE// 20", "MOSAICO",
                    "Todas las ventanas forman una CINTA sin límite y la pantalla enseña N a la vez. Una nueva "
                    "entra a la derecha y la cinta avanza; las de fuera esperan en el dock sin perder su sitio. "
                    "Arrastrar una ventana a otro hueco la cambia de sitio con la que había.")
        rejilla = Gtk.Grid(column_spacing=10, row_spacing=10, column_homogeneous=True, margin_top=6)
        self.tarjetas = {}
        for i, (m, tit, desc) in enumerate(MODOS_MOSAICO):
            t = Tarjeta(tit, desc, lambda m=m: self._poner(modo=m))
            rejilla.attach(t, i % 2, i // 2, 1, 1)
            self.tarjetas[m] = t
        s.add(rejilla)
        s.add(_lbl("SEPARACIÓN ENTRE VENTANAS", "aj-key"))
        self.hueco = Chips([(h, f"{h} PX") for h in HUECOS], lambda v: self._poner(hueco=v))
        s.add(self.hueco)
        self.aviso = _lbl("", "aj-warn", wrap=True)
        s.add(self.aviso)
        self.append(s)

        a = Seccion(win, "KEY// 21", "ATAJOS")
        g = Gtk.Grid(column_spacing=24, row_spacing=8)
        for i, (k, d) in enumerate(ATAJOS_MOSAICO):
            g.attach(_lbl(k, "aj-key"), 0, i, 1, 1)
            g.attach(_lbl(d, "aj-txt"), 1, i, 1, 1)
        a.add(g)
        self.append(a)
        self.refrescar()

    def _poner(self, **cambios):
        cfg = mosaico.leer()
        cfg.update(cambios)
        energia.guardar_seccion("mosaico", cfg)
        self.refrescar()

    def refrescar(self):
        cfg = mosaico.leer()
        for m, t in self.tarjetas.items():
            _on(t, m == cfg["modo"])
        self.hueco.marcar(cfg["hueco"])
        self.aviso.set_text("" if sistema_proceso("neural-tile") else
                            "NEURAL//TILE (mosaico.py) no está corriendo: se guardará pero no se aplicará.")


class PaginaAnimaciones(Gtk.Box):
    MOMENTOS = [("open", "AL ABRIR"), ("close", "AL CERRAR"), ("minimize", "AL MINIMIZAR / RESTAURAR")]

    def __init__(self, win):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        s = Seccion(win, "ANIM// 22", "ANIMACIONES",
                    "Efecto de las ventanas en cada momento. HOLOGRAMA usa el color del tema activo.")
        self.chips = {}
        for clave, titulo in self.MOMENTOS:
            s.add(_lbl(titulo, "aj-key"))
            ch = Chips([(e, animaciones.NOMBRE[e]) for e in animaciones.PARA[clave]],
                       lambda v, c=clave: self._poner(**{c: v}))
            s.add(ch)
            self.chips[clave] = ch
        s.add(_lbl("VELOCIDAD", "aj-key"))
        self.vel = Chips([(k, n) for k, n, _h, _o in animaciones.VELOCIDADES],
                         lambda v: self._poner(velocidad=v))
        s.add(self.vel)
        fila = Gtk.Box(spacing=12, margin_top=8)
        probar = Gtk.Button(label="▶ PROBAR")
        probar.add_css_class("aj-chip")
        probar.connect("clicked", lambda *_: animaciones.probar())
        fila.append(probar)
        fila.append(_lbl("Abre una ventana de prueba que se cierra sola.", "aj-small"))
        s.add(fila)
        self.desc = _lbl("", "aj-small", wrap=True)
        s.add(self.desc)
        self.append(s)
        self.refrescar()

    def _poner(self, **cambios):
        cfg = animaciones.leer()
        cfg.update(cambios)
        animaciones.guardar(cfg)
        self.refrescar()

    def refrescar(self):
        cfg = animaciones.leer()
        for clave, ch in self.chips.items():
            ch.marcar(cfg[clave])
        self.vel.marcar(cfg["velocidad"])
        desc = {e: d for e, _n, d in animaciones.EFECTOS}
        usados = dict.fromkeys(cfg[c] for c, _t in self.MOMENTOS)
        self.desc.set_text("   ".join(f"{animaciones.NOMBRE.get(e, e)}: {desc.get(e, '')}" for e in usados))


def sistema_proceso(nombre: str) -> bool:
    for p in Path("/proc").glob("[0-9]*/comm"):
        try:
            if p.read_text().strip() == nombre:
                return True
        except OSError:
            pass
    return False


PAGINAS = [("INACTIVIDAD", PaginaInactividad), ("ENERGÍA", PaginaEnergia), ("BATERÍA", PaginaBateria),
           ("RED", PaginaRed), ("PANTALLA", PaginaPantalla), ("SONIDO", PaginaSonido),
           ("TECLADO Y RATÓN", PaginaEntrada), ("APARIENCIA", PaginaApariencia), ("MOSAICO", PaginaMosaico),
           ("ANIMACIONES", PaginaAnimaciones), ("SISTEMA", PaginaSistema)]
CADA_2S = ("ENERGÍA", "BATERÍA", "SISTEMA", "MOSAICO", "RED", "PANTALLA", "SONIDO", "INACTIVIDAD")


def _normal(texto: str) -> str:
    for a, b in (("Í", "I"), ("Á", "A"), ("Ó", "O"), ("É", "E"), ("Ú", "U"), (" ", ""), ("_", ""), ("-", "")):
        texto = texto.replace(a, b)
    return texto.upper()


class Ventana(VentanaNeural):
    def __init__(self, app: "App"):
        super().__init__(app, "SYS//CONFIG  ·  NEURAL//DECK", "NEURAL//AJUSTES", (1240, 820))
        self.set_size_request(640, 420)

        cuerpo = Gtk.Box(spacing=18, vexpand=True, margin_top=10)
        nav = Frame(self, Gtk.Orientation.VERTICAL, 12, ("tl", "br"), True, "CFG// 00")
        navb = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, margin_start=PAD + 8, margin_end=PAD + 8,
                       margin_top=PAD + 26, margin_bottom=PAD + 14)
        nav.append(navb)
        nav_sc = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True, propagate_natural_width=True)
        nav_sc.add_css_class("aj-scroll")
        nav_sc.add_css_class("nx-scroll")
        nav_sc.set_child(nav)
        cuerpo.append(nav_sc)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=120,
                               hexpand=True)
        self.paginas = {}
        self.nav = {}
        for nombre, cls in PAGINAS:
            pag = cls(self)
            sc = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.AUTOMATIC, vexpand=True)
            sc.add_css_class("aj-scroll")
            sc.add_css_class("nx-scroll")
            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, halign=Gtk.Align.FILL, margin_end=8)
            col.append(pag)
            sc.set_child(col)
            self.stack.add_named(sc, nombre)
            self.paginas[nombre] = pag
            b = Gtk.Button(label=nombre)
            b.add_css_class("aj-nav")
            b.connect("clicked", lambda _b, n=nombre: self.ir(n))
            navb.append(b)
            self.nav[nombre] = b
        cuerpo.append(self.stack)
        self.cuerpo.append(cuerpo)
        self.p_izq.set_text("ESC CERRAR  ·  LOS CAMBIOS SE GUARDAN Y APLICAN AL MOMENTO")

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", lambda _c, k, *_: (self.close(), True)[1] if k == Gdk.KEY_Escape else False)
        self.add_controller(keys)
        self.ir(PAGINAS[0][0])
        self.timer = GLib.timeout_add_seconds(2, self._tick)
        self.connect("close-request", lambda *_: (GLib.source_remove(self.timer), False)[1])

    def ir(self, nombre: str):
        self.stack.set_visible_child_name(nombre)
        for n, b in self.nav.items():
            _on(b, n == nombre)
        self.paginas[nombre].refrescar()

    def ir_a(self, pedida: str) -> None:
        for nombre, _cls in PAGINAS:
            if _normal(nombre) == _normal(pedida) or _normal(nombre).startswith(_normal(pedida)):
                self.ir(nombre)
                return

    def _tick(self):
        nombre = self.stack.get_visible_child_name()
        if nombre in CADA_2S and self.get_mapped():
            self.paginas[nombre].refrescar()
        return GLib.SOURCE_CONTINUE

    def nuevo_tema(self):
        super().nuevo_tema()

        def redibujar(w):
            w.queue_draw()
            c = w.get_first_child()
            while c is not None:
                redibujar(c)
                c = c.get_next_sibling()

        redibujar(self)


class App(AppNeural):
    def __init__(self):
        super().__init__(APP_ID)
        self.win = None

    def css_extra(self, t: dict) -> str:
        return _css(t)

    def crear_ventana(self, args, cwd):
        if self.win is None:
            self.win = Ventana(self)
            self.win.connect("destroy", lambda *_: setattr(self, "win", None))
        if "--pagina" in args:
            i = args.index("--pagina")
            if i + 1 < len(args):
                self.win.ir_a(args[i + 1])
        self.win.present()


if __name__ == "__main__":
    nombrar_proceso(PROC)
    GLib.set_prgname(APP_ID)
    sys.exit(App().run(sys.argv))
