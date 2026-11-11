#!/usr/bin/env python3
"""NEURAL//TILE: mosaico de ventanas en CINTA para Wayfire (por IPC).

Modos (sección "mosaico" de ~/.config/live-wallpaper/ajustes.json):
  0 = LIBRE (ventanas flotantes, como siempre)
  1..4 = CINTA: todas las ventanas del escritorio forman una fila sin límite y
     la pantalla es una "ventanilla" que enseña N a la vez:
     1 → pantalla completa, 2 → mitades, 3 → una grande + dos, 4 → cuadrícula 2×2.
  Una ventana nueva entra justo a la derecha de lo que ves (la cinta avanza).
  Las que quedan fuera de la ventanilla se minimizan (siguen en el dock) pero
  conservan su sitio: se recorren con las flechas (Super+Alt+←/→, o la vista
  NEURAL//CINTA con Super). Traer una desde el dock mueve la ventanilla hasta ella.
  Si mueves una ventana a otro hueco (arrastrando o con Super+←/→ del plugin grid),
  intercambia su sitio con la que estaba allí en vez de volver atrás.
  Al volver a LIBRE cada ventana recupera su tamaño/posición de antes.

Uso:
  mosaico.py                demonio (proceso "neural-tile", instancia única)
  mosaico.py --modo N       cambia el modo (0-4) y sale
  mosaico.py --siguiente    pasa al siguiente modo (LIBRE→1→2→3→4→LIBRE)
  mosaico.py --cmd ORDEN [ARG]   habla con el demonio (socket):
       desplazar ±1 · mover ±1 · ir ID · estado · captura · vista 0|1
Señales al demonio: SIGUSR1 / SIGUSR2 = mover la ventana activa al hueco siguiente / anterior.
Miniaturas de las ventanas (para la vista): ~/.cache/neural-tile/mini/ID.png
"""
from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from energia import AJUSTES, guardar_seccion, leer_todo  # noqa: E402

PROC = "neural-tile"
MODOS = (0, 1, 2, 3, 4)
DEFECTO = {"modo": 0, "hueco": 10}
EVENTOS = {"view-mapped", "view-unmapped", "view-minimized", "view-fullscreen",
           "view-set-output", "view-wset-changed", "view-workspace-changed",
           "wset-workspace-changed", "output-wset-changed", "view-focused"}
MINI = Path.home() / ".cache" / "neural-tile" / "mini"
ESCALA_MINI = 0.3


def _socket_path() -> Path:
    run = Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}")
    return run / "neural-tile.sock"


def leer() -> dict:
    d = leer_todo().get("mosaico", {})
    out = dict(DEFECTO)
    if isinstance(d, dict):
        if d.get("modo") in MODOS:
            out["modo"] = d["modo"]
        try:
            out["hueco"] = max(0, min(40, int(d.get("hueco", out["hueco"]))))
        except (TypeError, ValueError):
            pass
    return out


def nombre_modo(m: int) -> str:
    return "LIBRE" if m == 0 else ("1 VENTANA" if m == 1 else f"{m} VENTANAS")


def rects(n: int, wa: dict, g: int) -> list[dict]:
    """Huecos para n ventanas dentro del área de trabajo wa con separación g."""
    x, y = int(wa["x"]) + g, int(wa["y"]) + g
    w, h = int(wa["width"]) - 2 * g, int(wa["height"]) - 2 * g
    mw, mh = (w - g) // 2, (h - g) // 2

    def r(a, b, c, d):
        return {"x": a, "y": b, "width": c, "height": d}

    if n <= 1:
        return [r(x, y, w, h)]
    if n == 2:
        return [r(x, y, mw, h), r(x + mw + g, y, w - mw - g, h)]
    if n == 3:
        return [r(x, y, mw, h), r(x + mw + g, y, w - mw - g, mh),
                r(x + mw + g, y + mh + g, w - mw - g, h - mh - g)]
    return [r(x, y, mw, mh), r(x + mw + g, y, w - mw - g, mh),
            r(x, y + mh + g, mw, h - mh - g), r(x + mw + g, y + mh + g, w - mw - g, h - mh - g)]


