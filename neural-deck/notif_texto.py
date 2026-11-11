"""Texto de las notificaciones, compartido por NEURAL//NOTIFY (notificaciones.py) y el
historial del panel (panel.py): saneado del markup y filtro de enlaces."""
from __future__ import annotations

import html
import re

import gi

gi.require_version("Pango", "1.0")
from gi.repository import GLib, Pango  # noqa: E402

_DESKTOP_OK = re.compile(r"[A-Za-z0-9._-]{1,200}")


def markup(body: str) -> str:
    """El cuerpo puede traer <b> <i> <u> <a href>: dejar esos (sin mas atributos)
    y escapar todo lo demas. OJO: Pango NO entiende <a> (los enlaces los procesa
    GtkLabel antes de Pango), asi que se valida con Pango el texto SIN los <a>,
    y los <a> se comprueban aparte (sin anidar, todos cerrados)."""
    out, pos, abierto = [], 0, False
    for m in re.finditer(r"<(/?)(b|i|u|a)(?:\s+href=\"([^\"]*)\")?\s*>", body):
        out.append(html.escape(html.unescape(body[pos:m.start()]), quote=False))
        cierra, tag, href = m.group(1), m.group(2), m.group(3)
        pos = m.end()
        if tag == "a":
            if cierra:
                if not abierto:
                    continue  # </a> suelto: fuera
                abierto = False
                out.append("</a>")
            elif href is not None and not abierto:
                abierto = True
                out.append(f'<a href="{html.escape(html.unescape(href), quote=True)}">')
            continue  # <a> sin href o anidado: fuera
        out.append(f"<{cierra}{tag}>")
    out.append(html.escape(html.unescape(body[pos:]), quote=False))
    if abierto:
        out.append("</a>")
    text = "".join(out)
    try:  # si las <b>/<i>/<u> no cuadran: texto plano
        Pango.parse_markup(re.sub(r"</?a\b[^>]*>", "", text), -1, "\0")
        return text
    except GLib.Error:
        return html.escape(re.sub(r"<[^>]+>", "", body), quote=False)


def es_web(uri: str) -> bool:
    """Solo se abren enlaces web: nada de file:// ni otros esquemas (los manda cualquier app)."""
    return uri.lower().startswith(("https://", "http://"))


def abrir_web(uri: str) -> bool:
    """Abre un enlace web con el navegador predeterminado SIN ventana padre (ver panel.py).
    Devuelve False si no es web (bloqueado) o no se pudo."""
    if not es_web(uri):
        return False
    from gi.repository import Gio
    try:
        return Gio.AppInfo.launch_default_for_uri(uri, None)
    except GLib.Error:
        return False


def desktop_valido(d) -> str:
    """El hint desktop-entry lo manda la app: aceptarlo solo si parece un id de .desktop."""
    d = str(d or "").removesuffix(".desktop")
    return d if _DESKTOP_OK.fullmatch(d) else ""
