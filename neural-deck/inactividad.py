#!/usr/bin/env python3
"""NEURAL//IDLE: bloqueo y apagado de pantalla por inactividad.

Lee ~/.config/live-wallpaper/ajustes.json (seccion "inactividad"):
    {"inactividad": {"bloqueo_min": 5, "pantalla_min": 10}}   (0 = nunca)
y lo aplica:
- swayidle (protocolo ext-idle-notify): bloquea con bloqueo.py -f al pasar
  bloqueo_min sin usar el portatil, antes de suspender (before-sleep) y
  cuando logind lo pide (lock: tapa con HandleLidSwitch=lock, loginctl
  lock-session).
- Wayfire (plugin idle, por la IPC): apaga la pantalla a los pantalla_min
  (dpms_timeout) y desactiva el salvapantallas del cubo.
Los videos / juegos que piden no apagar la pantalla (idle-inhibit) lo frenan.
Si ajustes.json cambia (lo escribira el menu de ajustes), se aplica al momento.
Sin GTK: solo GLib para vigilar el archivo y los procesos."""
from __future__ import annotations

import ctypes
import json
import shlex
import signal
import subprocess
import sys
from pathlib import Path

from gi.repository import Gio, GLib, GLibUnix

from board import CONFIG_DIR
from wayfire_ipc import WayfireIPC

PROC = "neural-idle"
AJUSTES = CONFIG_DIR / "ajustes.json"
DEFECTO = {"bloqueo_min": 5, "pantalla_min": 10, "suspender_min": 0,  # 0 = nunca
           "bloquear_al_suspender": True}
SUSPENDER = "systemctl suspend"
LOCK = f"python3 {Path(__file__).with_name('bloqueo.py')} -f"
LOG = Path.home() / ".cache" / "neural-lock.log"


def leer() -> dict:
    try:
        datos = json.loads(AJUSTES.read_text()).get("inactividad", {})
    except (OSError, json.JSONDecodeError, AttributeError):
        datos = {}
    out = dict(DEFECTO)
    if not isinstance(datos, dict):
        return out
    for k, d in DEFECTO.items():
        if isinstance(d, bool):
            if isinstance(datos.get(k), bool):
                out[k] = datos[k]
            continue
        try:
            out[k] = max(0, int(datos.get(k, d)))
        except (TypeError, ValueError):
            pass
    return out


def asegurar_archivo() -> None:
    """Crea ajustes.json con los valores por defecto si falta la seccion."""
    try:
        todo = json.loads(AJUSTES.read_text())
    except (OSError, json.JSONDecodeError):
        todo = {}
    if not isinstance(todo, dict):
        todo = {}
    if "inactividad" not in todo:
        todo["inactividad"] = dict(DEFECTO)
        AJUSTES.parent.mkdir(parents=True, exist_ok=True)
        AJUSTES.write_text(json.dumps(todo, indent=2, ensure_ascii=False) + "\n")


class Idle:
    def __init__(self, loop: GLib.MainLoop):
        self.loop = loop
        self.proc: subprocess.Popen | None = None
        self.actual: dict | None = None
        self.pending = 0
        asegurar_archivo()
        self.mon = Gio.File.new_for_path(str(AJUSTES)).monitor_file(Gio.FileMonitorFlags.NONE, None)
        self.mon.connect("changed", lambda *_: self._programar())
        # si se edita wayfire.ini, Wayfire recarga y puede volver a sus valores
        self.mon_wf = Gio.File.new_for_path(str(Path.home() / ".config" / "wayfire.ini")).monitor_file(
            Gio.FileMonitorFlags.NONE, None)
        self.mon_wf.connect("changed", lambda *_: GLib.timeout_add(800, lambda: (self._wayfire(), False)[1]))
        self.aplicar()
        GLib.timeout_add_seconds(5, self._vigilar)

    def _programar(self):
        # varios avisos seguidos al guardar: aplicar una sola vez
        if self.pending:
            GLib.source_remove(self.pending)
        self.pending = GLib.timeout_add(300, lambda: (setattr(self, "pending", 0), self.aplicar(), False)[2])

    def aplicar(self):
        cfg = leer()
        if cfg == self.actual and self.proc is not None and self.proc.poll() is None:
            return
        self.actual = cfg
        self._wayfire()
        self._swayidle()
        m = lambda k: f"{cfg[k]} min" if cfg[k] else "nunca"  # noqa: E731
        print(f"inactividad: bloqueo {m('bloqueo_min')}, pantalla {m('pantalla_min')}, "
              f"suspender {m('suspender_min')}", flush=True)

    def _wayfire(self):
        cfg = self.actual or leer()
        dpms = cfg["pantalla_min"] * 60 if cfg["pantalla_min"] else -1
        try:
            WayfireIPC().call("wayfire/set-config-options",
                              {"idle/dpms_timeout": dpms, "idle/screensaver_timeout": -1})
        except Exception as e:  # sin IPC: el bloqueo sigue funcionando
            print(f"inactividad: IPC de Wayfire: {e}", file=sys.stderr, flush=True)

    def _swayidle(self):
        self._parar()
        cfg = self.actual
        lock = f"{LOCK} >> {shlex.quote(str(LOG))} 2>&1"
        args = ["swayidle", "-w"]
        if cfg["bloqueo_min"]:
            args += ["timeout", str(cfg["bloqueo_min"] * 60), lock]
        if cfg["suspender_min"]:  # antes de dormir bloquea igual (before-sleep)
            args += ["timeout", str(cfg["suspender_min"] * 60), SUSPENDER]
        if cfg["bloquear_al_suspender"]:
            args += ["before-sleep", lock]
        args += ["lock", lock]
        self.proc = subprocess.Popen(args)

    def _parar(self):
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None

    def _vigilar(self):
        if self.proc is not None and self.proc.poll() is not None:
            print(f"inactividad: swayidle salio ({self.proc.returncode}); lo relanzo", file=sys.stderr, flush=True)
            self._swayidle()
        return GLib.SOURCE_CONTINUE

    def salir(self):
        self._parar()
        self.loop.quit()


def main() -> int:
    try:
        ctypes.CDLL(None).prctl(15, PROC.encode(), 0, 0, 0)  # PR_SET_NAME
    except (OSError, AttributeError):
        pass
    import os
    otros = [x for x in subprocess.run(["pgrep", "-x", PROC], capture_output=True, text=True).stdout.split()
             if x != str(os.getpid())]
    if otros:
        print(f"inactividad: ya esta corriendo (pid {otros[0]})", file=sys.stderr)
        return 0
    loop = GLib.MainLoop()
    idle = Idle(loop)
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        GLibUnix.signal_add(GLib.PRIORITY_DEFAULT, sig, lambda *_: (idle.salir(), GLib.SOURCE_REMOVE)[1])
    loop.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
