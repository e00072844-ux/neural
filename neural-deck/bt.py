"""Bluetooth para el panel rapido: habla con BlueZ (bluetoothd) por D-Bus.

- Adaptador (org.bluez.Adapter1): encender / apagar, buscar dispositivos.
- Dispositivos (org.bluez.Device1): emparejar, conectar, desconectar, olvidar.
- Agente de emparejamiento (org.bluez.Agent1, capacidad KeyboardDisplay):
  solo acepta mientras el usuario esta emparejando desde el panel, solo del
  dispositivo elegido y solo si llama bluetoothd; los codigos a comprobar se
  le PREGUNTAN al usuario (SI/NO en el panel), nunca se aceptan solos.
Las llamadas largas (Pair, Connect) van asincronas para no congelar el panel."""
from __future__ import annotations

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

BUS = "org.bluez"
AGENT_PATH = "/org/local/neuralbar/agent"
AGENT_XML = """
<node><interface name="org.bluez.Agent1">
  <method name="Release"/>
  <method name="RequestPinCode"><arg type="o" direction="in"/><arg type="s" direction="out"/></method>
  <method name="DisplayPinCode"><arg type="o" direction="in"/><arg type="s" direction="in"/></method>
  <method name="RequestPasskey"><arg type="o" direction="in"/><arg type="u" direction="out"/></method>
  <method name="DisplayPasskey"><arg type="o" direction="in"/><arg type="u" direction="in"/><arg type="q" direction="in"/></method>
  <method name="RequestConfirmation"><arg type="o" direction="in"/><arg type="u" direction="in"/></method>
  <method name="RequestAuthorization"><arg type="o" direction="in"/></method>
  <method name="AuthorizeService"><arg type="o" direction="in"/><arg type="s" direction="in"/></method>
  <method name="Cancel"/>
</interface></node>"""

# icono de BlueZ -> etiqueta corta
TIPOS = {"audio-headset": "AURIC", "audio-headphones": "AURIC", "audio-card": "AUDIO", "input-keyboard": "TECLADO",
         "input-mouse": "RATÓN", "input-gaming": "MANDO", "input-tablet": "TABLETA", "phone": "MÓVIL",
         "computer": "PC"}


class BtError(Exception):
    pass


# El objeto del agente se exporta UNA vez por proceso en la conexion compartida al bus
# del sistema y reparte las llamadas a la instancia Bluetooth actual. (Antes cada
# instancia lo exportaba: si una fallaba a medias, el objeto quedaba exportado y
# todas las siguientes reventaban con "already exported" -> el panel no abria.)
_agente_actual = None
_agente_id = 0


def _despachar(conn, sender, path, iface, method, params, inv):
    if _agente_actual is None:
        inv.return_dbus_error("org.bluez.Error.Rejected", "panel cerrado")
        return
    _agente_actual._agente(conn, sender, path, iface, method, params, inv)


