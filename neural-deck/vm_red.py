"""Lo que NEURAL//VM necesita fuera de QEMU: redes de NetworkManager, USB del
equipo y hugepages. Sin GTK (se puede probar desde la terminal).

Redes (las crea NetworkManager, sin sudo; la sesión activa puede):
  interna  puente «neuralvm0» con ipv4.method shared: NetworkManager le da
           DHCP (10.77.0.x) y NAT hacia la conexión que haya (WiFi o cable).
           Las VMs se ven entre ellas y con este equipo; los demás equipos de
           la casa no las ven.
  puente   puente «neuralbr0» con una tarjeta de cable dentro: este equipo y
           las VMs salen a tu router como equipos distintos, cada uno con su IP.
           Con WiFi no se puede (la tarjeta WiFi no deja hacer puente).
QEMU entra en esos puentes con qemu-bridge-helper (setuid), que solo deja los
nombrados en /etc/qemu/bridge.conf (lo pone `sudo ~/neural-deck/root/instalar.sh`).

USB: pasar un dispositivo a la VM necesita leer/escribir /dev/bus/usb/…; la
regla de udev de instalar.sh se lo da a la sesión activa (uaccess). Lo mismo
para los discos conectados por USB (/dev/sdX), que una VM puede usar enteros.

Hugepages: memoria de la VM en páginas de 2 MB reservadas (algo más rápida).
Las reserva el ayudante /usr/libexec/neural-vm-ayudante (polkit, sin
contraseña en la sesión activa)."""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

INTERNA_BR = "neuralvm0"
INTERNA_CON = "NEURAL-VM interna"
INTERNA_IP = "10.77.0.1/24"
PUENTE_BR = "neuralbr0"
PUENTE_CON = "NEURAL-VM puente"
BRIDGE_CONF = Path("/etc/qemu/bridge.conf")
AYUDANTE = Path("/usr/libexec/neural-vm-ayudante")


