"""Resuelve las apps de los paneles (nombre + icono en base64) y las lanza.
El dock.json de la version anterior solo se usa para sembrar el primer panel
de apps; si no existe se siembra desde los favoritos de GNOME, o en el LFS
(sin GNOME) desde FALLBACK_SEED."""
from __future__ import annotations

import base64
import json
import mimetypes

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, Gtk, Gdk

from board import CONFIG_DIR

CONFIG_PATH = CONFIG_DIR / "dock.json"
FALLBACK_SEED = ["org.mozilla.firefox.desktop", "foot.desktop", "org.codeberg.dnkl.foot.desktop"]


def _icon_to_data_uri(gicon: Gio.Icon | None, size: int = 64) -> str | None:
    if gicon is None:
        return None
    display = Gdk.Display.get_default()
    if display is None:
        return None
    theme = Gtk.IconTheme.get_for_display(display)
    paintable = theme.lookup_by_gicon(
        gicon, size, 1, Gtk.TextDirection.NONE, Gtk.IconLookupFlags.PRELOAD
    )
    if paintable is None:
        return None
    file = paintable.get_file()
    if file is None:
        return None
    path = file.get_path()
    if not path:
        return None
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return None
    mime, _ = mimetypes.guess_type(path)
    mime = mime or "image/png"
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _seed_from_favorites() -> list[str]:
    # Gio.Settings.new() aborta el proceso si el esquema no esta instalado
    source = Gio.SettingsSchemaSource.get_default()
    if source is None or source.lookup("org.gnome.shell", True) is None:
        return FALLBACK_SEED
    settings = Gio.Settings.new("org.gnome.shell")
    return list(settings.get_strv("favorite-apps"))


def seed_app_ids() -> list[str]:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return _seed_from_favorites()


def _app_dict(app_id: str, info: Gio.DesktopAppInfo) -> dict:
    return {
        "id": app_id,
        "name": info.get_display_name() or app_id,
        "icon": _icon_to_data_uri(info.get_icon()),
    }


def resolve_apps(app_ids: list[str]) -> dict[str, dict]:
    """{id: {id, name, icon}} de las apps que siguen instaladas. Las
    desinstaladas se omiten pero su id queda en el panel por si vuelven."""
    apps = {}
    for app_id in app_ids:
        try:
            info = Gio.DesktopAppInfo.new(app_id)
        except TypeError:
            info = None
        if info is None:
            continue
        apps[app_id] = _app_dict(app_id, info)
    return apps


def list_installable_apps() -> list[dict]:
    """Todas las apps instaladas con lanzador visible, para el selector."""
    apps = []
    for info in Gio.AppInfo.get_all():
        if not isinstance(info, Gio.DesktopAppInfo):
            continue
        if info.get_nodisplay() or not info.should_show():
            continue
        app_id = info.get_id()
        if not app_id:
            continue
        apps.append(_app_dict(app_id, info))
    apps.sort(key=lambda a: a["name"].lower())
    return apps


def launch_app(app_id: str) -> bool:
    info = Gio.DesktopAppInfo.new(app_id)
    if info is None:
        return False
    try:
        return bool(info.launch([], None))
    except Exception:
        return False
