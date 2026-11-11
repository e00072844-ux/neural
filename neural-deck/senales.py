"""Manejadores de señales que NO se mueren (5-nov).

Con GLibUnix.signal_add, si el manejador lanza una excepción GLib QUITA el
manejador; la siguiente señal (SIGUSR1/SIGUSR2/SIGWINCH) ya no la atiende
nadie y la acción por defecto de Linux es MATAR el proceso. Así murió la
barra: BIA subía el volumen (SIGUSR1) con el panel rápido abierto. Con
segura() el error se apunta en el registro y la señal sigue atendida."""
from __future__ import annotations

import sys
import traceback


def segura(nombre: str, accion):
    def manejador(*_):
        try:
            accion()
        except Exception:  # noqa: BLE001
            print(f"[{nombre}] fallo atendiendo la señal (sigo vivo):", file=sys.stderr, flush=True)
            traceback.print_exc()
        return True  # GLib.SOURCE_CONTINUE
    return manejador
