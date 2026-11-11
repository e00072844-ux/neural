#!/usr/bin/env python3
"""NEURAL//NEXO: servicio de transferencia local (sin servidores ni internet).

Corre como servicio de usuario; la ventana (nexo.py) solo es un mando por el
socket Unix $XDG_RUNTIME_DIR/neural-nexo.sock. Si se cierra la ventana o se
corta la red, las transferencias siguen o se reanudan desde lo ya recibido.

Mensajes dentro del canal (nexo_proto.py), "t":
  ping | pong | pair_req | pair_ok | pair_no
  offer {tid, files:[{fid,name,size}]} → answer {tid, ok}
  get {tid, fid, off}  → data {tid, fid, off} + bytes | err {tid, fid, why}
  stop {tid, fid} | verify {tid, fid, sha} → verified {tid, fid, ok}
  done {tid} | cancel {tid}
Manda el receptor: pide cada archivo desde lo que ya tiene en disco.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import signal
import socket
import struct
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from nexo_bt import BT
from nexo_proto import CanalRoto, Identidad, saludo
from nexo_terminal import ESPERA_DESCONECTADA, MAX_POR_PAR, Terminal, registrar

PUERTO = int(os.environ.get("NEXO_TCP", "47800"))
PUERTO_UDP = 47801
GRUPO = "239.77.80.1"
BASE = Path(os.environ.get("NEXO_HOME", Path.home() / ".local/state/neural-nexo"))
SOCK = Path(os.environ.get("NEXO_SOCK", Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "neural-nexo.sock"))
RECIBIDOS_DEF = Path(os.environ.get("NEXO_RECIBIDOS", Path.home() / "Descargas/NEXO"))

BLOQUE_LAN = 1024 * 1024
BLOQUE_BT = 16 * 1024
MAX_BT = 32 * 1024 * 1024  # lo que se atreve a pasar por Bluetooth (~200 KB/s)
PARALELOS = 3
LATIDO = 4
MUERTO = 14
MAX_ARCHIVOS = 50_000
MAX_HISTORIAL = 200
IFACES_NO = ("lo", "docker", "virbr", "neuralvm", "tun", "wg", "veth")
RE_TID = re.compile(r"^[0-9a-f]{32}$")
MAX_SALUDOS = 32  # conexiones a medio autenticar
MAX_SIN_PAR = 16  # sesiones de dispositivos sin emparejar
VIDA_SIN_PAR = 180


def limpiar_nombre(nombre) -> str | None:
    """Ruta relativa segura, o None. Lo que manda el otro nunca sale de la carpeta."""
    if not isinstance(nombre, str) or not 0 < len(nombre) <= 4096 or "\0" in nombre or "\\" in nombre:
        return None
    partes = nombre.split("/")
    if any(p in ("", ".", "..") or len(p.encode()) > 240 for p in partes) or partes[0] == ".nexo-parcial":
        return None
    return "/".join(partes)


def sha_archivo(ruta) -> str:
    h = hashlib.sha256()
    with open(ruta, "rb") as f:
        while b := f.read(4 * 1024 * 1024):
            h.update(b)
    return h.hexdigest()


def destino_libre(base: Path, rel: str) -> Path:
    d = base / rel
    if not d.exists():
        return d
    n = 1
    while True:
        c = d.with_name(f"{d.stem} ({n}){d.suffix}")
        if not c.exists():
            return c
        n += 1


def interfaces() -> list[tuple[str, str, str]]:
    """[(nombre, ip, broadcast)] IPv4 de las interfaces útiles."""
    try:
        s = subprocess.run(["ip", "-j", "-4", "addr"], capture_output=True, text=True, timeout=3).stdout
        datos = json.loads(s or "[]")
    except (OSError, ValueError, subprocess.SubprocessError):
        return []
    res = []
    for i in datos:
        if i.get("operstate") == "DOWN" or i.get("ifname", "lo").startswith(IFACES_NO):
            continue
        for a in i.get("addr_info", []):
            if a.get("family") == "inet" and a.get("broadcast"):
                res.append((i["ifname"], a["local"], a["broadcast"]))
    return res


class Almacen:
    def __init__(self, ruta: Path, defecto):
        self.ruta = ruta
        try:
            self.d = json.loads(ruta.read_text())
        except (OSError, ValueError):
            self.d = defecto

    def guardar(self) -> None:
        tmp = self.ruta.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.d))
        os.replace(tmp, self.ruta)


@dataclass
class Sesion:
    canal: object
    peer: str
    via: str
    iniciador: bool
    ip: str = ""
    ult_rx: float = field(default_factory=time.monotonic)
    creada: float = field(default_factory=time.monotonic)
    envios: dict = field(default_factory=dict)
    activos: set = field(default_factory=set)
    verif: set = field(default_factory=set)
    registrada: bool = False


class Faro(asyncio.DatagramProtocol):
    def __init__(self, motor):
        self.m = motor

    def datagram_received(self, data, addr):
        if data[:4] != b"NXOB" or len(data) > 1024:
            return
        try:
            d = json.loads(data[4:])
            self.m.visto(str(d["id"]), str(d["name"])[:64], str(d.get("kind", "pc"))[:16], addr[0], int(d["port"]))
        except (ValueError, KeyError, TypeError):
            pass


class Motor:
    def __init__(self):
        BASE.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.ident = Identidad(BASE / "identidad.pem")
        self.cfg = Almacen(BASE / "config.json", {"name": socket.gethostname(), "recibidos": str(RECIBIDOS_DEF)})
        self.pares = Almacen(BASE / "pares.json", {})
        self.entrada = Almacen(BASE / "entrada.json", {})
        self.salida = Almacen(BASE / "salida.json", {})
        self.vecinos: dict[str, dict] = {}
        self.sesiones: dict[str, dict[str, Sesion]] = {}
        self.provisionales: dict[str, Sesion] = {}
        self.manuales: set[tuple[str, int]] = set()
        self.quiero_par: set[str] = set()
        self.par: dict | None = None
        self.marcando: set[str] = set()
        self.espera_addr: dict[str, float] = {}
        self.fds: dict[tuple, int] = {}
        self.vel: dict[str, dict] = {}
        self.clientes: list[asyncio.StreamWriter] = []
        self.avisos: list[str] = []
        self.ifaces: list[tuple] = []
        self.loop: asyncio.AbstractEventLoop | None = None
        self.bt: BT | None = None
        self.tareas: set[asyncio.Task] = set()
        self.grupos: set[str] = set()
        self.udp: socket.socket | None = None
        self.terms: dict[str, Terminal] = {}
        self.saludando = 0

    # ---- utilidades ----
    def lanzar(self, coro) -> asyncio.Task:
        t = asyncio.ensure_future(coro)
        self.tareas.add(t)
        t.add_done_callback(self.tareas.discard)
        return t

    @property
    def recibidos(self) -> Path:
        return Path(self.cfg.d["recibidos"])

    def parcial(self, tid: str, fid: int) -> Path:
        return self.recibidos / ".nexo-parcial" / tid / str(fid)

    def nombre_de(self, pid: str) -> str:
        return self.pares.d.get(pid, {}).get("name") or self.vecinos.get(pid, {}).get("name") or pid[:8]

    def avisar(self, texto: str) -> None:
        self.avisos.append(texto)

    def notificar(self, titulo: str, cuerpo: str) -> None:
        try:
            subprocess.Popen(["notify-send", "-a", "NEURAL//NEXO", "-i", "org.local.NeuralNexo", titulo, cuerpo],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            pass

    def mejor(self, pid: str) -> Sesion | None:
        s = self.sesiones.get(pid, {})
        return s.get("lan") or s.get("bt")

    def visto(self, pid: str, name: str, kind: str, ip: str, port: int, autenticado: bool = False) -> None:
        """Los faros UDP no llevan firma: solo sirven de pista. Lo guardado de un par cambia tras el handshake."""
        if pid == self.ident.id or not re.fullmatch(r"[0-9a-f]{32}", pid) or not 0 < port < 65536:
            return
        v = self.vecinos.setdefault(pid, {"addrs": {}})
        v.update(name=name, kind=kind, port=port, ts=time.monotonic())
        v["addrs"][ip] = time.monotonic()
        if len(v["addrs"]) > 8:
            del v["addrs"][min(v["addrs"], key=v["addrs"].get)]
        p = self.pares.d.get(pid)
        if autenticado and p and (p.get("name") != name or p.get("port") != port or ip not in p.get("addrs", [])):
            p.update(name=name, port=port, addrs=([ip] + [a for a in p.get("addrs", []) if a != ip])[:6])
            self.pares.guardar()

    def hello(self) -> dict:
        return {"name": self.cfg.d["name"], "kind": "pc", "port": PUERTO}

    # ---- conexiones ----
    async def atender(self, r, w, via: str, iniciador: bool, ip: str = "", cancel_si=None) -> None:
        ses = None
        try:
            if via == "lan":
                s = w.get_extra_info("socket")
                s.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                s.setsockopt(socket.IPPROTO_TCP, socket.TCP_USER_TIMEOUT, MUERTO * 1000)
            if not iniciador and self.saludando >= MAX_SALUDOS:
                return
            self.saludando += 1
            try:
                canal = await asyncio.wait_for(saludo(r, w, self.ident, iniciador, self.hello()), 12)
            finally:
                self.saludando -= 1
            pid = canal.peer_id
            if pid == self.ident.id:
                canal.cerrar()
                return
            h = canal.hello
            if via == "lan" and ip:
                self.visto(pid, str(h.get("name", ""))[:64], str(h.get("kind", "pc"))[:16], ip,
                           int(h.get("port", PUERTO)), autenticado=True)
            ses = Sesion(canal, pid, via, iniciador, ip)
            if pid in self.pares.d:
                if not self.registrar(ses):
                    canal.cerrar()
                    return
            else:
                if pid not in self.provisionales and len(self.provisionales) >= MAX_SIN_PAR:
                    canal.cerrar()
                    ses = None
                    return
                self.provisionales[pid] = ses
            if pid in self.quiero_par:
                self.quiero_par.discard(pid)
                self.abrir_par(ses, yo_ok=False)
                await canal.enviar({"t": "pair_req"})
            latido = self.lanzar(self.latir(ses))
            try:
                while True:
                    msg, datos = await canal.recibir()
                    ses.ult_rx = time.monotonic()
                    await self.procesar(ses, msg, datos)
            finally:
                latido.cancel()
        except (CanalRoto, asyncio.TimeoutError, OSError, ValueError, TypeError) as e:
            if ses is None:
                self.espera_addr[ip] = time.monotonic() + 2
            del e
        finally:
            if ses:
                self.soltar(ses)
                ses.canal.cerrar()
            else:
                try:
                    w.close()
                except (OSError, RuntimeError):
                    pass

    def registrar(self, ses: Sesion) -> bool:
        lista = self.sesiones.setdefault(ses.peer, {})
        vieja = lista.get(ses.via)
        if vieja:
            # Si dos se marcaron a la vez, los dos lados conservan la misma: la que
            # inició el id menor. Una vieja que ya no habla siempre pierde.
            callada = time.monotonic() - vieja.ult_rx > 6
            yo_menor = self.ident.id < ses.peer
            if not callada and vieja.iniciador == yo_menor:
                return False
            self.soltar(vieja)
            vieja.canal.cerrar()
        lista[ses.via] = ses
        ses.registrada = True
        if self.provisionales.get(ses.peer) is ses:
            del self.provisionales[ses.peer]
        if ses.via == "lan" and lista.get("bt"):  # volvió la red: se deja de usar Bluetooth
            self.lanzar(self.dejar_bt(lista["bt"]))
        self.lanzar(self.al_conectar(ses))
        return True

    async def dejar_bt(self, ses: Sesion) -> None:
        for tid, fid in list(ses.activos):
            await self.enviar(ses, {"t": "stop", "tid": tid, "fid": fid})
        ses.activos.clear()

    def soltar(self, ses: Sesion) -> None:
        for t in ses.envios.values():
            t.cancel()
        ses.envios.clear()
        ses.activos.clear()
        for t in self.terms.values():
            if t.ses is ses:  # el shell sigue vivo: se podrá retomar
                t.ses, t.solta = None, time.monotonic()
        if self.provisionales.get(ses.peer) is ses:
            del self.provisionales[ses.peer]
        if self.sesiones.get(ses.peer, {}).get(ses.via) is ses:
            del self.sesiones[ses.peer][ses.via]
            self.bombear(ses.peer)  # si queda Bluetooth, sigue por ahí lo que quepa
        if self.par and self.par.get("ses") is ses and not self.par.get("cerrando"):
            self.par = None

    async def enviar(self, ses: Sesion, msg: dict, datos: bytes = b"") -> bool:
        try:
            await ses.canal.enviar(msg, datos)
            return True
        except (ConnectionError, OSError, RuntimeError):
            ses.canal.cerrar()
            return False

    async def latir(self, ses: Sesion) -> None:
        while True:
            await asyncio.sleep(LATIDO)
            ahora = time.monotonic()
            sin_par_vieja = not ses.registrada and ahora - ses.creada > VIDA_SIN_PAR and not (self.par and self.par.get("ses") is ses)
            if ahora - ses.ult_rx > MUERTO or sin_par_vieja:
                ses.canal.cerrar()
                return
            await self.enviar(ses, {"t": "ping"})

    async def marcar(self, pid: str, ip: str, port: int) -> None:
        try:
            r, w = await asyncio.wait_for(asyncio.open_connection(ip, port), 3)
        except (OSError, asyncio.TimeoutError):
            self.espera_addr[f"{ip}:{port}"] = time.monotonic() + 5
            return
        finally:
            self.marcando.discard(f"{pid}{ip}")
        await self.atender(r, w, "lan", True, ip)

    def candidatos(self, pid: str) -> list[tuple[str, int]]:
        ahora = time.monotonic()
        v, p = self.vecinos.get(pid, {}), self.pares.d.get(pid, {})
        port = p.get("port") or v.get("port") or PUERTO
        verificadas = p.get("addrs", [])  # primero las confirmadas por un handshake
        ips = verificadas + [a for a in sorted(v.get("addrs", {}), key=lambda a: -v["addrs"][a]) if a not in verificadas]
        return [(ip, port) for ip in ips if not ip.startswith("127.") and self.espera_addr.get(f"{ip}:{port}", 0) <= ahora]

    async def vigilar(self) -> None:
        """Mantiene viva la conexión con cada par: reintenta por todas las vías."""
        ciclo = 0
        while True:
            for pid in list(self.pares.d):
                if self.sesiones.get(pid, {}).get("lan"):
                    continue
                for ip, port in self.candidatos(pid)[:4]:
                    if f"{pid}{ip}" not in self.marcando:
                        self.marcando.add(f"{pid}{ip}")
                        self.lanzar(self.marcar(pid, ip, port))
            for ip, port in list(self.manuales):
                self.manuales.discard((ip, port))
                self.lanzar(self.marcar("manual", ip, port))
            ciclo += 1
            if self.bt and ciclo % 15 == 0 and any(not self.sesiones.get(p) for p in self.pares.d):
                self.bt.marcar()
            await asyncio.sleep(2)

    def bt_entrante(self, fd: int) -> None:
        self.loop.call_soon_threadsafe(lambda: self.lanzar(self.abrir_bt(fd)))

    async def abrir_bt(self, fd: int) -> None:
        s = socket.socket(fileno=fd)
        s.setblocking(False)
        r, w = await asyncio.open_connection(sock=s)
        await self.atender(r, w, "bt", False)

    # ---- descubrimiento ----
    async def faro(self) -> None:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("", PUERTO_UDP))
        s.setblocking(False)
        self.udp = s
        await self.loop.create_datagram_endpoint(lambda: Faro(self), sock=s)
        ciclo = 0
        while True:
            if ciclo % 5 == 0:
                self.ifaces = await self.loop.run_in_executor(None, interfaces)
                for _, ip, _b in self.ifaces:
                    if ip not in self.grupos:
                        try:
                            s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                                         socket.inet_aton(GRUPO) + socket.inet_aton(ip))
                            self.grupos.add(ip)
                        except OSError:
                            pass
            self.emitir_faro()
            ciclo += 1
            await asyncio.sleep(2 if ciclo < 10 else 3)

    def emitir_faro(self) -> None:
        carga = b"NXOB" + json.dumps({"id": self.ident.id, "name": self.cfg.d["name"], "kind": "pc",
                                      "port": PUERTO}).encode()
        conocidas = {a for p in self.pares.d.values() for a in p.get("addrs", [])}
        for _n, ip, broad in self.ifaces:  # sale por CADA interfaz, no por la que elija la ruta
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as t:
                    t.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                    t.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(ip))
                    t.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
                    t.bind((ip, 0))
                    for dst in (broad, GRUPO, *conocidas):
                        try:
                            t.sendto(carga, (dst, PUERTO_UDP))
                        except OSError:
                            pass
            except OSError:
                pass

    # ---- emparejar ----
    def abrir_par(self, ses: Sesion, yo_ok: bool, el_ok: bool = False) -> None:
        self.par = {"ses": ses, "id": ses.peer, "sas": ses.canal.sas, "yo_ok": yo_ok, "el_ok": el_ok,
                    "desde": time.monotonic()}

    async def fin_par_si_toca(self) -> None:
        p = self.par
        if not (p and p["yo_ok"] and p["el_ok"]):
            return
        ses, h = p["ses"], p["ses"].canal.hello
        self.pares.d[ses.peer] = {"name": str(h.get("name", ses.peer[:8]))[:64], "kind": str(h.get("kind", "pc"))[:16],
                                  "port": int(h.get("port", PUERTO)), "addrs": [ses.ip] if ses.ip else [],
                                  "auto": False}
        self.pares.guardar()
        self.par = None
        if not ses.registrada and not self.registrar(ses):
            ses.canal.cerrar()
        self.avisar(f"Emparejado con {self.nombre_de(ses.peer)}")

    # ---- protocolo ----
    async def procesar(self, ses: Sesion, m: dict, datos: bytes) -> None:
        t = m.get("t")
        if t == "ping":
            await self.enviar(ses, {"t": "pong"})
        elif t == "pong":
            pass
        elif t == "pair_req":
            if self.par and self.par.get("ses") is not ses:
                await self.enviar(ses, {"t": "pair_no"})
            elif not self.par:
                self.abrir_par(ses, yo_ok=False)
                self.notificar("Solicitud de emparejamiento", f"{self.nombre_de(ses.peer)}: código {ses.canal.sas}")
        elif t == "pair_ok":
            if self.par and self.par["ses"] is ses:
                self.par["el_ok"] = True
                await self.fin_par_si_toca()
        elif t == "pair_no":
            if self.par and self.par["ses"] is ses:
                self.par = None
                self.avisar("El otro dispositivo rechazó el emparejamiento")
        elif ses.peer not in self.pares.d or not ses.registrada:
            return  # sin emparejar no se atiende nada más
        elif t == "offer":
            await self.h_offer(ses, m)
        elif t == "answer":
            self.h_answer(ses, m)
        elif t == "get":
            self.h_get(ses, m)
        elif t == "stop":
            tarea = ses.envios.pop((m.get("tid"), m.get("fid")), None)
            if tarea:
                tarea.cancel()
        elif t == "data":
            await self.h_data(ses, m, datos)
        elif t == "err":
            self.h_err(ses, m)
        elif t == "verify":
            self.lanzar(self.h_verify(ses, m))
        elif t == "verified":
            self.lanzar(self.h_verified(ses, m))
        elif t == "done":
            x = self.salida.d.get(m.get("tid"))
            if x and x["peer"] == ses.peer and x["estado"] == "activa":
                x["estado"] = "hecha"
                self.salida.guardar()
                self.notificar("Envío completado", f"{x['titulo']} → {self.nombre_de(ses.peer)}")
        elif t == "cancel":
            self.h_cancel(ses, m)
        elif isinstance(t, str) and t.startswith("term_"):
            await self.h_term(ses, t, m, datos)

    # ---- terminal remota ----
    async def h_term(self, ses: Sesion, t: str, m: dict, datos: bytes) -> None:
        par = self.pares.d[ses.peer]
        if t == "term_open":
            if not par.get("shell"):
                await self.enviar(ses, {"t": "term_no", "why": "La terminal está desactivada para este dispositivo. Actívala en NEURAL//NEXO en la laptop."})
                return
            cols, rows = m.get("cols", 80), m.get("rows", 24)
            if not isinstance(cols, int) or not isinstance(rows, int):
                return
            x = self.terms.get(m.get("sid") or "")
            if x and x.peer == ses.peer:
                x.ses, x.solta = ses, 0.0
                x.tam(cols, rows)
                await self.enviar(ses, {"t": "term_ok", "sid": x.sid, "nueva": False})
                buf = bytes(x.anillo)
                for i in range(0, len(buf), 16 * 1024):
                    await self.enviar(ses, {"t": "term_out", "sid": x.sid, "replay": True}, buf[i:i + 16 * 1024])
                x.redibujar()
                registrar(BASE, par["name"], "retomó sesión")
                return
            if sum(y.peer == ses.peer for y in self.terms.values()) >= MAX_POR_PAR:
                await self.enviar(ses, {"t": "term_no", "why": "Ya hay demasiadas terminales abiertas"})
                return
            try:
                x = Terminal(self, ses.peer, cols, rows)
            except OSError as e:
                await self.enviar(ses, {"t": "term_no", "why": str(e)[:200]})
                return
            x.ses = ses
            self.terms[x.sid] = x
            registrar(BASE, par["name"], "abrió sesión")
            self.notificar("Terminal remota abierta", f"{self.nombre_de(ses.peer)} tiene una terminal en esta laptop")
            await self.enviar(ses, {"t": "term_ok", "sid": x.sid, "nueva": True})
            return
        x = self.terms.get(m.get("sid") or "")
        if not x or x.peer != ses.peer or not par.get("shell"):
            return
        if t == "term_in":
            x.entrada(datos)
        elif t == "term_size" and isinstance(m.get("cols"), int) and isinstance(m.get("rows"), int):
            x.tam(m["cols"], m["rows"])
        elif t == "term_close":
            x.cerrar()

    def term_fin(self, x: Terminal) -> None:
        if self.terms.pop(x.sid, None):
            registrar(BASE, self.nombre_de(x.peer), "cerró sesión")
            self.notificar("Terminal remota cerrada", self.nombre_de(x.peer))
        x.liberar()

    def term_cerrar_de(self, pid: str) -> None:
        for x in list(self.terms.values()):
            if x.peer == pid:
                x.cerrar()

    async def h_offer(self, ses: Sesion, m: dict) -> None:
        tid = m.get("tid")
        archivos = m.get("files")
        if not (isinstance(tid, str) and RE_TID.match(tid) and isinstance(archivos, list)
                and 0 < len(archivos) <= MAX_ARCHIVOS):
            return
        x = self.entrada.d.get(tid)
        if x:
            if x["peer"] != ses.peer:
                return
            if x["estado"] in ("activa", "hecha", "rechazada"):
                await self.enviar(ses, {"t": "answer", "tid": tid, "ok": x["estado"] != "rechazada"})
            return
        lista, vistos = [], set()
        for f in archivos:
            if not isinstance(f, dict):
                return
            nombre, size, fid = limpiar_nombre(f.get("name")), f.get("size"), f.get("fid")
            if nombre is None or not isinstance(size, int) or size < 0 or not isinstance(fid, int) or fid in vistos:
                await self.enviar(ses, {"t": "answer", "tid": tid, "ok": False})
                return
            vistos.add(fid)
            lista.append({"fid": fid, "name": nombre, "size": size, "estado": "pendiente", "intentos": 0})
        total = sum(f["size"] for f in lista)
        titulo = lista[0]["name"].split("/")[0] + (f" +{len(lista) - 1}" if len(lista) > 1 else "")
        self.entrada.d[tid] = {"peer": ses.peer, "files": lista, "estado": "pendiente", "total": total,
                               "titulo": titulo, "ts": time.time()}
        self.podar()
        self.entrada.guardar()
        if self.pares.d[ses.peer].get("auto"):
            await self.aceptar(tid)
        else:
            self.notificar(f"{self.nombre_de(ses.peer)} quiere enviarte archivos", titulo)
            self.abrir_ventana()

    def abrir_ventana(self) -> None:
        if self.clientes or not os.environ.get("WAYLAND_DISPLAY"):
            return
        try:
            subprocess.Popen(["python3", str(Path(__file__).with_name("nexo.py"))], start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            pass

    async def aceptar(self, tid: str) -> None:
        x = self.entrada.d.get(tid)
        if not x or x["estado"] != "pendiente":
            return
        self.recibidos.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(self.recibidos).free < x["total"] + 64 * 1024 * 1024:
            self.avisar("No hay espacio suficiente para recibir eso")
            return
        x["estado"] = "activa"
        self.entrada.guardar()
        ses = self.mejor(x["peer"])
        if ses:
            await self.enviar(ses, {"t": "answer", "tid": tid, "ok": True})
        self.bombear(x["peer"])

    def h_answer(self, ses: Sesion, m: dict) -> None:
        x = self.salida.d.get(m.get("tid"))
        if x and x["peer"] == ses.peer and x["estado"] == "ofrecida":
            x["estado"] = "activa" if m.get("ok") else "rechazada"
            self.salida.guardar()
            if not m.get("ok"):
                self.notificar("Envío rechazado", x["titulo"])

    def h_get(self, ses: Sesion, m: dict) -> None:
        tid, fid, off = m.get("tid"), m.get("fid"), m.get("off")
        x = self.salida.d.get(tid)
        if not x or x["peer"] != ses.peer or x["estado"] not in ("ofrecida", "activa") or not isinstance(off, int) \
                or off < 0 or not isinstance(fid, int) or not 0 <= fid < len(x["files"]):
            return
        if x["estado"] == "ofrecida":
            x["estado"] = "activa"
            self.salida.guardar()
        vieja = ses.envios.pop((tid, fid), None)
        if vieja:
            vieja.cancel()
        ses.envios[(tid, fid)] = self.lanzar(self.emitir(ses, x, tid, fid, off))

    async def emitir(self, ses: Sesion, x: dict, tid: str, fid: int, off: int) -> None:
        f = x["files"][fid]
        bloque = BLOQUE_LAN if ses.via == "lan" else BLOQUE_BT
        fd = -1
        try:
            st = os.stat(f["path"])
            if st.st_size != f["size"] or st.st_mtime_ns != f["mtime"]:
                raise OSError("el archivo cambió")
            fd = os.open(f["path"], os.O_RDONLY)
            f["env"] = off
            while off < f["size"]:
                b = await self.loop.run_in_executor(None, os.pread, fd, min(bloque, f["size"] - off), off)
                if not b:
                    raise OSError("archivo más corto de lo anunciado")
                if not await self.enviar(ses, {"t": "data", "tid": tid, "fid": fid, "off": off}, b):
                    return
                off += len(b)
                f["env"] = off
                self.contar(tid, len(b))
        except OSError as e:
            await self.enviar(ses, {"t": "err", "tid": tid, "fid": fid, "why": str(e)[:200]})
        finally:
            if fd >= 0:
                os.close(fd)
            ses.envios.pop((tid, fid), None)

    def contar(self, tid: str, n: int) -> None:
        v = self.vel.setdefault(tid, {"b": 0, "bps": 0.0, "t": time.monotonic()})
        v["b"] += n

    def fd_parcial(self, tid: str, fid: int) -> int:
        fd = self.fds.get((tid, fid))
        if fd is None:
            p = self.parcial(tid, fid)
            p.parent.mkdir(parents=True, exist_ok=True)
            fd = self.fds[(tid, fid)] = os.open(p, os.O_RDWR | os.O_CREAT, 0o600)
        return fd

    def cerrar_fd(self, tid: str, fid: int) -> None:
        fd = self.fds.pop((tid, fid), None)
        if fd is not None:
            os.close(fd)

    def bombear(self, pid: str) -> None:
        """Pide lo que falte al mejor enlace disponible. Se puede llamar siempre."""
        ses = self.mejor(pid)
        if not ses:
            return
        for tid, x in self.entrada.d.items():
            if x["peer"] != pid or x["estado"] != "activa":
                continue
            for f in x["files"]:
                clave = (tid, f["fid"])
                if f["estado"] == "verif" and clave not in ses.verif:
                    ses.verif.add(clave)
                    self.lanzar(self.enviar(ses, {"t": "verify", "tid": tid, "fid": f["fid"], "sha": f["sha"]}))
                if f["estado"] != "pendiente" or clave in ses.activos:
                    continue
                try:
                    tam = self.parcial(tid, f["fid"]).stat().st_size
                except OSError:
                    tam = 0
                if tam > f["size"]:
                    self.parcial(tid, f["fid"]).unlink(missing_ok=True)
                    tam = 0
                f["recv"] = tam
                if tam >= f["size"]:
                    f["estado"] = "hash"
                    self.lanzar(self.comprobar(pid, tid, x, f))
                    continue
                if ses.via == "bt" and f["size"] - tam > MAX_BT:
                    continue
                if len(ses.activos) >= PARALELOS:
                    return
                ses.activos.add(clave)
                self.lanzar(self.enviar(ses, {"t": "get", "tid": tid, "fid": f["fid"], "off": tam}))

    async def h_data(self, ses: Sesion, m: dict, datos: bytes) -> None:
        tid, fid, off = m.get("tid"), m.get("fid"), m.get("off")
        clave = (tid, fid)
        x = self.entrada.d.get(tid)
        if clave not in ses.activos or not x or x["peer"] != ses.peer or x["estado"] != "activa":
            return
        f = next((a for a in x["files"] if a["fid"] == fid), None)
        if not f or off != f.get("recv") or off + len(datos) > f["size"]:
            return
        fd = self.fd_parcial(tid, fid)
        await self.loop.run_in_executor(None, os.pwrite, fd, datos, off)
        if clave not in ses.activos or (tid, fid) not in self.fds:
            return  # cancelado o cambiado de enlace mientras se escribía
        f["recv"] = off + len(datos)
        self.contar(tid, len(datos))
        if f["recv"] >= f["size"]:
            ses.activos.discard(clave)
            self.cerrar_fd(tid, fid)
            f["estado"] = "hash"
            self.lanzar(self.comprobar(ses.peer, tid, x, f))
            self.bombear(ses.peer)

    async def comprobar(self, pid: str, tid: str, x: dict, f: dict) -> None:
        p = self.parcial(tid, f["fid"])
        if f["size"] == 0:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.touch()
        try:
            f["sha"] = await self.loop.run_in_executor(None, sha_archivo, p)
        except OSError:
            f["estado"] = "pendiente"
            return
        if x["estado"] != "activa":
            return
        f["estado"] = "verif"
        self.entrada.guardar()
        self.bombear(pid)

    async def h_verify(self, ses: Sesion, m: dict) -> None:
        tid, fid = m.get("tid"), m.get("fid")
        x = self.salida.d.get(tid)
        if not x or x["peer"] != ses.peer or not isinstance(fid, int) or not 0 <= fid < len(x["files"]):
            return
        f = x["files"][fid]
        try:
            st = os.stat(f["path"])
            igual = st.st_size == f["size"] and st.st_mtime_ns == f["mtime"]
            sha = f.get("sha") or await self.loop.run_in_executor(None, sha_archivo, f["path"]) if igual else ""
        except OSError:
            sha = ""
        if sha:
            f["sha"] = sha
        await self.enviar(ses, {"t": "verified", "tid": tid, "fid": fid, "ok": bool(sha) and sha == m.get("sha")})

    async def h_verified(self, ses: Sesion, m: dict) -> None:
        tid, fid = m.get("tid"), m.get("fid")
        x = self.entrada.d.get(tid)
        if not x or x["peer"] != ses.peer or x["estado"] != "activa":
            return
        f = next((a for a in x["files"] if a["fid"] == fid), None)
        if not f or f["estado"] != "verif":
            return
        ses.verif.discard((tid, fid))
        p = self.parcial(tid, fid)
        if m.get("ok"):
            d = destino_libre(self.recibidos, f["name"])
            d.parent.mkdir(parents=True, exist_ok=True)
            os.replace(p, d)
            os.chmod(d, 0o644)
            f["estado"], f["dest"] = "ok", str(d)
        else:
            f["intentos"] += 1
            p.unlink(missing_ok=True)
            f["recv"] = 0
            f["estado"] = "error" if f["intentos"] >= 3 else "pendiente"
        self.entrada.guardar()
        self.revisar_fin(ses, tid, x)
        self.bombear(ses.peer)

    def h_err(self, ses: Sesion, m: dict) -> None:
        tid, fid = m.get("tid"), m.get("fid")
        x = self.entrada.d.get(tid)
        if not x or x["peer"] != ses.peer:
            return
        f = next((a for a in x["files"] if a["fid"] == fid), None)
        if f and f["estado"] == "pendiente":
            ses.activos.discard((tid, fid))
            self.cerrar_fd(tid, fid)
            f["estado"], f["error"] = "error", str(m.get("why", ""))[:200]
            self.entrada.guardar()
            self.revisar_fin(ses, tid, x)
            self.bombear(ses.peer)

    def revisar_fin(self, ses: Sesion, tid: str, x: dict) -> None:
        if any(f["estado"] not in ("ok", "error") for f in x["files"]):
            return
        malos = sum(f["estado"] == "error" for f in x["files"])
        x["estado"] = "hecha" if not malos else "fallida"
        self.entrada.guardar()
        shutil.rmtree(self.recibidos / ".nexo-parcial" / tid, ignore_errors=True)
        self.lanzar(self.enviar(ses, {"t": "done", "tid": tid}))
        self.notificar("Recibido" if not malos else f"Recibido con {malos} fallo(s)",
                       f"{x['titulo']} ← {self.nombre_de(ses.peer)}")

    def h_cancel(self, ses: Sesion, m: dict) -> None:
        tid = m.get("tid")
        x = self.entrada.d.get(tid)
        if x and x["peer"] == ses.peer and x["estado"] in ("pendiente", "activa"):
            self.limpiar_entrada(tid, x)
            x["estado"] = "cancelada"
            self.entrada.guardar()
        y = self.salida.d.get(tid)
        if y and y["peer"] == ses.peer and y["estado"] in ("ofrecida", "activa"):
            self.parar_envios(ses, tid)
            y["estado"] = "cancelada"
            self.salida.guardar()

    def limpiar_entrada(self, tid: str, x: dict) -> None:
        for f in x["files"]:
            self.cerrar_fd(tid, f["fid"])
        for s in self.sesiones.get(x["peer"], {}).values():
            s.activos = {c for c in s.activos if c[0] != tid}
        shutil.rmtree(self.recibidos / ".nexo-parcial" / tid, ignore_errors=True)

    @staticmethod
    def parar_envios(ses: Sesion, tid: str) -> None:
        for c in [c for c in ses.envios if c[0] == tid]:
            ses.envios.pop(c).cancel()

    async def al_conectar(self, ses: Sesion) -> None:
        for tid, x in list(self.salida.d.items()):
            if x["peer"] != ses.peer:
                continue
            if x["estado"] == "ofrecida":
                await self.oferta(ses, tid, x)
            elif x["estado"] == "cancelada" and x.get("avisar"):
                if await self.enviar(ses, {"t": "cancel", "tid": tid}):
                    x["avisar"] = False
        for tid, x in list(self.entrada.d.items()):
            if x["peer"] != ses.peer:
                continue
            if x["estado"] == "activa":
                await self.enviar(ses, {"t": "answer", "tid": tid, "ok": True})
            elif x["estado"] in ("rechazada", "cancelada") and x.get("avisar"):
                if await self.enviar(ses, {"t": "cancel", "tid": tid}):
                    x["avisar"] = False
            elif x["estado"] == "hecha":
                await self.enviar(ses, {"t": "done", "tid": tid})
        self.bombear(ses.peer)

    async def oferta(self, ses: Sesion, tid: str, x: dict) -> None:
        await self.enviar(ses, {"t": "offer", "tid": tid, "files": [
            {"fid": i, "name": f["name"], "size": f["size"]} for i, f in enumerate(x["files"])]})

    def podar(self) -> None:
        for alm in (self.entrada, self.salida):
            viejos = sorted((k for k, v in alm.d.items() if v["estado"] not in ("pendiente", "activa", "ofrecida")),
                            key=lambda k: alm.d[k].get("ts", 0))
            for k in viejos[:-MAX_HISTORIAL]:
                del alm.d[k]

    # ---- órdenes de la ventana ----
    @staticmethod
    def expandir(rutas: list[str]) -> list[dict]:
        res = []

        def uno(p: Path, rel: str):
            st = p.lstat()
            res.append({"path": str(p), "name": rel, "size": st.st_size, "mtime": st.st_mtime_ns})

        for r in rutas:
            p = Path(r)
            if p.is_symlink():
                continue
            if p.is_file():
                uno(p, p.name)
            elif p.is_dir():
                for raiz, dirs, archivos in os.walk(p):
                    dirs[:] = [d for d in dirs if not Path(raiz, d).is_symlink()]
                    for a in archivos:
                        q = Path(raiz, a)
                        if q.is_file() and not q.is_symlink():
                            uno(q, f"{p.name}/{q.relative_to(p)}")
        return res

    async def orden(self, c: dict) -> None:
        k = c.get("c")
        if k == "enviar":
            pid = c["id"]
            if pid not in self.pares.d:
                return
            archivos = await self.loop.run_in_executor(None, self.expandir, c["rutas"])
            if not archivos:
                self.avisar("No hay archivos que enviar")
                return
            tid = uuid.uuid4().hex
            titulo = c["rutas"][0].rstrip("/").rsplit("/", 1)[-1] + (f" +{len(c['rutas']) - 1}" if len(c["rutas"]) > 1 else "")
            x = {"peer": pid, "files": archivos, "estado": "ofrecida", "total": sum(a["size"] for a in archivos),
                 "titulo": titulo, "ts": time.time()}
            self.salida.d[tid] = x
            self.podar()
            self.salida.guardar()
            ses = self.mejor(pid)
            if ses:
                await self.oferta(ses, tid, x)
        elif k == "aceptar":
            await self.aceptar(c["tid"])
        elif k in ("rechazar", "cancelar"):
            await self.cancelar(c["tid"], "rechazada" if k == "rechazar" else "cancelada")
        elif k == "emparejar":
            await self.emparejar(c["id"])
        elif k == "par_si" and self.par:
            self.par["yo_ok"] = True
            await self.enviar(self.par["ses"], {"t": "pair_ok"})
            await self.fin_par_si_toca()
        elif k == "par_no" and self.par:
            await self.enviar(self.par["ses"], {"t": "pair_no"})
            self.par = None
        elif k == "terminal" and c.get("id") in self.pares.d:
            self.pares.d[c["id"]]["shell"] = bool(c.get("v"))
            self.pares.guardar()
            registrar(BASE, self.nombre_de(c["id"]), "permiso de terminal " + ("ACTIVADO" if c.get("v") else "desactivado"))
            if not c.get("v"):
                self.term_cerrar_de(c["id"])
        elif k == "term_cortar":
            x = self.terms.get(c.get("sid") or "")
            if x:
                registrar(BASE, self.nombre_de(x.peer), "cortada desde la laptop")
                x.cerrar()
        elif k == "olvidar":
            self.term_cerrar_de(c["id"])
            self.pares.d.pop(c["id"], None)
            self.pares.guardar()
            for s in list(self.sesiones.pop(c["id"], {}).values()):
                self.soltar(s)
                s.canal.cerrar()
        elif k == "auto" and c.get("id") in self.pares.d:
            self.pares.d[c["id"]]["auto"] = bool(c.get("v"))
            self.pares.guardar()
        elif k == "nombre" and str(c.get("v", "")).strip():
            self.cfg.d["name"] = str(c["v"]).strip()[:64]
            self.cfg.guardar()
        elif k == "ip":
            texto = str(c.get("v", "")).strip()
            ip, _, port = texto.partition(":")
            socket.inet_aton(ip)
            self.manuales.add((ip, int(port or PUERTO)))
        elif k == "limpiar":
            for alm in (self.entrada, self.salida):
                for t in [t for t, v in alm.d.items() if v["estado"] not in ("pendiente", "activa", "ofrecida")]:
                    del alm.d[t]
                alm.guardar()

    async def emparejar(self, pid: str) -> None:
        ses = self.provisionales.get(pid) or self.mejor(pid)
        if ses:
            self.abrir_par(ses, yo_ok=False)
            await self.enviar(ses, {"t": "pair_req"})
            return
        cand = self.candidatos(pid)
        if not cand:
            self.avisar("No se encuentra ese dispositivo en la red")
            return
        self.quiero_par.add(pid)
        for ip, port in cand[:4]:
            self.lanzar(self.marcar(pid, ip, port))

    async def cancelar(self, tid: str, estado: str) -> None:
        x = self.entrada.d.get(tid)
        y = self.salida.d.get(tid)
        if x and x["estado"] in ("pendiente", "activa"):
            self.limpiar_entrada(tid, x)
            x["estado"] = estado
            ses = self.mejor(x["peer"])
            if estado == "rechazada" and ses:
                await self.enviar(ses, {"t": "answer", "tid": tid, "ok": False})
            elif not (ses and await self.enviar(ses, {"t": "cancel", "tid": tid})):
                x["avisar"] = True
            self.entrada.guardar()
        elif y and y["estado"] in ("ofrecida", "activa"):
            y["estado"] = estado
            ses = self.mejor(y["peer"])
            if ses:
                self.parar_envios(ses, tid)
            if not (ses and await self.enviar(ses, {"t": "cancel", "tid": tid})):
                y["avisar"] = True
            self.salida.guardar()

    # ---- estado para la ventana ----
    def medir(self) -> None:
        ahora = time.monotonic()
        for v in self.vel.values():
            dt = ahora - v["t"]
            if dt >= 0.4:
                v["bps"] = 0.5 * v["bps"] + 0.5 * v["b"] / dt
                v["b"], v["t"] = 0, ahora

    def estado(self) -> dict:
        ahora = time.monotonic()
        disp = []
        ids = list(self.pares.d) + [v for v, d in self.vecinos.items()
                                    if v not in self.pares.d and (ahora - d.get("ts", 0) < 12 or v in self.provisionales)]
        for pid in ids:
            p, v = self.pares.d.get(pid, {}), self.vecinos.get(pid, {})
            s = self.sesiones.get(pid, {})
            via = "lan" if "lan" in s else "bt" if "bt" in s else None
            disp.append({"id": pid, "name": self.nombre_de(pid), "kind": p.get("kind") or v.get("kind", "pc"),
                         "par": pid in self.pares.d, "auto": p.get("auto", False), "shell": p.get("shell", False), "via": via,
                         "ip": s["lan"].ip if via == "lan" else "", "visto": ahora - v.get("ts", 0) < 12 if v else False})
        trans = []
        for dir_, alm in (("in", self.entrada), ("out", self.salida)):
            for tid, x in alm.d.items():
                if dir_ == "in":
                    hecho = sum(f["size"] if f["estado"] in ("ok", "hash", "verif", "error") else f.get("recv", 0)
                                for f in x["files"])
                else:
                    hecho = sum(f.get("env", 0) for f in x["files"])
                malos = [f.get("error", "falló la verificación") for f in x["files"] if f["estado"] == "error"] \
                    if dir_ == "in" else []
                trans.append({"tid": tid, "dir": dir_, "peer": self.nombre_de(x["peer"]), "estado": x["estado"],
                              "titulo": x["titulo"], "total": x["total"], "hecho": min(hecho, x["total"]),
                              "n": len(x["files"]), "ts": x.get("ts", 0), "bps": self.vel.get(tid, {}).get("bps", 0),
                              "enlace": (self.mejor(x["peer"]).via if self.mejor(x["peer"]) else None),
                              "dest": str(self.recibidos), "error": malos[0] if malos else ""})
        trans.sort(key=lambda t: -t["ts"])
        par = None
        if self.par:
            par = {"id": self.par["id"], "name": self.nombre_de(self.par["id"]), "sas": self.par["sas"],
                   "yo_ok": self.par["yo_ok"], "el_ok": self.par["el_ok"]}
        return {"yo": {"id": self.ident.id, "name": self.cfg.d["name"], "ips": [i[1] for i in self.ifaces],
                       "bt": bool(self.bt and self.bt.activo), "recibidos": str(self.recibidos)},
                "disp": disp, "trans": trans, "par": par,
                "term": [{"sid": t.sid, "peer": self.nombre_de(t.peer), "activa": bool(t.ses), "desde": t.desde}
                         for t in self.terms.values()]}

    async def cliente(self, r, w) -> None:
        self.clientes.append(w)
        await self.mandar(w)
        try:
            while line := await r.readline():
                try:
                    await self.orden(json.loads(line))
                except (ValueError, KeyError, TypeError, OSError) as e:
                    self.avisar(f"Orden no válida: {e}")
                await self.mandar(w)
        except (ConnectionError, OSError):
            pass
        finally:
            if w in self.clientes:
                self.clientes.remove(w)
            w.close()

    async def mandar(self, w) -> None:
        try:
            w.write(json.dumps({**self.estado(), "avisos": self.avisos}).encode() + b"\n")
            await w.drain()
        except (ConnectionError, OSError, RuntimeError):
            pass

    async def difundir(self) -> None:
        while True:
            await asyncio.sleep(0.5)
            self.medir()
            if self.par and time.monotonic() - self.par["desde"] > 120:
                self.par = None
            for x in list(self.terms.values()):
                if x.solta and time.monotonic() - x.solta > ESPERA_DESCONECTADA:
                    x.solta = 0.0
                    x.cerrar()
            for w in list(self.clientes):
                await self.mandar(w)
            if self.clientes:
                self.avisos.clear()

    async def correr(self) -> None:
        self.loop = asyncio.get_running_loop()
        for x in self.entrada.d.values():  # lo que quedó a medias vuelve a la cola
            for f in x["files"]:
                if f["estado"] == "hash":
                    f["estado"] = "pendiente"
        servidor = await asyncio.start_server(self._entrante, "0.0.0.0", PUERTO, backlog=32)
        SOCK.unlink(missing_ok=True)
        unix = await asyncio.start_unix_server(self.cliente, path=str(SOCK))
        os.chmod(SOCK, 0o600)
        if not os.environ.get("NEXO_SIN_BT"):
            self.bt = BT(self.bt_entrante, lambda t: self.loop.call_soon_threadsafe(self.avisar, t))
            self.bt.iniciar()
        for c in (self.faro(), self.vigilar(), self.difundir()):
            self.lanzar(c)
        fin = self.loop.create_future()
        for s in (signal.SIGTERM, signal.SIGINT):
            self.loop.add_signal_handler(s, lambda: fin.done() or fin.set_result(None))
        print(f"NEXO {self.ident.id[:8]} escuchando en :{PUERTO}", flush=True)
        await fin
        for x in self.terms.values():
            x.cerrar()
        servidor.close()
        unix.close()
        self.entrada.guardar()
        self.salida.guardar()
        SOCK.unlink(missing_ok=True)

    async def _entrante(self, r, w) -> None:
        pn = w.get_extra_info("peername")
        await self.atender(r, w, "lan", False, pn[0] if pn else "")


if __name__ == "__main__":
    asyncio.run(Motor().correr())
