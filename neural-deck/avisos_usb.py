"""NEURAL//USB — avisa cuando conectas o quitas un disco USB / extraíble.

Corre en segundo plano (autostart de Wayfire). Vigila UDisks2 (usb.Udisks) y, cuando
aparece un disco nuevo, manda una notificación a NEURAL//NOTIFY con su nombre,
tamaño y tipo, y dos botones:
  ABRIR    → lo monta (polkit pide la contraseña con NEURAL//AUTH) y abre NEURAL//FILES ahí
  EXPULSAR → desmonta, cierra LUKS y apaga el disco
Los discos que ya estaban al arrancar no avisan. Al quitar uno: aviso efímero.
Se vigila el disco físico (Drive de udisks), no sus particiones: así formatear o
reparticionar en NEURAL//DISCO no se confunde con desenchufar.
"""
from __future__ import annotations

import ctypes
import subprocess
import sys
from pathlib import Path

from gi.repository import Gio, GLib

import usb

DECK = Path(__file__).resolve().parent
ESPERA_MS = 1500  # dar tiempo a que udisks vea particiones y sistemas de archivos


def tam_humano(n: float) -> str:
    for u in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or u == "TB":
            return f"{n:.0f} {u}" if u == "B" else f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} TB"


class Avisos:
    def __init__(self):
        self.ud = usb.Udisks(self._cambio)
        self.conocidos: dict[str, str] = {}  # drive → nombre, para el aviso al quitarlo
        self.avisos: dict[int, str] = {}  # id de notificación → drive
        self._pend: set[str] = set()
        self.conocidos = self._drives()
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.bus.signal_subscribe(None, "org.freedesktop.Notifications", "ActionInvoked",
                                  "/org/freedesktop/Notifications", None, Gio.DBusSignalFlags.NONE,
                                  self._accion)
        self.bus.signal_subscribe(None, "org.freedesktop.Notifications", "NotificationClosed",
                                  "/org/freedesktop/Notifications", None, Gio.DBusSignalFlags.NONE,
                                  self._cerrada)

    # ---------- cambios en UDisks ----------
    def _drives(self) -> dict[str, str]:
        """Discos físicos externos presentes: ruta del Drive → nombre (marca + modelo)."""
        out = {}
        for obj in self.ud.om.get_objects():
            dr = obj.get_interface(usb.I_DRIVE)
            if dr is None:
                continue
            externo = (usb._prop(dr, "ConnectionBus", "") == "usb" or usb._prop(dr, "Removable", False)
                       or usb._prop(dr, "MediaRemovable", False))
            if externo:
                nombre = " ".join(x for x in (usb._prop(dr, "Vendor", ""), usb._prop(dr, "Model", "")) if x)
                out[obj.get_object_path()] = nombre.strip() or "Disco externo"
        return out

    def _cambio(self):
        actuales = self._drives()
        for drive in actuales.keys() - self.conocidos.keys():
            if drive not in self._pend:
                self._pend.add(drive)
                GLib.timeout_add(ESPERA_MS, self._nuevo, drive)
        for drive in self.conocidos.keys() - actuales.keys():
            self._notificar("USB RETIRADO", self.conocidos[drive], [], transitorio=True)
        self.conocidos = {d: self.conocidos.get(d, n) for d, n in actuales.items()}

    def _nuevo(self, drive):
        self._pend.discard(drive)
        if drive not in self.conocidos:  # lo quitaron antes de que acabara la espera
            return GLib.SOURCE_REMOVE
        partes = [d for d in self.ud.dispositivos() if d["drive"] == drive]
        dr = self.ud._iface(drive, usb.I_DRIVE)
        titulo = "USB CONECTADO" if usb._prop(dr, "ConnectionBus", "") == "usb" else "DISCO CONECTADO"
        if not partes:  # vacío o sin formato conocido: lo que toca es NEURAL//DISCO
            tam = usb._prop(dr, "Size", 0)
            cuerpo = f"{self.conocidos[drive]} · {tam_humano(tam)} · sin formato reconocible"
            nid = self._notificar(titulo, cuerpo, ["disco", "ABRIR EN DISCO"])
            if nid:
                self.avisos[nid] = drive
            return GLib.SOURCE_REMOVE
        lineas = []
        for d in partes:
            tipo = "LUKS (cifrado)" if d["cifrado"] else (d["tipo"] or "?")
            estado = f" · montado en {d['puntos'][0]}" if d["puntos"] else ""
            lineas.append(f"{d['nombre']} · {tam_humano(d['tam'])} · {tipo}{estado}")
        acciones = ["abrir", "ABRIR", "expulsar", "EXPULSAR"]
        nid = self._notificar(titulo, "\n".join(lineas), acciones)
        if nid:
            self.avisos[nid] = drive
        return GLib.SOURCE_REMOVE

    # ---------- notificaciones ----------
    def _notificar(self, titulo, cuerpo, acciones, transitorio=False, urgencia=1) -> int:
        hints = {"urgency": GLib.Variant("y", urgencia), "desktop-entry": GLib.Variant("s", "neural-files")}
        if transitorio:
            hints["transient"] = GLib.Variant("b", True)
        try:
            r = self.bus.call_sync("org.freedesktop.Notifications", "/org/freedesktop/Notifications",
                                   "org.freedesktop.Notifications", "Notify",
                                   GLib.Variant("(susssasa{sv}i)", ("NEURAL//USB", 0, "drive-removable-media",
                                                                    titulo, GLib.markup_escape_text(cuerpo),
                                                                    acciones, hints, -1)),
                                   GLib.VariantType("(u)"), Gio.DBusCallFlags.NONE, 5000, None)
            return r.unpack()[0]
        except GLib.Error as e:
            print("neural-usb: no se pudo notificar:", e.message, file=sys.stderr)
            return 0

    def _cerrada(self, _c, _s, _p, _i, _sig, params):
        self.avisos.pop(params.unpack()[0], None)

    def _accion(self, _c, _s, _p, _i, _sig, params):
        nid, clave = params.unpack()
        drive = self.avisos.pop(nid, None)
        if drive is None:
            return
        if clave == "abrir":
            self._abrir(drive)
        elif clave == "disco":
            subprocess.Popen(["python3", str(DECK / "disco.py")], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
        elif clave == "expulsar":
            self.ud.expulsar(drive, lambda ok, txt: self._notificar(
                "LISTO PARA QUITAR" if ok else "NO SE PUDO EXPULSAR",
                self.conocidos.get(drive, "") if ok else txt, [], transitorio=ok, urgencia=1 if ok else 2))

    def _abrir(self, drive):
        partes = [d for d in self.ud.dispositivos() if d["drive"] == drive]
        montada = next((d for d in partes if d["puntos"]), None)
        if montada:
            self._files(montada["puntos"][0])
            return
        normal = next((d for d in partes if not d["cifrado"] and d["tipo"]), None)
        if normal is None:
            self._files(None)  # cifrado o raro: FILES lo muestra en USB / EXTERNOS y pide la clave
            return

        def fin(ok, texto):
            if ok:
                self._files(texto)
            else:
                self._notificar("NO SE PUDO MONTAR", f"{normal['nombre']}: {texto}", [], urgencia=2)
        self.ud.montar(normal["ruta"], fin)

    def _files(self, ruta):
        argv = ["python3", str(DECK / "files.py")] + ([ruta] if ruta else [])
        subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def main():
    try:
        ctypes.CDLL(None).prctl(15, b"neural-usb", 0, 0, 0)
    except (OSError, AttributeError):
        pass
    try:
        Avisos()
    except GLib.Error as e:
        print("neural-usb: sin UDisks2:", e.message, file=sys.stderr)
        return 1
    GLib.MainLoop().run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