class Bluetooth:
    def __init__(self, on_codigo=None, on_confirmar=None):
        """on_codigo(texto) se llama cuando el emparejamiento muestra un codigo.
        on_confirmar(texto, responder) pide SI/NO al usuario; responder(bool).
        Sin on_confirmar, las confirmaciones se RECHAZAN."""
        try:
            self.bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        except GLib.Error as e:
            raise BtError(f"sin bus del sistema: {e.message}") from e
        self.on_codigo = on_codigo or (lambda _t: None)
        self.on_confirmar = on_confirmar
        self.emparejando: str | None = None  # path del dispositivo que el usuario eligio emparejar
        self.pendiente = None  # invocation de una confirmacion esperando SI/NO
        if self._dueno_bluez() is None:
            raise BtError("bluetoothd no está en marcha")
        try:
            self.adapter = self._adapter_path()
            self._registrar_agente()
        except GLib.Error as e:  # cualquier fallo de D-Bus = BtError, que el panel ya sabe mostrar
            raise BtError(e.message) from e

    # ---------- D-Bus ----------
    # NO_AUTO_START: si bluetoothd no esta en marcha, fallar AL MOMENTO en vez de que
    # D-Bus intente arrancarlo y el panel se quede congelado esperando (hasta 25 s)
    def _call(self, path, iface, method, args=None, reply=None, timeout=2000):
        try:
            return self.bus.call_sync(BUS, path, iface, method, args, reply, Gio.DBusCallFlags.NO_AUTO_START,
                                      timeout, None)
        except GLib.Error as e:
            raise BtError(_msg(e)) from e

    def _call_async(self, path, iface, method, done, timeout=30000):
        """done(error | None) en el hilo de GTK."""

        def fin(bus, res):
            try:
                bus.call_finish(res)
                done(None)
            except GLib.Error as e:
                done(_msg(e))

        self.bus.call(BUS, path, iface, method, None, None, Gio.DBusCallFlags.NO_AUTO_START, timeout, None, fin)

    def _get(self, path, iface, prop):
        v = self._call(path, "org.freedesktop.DBus.Properties", "Get", GLib.Variant("(ss)", (iface, prop)))
        return v.unpack()[0]

    def _set(self, path, iface, prop, value: GLib.Variant):
        self._call(path, "org.freedesktop.DBus.Properties", "Set", GLib.Variant("(ssv)", (iface, prop, value)))

    def _objetos(self) -> dict:
        return self._call("/", "org.freedesktop.DBus.ObjectManager", "GetManagedObjects").unpack()[0]

    def _adapter_path(self) -> str:
        for path, ifaces in self._objetos().items():
            if "org.bluez.Adapter1" in ifaces:
                return path
        raise BtError("no hay adaptador Bluetooth")

    # ---------- agente ----------
    def _registrar_agente(self):
        global _agente_actual, _agente_id
        _agente_actual = self
        if not _agente_id:
            info = Gio.DBusNodeInfo.new_for_xml(AGENT_XML).interfaces[0]
            _agente_id = self.bus.register_object(AGENT_PATH, info, _despachar, None, None)
        try:
            self._call("/org/bluez", "org.bluez.AgentManager1", "RegisterAgent",
                       GLib.Variant("(os)", (AGENT_PATH, "KeyboardDisplay")))
        except BtError:
            pass  # otro agente (bluetoothctl abierto): el panel sigue sirviendo para lo demas

    def _dueno_bluez(self) -> str | None:
        try:
            r = self.bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                                   "GetNameOwner", GLib.Variant("(s)", (BUS,)), GLib.VariantType("(s)"),
                                   Gio.DBusCallFlags.NONE, 2000, None)
            return r.unpack()[0]
        except GLib.Error:
            return None

    def _agente(self, _conn, sender, _path, _iface, method, params, inv):
        if sender != self._dueno_bluez():  # solo bluetoothd puede hablar con el agente
            inv.return_dbus_error("org.bluez.Error.Rejected", "solo bluetoothd")
            return
        if method in ("Release", "Cancel"):
            if method == "Cancel":
                self._cancelar_pendiente()
                self.on_codigo("Emparejamiento cancelado")
            inv.return_value(None)
            return
        args = params.unpack()
        # nadie pidio emparejar desde el panel, o la peticion es de OTRO dispositivo: no aceptar nada
        if self.emparejando is None or args[0] != self.emparejando:
            inv.return_dbus_error("org.bluez.Error.Rejected", "no se esta emparejando ese dispositivo")
            return
        if method == "RequestConfirmation":
            self._preguntar(f"¿El otro muestra {args[1]:06d}?", inv)
        elif method == "RequestAuthorization":
            self._preguntar("¿Emparejar sin código con este dispositivo?", inv)
        elif method == "DisplayPasskey":
            self.on_codigo(f"Teclea en el dispositivo: {args[1]:06d} + Enter")
            inv.return_value(None)
        elif method == "DisplayPinCode":
            self.on_codigo(f"Teclea en el dispositivo: {args[1]} + Enter")
            inv.return_value(None)
        elif method == "RequestPinCode":  # aparatos viejos (BT 2.0) con PIN fijo
            self.on_codigo("PIN pedido: probando 0000")
            inv.return_value(GLib.Variant("(s)", ("0000",)))
        elif method == "RequestPasskey":
            inv.return_dbus_error("org.bluez.Error.Rejected", "pedir clave numerica aun no esta hecho")
        elif method == "AuthorizeService":  # el dispositivo que se esta emparejando ahora
            inv.return_value(None)
        else:
            inv.return_dbus_error("org.bluez.Error.Rejected", method)

    def _preguntar(self, texto: str, inv) -> None:
        self._cancelar_pendiente()
        if self.on_confirmar is None:
            inv.return_dbus_error("org.bluez.Error.Rejected", "sin forma de preguntar al usuario")
            return
        self.pendiente = inv

        def responder(si: bool):
            if self.pendiente is not inv:  # ya cancelada o reemplazada
                return
            self.pendiente = None
            if si:
                inv.return_value(None)
            else:
                inv.return_dbus_error("org.bluez.Error.Rejected", "rechazado por el usuario")

        self.on_confirmar(texto, responder)

    def _cancelar_pendiente(self) -> None:
        if self.pendiente is not None:
            inv, self.pendiente = self.pendiente, None
            try:
                inv.return_dbus_error("org.bluez.Error.Canceled", "cancelado")
            except Exception:  # noqa: BLE001 (BlueZ ya la dio por perdida)
                pass

    def cerrar(self):
        try:
            self._call("/org/bluez", "org.bluez.AgentManager1", "UnregisterAgent", GLib.Variant("(o)", (AGENT_PATH,)))
        except BtError:
            pass
        global _agente_actual
        if _agente_actual is self:  # el objeto sigue exportado; solo deja de atender
            _agente_actual = None

    # ---------- adaptador ----------
    def encendido(self) -> bool:
        return bool(self._get(self.adapter, "org.bluez.Adapter1", "Powered"))

    def encender(self, on: bool) -> None:
        self._set(self.adapter, "org.bluez.Adapter1", "Powered", GLib.Variant("b", on))

    def buscando(self) -> bool:
        return bool(self._get(self.adapter, "org.bluez.Adapter1", "Discovering"))

    def buscar(self, on: bool) -> None:
        try:
            self._call(self.adapter, "org.bluez.Adapter1", "StartDiscovery" if on else "StopDiscovery")
        except BtError as e:
            if "InProgress" not in str(e) and "not started" not in str(e).lower():
                raise

    def dispositivos(self) -> list[dict]:
        """Los de este adaptador: conectados, luego emparejados, luego por señal."""
        out = []
        for path, ifaces in self._objetos().items():
            d = ifaces.get("org.bluez.Device1")
            if not d or d.get("Adapter") != self.adapter:
                continue
            nombre = d.get("Alias") or d.get("Name") or d.get("Address", "?")
            if not d.get("Name") and not d.get("Paired"):
                continue  # sin nombre ni emparejado = ruido (balizas, etc.)
            out.append({"path": path, "nombre": nombre, "addr": d.get("Address", ""),
                        "paired": bool(d.get("Paired")), "connected": bool(d.get("Connected")),
                        "rssi": d.get("RSSI"), "tipo": TIPOS.get(d.get("Icon", ""), ""),
                        "bateria": ifaces.get("org.bluez.Battery1", {}).get("Percentage")})
        out.sort(key=lambda d: (not d["connected"], not d["paired"], -(d["rssi"] or -200)))
        return out

    # ---------- dispositivo ----------
    def emparejar_y_conectar(self, path: str, done) -> None:
        """Pair -> Trusted -> Connect; done(error | None)."""
        self.emparejando = path

        def tras_pair(err):
            self.emparejando = None
            self._cancelar_pendiente()
            if err and "AlreadyExists" not in err:
                done(err)
                return
            try:
                self._set(path, "org.bluez.Device1", "Trusted", GLib.Variant("b", True))
            except BtError:
                pass
            self.conectar(path, done)

        self._call_async(path, "org.bluez.Device1", "Pair", tras_pair, timeout=60000)

    def conectar(self, path: str, done) -> None:
        self._call_async(path, "org.bluez.Device1", "Connect", done)

    def desconectar(self, path: str, done) -> None:
        self._call_async(path, "org.bluez.Device1", "Disconnect", done)

    def olvidar(self, path: str) -> None:
        self._call(self.adapter, "org.bluez.Adapter1", "RemoveDevice", GLib.Variant("(o)", (path,)))


def _msg(e: GLib.Error) -> str:
    m = Gio.DBusError.get_remote_error(e) or ""
    Gio.DBusError.strip_remote_error(e)  # quita el "GDBus.Error:org.bluez...:" del mensaje
    text = e.message
    corto = m.rsplit(".", 1)[-1]
    return f"{corto}: {text}" if corto and corto not in text else text
