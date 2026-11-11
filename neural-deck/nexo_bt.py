"""NEURAL//NEXO: Bluetooth (perfil RFCOMM propio registrado en BlueZ por D-Bus).

BlueZ entrega cada conexión, entrante o saliente, como un descriptor por
NewConnection; quién es el otro lo demuestra después el handshake de NEXO.
Solo se marca a dispositivos ya emparejados a nivel de sistema."""
from __future__ import annotations

import threading
from typing import Callable

UUID = "6e657830-4e45-584f-8e58-4f4e45584f31"
CANAL = 22
RUTA = "/org/local/neural/nexo"


class BT:
    def __init__(self, al_conectar: Callable[[int], None], avisar: Callable[[str], None]):
        self.al_conectar = al_conectar
        self.avisar = avisar
        self.bus = None
        self.activo = False

    def iniciar(self) -> None:
        threading.Thread(target=self._hilo, daemon=True, name="nexo-bt").start()

    def _hilo(self) -> None:
        try:
            import dbus
            import dbus.mainloop.glib
            import dbus.service
            from gi.repository import GLib

            dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
            self.bus = dbus.SystemBus()
            exterior = self

            class Perfil(dbus.service.Object):
                @dbus.service.method("org.bluez.Profile1", in_signature="oha{sv}")
                def NewConnection(self, _ruta, fd, _props):
                    exterior.al_conectar(fd.take())

                @dbus.service.method("org.bluez.Profile1", in_signature="o")
                def RequestDisconnection(self, _ruta):
                    pass

                @dbus.service.method("org.bluez.Profile1")
                def Release(self):
                    exterior.activo = False

            self._perfil = Perfil(self.bus, RUTA)
            gestor = dbus.Interface(self.bus.get_object("org.bluez", "/org/bluez"), "org.bluez.ProfileManager1")
            gestor.RegisterProfile(RUTA, UUID, {"Name": "NEXO", "Service": UUID, "Channel": dbus.UInt16(CANAL),
                                                "RequireAuthentication": True, "RequireAuthorization": False,
                                                "AutoConnect": False})
            self.activo = True
            self._glib = GLib
            GLib.MainLoop().run()
        except Exception as e:  # sin adaptador o sin BlueZ: NEXO sigue por red
            self.activo = False
            self.avisar(f"Bluetooth no disponible: {e}")

    def marcar(self) -> None:
        """Intenta conectar el perfil con los emparejados que lo anuncian."""
        if self.activo:
            self._glib.idle_add(self._marcar)

    def _marcar(self) -> bool:
        import dbus

        try:
            om = dbus.Interface(self.bus.get_object("org.bluez", "/"), "org.freedesktop.DBus.ObjectManager")
            for ruta, ifaces in om.GetManagedObjects().items():
                d = ifaces.get("org.bluez.Device1")
                if d and d.get("Paired") and UUID in [str(u) for u in d.get("UUIDs", [])]:
                    disp = dbus.Interface(self.bus.get_object("org.bluez", ruta), "org.bluez.Device1")
                    disp.ConnectProfile(UUID, reply_handler=lambda: None, error_handler=lambda _e: None, timeout=20)
        except dbus.DBusException:
            pass
        return False
