"""Cliente minimo de la IPC de Wayfire (plugins ipc, ipc-rules y wm-actions).

Cada mensaje es JSON con una cabecera de 4 bytes (largo, little-endian), por
el socket de $WAYFIRE_SOCKET (lo pone el plugin ipc a los procesos que lanza
Wayfire). Una conexion para pedidos (bloqueante: es local y responde al
instante) y otra que solo escucha eventos y los entrega en el bucle de GLib."""
from __future__ import annotations

import glob
import json
import os
import socket
import struct

from gi.repository import GLib


def _find_socket() -> str | None:
    """Los procesos abiertos antes de cargar el plugin ipc no tienen
    WAYFIRE_SOCKET: buscar el socket donde lo crea Wayfire."""
    runtime = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    # solo en XDG_RUNTIME_DIR (privado, 0700): en /tmp cualquiera podria dejar un socket falso
    found = glob.glob(f"{runtime}/wayfire-*.socket")
    return max(found, key=os.path.getmtime) if found else None


def _read_exact(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise OSError("Wayfire cerro la conexion")
        buf += chunk
    return buf


def _send(sock: socket.socket, method: str, data: dict | None) -> None:
    body = json.dumps({"method": method, "data": data or {}}).encode()
    sock.sendall(struct.pack("<I", len(body)) + body)


def _recv(sock: socket.socket):
    (n,) = struct.unpack("<I", _read_exact(sock, 4))
    return json.loads(_read_exact(sock, n))


class WayfireIPC:
    def __init__(self, path: str | None = None):
        self.path = path or os.environ.get("WAYFIRE_SOCKET") or _find_socket()
        if not self.path:
            raise OSError("no hay WAYFIRE_SOCKET (¿falta el plugin ipc en wayfire.ini?)")
        self.sock = self._connect()
        self.events: socket.socket | None = None

    def _connect(self) -> socket.socket:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(self.path)
        return sock

    def call(self, method: str, data: dict | None = None):
        """Devuelve la respuesta ya decodificada; RuntimeError si Wayfire
        contesta con error (p. ej. "No such method": falta ese plugin)."""
        _send(self.sock, method, data)
        reply = _recv(self.sock)
        if isinstance(reply, dict) and "error" in reply:
            raise RuntimeError(f"{method}: {reply['error']}")
        return reply

    def watch(self, callback) -> None:
        """callback(evento) por cada evento de Wayfire; callback(None) si se
        corta la conexion (Wayfire se cerro o se reinicio el plugin)."""
        self.events = self._connect()
        _send(self.events, "window-rules/events/watch", None)
        self.events.settimeout(2)
        reply = _recv(self.events)  # {"result": "ok"} antes de los eventos
        self.events.settimeout(None)
        if isinstance(reply, dict) and "error" in reply:
            raise RuntimeError(f"events/watch: {reply['error']}")

        def on_data(_fd, cond):
            if cond & (GLib.IO_HUP | GLib.IO_ERR):
                callback(None)
                return False
            try:
                msg = _recv(self.events)
            except (OSError, ValueError):
                callback(None)
                return False
            if isinstance(msg, dict) and "event" in msg:
                callback(msg)
            return True

        GLib.io_add_watch(self.events.fileno(), GLib.PRIORITY_DEFAULT,
                          GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR, on_data)