def pedir(orden: str, timeout: float = 1.5, **datos) -> dict | None:
    """Cliente del socket del demonio. None si no está corriendo."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect(str(_socket_path()))
            s.sendall((json.dumps({"cmd": orden, **datos}) + "\n").encode())
            buf = b""
            while not buf.endswith(b"\n"):
                trozo = s.recv(65536)
                if not trozo:
                    break
                buf += trozo
        return json.loads(buf or b"null")
    except (OSError, ValueError):
        return None


class Mosaico:
    def __init__(self, loop):
        from gi.repository import Gio, GLib
        from wayfire_ipc import WayfireIPC

        self.GLib, self.Gio, self.loop = GLib, Gio, loop
        self.ipc = WayfireIPC()
        self.cfg = leer()
        self.cinta: dict[tuple, list[int]] = {}    # escritorio → ids en orden de la cinta
        self.inicio: dict[tuple, int] = {}         # escritorio → primera ventana visible
        self.ocultas: dict[int, tuple] = {}        # id minimizado POR NOSOTROS → escritorio
        self.flotante: dict[int, dict] = {}        # id → geometría antes del mosaico
        self.asignado: dict[int, dict] = {}        # id → hueco que le dimos la última vez
        self.en_vista: dict[tuple, set[int]] = {}  # escritorio → ids en la ventanilla tras el último orden
        self.vista_abierta = False                 # la vista NEURAL//CINTA está en pantalla
        self.pendiente = 0
        self.aviso_id = 0
        self.ipc.watch(self._evento)
        mon = Gio.File.new_for_path(str(AJUSTES)).monitor_file(Gio.FileMonitorFlags.NONE, None)
        mon.connect("changed", lambda *_: self._ajustes())
        self._mon = mon
        MINI.mkdir(parents=True, exist_ok=True)
        self._servidor()
        print(f"mosaico: arrancado en modo {nombre_modo(self.cfg['modo'])}", flush=True)
        self.programar(50)

    def _reconectar(self):
        from wayfire_ipc import WayfireIPC
        self._intentos += 1
        try:
            ipc = WayfireIPC()
            ipc.call("window-rules/list-views")
            ipc.watch(self._evento)
        except (OSError, RuntimeError, ValueError):
            if self._intentos >= 30:  # Wayfire de verdad se fue: salir como antes
                print("mosaico: sin IPC de Wayfire; salgo", flush=True)
                self.loop.quit()
                return False
            return True
        self.ipc = ipc
        print("mosaico: IPC recuperada", flush=True)
        self.programar(50)
        return False

    # ---------- entrada ----------
    def _evento(self, msg):
        if msg is None:
            # Wayfire recargó plugins/config: reconectar en vez de morir (antes salía
            # y las ventanas que había minimizado se quedaban sin quien las trajera)
            print("mosaico: Wayfire cerró la IPC; reconectando", flush=True)
            self._intentos = 0
            self.GLib.timeout_add(1000, self._reconectar)
            return
        if msg.get("event") in EVENTOS:
            self.programar()

    def _ajustes(self):
        nuevo = leer()
        if nuevo != self.cfg:
            antes, self.cfg = self.cfg, nuevo
            print(f"mosaico: modo {nombre_modo(nuevo['modo'])}", flush=True)
            if antes["modo"] != nuevo["modo"]:
                self._avisar()
            self.programar(30)

    def programar(self, ms=120):
        if self.pendiente:
            self.GLib.source_remove(self.pendiente)

        def ya():
            self.pendiente = 0
            self._ordenar_seguro()
            return self.GLib.SOURCE_REMOVE

        self.pendiente = self.GLib.timeout_add(ms, ya)

    def _ordenar_seguro(self):
        try:
            self.ordenar()
        except (OSError, RuntimeError, KeyError) as e:
            print(f"mosaico: {e}", flush=True)

    def _ya(self):
        """Ordenar ahora mismo (para órdenes del teclado / la vista)."""
        if self.pendiente:
            self.GLib.source_remove(self.pendiente)
            self.pendiente = 0
        self._ordenar_seguro()

    # ---------- socket de órdenes ----------
    def _servidor(self):
        ruta = _socket_path()
        try:
            ruta.unlink()
        except FileNotFoundError:
            pass
        self.srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.srv.bind(str(ruta))
        os.chmod(ruta, 0o600)
        self.srv.listen(8)
        self.GLib.io_add_watch(self.srv.fileno(), self.GLib.IO_IN, self._aceptar)

    def _aceptar(self, *_):
        try:
            con, _a = self.srv.accept()
        except OSError:
            return True
        with con:
            con.settimeout(1.0)
            try:
                buf = b""
                while not buf.endswith(b"\n"):
                    t = con.recv(4096)
                    if not t:
                        break
                    buf += t
                pet = json.loads(buf or b"{}")
                resp = self._orden(pet)
            except (OSError, ValueError, RuntimeError, KeyError) as e:
                resp = {"error": str(e)}
            try:
                con.sendall((json.dumps(resp) + "\n").encode())
            except OSError:
                pass
        return True

    def _orden(self, pet: dict) -> dict:
        cmd = pet.get("cmd")
        if cmd == "estado":
            return self.estado()
        if cmd == "desplazar":
            self.desplazar(int(pet.get("paso", 1)))
        elif cmd == "mover":
            self.mover_activa(int(pet.get("paso", 1)), pet.get("id"))
        elif cmd == "ir":
            self.ir(int(pet["id"]))
        elif cmd == "captura":
            self.captura_visibles()
        elif cmd == "vista":
            self.vista_abierta = bool(pet.get("abierta"))
            if not self.vista_abierta:
                self.programar(60)
        else:
            return {"error": f"orden desconocida: {cmd}"}
        return self.estado()

    # ---------- escritorio activo ----------
    def _clave_salida(self, o) -> tuple:
        ws = o.get("workspace", {})
        return (o["id"], o.get("wset-index"), ws.get("x"), ws.get("y"))

    def _activa(self):
        """(salida, clave) de la salida con el foco."""
        try:
            o = (self.ipc.call("window-rules/get-focused-output") or {}).get("info")
        except RuntimeError:
            o = None
        if not o:
            o = self.ipc.call("window-rules/list-outputs")[0]
        return o, self._clave_salida(o)

    def _vistas(self):
        return [v for v in self.ipc.call("window-rules/list-views")
                if v.get("type") == "toplevel" and v.get("mapped") and v.get("parent", -1) == -1]

    # ---------- acciones ----------
    def estado(self) -> dict:
        self._ya()
        o, clave = self._activa()
        vistas = self._vistas()
        por_id = {v["id"]: v for v in vistas}
        n = self.cfg["modo"]
        try:
            foco = ((self.ipc.call("window-rules/get-focused-view") or {}).get("info") or {}).get("id")
        except RuntimeError:
            foco = None
        if n:
            ids = [i for i in self.cinta.get(clave, []) if i in por_id]
            ini = self.inicio.get(clave, 0)
        else:  # LIBRE: todas las del escritorio (también minimizadas), por orden de apertura
            ids = sorted(v["id"] for v in vistas if self._aqui(v, o, incluir_min=True))
            ini = 0
        lista = []
        for i in ids:
            v = por_id[i]
            mini = MINI / f"{i}.png"
            lista.append({"id": i, "titulo": v.get("title") or "", "app_id": v.get("app-id") or "",
                          "minimizada": bool(v.get("minimized")),
                          "mini": str(mini) if mini.exists() else None})
        return {"modo": n, "cinta": lista, "inicio": ini, "foco": foco}

    def desplazar(self, paso: int):
        if not self.cfg["modo"]:
            return
        _o, clave = self._activa()
        cinta = self.cinta.get(clave, [])
        n = self.cfg["modo"]
        if len(cinta) <= n:
            return
        ini = max(0, min(len(cinta) - n, self.inicio.get(clave, 0) + paso))
        if ini == self.inicio.get(clave, 0):
            return
        self.inicio[clave] = ini
        self._ya()
        # foco a la que acaba de entrar por ese lado
        entra = cinta[ini + n - 1] if paso > 0 else cinta[ini]
        self._enfocar(entra)

    def ir(self, vid: int):
        """Mover la ventanilla para que se vea vid y darle el foco."""
        if self.cfg["modo"]:
            for clave, cinta in self.cinta.items():
                if vid in cinta:
                    i, n, ini = cinta.index(vid), self.cfg["modo"], self.inicio.get(clave, 0)
                    if i < ini:
                        self.inicio[clave] = i
                    elif i >= ini + n:
                        self.inicio[clave] = i - n + 1
                    self._ya()
                    break
        self._enfocar(vid)

    def _enfocar(self, vid: int):
        try:
            self.ipc.call("window-rules/focus-view", {"id": vid})
        except RuntimeError:
            pass

    def mover_activa(self, paso: int, vid: int | None = None):
        if vid is None:
            try:
                foco = (self.ipc.call("window-rules/get-focused-view") or {}).get("info") or {}
            except RuntimeError:
                return
            vid = foco.get("id")
        for clave, lista in self.cinta.items():
            if vid in lista and len(lista) > 1:
                i = lista.index(vid)
                j = i + paso
                if not 0 <= j < len(lista):
                    return
                lista[i], lista[j] = lista[j], lista[i]
                # que la ventana movida siga a la vista
                n, ini = self.cfg["modo"], self.inicio.get(clave, 0)
                if j < ini:
                    self.inicio[clave] = j
                elif j >= ini + n:
                    self.inicio[clave] = j - n + 1
                self._ya()
                self._enfocar(vid)
                return

    def _avisar(self):
        m = self.cfg["modo"]
        cuerpo = ("Ventanas flotantes" if m == 0 else
                  "CINTA: Super+Alt+←/→ recorre · Super abre la vista")
        try:
            bus = self.Gio.bus_get_sync(self.Gio.BusType.SESSION, None)
            V = self.GLib.Variant
            r = bus.call_sync("org.freedesktop.Notifications", "/org/freedesktop/Notifications",
                              "org.freedesktop.Notifications", "Notify",
                              V("(susssasa{sv}i)", ("NEURAL//TILE", self.aviso_id, "view-grid",
                                                    f"MOSAICO · {nombre_modo(m)}", cuerpo, [],
                                                    {"transient": V("b", True), "urgency": V("y", 0)},
                                                    1600)),
                              None, self.Gio.DBusCallFlags.NONE, 500, None)
            self.aviso_id = r.unpack()[0]
        except Exception as e:  # el aviso es opcional
            print(f"mosaico: sin aviso ({e})", flush=True)

    # ---------- miniaturas ----------
    def _capturar(self, salida: dict, vistas: list[dict]):
        """Una sola captura de la salida y un recorte por ventana (escala ESCALA_MINI)."""
        if self.vista_abierta or not vistas:
            return
        try:
            png = subprocess.run(["grim", "-o", salida["name"], "-s", str(ESCALA_MINI), "-"],
                                 capture_output=True, timeout=2).stdout
            from gi.repository import GdkPixbuf
            ld = GdkPixbuf.PixbufLoader.new_with_type("png")
            ld.write(png)
            ld.close()
            pb = ld.get_pixbuf()
        except Exception as e:  # sin grim / sin salida: sin miniaturas
            print(f"mosaico: sin miniatura ({e})", flush=True)
            return
        geo = salida["geometry"]
        W, H = pb.get_width(), pb.get_height()
        for v in vistas:
            g = v["geometry"]
            x = max(0, int((g["x"] - geo["x"]) * ESCALA_MINI))
            y = max(0, int((g["y"] - geo["y"]) * ESCALA_MINI))
            w = min(W - x, int(g["width"] * ESCALA_MINI))
            h = min(H - y, int(g["height"] * ESCALA_MINI))
            if w > 8 and h > 8:
                try:
                    pb.new_subpixbuf(x, y, w, h).savev(str(MINI / f"{v['id']}.png"), "png", [], [])
                except Exception:
                    pass

    def captura_visibles(self):
        o, _clave = self._activa()
        vistas = [v for v in self._vistas() if not v.get("minimized") and self._aqui(v, o)]
        self._capturar(o, vistas)

    # ---------- lógica ----------
    @staticmethod
    def _aqui(v, o, incluir_min=False) -> bool:
        if v.get("output-id") != o["id"] or v.get("fullscreen"):
            return False
        if v.get("minimized"):
            return incluir_min
        geo, vg = o["geometry"], v["geometry"]
        cx, cy = vg["x"] + vg["width"] / 2, vg["y"] + vg["height"] / 2
        return 0 <= cx - geo["x"] < geo["width"] and 0 <= cy - geo["y"] < geo["height"]

    def ordenar(self):
        vistas = self._vistas()
        por_id = {v["id"]: v for v in vistas}
        # olvidar ventanas cerradas / ocultas que el usuario ya restauró
        for vid in list(self.ocultas):
            if vid not in por_id or not por_id[vid].get("minimized"):
                self.ocultas.pop(vid)
        for d in (self.flotante, self.asignado):
            for vid in list(d):
                if vid not in por_id:
                    d.pop(vid)
        for f in MINI.glob("*.png"):
            if f.stem.isdigit() and int(f.stem) not in por_id:
                f.unlink(missing_ok=True)

        for o in self.ipc.call("window-rules/list-outputs"):
            self._ordenar_salida(o, vistas, por_id)

    def _ordenar_salida(self, o, vistas, por_id):
        clave = self._clave_salida(o)
        wa = o["workarea"]
        n, g = self.cfg["modo"], self.cfg["hueco"]
        visibles = [v for v in vistas if not v.get("minimized") and self._aqui(v, o)]
        vis_ids = {v["id"] for v in visibles}
        mias = {vid for vid, k in self.ocultas.items() if k == clave}

        if n == 0:
            tocadas = set(self.cinta.pop(clave, [])) | mias | vis_ids
            self.inicio.pop(clave, None)
            self.en_vista.pop(clave, None)
            for vid in mias:
                self._minimizar(vid, False)
            for vid in tocadas:
                if vid in por_id and vid in self.flotante:
                    self._configurar(por_id[vid], self.flotante.pop(vid))
                self.asignado.pop(vid, None)
            return

        foco = lambda i: por_id[i].get("last-focus-timestamp", 0)  # noqa: E731
        # la cinta: las que ya tenía y siguen aquí (visibles u ocultadas por nosotros);
        # una que el usuario minimiza a mano sale de la cinta (y vuelve como nueva)
        cinta = [i for i in self.cinta.get(clave, []) if i in vis_ids or i in mias]
        ini = self.inicio.get(clave, 0)
        # nuevas: entran justo a la derecha de la ventanilla y la cinta avanza
        for vid in sorted(vis_ids - set(cinta), key=foco):
            pos = min(ini + n, len(cinta))
            cinta.insert(pos, vid)
            ini = max(0, pos - n + 1)
        # traída desde el dock / Alt+Tab: visible, fuera de la ventanilla y que NO estaba en
        # ella la última vez (las que acaba de empujar una nueva no cuentan)
        antes = self.en_vista.get(clave, set())
        fuera = [i for k, i in enumerate(cinta)
                 if i in vis_ids and i not in antes and not ini <= k < ini + n]
        if fuera:
            vid = max(fuera, key=foco)
            k = cinta.index(vid)
            ini = k if k < ini else k - n + 1
        ini = max(0, min(ini, max(0, len(cinta) - n)))
        vista = cinta[ini:ini + n]
        huecos = rects(len(vista), wa, g)

        # movida a mano a otro hueco (arrastre, grid…): intercambiar en vez de devolverla
        for a, vid in enumerate(vista):
            v, asig = por_id.get(vid), self.asignado.get(vid)
            if v is None or asig is None or v.get("minimized"):
                continue
            vg = v["geometry"]
            # solo si la MOVIERON (una app más grande que su hueco no cuenta)
            if abs(vg["x"] - asig["x"]) < 20 and abs(vg["y"] - asig["y"]) < 20:
                continue
            cx, cy = vg["x"] + vg["width"] / 2, vg["y"] + vg["height"] / 2
            for b, r in enumerate(huecos):
                if b != a and r["x"] <= cx < r["x"] + r["width"] and r["y"] <= cy < r["y"] + r["height"]:
                    vista[a], vista[b] = vista[b], vista[a]
                    break
            else:
                continue
            break
        cinta[ini:ini + n] = vista

        # fuera de la ventanilla → minimizar (con miniatura antes de que se vaya)
        salen = [por_id[i] for i in cinta if i not in vista and i in vis_ids]
        if salen:
            self._capturar(o, salen)
        for v in salen:
            self._minimizar(v["id"], True, clave)
        for vid in vista:
            if vid in mias:
                self._minimizar(vid, False)
        self.cinta[clave], self.inicio[clave] = cinta, ini
        self.en_vista[clave] = set(vista)

        for vid, r in zip(vista, huecos):
            v = por_id.get(vid)
            if v is None:
                continue
            if vid not in self.flotante:
                self.flotante[vid] = dict(v["geometry"])
            self.asignado[vid] = dict(r)
            self._configurar(v, r)

    def _configurar(self, v, r):
        vg = v["geometry"]
        if all(abs(vg[k] - r[k]) < 1 for k in ("x", "y", "width", "height")):
            return
        self.ipc.call("window-rules/configure-view",
                      {"id": v["id"], "geometry": {k: int(r[k]) for k in ("x", "y", "width", "height")}})

    def _minimizar(self, vid, estado, clave=None):
        self.ipc.call("wm-actions/set-minimized", {"view_id": vid, "state": estado})
        if estado:
            self.ocultas[vid] = clave
        else:
            self.ocultas.pop(vid, None)


def cambiar_modo(m: int) -> None:
    cfg = leer()
    cfg["modo"] = m
    guardar_seccion("mosaico", cfg)


def main() -> int:
    if "--modo" in sys.argv:
        m = int(sys.argv[sys.argv.index("--modo") + 1])
        if m not in MODOS:
            print("modo: 0 (libre) a 4", file=sys.stderr)
            return 2
        cambiar_modo(m)
        return 0
    if "--siguiente" in sys.argv:
        cambiar_modo((leer()["modo"] + 1) % len(MODOS))
        return 0
    if "--cmd" in sys.argv:
        resto = sys.argv[sys.argv.index("--cmd") + 1:]
        if not resto:
            return 2
        orden, arg = resto[0], (resto[1] if len(resto) > 1 else None)
        datos = {"paso": int(arg)} if orden in ("desplazar", "mover") and arg else {}
        if orden == "ir" and arg:
            datos = {"id": int(arg)}
        if orden == "vista":
            datos = {"abierta": arg == "1"}
        r = pedir(orden, **datos)
        if orden == "estado":
            print(json.dumps(r, indent=2, ensure_ascii=False))
        return 0 if r is not None else 1

    import ctypes
    import fcntl

    import gi
    gi.require_version("Gio", "2.0")
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GLib, GLibUnix

    try:
        ctypes.CDLL(None).prctl(15, PROC.encode(), 0, 0, 0)
    except (OSError, AttributeError):
        pass
    run = Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}")
    candado = open(run / "neural-tile.lock", "a+")
    try:
        fcntl.flock(candado, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("mosaico: ya está corriendo", file=sys.stderr)
        return 0

    loop = GLib.MainLoop()
    m = Mosaico(loop)
    from senales import segura  # 5-nov: un fallo moviendo ya no deja la señal sin dueño (= muerte)
    GLibUnix.signal_add(GLib.PRIORITY_DEFAULT, signal.SIGUSR1, segura("mosaico SIGUSR1", lambda: m.mover_activa(1)))
    GLibUnix.signal_add(GLib.PRIORITY_DEFAULT, signal.SIGUSR2, segura("mosaico SIGUSR2", lambda: m.mover_activa(-1)))
    for sig in (signal.SIGTERM, signal.SIGINT):
        GLibUnix.signal_add(GLib.PRIORITY_DEFAULT, sig, lambda *_: (loop.quit(), False)[1])
    loop.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
