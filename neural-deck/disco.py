#!/usr/bin/env python3
"""NEURAL//DISCO: discos, particiones y salud (vía UDisks2, sin root).

Izquierda: los discos (internos primero, luego USB). Derecha: el disco elegido,
con una barra de particiones a escala (clic para elegir) y el detalle de la parte
elegida: sistema de archivos, uso, punto de montaje. Montar / desmontar / expulsar
pasan por polkit. El disco del sistema sale bloqueado (🔒): solo se mira.
La lógica está en disco_motor.py.
"""
from __future__ import annotations

import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gio, GLib, Gtk, Pango, PangoCairo  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ventana import AppNeural, VentanaNeural, boton, lbl, nombrar_proceso, on  # noqa: E402
from estilo import MONO, css_color, source  # noqa: E402
import disco_motor as M  # noqa: E402
from analizador import Analizador  # noqa: E402
from disco_motor import tam_txt  # noqa: E402

APP_ID = "org.local.NeuralDisco"
PROC = "neural-disco"
BUS_TXT = {"usb": "USB", "nvme": "NVME", "ata": "SATA", "sdio": "SD", "loop": "LOOP"}
TABLA_TXT = {"gpt": "GPT", "dos": "MBR", "": "SIN TABLA"}
FS_TXT = {"crypto_LUKS": "LUKS", "vfat": "FAT32", "swap": "SWAP", "": "—"}
MIN_PX = 70  # ancho mínimo de cada trozo de la barra (si no, /boot ni se ve)


def fila(*hijos, sp=8, **kw) -> Gtk.Box:
    b = Gtk.Box(spacing=sp, **kw)
    for h in hijos:
        b.append(h)
    return b


def columna(*hijos, sp=6, **kw) -> Gtk.Box:
    b = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=sp, **kw)
    for h in hijos:
        b.append(h)
    return b


def vaciar(caja: Gtk.Widget):
    while (h := caja.get_first_child()) is not None:
        caja.remove(h)


def fs_txt(v: dict) -> str:
    t = v.get("tipo", "")
    if t == "vfat" and v.get("version", "").startswith("FAT1"):
        return v["version"].replace(" ", "")
    return FS_TXT.get(t, t.upper())


def segmentos(d: dict) -> list[dict]:
    """Trozos de la barra en orden: particiones (sin la extendida, sí sus lógicas) y huecos."""
    if d["contenido"]:
        return [{"clase": "vol", "v": d["contenido"], "inicio": 0, "tam": d["tam"]}]
    if not d["tabla"]:
        return [{"clase": "vacio", "inicio": 0, "tam": d["tam"]}]
    segs = [{"clase": "vol", "v": v, "inicio": v["inicio"], "tam": v["tam"]}
            for v in d["particiones"] if not v["extendida"]]
    segs += [{"clase": "libre", "inicio": i, "tam": t} for i, t in d["huecos"]]
    segs.sort(key=lambda s: s["inicio"])
    return segs


class Barra(Gtk.DrawingArea):
    """Las particiones del disco a escala; clic = elegir trozo."""

    def __init__(self, ventana: "Ventana"):
        super().__init__(hexpand=True)
        self.ven = ventana
        self.set_content_height(64)
        self.segs: list[dict] = []
        self.sel = -1
        self._cajas: list[tuple[float, float]] = []
        self.set_draw_func(self._dibujar)
        clic = Gtk.GestureClick()
        clic.connect("pressed", self._clic)
        self.add_controller(clic)
        self.set_cursor_from_name("pointer")

    def poner(self, segs: list[dict], sel: int):
        self.segs, self.sel = segs, sel
        self.queue_draw()

    def _anchos(self, w: float) -> list[float]:
        """Proporcional al tamaño, pero ningún trozo por debajo de MIN_PX."""
        n = len(self.segs)
        minimo = min(MIN_PX, w / max(n, 1))
        anchos = [0.0] * n
        libres = list(range(n))
        resto = w
        while libres:  # fijar los pequeños al mínimo y repartir el resto entre los grandes
            total = sum(self.segs[i]["tam"] for i in libres) or 1
            chicos = [i for i in libres if self.segs[i]["tam"] / total * resto < minimo]
            if not chicos:
                for i in libres:
                    anchos[i] = self.segs[i]["tam"] / total * resto
                break
            for i in chicos:
                anchos[i] = minimo
                resto -= minimo
                libres.remove(i)
        return anchos

    def _color(self, s: dict):
        t = self.ven.theme
        if s["clase"] != "vol":
            return t["muted"]
        tipo = s["v"]["tipo"]
        if tipo == "crypto_LUKS":
            return t["warn"]
        if tipo in ("vfat", "exfat", "ntfs"):
            return t["alt"]
        if tipo in ("swap", ""):
            return t["muted"]
        return t["accent"]

    def _dibujar(self, _a, cr, w, h):
        self._cajas = []
        if not self.segs:
            return
        t = self.ven.theme
        x = 0.0
        for i, (s, ancho) in enumerate(zip(self.segs, self._anchos(w))):
            c = self._color(s)
            x0, x1 = x + 1, x + ancho - 1
            self._cajas.append((x, x + ancho))
            # relleno
            source(cr, c, 0.10 if s["clase"] != "vol" else 0.22)
            cr.rectangle(x0, 0, x1 - x0, h)
            cr.fill()
            if s["clase"] != "vol":  # libre / vacío: rayado
                cr.save()
                cr.rectangle(x0, 0, x1 - x0, h)
                cr.clip()
                source(cr, c, 0.25)
                cr.set_line_width(1)
                for k in range(int(x0) - int(h), int(x1) + 1, 8):
                    cr.move_to(k, h)
                    cr.line_to(k + h, 0)
                cr.stroke()
                cr.restore()
            else:  # parte usada del sistema de archivos
                v = s["v"]["abierto"] or s["v"]
                if v.get("uso"):
                    u, tot = v["uso"]
                    source(cr, c, 0.45)
                    cr.rectangle(x0, h - 6, (x1 - x0) * u / tot, 6)
                    cr.fill()
            # borde
            elegido = i == self.sel
            source(cr, t["alt"] if elegido else c, 1.0 if elegido else 0.7)
            cr.set_line_width(2.5 if elegido else 1)
            cr.rectangle(x0 + 0.5, 0.5, x1 - x0 - 1, h - 1)
            cr.stroke()
            # texto
            if x1 - x0 > 40:
                lay = PangoCairo.create_layout(cr)
                lay.set_font_description(Pango.FontDescription.from_string(f"{MONO} 9"))
                lay.set_width(int((x1 - x0 - 10) * Pango.SCALE))
                lay.set_ellipsize(Pango.EllipsizeMode.END)
                if s["clase"] == "vol":
                    v = s["v"]
                    nombre = v["etiqueta"] or Path(v["dev"]).name
                    lay.set_text(f"{nombre}\n{fs_txt(v)} · {tam_txt(s['tam'])}", -1)
                else:
                    lay.set_text(f"{'LIBRE' if s['clase'] == 'libre' else 'SIN TABLA'}\n{tam_txt(s['tam'])}", -1)
                source(cr, t["text"], 0.95)
                cr.move_to(x0 + 6, 8)
                PangoCairo.show_layout(cr, lay)
            x += ancho

    def _clic(self, _g, _n, x, _y):
        for i, (a, b) in enumerate(self._cajas):
            if a <= x < b:
                self.ven.elegir_seg(i)
                return


