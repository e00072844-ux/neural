"""NEURAL//NEXO: terminales remotas. El shell vive aquí y sobrevive a los cortes de conexión.

Mensajes: term_open {sid?, cols, rows} → term_ok {sid, nueva} | term_no {why}
          term_in {sid} + bytes · term_size {sid, cols, rows} · term_close {sid}
          term_out {sid, replay?} + bytes · term_end {sid}
Solo atiende a dispositivos con "shell": true (apagado por defecto, se activa en la laptop)."""
from __future__ import annotations

import asyncio
import fcntl
import os
import pwd
import signal
import struct
import subprocess
import termios
import time
import uuid
from pathlib import Path

MAX_POR_PAR = 4
RING = 128 * 1024
TROZO = 16 * 1024
ESPERA_DESCONECTADA = 12 * 3600
COLA_MAX = 64


class Terminal:
    def __init__(self, motor, peer: str, cols: int, rows: int):
        self.m, self.peer, self.sid = motor, peer, uuid.uuid4().hex
        self.ses = None
        self.solta = 0.0
        self.desde = time.time()
        self.anillo = bytearray()
        self.cola: asyncio.Queue = asyncio.Queue()
        maestro, esclavo = os.openpty()
        self.fd = maestro
        os.set_blocking(maestro, False)
        self.tam(cols, rows, fd=esclavo)
        info = pwd.getpwuid(os.getuid())
        env = {k: v for k, v in os.environ.items() if not k.startswith(("NEXO_", "INVOCATION_", "JOURNAL_"))}
        env.update(TERM="xterm-256color", COLORTERM="truecolor", HOME=info.pw_dir, SHELL=info.pw_shell, NEXO_TERMINAL="1")
        # setsid --ctty: sesión propia con esta PTY como terminal de control
        self.p = subprocess.Popen(["setsid", "--ctty", info.pw_shell, "-l"], stdin=esclavo, stdout=esclavo, stderr=esclavo,
                                  cwd=info.pw_dir, env=env, close_fds=True)
        os.close(esclavo)
        self.leyendo = True
        motor.loop.add_reader(self.fd, self._leer)
        self.bomba = motor.lanzar(self._bombear())

    def tam(self, cols: int, rows: int, fd: int | None = None) -> None:
        cols, rows = max(10, min(500, int(cols))), max(3, min(300, int(rows)))
        fcntl.ioctl(self.fd if fd is None else fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

    def redibujar(self) -> None:
        """Al volver a conectar: que las apps a pantalla completa se repinten."""
        try:
            os.killpg(self.p.pid, signal.SIGWINCH)
        except OSError:
            pass

    def entrada(self, datos: bytes) -> None:
        try:
            os.write(self.fd, datos)
        except OSError:
            pass

    def _leer(self) -> None:
        try:
            d = os.read(self.fd, 65536)
        except BlockingIOError:
            return
        except OSError:
            d = b""
        if not d:
            self.m.loop.remove_reader(self.fd)
            self.leyendo = False
            self.cola.put_nowait(None)
            return
        self.anillo += d
        del self.anillo[:-RING]
        self.cola.put_nowait(d)
        if self.cola.qsize() > COLA_MAX and self.leyendo:  # el programa espera si el enlace va lento
            self.m.loop.remove_reader(self.fd)
            self.leyendo = False

    async def _bombear(self) -> None:
        while True:
            d = await self.cola.get()
            if d is None:
                break
            if not self.leyendo and self.cola.qsize() < COLA_MAX // 2:
                self.leyendo = True
                self.m.loop.add_reader(self.fd, self._leer)
            ses = self.ses
            if ses:
                for i in range(0, len(d), TROZO):
                    await self.m.enviar(ses, {"t": "term_out", "sid": self.sid}, d[i:i + TROZO])
        if self.ses:
            await self.m.enviar(self.ses, {"t": "term_end", "sid": self.sid})
        self.m.term_fin(self)

    def cerrar(self) -> None:
        try:
            os.killpg(self.p.pid, signal.SIGHUP)
        except OSError:
            pass

    def liberar(self) -> None:
        if self.leyendo:
            try:
                self.m.loop.remove_reader(self.fd)
            except (OSError, ValueError):
                pass
        try:
            os.close(self.fd)
        except OSError:
            pass
        self.p.poll()


def registrar(base: Path, nombre: str, evento: str) -> None:
    """Quién y cuándo; nunca lo que se escribió."""
    try:
        fd = os.open(base / "terminal.log", os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\t{nombre}\t{evento}\n")
    except OSError:
        pass
