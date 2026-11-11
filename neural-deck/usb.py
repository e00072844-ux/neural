"""Discos USB / externos vía UDisks2 (D-Bus del sistema), para NEURAL//FILES.

Udisks(on_change) vigila org.freedesktop.UDisks2 y llama a on_change() (agrupado)
cuando se conecta, desconecta, monta o desmonta algo. dispositivos() devuelve los
sistemas de archivos que no son del sistema (USB, extraíbles, loop), sin el NVMe
interno. Montar / desmontar / expulsar son asíncronos y pasan por polkit: si hace
falta contraseña la pide NEURAL//AUTH (auth.no_user_interaction = False).
"""
from __future__ import annotations

import os

from gi.repository import Gio, GLib

BUS = "org.freedesktop.UDisks2"
RAIZ = "/org/freedesktop/UDisks2"
I_BLOCK = BUS + ".Block"
I_FS = BUS + ".Filesystem"
I_PART = BUS + ".Partition"
I_DRIVE = BUS + ".Drive"
I_CRYPT = BUS + ".Encrypted"
TIEMPO = 120_000  # ms: una caja USB lenta puede tardar en montar


def _bytes(v) -> str:
    if v is None:
        return ""
    b = bytes(v.unpack()) if hasattr(v, "unpack") else bytes(v)
    return b.rstrip(b"\0").decode(errors="replace")


def _prop(proxy, nombre, defecto=None):
    if proxy is None:
        return defecto
    v = proxy.get_cached_property(nombre)
    return defecto if v is None else v.unpack()


def _opciones(**extra) -> GLib.Variant:
    d = {"auth.no_user_interaction": GLib.Variant("b", False)}
    for k, v in extra.items():
        d[k] = GLib.Variant("s", v)
    return GLib.Variant("(a{sv})", (d,))