class Tarjeta(Gtk.Box):
    def __init__(self, d: dict):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.add_css_class("nx-card")
        nombre = lbl(Path(d["dev"]).name, "nx-txt", xalign=0, hexpand=True)
        tam = lbl(tam_txt(d["tam"]), "nx-code on", xalign=1)
        self.append(fila(nombre, tam))
        self.append(lbl(d["modelo"] or "—", "nx-dim", xalign=0, ellipsize=Pango.EllipsizeMode.END))
        tags = fila(sp=4)
        tags.append(lbl(BUS_TXT.get(d["bus"], d["bus"].upper() or "?"), "nx-tag acc"))
        tags.append(lbl(TABLA_TXT.get(d["tabla"], d["tabla"].upper()), "nx-tag"))
        if d["protegido"]:
            tags.append(lbl("🔒 SISTEMA" if "sistema" in d["motivo"] else "🔒 SOLO LECTURA", "nx-tag warn"))
        elif d["aviso"]:
            tags.append(lbl("⚠ LLEVA UN SO", "nx-tag warn"))
        if d["salud"] and not d["salud"]["ok"]:
            tags.append(lbl("✖ SALUD", "nx-tag warn"))
        self.append(tags)


def etiqueta_mala(tipo: str, etq: str) -> str:
    if tipo == "vfat" and any(c in etq for c in '*?.,;:/\\|+=<>[]"'):
        return 'La etiqueta FAT no admite * ? . , ; : / \\ | + = < > [ ] "'
    return ""


class Campos:
    """Formato + etiqueta + contraseña LUKS: lo comparten «formatear» y «nueva partición»."""

    def __init__(self, formatos: list[str], tam: int, solo_tabla=False, sin_formato=False):
        self.claves = [k for k in M.FORMATOS if k in formatos]
        nombres = [M.FORMATOS[k][1] for k in self.claves]
        if solo_tabla:
            self.claves.append("vacia")
            nombres.append("— ninguno: solo tabla vacía —")
        if sin_formato:
            self.claves.append("ninguno")
            nombres.append("— sin formato —")
        self.formato = Gtk.DropDown.new_from_strings(nombres)
        if "vfat" in self.claves:  # USB pequeño → FAT32; grande → exFAT
            self.formato.set_selected(self.claves.index("exfat" if tam > 32e9 and "exfat" in self.claves else "vfat"))
        self.nota = lbl("", "nx-code", xalign=0, wrap=True, max_width_chars=44)
        self.etq = Gtk.Entry(placeholder_text="opcional (p. ej. DATOS)")
        self.c1 = Gtk.Entry(visibility=False, placeholder_text="contraseña")
        self.c2 = Gtk.Entry(visibility=False, placeholder_text="repítela")
        for e in (self.etq, self.c1, self.c2):
            e.add_css_class("nx-entry")
        self.caja_clave = columna(self.c1, self.c2, lbl("Si la olvidas, los datos se pierden para siempre.",
                                                        "nx-code warn", xalign=0), sp=4)
        self.l_clave = lbl("Contraseña", "nx-dim", xalign=0, valign=Gtk.Align.START)
        self.l_etq = lbl("Etiqueta", "nx-dim", xalign=0)
        self.al_cambiar(lambda *_: self._ajustar())
        self._ajustar()

    def clave(self) -> str:
        return self.claves[self.formato.get_selected()]

    def etiqueta(self) -> str:
        return self.etq.get_text().strip()

    def contrasena(self) -> str:
        return self.c1.get_text()

    def _ajustar(self):
        k = self.clave()
        con_fs = k in M.FORMATOS
        if con_fs:
            _t, _n, maxl, nota = M.FORMATOS[k]
            self.etq.set_max_length(maxl)
        else:
            nota = ("Deja el disco vacío, solo con la tabla: luego crea particiones a mano."
                    if k == "vacia" else "La partición se crea vacía, sin sistema de archivos.")
        self.nota.set_text(nota)
        for w in (self.etq, self.l_etq):
            w.set_visible(con_fs)
        for w in (self.caja_clave, self.l_clave):
            w.set_visible(k == "luks")

    def error(self) -> str:
        k = self.clave()
        if k not in M.FORMATOS:
            return ""
        err = etiqueta_mala(M.FORMATOS[k][0], self.etiqueta())
        if not err and k == "luks":
            if len(self.c1.get_text()) < 8:
                err = "La contraseña necesita al menos 8 caracteres"
            elif self.c1.get_text() != self.c2.get_text():
                err = "Las contraseñas no coinciden"
        return err

    def al_cambiar(self, f):
        self.formato.connect("notify::selected", f)
        for e in (self.etq, self.c1, self.c2):
            e.connect("changed", f)

    def poner_en(self, g: Gtk.Grid, sig):
        r = sig()
        g.attach(lbl("Formato", "nx-dim", xalign=0), 0, r, 1, 1)
        g.attach(self.formato, 1, r, 1, 1)
        g.attach(self.nota, 0, sig(), 2, 1)
        r = sig()
        g.attach(self.l_etq, 0, r, 1, 1)
        g.attach(self.etq, 1, r, 1, 1)
        r = sig()
        g.attach(self.l_clave, 0, r, 1, 1)
        g.attach(self.caja_clave, 1, r, 1, 1)