# ------------------------------------------------------------------ redes
def _nmcli(*args: str, timeout: float = 40) -> str:
    try:
        r = subprocess.run(["nmcli", *args], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as e:
        raise RuntimeError(f"nmcli: {e}") from e
    if r.returncode:
        raise RuntimeError((r.stderr or r.stdout).strip() or f"nmcli salió con {r.returncode}")
    return r.stdout


def _partir(linea: str) -> list[str]:
    """Campos de `nmcli -t` (separados por «:», con «\\:» escapado)."""
    return [c.replace("\\:", ":") for c in re.split(r"(?<!\\):", linea)]


def _conexiones() -> list[dict]:
    try:
        salida = _nmcli("-t", "-f", "NAME,TYPE,DEVICE,ACTIVE", "connection", "show", timeout=10)
    except RuntimeError:
        return []
    res = []
    for linea in salida.splitlines():
        c = _partir(linea)
        if len(c) >= 4:
            res.append({"nombre": c[0], "tipo": c[1], "disp": c[2], "activa": c[3] in ("yes", "sí", "si")})
    return res


def _ip(dev: str) -> str:
    try:
        r = subprocess.run(["ip", "-4", "-br", "addr", "show", "dev", dev], capture_output=True, text=True, timeout=5)
        partes = r.stdout.split()
        return partes[2] if len(partes) > 2 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def estado_red(cual: str) -> dict:
    """cual: "interna" | "puente" → {existe, activa, ip, puertos (tarjetas dentro), permitido}."""
    br, con = (INTERNA_BR, INTERNA_CON) if cual == "interna" else (PUENTE_BR, PUENTE_CON)
    cons = _conexiones()
    propia = next((c for c in cons if c["nombre"] == con), None)
    puertos = [c["nombre"][len(PUENTE_CON) + 2:-1] for c in cons
               if cual == "puente" and c["nombre"].startswith(PUENTE_CON + " (")]
    return {"existe": propia is not None, "activa": bool(propia and propia["activa"]),
            "ip": _ip(br) if propia else "", "puertos": puertos, "permitido": permitido(br)}


def permitido(br: str) -> bool:
    """¿Deja /etc/qemu/bridge.conf (y sus include) que QEMU use este puente?"""
    def leer(p: Path, prof=0) -> bool:
        try:
            lineas = p.read_text().splitlines()
        except OSError:
            return False
        for l in lineas:
            l = l.split("#", 1)[0].split()
            if len(l) == 2 and l[0] == "allow" and l[1] in (br, "all"):
                return True
            if len(l) == 2 and l[0] == "include" and prof < 3:
                if any(leer(Path(x), prof + 1) for x in sorted(Path("/").glob(l[1].lstrip("/")))):
                    return True
        return False
    return leer(BRIDGE_CONF)


def asegurar_interna() -> None:
    """Crea (la primera vez) y levanta la red interna."""
    nombres = {c["nombre"]: c for c in _conexiones()}
    if INTERNA_CON not in nombres:
        _nmcli("connection", "add", "type", "bridge", "ifname", INTERNA_BR, "con-name", INTERNA_CON,
               "bridge.stp", "no", "ipv4.method", "shared", "ipv4.addresses", INTERNA_IP,
               "ipv6.method", "disabled", "connection.autoconnect", "yes")
    elif nombres[INTERNA_CON]["activa"]:
        return
    _nmcli("connection", "up", INTERNA_CON)


def quitar_interna() -> None:
    if any(c["nombre"] == INTERNA_CON for c in _conexiones()):
        _nmcli("connection", "delete", INTERNA_CON)


def tarjetas_cable() -> list[str]:
    try:
        salida = _nmcli("-t", "-f", "DEVICE,TYPE", "device", timeout=10)
    except RuntimeError:
        return []
    return [c[0] for c in map(_partir, salida.splitlines()) if len(c) >= 2 and c[1] == "ethernet"]


def crear_puente(dev: str) -> str:
    """Puente por cable con `dev` dentro. Este equipo pasa a tomar su IP por el
    puente (la conexión de cable de siempre queda de reserva). Devuelve un aviso."""
    if dev not in tarjetas_cable():
        raise RuntimeError(f"{dev} no es una tarjeta de cable")
    quitar_puente()
    _nmcli("connection", "add", "type", "bridge", "ifname", PUENTE_BR, "con-name", PUENTE_CON,
           "bridge.stp", "no", "ipv4.method", "auto", "ipv6.method", "auto",
           "connection.autoconnect", "yes", "connection.autoconnect-priority", "10")
    _nmcli("connection", "add", "type", "ethernet", "ifname", dev, "con-name", f"{PUENTE_CON} ({dev})",
           "master", PUENTE_BR, "slave-type", "bridge",
           "connection.autoconnect", "yes", "connection.autoconnect-priority", "10")
    try:
        _nmcli("connection", "up", f"{PUENTE_CON} ({dev})", timeout=60)
        _nmcli("connection", "up", PUENTE_CON, timeout=60)
        return f"Puente creado con {dev}"
    except RuntimeError:
        return f"Puente creado con {dev}: se activará al conectar el cable"


def quitar_puente() -> None:
    for c in _conexiones():
        if c["nombre"] == PUENTE_CON or c["nombre"].startswith(PUENTE_CON + " ("):
            _nmcli("connection", "delete", c["nombre"])


# ------------------------------------------------------------------ USB
def _leer(p: Path) -> str:
    try:
        return p.read_text().strip()
    except OSError:
        return ""


def usb_dispositivos() -> list[dict]:
    """Dispositivos USB del equipo (sin hubs): {vid, pid, nombre, acceso}."""
    res, vistos = [], set()
    base = Path("/sys/bus/usb/devices")
    for d in sorted(base.glob("*")) if base.is_dir() else []:
        vid, pid = _leer(d / "idVendor"), _leer(d / "idProduct")
        if not vid or not pid or _leer(d / "bDeviceClass") == "09":
            continue
        try:
            bus, num = int(_leer(d / "busnum")), int(_leer(d / "devnum"))
        except ValueError:
            continue
        nombre = " ".join(x for x in (_leer(d / "manufacturer"), _leer(d / "product")) if x) or f"{vid}:{pid}"
        clave = (vid, pid, bus, num)
        if clave in vistos:
            continue
        vistos.add(clave)
        nodo = f"/dev/bus/usb/{bus:03d}/{num:03d}"
        res.append({"vid": vid, "pid": pid, "nombre": nombre, "nodo": nodo,
                    "acceso": os.access(nodo, os.R_OK | os.W_OK)})
    return res


# ------------------------------------------------------------------ discos USB
def _lsblk() -> list[dict]:
    try:
        r = subprocess.run(["lsblk", "-J", "-b", "-o", "NAME,PATH,TYPE,SIZE,TRAN,MODEL,SERIAL,MOUNTPOINTS,FSTYPE,LABEL"],
                           capture_output=True, text=True, timeout=10)
        import json
        return json.loads(r.stdout).get("blockdevices", [])
    except (OSError, ValueError, subprocess.SubprocessError):
        return []


def id_estable(dev: str) -> str:
    """Enlace de /dev/disk/by-id del disco (sigue valiendo aunque cambie /dev/sdX).
    Mejor el del disco real (ata-/nvme-, con su número de serie) que el del adaptador USB."""
    real = os.path.realpath(dev)
    base = Path("/dev/disk/by-id")
    enlaces = sorted(e for e in (base.iterdir() if base.is_dir() else [])
                     if not re.search(r"-part\d+$", e.name) and os.path.realpath(e) == real)
    for pref in ("ata-", "nvme-", "scsi-", "usb-"):
        for e in enlaces:
            if e.name.startswith(pref) and not e.name.startswith("nvme-eui"):
                return str(e)
    return str(enlaces[0]) if enlaces else dev


SISTEMA = ("/", "/home", "/boot", "/usr", "/var", "/efi", "[SWAP]")


def montajes(dev: str) -> list[dict]:
    """Particiones montadas en este equipo: [{part, punto}]."""
    real = os.path.realpath(dev)
    res = []
    for d in _lsblk():
        if d.get("path") != real:
            continue
        for x in [d] + d.get("children", []):
            for m in x.get("mountpoints") or []:
                if m:
                    res.append({"part": x["path"], "punto": m})
    return res


def desmontar(dev: str) -> None:
    """Desmonta lo que el escritorio montó (udisks, en /run/media); lo demás se niega."""
    for m in montajes(dev):
        if not m["punto"].startswith("/run/media/"):
            raise RuntimeError(f"{m['part']} está montada en {m['punto']}: desmóntala a mano")
        r = subprocess.run(["udisksctl", "unmount", "-b", m["part"], "--no-user-interaction"],
                           capture_output=True, text=True, timeout=60)
        if r.returncode:
            raise RuntimeError(f"no se pudo desmontar {m['part']}: " + (r.stderr.strip() or "ocupada"))


def discos_usb() -> list[dict]:
    """Discos conectados por USB: {dev, id, nombre, tam, particiones, montadas, acceso, sistema}."""
    res = []
    for d in _lsblk():
        if d.get("type") != "disk" or d.get("tran") != "usb":
            continue
        hijos = d.get("children", [])
        puntos = [m for x in [d] + hijos for m in (x.get("mountpoints") or []) if m]
        partes = [" ".join(v for v in (x.get("fstype"), x.get("label")) if v) for x in hijos]
        res.append({"dev": d["path"], "id": id_estable(d["path"]),
                    "nombre": (d.get("model") or "").strip() or d["name"], "tam": int(d.get("size") or 0),
                    "particiones": [p for p in partes if p], "montadas": puntos,
                    "acceso": os.access(d["path"], os.R_OK | os.W_OK),
                    "sistema": any(p in SISTEMA for p in puntos)})
    return res


# ------------------------------------------------------------------ hugepages
def hugepages() -> dict:
    """{total, libres, kb} (páginas reservadas, libres y tamaño de página)."""
    d = {"total": 0, "libres": 0, "kb": 2048}
    try:
        for l in open("/proc/meminfo"):
            k, v = l.split(":", 1)
            n = int(v.split()[0])
            if k == "HugePages_Total":
                d["total"] = n
            elif k == "HugePages_Free":
                d["libres"] = n
            elif k == "Hugepagesize":
                d["kb"] = n
    except (OSError, ValueError):
        pass
    return d


def paginas(ram_gb: int) -> int:
    return ram_gb * 1024 * 1024 // hugepages()["kb"]


def reservar_hugepages(n: int) -> None:
    """Deja reservadas exactamente n páginas (0 = liberar todas)."""
    if not AYUDANTE.exists():
        raise RuntimeError("falta el ayudante de hugepages: sudo ~/neural-deck/root/instalar.sh")
    r = subprocess.run(["pkexec", str(AYUDANTE), "hugepages", str(max(0, int(n)))],
                       capture_output=True, text=True, timeout=60)
    if r.returncode:
        raise RuntimeError(r.stderr.strip() or "no se pudieron reservar las hugepages")


if __name__ == "__main__":
    print("interna:", estado_red("interna"))
    print("puente: ", estado_red("puente"))
    print("cable:  ", tarjetas_cable())
    print("hugepages:", hugepages())
    for u in usb_dispositivos():
        print(f"  {u['vid']}:{u['pid']} {'ok ' if u['acceso'] else 'sin'} {u['nombre']}")
    for d in discos_usb():
        print("disco:", d)