class Udisks:
    def __init__(self, on_change=None):
        self.on_change = on_change
        self._pend = 0
        self.om = Gio.DBusObjectManagerClient.new_for_bus_sync(
            Gio.BusType.SYSTEM, Gio.DBusObjectManagerClientFlags.NONE, BUS, RAIZ, None, None, None)
        for s in ("object-added", "object-removed", "interface-added", "interface-removed",
                  "interface-proxy-properties-changed"):
            self.om.connect(s, self._cambio)

    # ---------- avisos agrupados ----------
    def _cambio(self, *_a):
        if self.on_change and not self._pend:
            self._pend = GLib.timeout_add(250, self._avisar)

    def _avisar(self):
        self._pend = 0
        if self.on_change:
            self.on_change()
        return GLib.SOURCE_REMOVE

    # ---------- lectura ----------
    def _iface(self, ruta: str, iface: str):
        obj = self.om.get_object(ruta) if ruta and ruta != "/" else None
        return obj.get_interface(iface) if obj is not None else None

    def dispositivos(self) -> list[dict]:
        out = []
        for obj in self.om.get_objects():
            blk = obj.get_interface(I_BLOCK)
            if blk is None:
                continue
            fs = obj.get_interface(I_FS)
            crypt = obj.get_interface(I_CRYPT)
            if fs is None and crypt is None:
                continue
            if _prop(blk, "HintIgnore", False):
                continue
            drive_ruta = _prop(blk, "Drive", "/")
            drive = self._iface(drive_ruta, I_DRIVE)
            # el LUKS ya abierto (dm-…) cuelga del cifrado: heredar su disco
            respaldo = _prop(blk, "CryptoBackingDevice", "/")
            if drive is None and respaldo != "/":
                drive_ruta = _prop(self._iface(respaldo, I_BLOCK), "Drive", "/")
                drive = self._iface(drive_ruta, I_DRIVE)
            externo = (_prop(drive, "ConnectionBus", "") == "usb" or _prop(drive, "Removable", False)
                       or _prop(drive, "MediaRemovable", False))
            if _prop(blk, "HintSystem", True) and not externo:
                continue
            if crypt is not None and _prop(crypt, "CleartextDevice", "/") != "/":
                continue  # ya desbloqueado: se lista su contenido
            puntos = [_bytes(p) for p in (_prop(fs, "MountPoints", []) if fs else [])]
            etiqueta = _prop(blk, "IdLabel", "") or _prop(blk, "HintName", "")
            modelo = " ".join(x for x in (_prop(drive, "Vendor", ""), _prop(drive, "Model", "")) if x).strip()
            tam = _prop(blk, "Size", 0)
            out.append({
                "ruta": obj.get_object_path(),
                "dev": _bytes(blk.get_cached_property("PreferredDevice")) or _bytes(blk.get_cached_property("Device")),
                "etiqueta": etiqueta,
                "modelo": modelo,
                "nombre": etiqueta or modelo or os.path.basename(_bytes(blk.get_cached_property("Device"))),
                "tipo": _prop(blk, "IdType", ""),
                "tam": tam,
                "puntos": puntos,
                "cifrado": crypt is not None,
                "drive": drive_ruta if drive is not None else "/",
                "usb": _prop(drive, "ConnectionBus", "") == "usb",
                "apagable": bool(_prop(drive, "CanPowerOff", False)),
                "expulsable": bool(_prop(drive, "Ejectable", False)),
            })
        out.sort(key=lambda d: (d["drive"], d["dev"]))
        return out

    # ---------- acciones (asíncronas; fin(ok, texto)) ----------
    def _llamar(self, ruta, iface, metodo, params, fin):
        proxy = self._iface(ruta, iface)
        if proxy is None:
            fin(False, "El dispositivo ya no está")
            return

        def hecho(p, res):
            try:
                r = p.call_finish(res)
                fin(True, r.unpack()[0] if r is not None and r.n_children() else "")
            except GLib.Error as e:
                fin(False, _limpio(e.message))
        proxy.call(metodo, params, Gio.DBusCallFlags.NONE, TIEMPO, None, hecho)

    def montar(self, ruta: str, fin, solo_lectura: bool = False):
        extra = {"options": "ro"} if solo_lectura else {}
        self._llamar(ruta, I_FS, "Mount", _opciones(**extra), fin)

    def desmontar(self, ruta: str, fin):
        self._llamar(ruta, I_FS, "Unmount", _opciones(), fin)

    def desbloquear(self, ruta: str, clave: str, fin):
        d = {"auth.no_user_interaction": GLib.Variant("b", False)}
        self._llamar(ruta, I_CRYPT, "Unlock", GLib.Variant("(sa{sv})", (clave, d)), fin)

    def expulsar(self, drive_ruta: str, fin):
        """Desmonta todo lo del disco, cierra sus LUKS y lo apaga (o expulsa)."""
        pendientes = []
        for obj in self.om.get_objects():
            blk = obj.get_interface(I_BLOCK)
            if blk is None:
                continue
            mio = _prop(blk, "Drive", "/") == drive_ruta
            resp = _prop(blk, "CryptoBackingDevice", "/")
            if not mio and resp != "/":
                mio = _prop(self._iface(resp, I_BLOCK), "Drive", "/") == drive_ruta
            if not mio:
                continue
            fs = obj.get_interface(I_FS)
            if fs is not None and _prop(fs, "MountPoints", []):
                pendientes.append((0, obj.get_object_path(), I_FS, "Unmount"))
            crypt = obj.get_interface(I_CRYPT)
            if crypt is not None and _prop(crypt, "CleartextDevice", "/") != "/":
                pendientes.append((1, obj.get_object_path(), I_CRYPT, "Lock"))
        pendientes.sort()
        drive = self._iface(drive_ruta, I_DRIVE)
        if drive is not None:
            if _prop(drive, "CanPowerOff", False):
                pendientes.append((2, drive_ruta, I_DRIVE, "PowerOff"))
            elif _prop(drive, "Ejectable", False):
                pendientes.append((2, drive_ruta, I_DRIVE, "Eject"))

        def siguiente(ok=True, texto=""):
            if not ok:
                fin(False, texto)
                return
            if not pendientes:
                fin(True, "")
                return
            _o, ruta, iface, metodo = pendientes.pop(0)
            self._llamar(ruta, iface, metodo, _opciones(), siguiente)
        siguiente()


def _limpio(msg: str) -> str:
    # "GDBus.Error:org.freedesktop.UDisks2.Error.DeviceBusy: Error unmounting …: target is busy"
    if ": " in msg and msg.startswith("GDBus.Error:"):
        msg = msg.split(": ", 1)[1]
    if "target is busy" in msg or "DeviceBusy" in msg:
        return "Ocupado: cierra lo que use el disco (terminal, editor…)"
    if "NotAuthorized" in msg or "not authorized" in msg.lower():
        return "Permiso denegado"
    return msg[:160]