class Confirmar:
    """«Escribe sdb para confirmar» + error + botón peligroso (solo se activa si coincide)."""

    def __init__(self, objetivo: str):
        self.objetivo = objetivo
        self.entrada = Gtk.Entry(placeholder_text="escríbelo aquí")
        self.entrada.add_css_class("nx-entry")
        self.error = lbl("", "nx-code warn", xalign=0, wrap=True, max_width_chars=44)
        self.boton = None
        self._err = ""

    def poner(self, err: str):
        self._err = err
        self.error.set_text(err)
        if self.boton:
            self.boton.set_sensitive(not err and self.entrada.get_text().strip() == self.objetivo)

    def al_cambiar(self, f):
        self.entrada.connect("changed", f)

    def poner_en(self, g: Gtk.Grid, sig, texto: str, accion, pop):
        def ir():
            self.poner(self._err)
            if self.boton.get_sensitive():
                accion()
        self.boton = boton(texto, "nx-accion peligro", ir)
        self.entrada.connect("activate", lambda *_: ir())
        g.attach(Gtk.Box(css_classes=["nx-sep"], margin_top=2), 0, sig(), 2, 1)
        r = sig()
        g.attach(lbl(f"Escribe «{self.objetivo}»", "nx-dim", xalign=0), 0, r, 1, 1)
        g.attach(self.entrada, 1, r, 1, 1)
        g.attach(self.error, 0, sig(), 2, 1)
        g.attach(fila(self.boton, boton("CANCELAR", "nx-accion", pop.popdown), sp=6, halign=Gtk.Align.END),
                 0, sig(), 2, 1)


class Ventana(VentanaNeural):
    def __init__(self, app):
        super().__init__(app, "SYS//DISCO · UDISKS2", "NEURAL//DISCO", (1180, 720))
        self.motor = M.Discos(self.refrescar)
        self.discos: dict[str, dict] = {}
        self.filas: dict[str, Gtk.ListBoxRow] = {}
        self.sel: str | None = None
        self.sel_seg = 0
        self.segs: list[dict] = []

        self.t_discos = boton("DISCOS", "nx-tab on", lambda: self.pestana("discos"))
        self.t_espacio = boton("ESPACIO", "nx-tab", lambda: self.pestana("espacio"))
        self.cab_centro.append(self.t_discos)
        self.cab_centro.append(self.t_espacio)
        self.cab_centro.append(Gtk.Box(hexpand=True))
        self.b_refrescar = boton("REFRESCAR", "nx-accion", self.refrescar)
        self.cab_centro.append(self.b_refrescar)

        self.pestanas = Gtk.Stack(vexpand=True, transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.cuerpo.append(self.pestanas)
        cuerpo = Gtk.Box(spacing=12, vexpand=True, margin_top=8)
        self.pestanas.add_named(cuerpo, "discos")
        self.analizador = Analizador(self)
        self.pestanas.add_named(self.analizador, "espacio")
        self.lista = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.lista.add_css_class("nx-lista")
        self.lista.connect("row-selected", self._elegido)
        sc = Gtk.ScrolledWindow(child=self.lista, hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        sc.add_css_class("nx-scroll")
        sc.set_size_request(300, -1)
        cuerpo.append(columna(lbl("DISCOS", "nx-h2", xalign=0), sc))

        self.detalle = Gtk.Stack(hexpand=True, vexpand=True)
        self.detalle.add_css_class("nx-lado")
        self.detalle.add_named(columna(lbl("Ningún disco elegido", "nx-dim"), valign=Gtk.Align.CENTER), "nada")
        dsc = Gtk.ScrolledWindow(child=self._panel(), hscrollbar_policy=Gtk.PolicyType.NEVER)
        dsc.add_css_class("nx-scroll")
        self.detalle.add_named(dsc, "disco")
        cuerpo.append(self.detalle)

        self.refrescar()

    def pestana(self, cual: str):
        self.pestanas.set_visible_child_name(cual)
        on(self.t_discos, "on", cual == "discos")
        on(self.t_espacio, "on", cual == "espacio")
        self.b_refrescar.set_visible(cual == "discos")

    def _analizar(self):
        v = self._vol_activo()
        if v and v["puntos"]:
            self.pestana("espacio")
            self.analizador.analizar(v["puntos"][0])

    # ------------------------------------------------------------ panel derecho
    def _panel(self) -> Gtk.Widget:
        self.d_nombre = lbl("", "nx-h1", xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        self.d_tags = fila(sp=4)
        self.d_info = lbl("", "nx-dim", xalign=0, wrap=True)
        self.d_aviso = lbl("", "nx-code warn", xalign=0, wrap=True)
        self.d_salud = lbl("", "nx-code", xalign=0)
        self.barra = Barra(self)
        self.b_expulsar = boton("EXPULSAR DISCO", "nx-accion", self._expulsar,
                                "Desmonta todo y apaga el disco: ya se puede desenchufar")
        self.b_form_disco = boton("FORMATEAR DISCO ENTERO", "nx-accion peligro",
                                  lambda: self._pop_formatear(self.b_form_disco, True),
                                  "Tabla nueva + una sola partición que lo ocupa todo")
        self.formatos = self.motor.formatos()
        self._trabajando = False
        self.t_prog = Gtk.ProgressBar(hexpand=True, visible=False)
        self.t_prog.add_css_class("nx-prog")
        self.t_txt = lbl("", "nx-code warn", xalign=0, visible=False)

        # parte elegida
        self.p_titulo = lbl("", "nx-txt", xalign=0, hexpand=True)
        self.p_rejilla = Gtk.Grid(column_spacing=14, row_spacing=4)
        self.p_uso = Gtk.ProgressBar(hexpand=True)
        self.p_uso.add_css_class("nx-prog")
        self.p_uso_txt = lbl("", "nx-code", xalign=0)
        self.b_montar = boton("MONTAR", "nx-accion fuerte", self._montar)
        self.b_desmontar = boton("DESMONTAR", "nx-accion", self._desmontar)
        self.b_abrir = boton("ABRIR CARPETA", "nx-accion", self._abrir)
        self.b_analizar = boton("ANALIZAR ESPACIO", "nx-accion", self._analizar, "Qué carpetas ocupan más")
        self.b_form_parte = boton("FORMATEAR PARTICIÓN", "nx-accion peligro",
                                  lambda: self._pop_formatear(self.b_form_parte, False),
                                  "Borra solo esta partición; el resto del disco no se toca")
        self.b_etiqueta = boton("ETIQUETA", "nx-accion", lambda: self._pop_etiqueta(self.b_etiqueta),
                                "Cambiar el nombre que se ve al conectarlo")
        self.b_borrar = boton("BORRAR PARTICIÓN", "nx-accion peligro", lambda: self._pop_borrar(self.b_borrar))
        self.b_nueva = boton("+ NUEVA PARTICIÓN", "nx-accion fuerte", lambda: self._pop_nueva(self.b_nueva))
        self.p_acciones = fila(self.b_montar, self.b_desmontar, self.b_abrir, self.b_analizar, sp=6)
        self.p_acciones2 = fila(self.b_nueva, self.b_etiqueta, self.b_form_parte, self.b_borrar, sp=6)
        self.p_nota = lbl("", "nx-code", xalign=0, wrap=True)

        self.partes = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)

        sep = lambda: Gtk.Box(css_classes=["nx-sep"], margin_top=4)  # noqa: E731
        return columna(
            fila(self.d_nombre, self.d_tags), self.d_info, self.d_salud, self.d_aviso,
            self.barra, self.t_prog, self.t_txt, sep(),
            lbl("ELEGIDA", "nx-h2", xalign=0), self.p_titulo, self.p_rejilla,
            self.p_uso, self.p_uso_txt, self.p_acciones, self.p_acciones2, self.p_nota, sep(),
            lbl("TODAS LAS PARTES", "nx-h2", xalign=0), self.partes, sep(),
            fila(self.b_form_disco, self.b_expulsar, sp=10),
            sp=8, margin_start=4, margin_end=8, margin_bottom=8)

    # ------------------------------------------------------------ lista
    def refrescar(self):
        try:
            nuevos = {d["ruta"]: d for d in self.motor.discos()}
        except GLib.Error as e:
            self.aviso(f"udisks: {e.message}", True, 9000)
            return
        if list(nuevos) != list(self.discos) or any(
                self._firma(nuevos[r]) != self._firma(self.discos[r]) for r in nuevos):
            self.lista.disconnect_by_func(self._elegido)
            while (r := self.lista.get_row_at_index(0)) is not None:
                self.lista.remove(r)
            self.filas = {}
            for ruta, d in nuevos.items():
                row = Gtk.ListBoxRow(child=Tarjeta(d))
                row.ruta = ruta
                self.lista.append(row)
                self.filas[ruta] = row
            self.lista.connect("row-selected", self._elegido)
        self.discos = nuevos
        n_usb = sum(1 for d in nuevos.values() if d["bus"] == "usb" or d["extraible"])
        self.p_izq.set_text(f"{len(nuevos)} discos · {n_usb} externos · udisks2")
        if self.sel not in self.filas:
            self.sel = next(iter(self.filas), None)
            self.sel_seg = 0
        if self.sel:
            self.lista.select_row(self.filas[self.sel])
            self.detalle.set_visible_child_name("disco")
            self._pintar()
        else:
            self.detalle.set_visible_child_name("nada")

    @staticmethod
    def _firma(d: dict):
        return (d["tam"], d["protegido"], d["aviso"], d["tabla"], d["salud"] and d["salud"]["ok"])

    def _elegido(self, _lb, row):
        if row is None or row.ruta == self.sel:
            return
        self.sel = row.ruta
        self.sel_seg = 0
        self.detalle.set_visible_child_name("disco")
        self._pintar()

    def elegir_seg(self, i: int):
        self.sel_seg = i
        self._pintar()

    # ------------------------------------------------------------ pintar
    def _pintar(self):
        d = self.discos.get(self.sel)
        if not d:
            return
        self.segs = segmentos(d)
        self.sel_seg = min(self.sel_seg, len(self.segs) - 1)
        self.d_nombre.set_text(d["dev"])
        vaciar(self.d_tags)
        self.d_tags.append(lbl(BUS_TXT.get(d["bus"], d["bus"].upper() or "?"), "nx-tag acc"))
        self.d_tags.append(lbl(TABLA_TXT.get(d["tabla"], d["tabla"].upper()), "nx-tag"))
        tipo = "SSD" if d["rotacion"] == 0 else ("HDD" if d["rotacion"] > 0 else "")
        datos = [d["modelo"] or "modelo desconocido", tam_txt(d["tam"]), tipo,
                 f"serie {d['serie']}" if d["serie"] else ""]
        self.d_info.set_text(" · ".join(x for x in datos if x))
        s = d["salud"]
        if s:
            partes = ["SALUD " + ("OK" if s["ok"] else "¡FALLO!")]
            if s["temp_c"] is not None:
                partes.append(f"{s['temp_c']} °C")
            if s["horas"]:
                partes.append(f"{s['horas']} h encendido")
            if s["aviso"]:
                partes.append(s["aviso"])
            self.d_salud.set_text(" · ".join(partes))
            on(self.d_salud, "warn", not s["ok"])
            on(self.d_salud, "on", s["ok"])
        else:
            self.d_salud.set_text("Salud: el disco no la informa (normal en memorias USB)")
            on(self.d_salud, "warn", False)
            on(self.d_salud, "on", False)
        aviso = "🔒 " + d["motivo"] + ": solo se puede mirar." if d["protegido"] else (
            "⚠ " + d["aviso"] if d["aviso"] else "")
        self.d_aviso.set_text(aviso)
        self.d_aviso.set_visible(bool(aviso))
        self.b_expulsar.set_visible(not d["protegido"] and (d["apagable"] or d["bus"] == "usb"))
        self.b_form_disco.set_visible(not d["protegido"])
        for b in (self.b_form_disco, self.b_expulsar):
            b.set_sensitive(not self._trabajando)
        self.barra.poner(self.segs, self.sel_seg)
        self._pintar_parte(d)
        self._pintar_partes(d)

    def _filas_rejilla(self, pares):
        vaciar(self.p_rejilla)
        for i, (k, v) in enumerate(pares):
            self.p_rejilla.attach(lbl(k, "nx-dim", xalign=0), 0, i, 1, 1)
            self.p_rejilla.attach(lbl(v, "nx-txt", xalign=0, selectable=True, wrap=True), 1, i, 1, 1)

    def _vol_activo(self) -> dict | None:
        """El volumen con sistema de archivos de la parte elegida (dentro del LUKS si está abierto)."""
        if not self.segs or self.segs[self.sel_seg]["clase"] != "vol":
            return None
        v = self.segs[self.sel_seg]["v"]
        return v["abierto"] or v

    def _pintar_parte(self, d: dict):
        seg = self.segs[self.sel_seg] if self.segs else None
        self.p_uso.set_visible(False)
        self.p_uso_txt.set_visible(False)
        self.b_montar.set_visible(False)
        self.b_desmontar.set_visible(False)
        self.b_abrir.set_visible(False)
        self.b_analizar.set_visible(False)
        for b in (self.b_form_parte, self.b_etiqueta, self.b_borrar, self.b_nueva):
            b.set_visible(False)
            b.set_sensitive(not self._trabajando)
        self.p_nota.set_text("")
        if seg is None:
            self.p_titulo.set_text("")
            self._filas_rejilla([])
            return
        if seg["clase"] == "libre":
            self.p_titulo.set_text(f"Espacio libre · {tam_txt(seg['tam'])}")
            self._filas_rejilla([("Empieza en", f"{seg['inicio'] // M.MIB} MiB"),
                                 ("Tamaño", tam_txt(seg["tam"]))])
            if d["protegido"]:
                return
            self.b_nueva.set_visible(True)
            if d["tabla"] == "dos" and sum(1 for v in d["particiones"] if not v["logica"]) >= 4:
                self.b_nueva.set_sensitive(False)
                self.p_nota.set_text("MBR solo admite 4 particiones: para más, formatea el disco en GPT.")
            return
        if seg["clase"] == "vacio":
            self.p_titulo.set_text("Disco sin tabla de particiones")
            self._filas_rejilla([("Tamaño", tam_txt(seg["tam"]))])
            self.p_nota.set_text("Para usarlo: FORMATEAR DISCO ENTERO (abajo) le crea tabla y partición.")
            return

        v = seg["v"]
        dentro = v["abierto"]
        titulo = v["etiqueta"] or v.get("nombre") or Path(v["dev"]).name
        self.p_titulo.set_text(f"{titulo} · {fs_txt(v)} · {tam_txt(seg['tam'])}")
        pares = [("Dispositivo", v["dev"])]
        if "numero" in v:
            pares.append(("Partición", f"nº {v['numero']}" + (f" · nombre GPT «{v['nombre']}»" if v["nombre"] else "")
                          + (" · lógica" if v["logica"] else "")))
        pares.append(("Formato", (fs_txt(v) + (" " + v["version"] if v["version"] and v["tipo"] != "vfat" else ""))
                      if v["tipo"] else "sin formato (o desconocido)"))
        if v["etiqueta"]:
            pares.append(("Etiqueta", v["etiqueta"]))
        if v["uuid"]:
            pares.append(("UUID", v["uuid"]))
        if v["cifrado"]:
            pares.append(("Cifrado", f"abierto como {dentro['dev']} ({fs_txt(dentro)})" if dentro else "cerrado"))
        act = dentro or v
        pares.append(("Montada en", ", ".join(act["puntos"]) if act["puntos"] else "no montada"))
        if act["swap"]:
            pares.append(("Swap", "en uso"))
        self._filas_rejilla(pares)

        if act.get("uso"):
            u, tot = act["uso"]
            self.p_uso.set_fraction(u / tot)
            self.p_uso_txt.set_text(f"{tam_txt(u)} usados de {tam_txt(tot)} · {tam_txt(tot - u)} libres")
            self.p_uso.set_visible(True)
            self.p_uso_txt.set_visible(True)

        self.b_analizar.set_visible(bool(act["puntos"]) and not act["swap"])
        if d["protegido"]:
            return
        es_part = "numero" in v
        self.b_form_parte.set_visible(es_part)
        self.b_borrar.set_visible(es_part)
        if es_part and not d["huecos"]:
            self.p_nota.set_text("Para crear otra partición hace falta espacio libre: borra esta, o usa "
                                 "FORMATEAR DISCO ENTERO → «solo tabla vacía» y luego pulsa el hueco de la barra.")
        es_fs = act["uso_id"] == "filesystem" or act["tipo"] in ("vfat", "exfat", "ntfs", "ext4", "btrfs", "xfs")
        if v["cifrado"] and not dentro:
            self.p_nota.set_text("Cifrada y cerrada: ábrela desde NEURAL//FILES (pide la clave).")
        elif es_fs:
            self.b_etiqueta.set_visible(es_part and act["tipo"] in ("vfat", "exfat", "ext4", "ntfs", "btrfs"))
            montada = bool(act["puntos"])
            self.b_montar.set_visible(not montada)
            self.b_desmontar.set_visible(montada)
            self.b_abrir.set_visible(montada)

    def _pintar_partes(self, d: dict):
        vaciar(self.partes)
        for i, s in enumerate(self.segs):
            if s["clase"] == "vol":
                v = s["v"]
                act = v["abierto"] or v
                txt = f"{Path(v['dev']).name:<12} {fs_txt(v):<7} {tam_txt(s['tam']):>9}"
                extra = v["etiqueta"] or ""
                if act["puntos"]:
                    extra += ("  → " if extra else "→ ") + act["puntos"][0]
            elif s["clase"] == "libre":
                txt, extra = f"{'(libre)':<12} {'':<7} {tam_txt(s['tam']):>9}", ""
            else:
                txt, extra = f"{'(sin tabla)':<12} {'':<7} {tam_txt(s['tam']):>9}", ""
            b = Gtk.Button()
            b.add_css_class("nx-item")
            b.set_child(fila(lbl(txt, "nx-code" + (" on" if i == self.sel_seg else ""), xalign=0),
                             lbl(extra, "nx-dim", xalign=0, ellipsize=Pango.EllipsizeMode.END, hexpand=True)))
            b.connect("clicked", lambda _b, i=i: self.elegir_seg(i))
            self.partes.append(b)
        # la extendida (MBR) no sale en la barra: se nombra aparte
        for v in d["particiones"]:
            if v["extendida"]:
                self.partes.append(lbl(f"{Path(v['dev']).name}: partición extendida "
                                       f"({tam_txt(v['tam'])}) que contiene las lógicas", "nx-code", xalign=0))

    # ------------------------------------------------------------ acciones
    def _fin(self, ok_txt: str):
        def f(ok, texto):
            self.aviso(ok_txt if ok else f"ERROR: {texto}", not ok, 4000 if ok else 9000)
            self.refrescar()
        return f

    def _montar(self):
        v = self._vol_activo()
        if v:
            self.aviso("Montando…")
            self.motor.montar(v["ruta"], lambda ok, t: self._fin(f"Montada en {t}")(ok, t))

    def _desmontar(self):
        v = self._vol_activo()
        if v:
            self.motor.desmontar(v["ruta"], self._fin("Desmontada"))

    def _abrir(self):
        v = self._vol_activo()
        if v and v["puntos"]:
            Gio.AppInfo.launch_default_for_uri(Path(v["puntos"][0]).as_uri(), None)

    def _expulsar(self):
        d = self.discos.get(self.sel)
        if d:
            self.aviso("Expulsando…")
            self.motor.expulsar(d["drive"], self._fin(f"{Path(d['dev']).name} expulsado: ya puedes desenchufarlo"))

    # ------------------------------------------------------------ formularios
    def _popover(self, ancla: Gtk.Widget, construir) -> Gtk.Popover:
        """Popover NEURAL con una rejilla; construir(pop, g, sig) la rellena (sig() = siguiente fila)."""
        pop = Gtk.Popover()
        pop.add_css_class("nx-menu")
        g = Gtk.Grid(column_spacing=10, row_spacing=6, margin_start=8, margin_end=8, margin_top=8, margin_bottom=8)
        n = iter(range(1000))
        construir(pop, g, lambda: next(n))
        pop.set_child(g)
        pop.set_parent(ancla)
        pop.connect("closed", lambda p: GLib.idle_add(p.unparent))
        pop.popup()
        return pop

    def _pop_formatear(self, ancla: Gtk.Widget, entero: bool):
        d = self.discos.get(self.sel)
        if not d or d["protegido"] or self._trabajando:
            return
        if entero:
            objetivo, ruta, tam = Path(d["dev"]).name, d["ruta"], d["tam"]
            n = len(d["particiones"])
            peligro = (f"Se borrará TODO lo que hay en {objetivo} ({d['modelo'] or '?'} · {tam_txt(tam)})"
                       + (f": {n} partición(es)." if n else "."))
        else:
            seg = self.segs[self.sel_seg]
            v = seg["v"]
            objetivo, ruta, tam = Path(v["dev"]).name, v["ruta"], seg["tam"]
            peligro = (f"Se borrará {objetivo} ({fs_txt(v)} · {tam_txt(tam)}"
                       + (f" · «{v['etiqueta']}»" if v["etiqueta"] else "") + "). El resto del disco no se toca.")

        def construir(pop, g, sig):
            campos = Campos(self.formatos, tam, solo_tabla=entero)
            f_tabla = Gtk.DropDown.new_from_strings(["MBR · máxima compatibilidad", "GPT · moderno (>2 TB, UEFI)"])
            f_tabla.set_selected(0 if tam < 2e12 else 1)
            f_ceros = Gtk.CheckButton(label="Borrado seguro: escribir ceros (lento)")
            f_ceros.set_tooltip_text("Sin esto solo se borra el índice: los datos viejos aún se podrían recuperar")
            f_so = Gtk.CheckButton(label="Sé que este disco lleva otro sistema y quiero borrarlo")
            conf = Confirmar(objetivo)

            def validar(*_):
                err = campos.error()
                if not err and entero and d["aviso"] and not f_so.get_active():
                    err = "Marca la casilla del otro sistema"
                conf.poner(err)

            def ir():
                k = campos.clave()
                etq, clave, ceros = campos.etiqueta(), campos.contrasena(), f_ceros.get_active()
                tabla = ("dos", "gpt")[f_tabla.get_selected()]
                pop.popdown()
                nombre = "tabla vacía " + TABLA_TXT[tabla] if k == "vacia" else M.FORMATOS[k][1]
                self._empezar(d["ruta"], f"Formateando {objetivo} ({nombre})"
                              + (" escribiendo ceros, tarda" if ceros else "") + "… no lo desenchufes")
                fin = self._fin_trabajo(f"{objetivo} listo: {nombre}")
                if entero:
                    self.motor.formatear_disco(ruta, tabla, k, etq, clave, ceros, fin)
                else:
                    self.motor.formatear_parte(ruta, k, etq, clave, ceros, fin)

            g.attach(lbl("FORMATEAR " + ("DISCO ENTERO" if entero else "PARTICIÓN"), "nx-h2", xalign=0), 0, sig(), 2, 1)
            g.attach(lbl(peligro, "nx-txt", xalign=0, wrap=True, max_width_chars=44), 0, sig(), 2, 1)
            if entero and d["aviso"]:
                g.attach(lbl("⚠ " + d["aviso"], "nx-code warn", xalign=0, wrap=True, max_width_chars=44), 0, sig(), 2, 1)
            campos.poner_en(g, sig)
            if entero:
                r = sig()
                g.attach(lbl("Tabla", "nx-dim", xalign=0), 0, r, 1, 1)
                g.attach(f_tabla, 1, r, 1, 1)
            g.attach(f_ceros, 0, sig(), 2, 1)
            if entero and d["aviso"]:
                g.attach(f_so, 0, sig(), 2, 1)
            conf.poner_en(g, sig, "FORMATEAR", ir, pop)
            campos.al_cambiar(validar)
            conf.al_cambiar(validar)
            f_so.connect("toggled", validar)
            validar()
            conf.entrada.grab_focus()
        self._popover(ancla, construir)

    def _pop_nueva(self, ancla: Gtk.Widget):
        d = self.discos.get(self.sel)
        seg = self.segs[self.sel_seg] if self.segs else None
        if not d or d["protegido"] or self._trabajando or not seg or seg["clase"] != "libre":
            return
        max_gb = seg["tam"] / 1e9

        def construir(pop, g, sig):
            campos = Campos(self.formatos, seg["tam"], sin_formato=True)
            f_tam = Gtk.SpinButton.new_with_range(0.01, max(0.01, round(max_gb, 2)), 0.1)
            f_tam.set_digits(2)
            f_tam.set_width_chars(7)
            f_tam.set_value(round(max_gb, 2))
            f_tam.add_css_class("nx-entry")
            error = lbl("", "nx-code warn", xalign=0, wrap=True, max_width_chars=44)
            b_ir = boton("CREAR", "nx-accion fuerte", lambda: ir())

            def validar(*_):
                err = campos.error()
                error.set_text(err)
                b_ir.set_sensitive(not err)

            def ir():
                validar()
                if not b_ir.get_sensitive():
                    return
                k = campos.clave()
                gb = f_tam.get_value()
                tam = seg["tam"] if gb >= round(max_gb, 2) else int(gb * 1e9)
                pop.popdown()
                self._empezar(d["ruta"], f"Creando partición de {tam_txt(tam)}…")
                self.motor.crear_parte(d["ruta"], seg["inicio"], tam, None if k == "ninguno" else k,
                                       campos.etiqueta(), campos.contrasena(),
                                       self._fin_trabajo(f"Partición de {tam_txt(tam)} creada"))

            g.attach(lbl("NUEVA PARTICIÓN", "nx-h2", xalign=0), 0, sig(), 2, 1)
            g.attach(lbl(f"En el espacio libre de {Path(d['dev']).name}: {tam_txt(seg['tam'])}. "
                         "No se borra nada.", "nx-txt", xalign=0, wrap=True, max_width_chars=44), 0, sig(), 2, 1)
            r = sig()
            g.attach(lbl("Tamaño (GB)", "nx-dim", xalign=0), 0, r, 1, 1)
            g.attach(fila(f_tam, boton("TODO", "nx-chip", lambda: f_tam.set_value(round(max_gb, 2))), sp=4), 1, r, 1, 1)
            campos.poner_en(g, sig)
            g.attach(error, 0, sig(), 2, 1)
            g.attach(fila(b_ir, boton("CANCELAR", "nx-accion", pop.popdown), sp=6, halign=Gtk.Align.END),
                     0, sig(), 2, 1)
            campos.al_cambiar(validar)
            validar()
        self._popover(ancla, construir)

    def _pop_borrar(self, ancla: Gtk.Widget):
        d = self.discos.get(self.sel)
        v = self._parte_sel()
        if not d or d["protegido"] or self._trabajando or v is None:
            return
        objetivo = Path(v["dev"]).name

        def construir(pop, g, sig):
            conf = Confirmar(objetivo)

            def ir():
                pop.popdown()
                self._empezar(d["ruta"], f"Borrando {objetivo}…")
                self.motor.borrar_parte(v["ruta"], self._fin_trabajo(f"{objetivo} borrada: queda espacio libre"))

            g.attach(lbl("BORRAR PARTICIÓN", "nx-h2", xalign=0), 0, sig(), 2, 1)
            g.attach(lbl(f"Se borrará {objetivo} ({fs_txt(v)} · {tam_txt(v['tam'])}"
                         + (f" · «{v['etiqueta']}»" if v["etiqueta"] else "")
                         + ") con todo lo que tiene. Su espacio quedará libre.", "nx-txt", xalign=0, wrap=True,
                         max_width_chars=44), 0, sig(), 2, 1)
            conf.poner_en(g, sig, "BORRAR", ir, pop)
            conf.al_cambiar(lambda *_: conf.poner(""))
            conf.poner("")
            conf.entrada.grab_focus()
        self._popover(ancla, construir)

    def _pop_etiqueta(self, ancla: Gtk.Widget):
        v = self._parte_sel()
        d = self.discos.get(self.sel)
        if not d or d["protegido"] or self._trabajando or v is None:
            return
        act = v["abierto"] or v
        tipo = act["tipo"]
        maxl = next((m for _k, (t, _n, m, _x) in M.FORMATOS.items() if t == tipo), 16)

        def construir(pop, g, sig):
            e = Gtk.Entry(text=act["etiqueta"], max_length=maxl)
            e.add_css_class("nx-entry")
            error = lbl("", "nx-code warn", xalign=0, wrap=True, max_width_chars=36)
            b_ir = boton("CAMBIAR", "nx-accion fuerte", lambda: ir())

            def validar(*_):
                err = etiqueta_mala(tipo, e.get_text())
                error.set_text(err)
                b_ir.set_sensitive(not err)

            def ir():
                if not b_ir.get_sensitive():
                    return
                pop.popdown()
                nueva = e.get_text().strip()
                self.motor.etiquetar(v["ruta"], nueva, self._fin(f"Etiqueta: «{nueva}»" if nueva else "Etiqueta quitada"))

            g.attach(lbl("CAMBIAR ETIQUETA", "nx-h2", xalign=0), 0, sig(), 2, 1)
            g.attach(lbl(f"{fs_txt(act)} · máx. {maxl} letras" + (" · se guarda en MAYÚSCULAS" if tipo == "vfat" else ""),
                         "nx-code", xalign=0), 0, sig(), 2, 1)
            g.attach(e, 0, sig(), 2, 1)
            g.attach(error, 0, sig(), 2, 1)
            g.attach(fila(b_ir, boton("CANCELAR", "nx-accion", pop.popdown), sp=6, halign=Gtk.Align.END),
                     0, sig(), 2, 1)
            e.connect("changed", validar)
            e.connect("activate", lambda *_: ir())
            validar()
            e.grab_focus()
        self._popover(ancla, construir)

    def _parte_sel(self) -> dict | None:
        """La partición elegida (con número), o None si es hueco / disco sin tabla."""
        if not self.segs or self.segs[self.sel_seg]["clase"] != "vol":
            return None
        v = self.segs[self.sel_seg]["v"]
        return v if "numero" in v else None

    def _empezar(self, ruta_disco: str, texto: str):
        self._trabajando = True
        self._ruta_trabajo = ruta_disco
        self.t_txt.set_text(texto)
        self.t_txt.set_visible(True)
        self.t_prog.set_visible(True)
        self.t_prog.set_fraction(0)
        self._pintar()
        GLib.timeout_add(400, self._latido)

    def _latido(self):
        if not self._trabajando:
            return GLib.SOURCE_REMOVE
        p = self.motor.progreso(self._ruta_trabajo)
        if p is None:
            self.t_prog.pulse()
        else:
            self.t_prog.set_fraction(p)
        return GLib.SOURCE_CONTINUE

    def _fin_trabajo(self, ok_txt: str):
        def f(ok, texto):
            self._trabajando = False
            self.t_prog.set_visible(False)
            self.t_txt.set_visible(False)
            self.aviso(ok_txt if ok else f"ERROR: {texto}", not ok, 6000 if ok else 15000)
            self.refrescar()
            self._pintar()
        return f


class App(AppNeural):
    def __init__(self):
        super().__init__(APP_ID)

    def css_extra(self, t):
        return (f".nx-tag.on {{ color: {css_color(t['alt'])}; }}\n"
                f"checkbutton {{ color: {css_color(t['text'])}; }}\n")

    def crear_ventana(self, args, cwd):
        ventanas = self.get_windows()
        if ventanas:
            self.traer(ventanas[0])
            return
        Ventana(self).present()


if __name__ == "__main__":
    nombrar_proceso(PROC)
    GLib.set_prgname(APP_ID)
    sys.exit(App().run(sys.argv))
