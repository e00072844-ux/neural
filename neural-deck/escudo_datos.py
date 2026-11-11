"""NEURAL//ESCUDO: lee el cortafuegos, los puertos abiertos, las conexiones y los bloqueos. Sin root ni GTK."""
from __future__ import annotations

import binascii
import ipaddress
import re
import subprocess
from pathlib import Path

ETC = Path("/etc/ufw")
AYUDANTE = "/usr/libexec/neural-escudo-ayudante"
RE_TUPLA = re.compile(r"^### tuple ### (\S+) (\S+) (\S+) (\S+) (\S+) (\S+) (\S+)(?: comment=(\S+))?")
RE_PROC = re.compile(r'users:\(\("([^"]+)"')
ANY = {"0.0.0.0/0", "::/0", "any"}


def activo(etc: Path = ETC) -> bool:
    try:
        return re.search(r"^ENABLED=yes", (etc / "ufw.conf").read_text(), re.M) is not None
    except OSError:
        return False


def ayudante_instalado() -> bool:
    return Path(AYUDANTE).exists()


def _comentario(h: str | None) -> str:
    try:
        return binascii.unhexlify(h).decode() if h else ""
    except (ValueError, binascii.Error):
        return ""


def parsear(texto: str) -> list[dict]:
    r = []
    for l in texto.splitlines():
        m = RE_TUPLA.match(l)
        if m:
            acc, proto, dport, dst, sport, src, dir_, com = m.groups()
            origen = "any" if src in ANY else src
            r.append({"accion": acc, "proto": proto, "puerto": dport, "origen": origen, "dir": dir_, "comentario": _comentario(com),
                      "editable": dir_ == "in" and dst in ANY and sport == "any" and dport != "any"
                      and acc in ("allow", "deny", "reject", "limit")})
    return r


def reglas(etc: Path = ETC) -> list[dict]:
    """ufw duplica en IPv6 las reglas "desde cualquiera": aquí salen una sola vez."""
    vistas, r = set(), []
    for n in ("user.rules", "user6.rules"):
        try:
            texto = (etc / n).read_text()
        except OSError:
            continue
        for x in parsear(texto):
            k = (x["accion"], x["proto"], x["puerto"], x["origen"], x["dir"])
            if k not in vistas:
                vistas.add(k)
                r.append(x)
    return r


def _cubre(regla: dict, puerto: int, proto: str) -> bool:
    if regla["dir"] != "in" or regla["proto"] not in (proto, "any"):
        return False
    if regla["puerto"] == "any":
        return True
    for parte in regla["puerto"].split(","):
        a, _, b = parte.partition(":")
        if a.isdigit() and int(a) <= puerto <= int(b or a):
            return True
    return False


def _separar(dir_: str) -> tuple[str, str]:
    h, _, p = dir_.rpartition(":")
    return h.strip("[]").split("%")[0], p


def exposicion(salida: str, rs: list[dict], fw_activo: bool) -> list[dict]:
    """Quién escucha y si desde fuera se puede llegar: LOCAL, CERRADO, SOLO_RED o ABIERTO."""
    r, vistos = [], set()
    for l in salida.splitlines():
        c = l.split()
        if len(c) < 6:
            continue
        host, port = _separar(c[4])
        if not port.isdigit():
            continue
        proto = c[0]
        proc = (m.group(1) if (m := RE_PROC.search(l)) else "(sistema)")
        clave = (proto, port, host in ("0.0.0.0", "::", "*"), host.startswith(("127.", "::1", "::ffff:127.")))
        if clave in vistos:
            continue
        vistos.add(clave)
        if host.startswith(("127.", "::1", "::ffff:127.")):
            est = "LOCAL"
        elif not fw_activo:
            est = "ABIERTO"
        else:
            perm = next((x for x in rs if x["accion"] in ("allow", "limit") and _cubre(x, int(port), proto)), None)
            if perm is None:
                est = "CERRADO"
            elif perm["origen"] == "any":
                est = "ABIERTO"
            else:
                est = "SOLO_RED"
        r.append({"proto": proto, "puerto": int(port), "ip": host, "proc": proc, "estado": est})
    orden = {"ABIERTO": 0, "SOLO_RED": 1, "CERRADO": 2, "LOCAL": 3}
    return sorted(r, key=lambda x: (orden[x["estado"]], x["puerto"]))


def puertos() -> list[dict]:
    try:
        s = subprocess.run(["ss", "-tulnpH"], capture_output=True, text=True, timeout=4).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return exposicion(s, reglas(), activo())


def tipo_ip(ip: str) -> str:
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return "?"
    if a.is_loopback:
        return "ESTE EQUIPO"
    return "RED LOCAL" if a.is_private or a.is_link_local else "INTERNET"


def parsear_conexiones(salida: str) -> list[dict]:
    r = []
    for l in salida.splitlines():
        c = l.split()
        if len(c) < 3:
            continue
        ip, port = _separar(c[2])
        t = tipo_ip(ip.removeprefix("::ffff:"))
        if t == "ESTE EQUIPO":
            continue
        m = RE_PROC.search(l)
        r.append({"proto": c[0], "app": m.group(1) if m else "(otro usuario)", "ip": ip.removeprefix("::ffff:"), "puerto": port, "tipo": t})
    return sorted(r, key=lambda x: (x["app"], x["ip"]))


def conexiones() -> list[dict]:
    try:
        s = subprocess.run(["ss", "-tunpH", "state", "established"], capture_output=True, text=True, timeout=4).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    # con "state" ss omite esa columna: netid recv send local peer
    return parsear_conexiones("\n".join(_sin_colas(l) for l in s.splitlines()))


def _sin_colas(l: str) -> str:
    c = l.split(None, 3)
    return f"{c[0]} {c[3]}" if len(c) == 4 else ""


RE_CAMPO = re.compile(r"\b(IN|SRC|DST|PROTO|DPT)=(\S*)")


def parsear_bloqueos(salida: str) -> list[dict]:
    """Agrupados: quién intentó entrar y a qué. El ruido de multidifusión se ignora."""
    g: dict[tuple, dict] = {}
    for l in salida.splitlines():
        if "UFW BLOCK" not in l:
            continue
        d = dict(RE_CAMPO.findall(l))
        dst = d.get("DST", "")
        if not d.get("SRC") or not dst or dst.startswith(("224.", "239.", "ff0")) or dst.endswith(".255"):
            continue
        k = (d["SRC"], d.get("PROTO", "?"), d.get("DPT", ""))
        x = g.setdefault(k, {"src": d["SRC"], "proto": d.get("PROTO", "?"), "puerto": d.get("DPT", ""), "iface": d.get("IN", ""),
                             "veces": 0, "ult": l.split()[0], "tipo": tipo_ip(d["SRC"])})
        x["veces"] += 1
        x["ult"] = l.split()[0]
    return sorted(g.values(), key=lambda x: x["ult"], reverse=True)


def bloqueos() -> list[dict]:
    try:
        s = subprocess.run(["journalctl", "-k", "--since", "-7d", "-g", "UFW BLOCK", "-n", "2000", "--no-pager", "-o", "short-iso"],
                           capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return parsear_bloqueos(s)


def red_local() -> str:
    """La subred de la ruta por defecto, para proponerla como origen."""
    try:
        s = subprocess.run(["ip", "-4", "route"], capture_output=True, text=True, timeout=3).stdout
        dev = re.search(r"^default .*?dev (\S+)", s, re.M).group(1)
        return re.search(rf"^(\d+\.\d+\.\d+\.\d+/\d+) dev {re.escape(dev)} ", s, re.M).group(1)
    except (OSError, subprocess.SubprocessError, AttributeError):
        return "any"


def ayudante(*args: str) -> str | None:
    """None si funcionó; si no, el mensaje."""
    if not ayudante_instalado():
        return "Falta instalar el ayudante (sudo ~/neural-deck/root/instalar.sh)"
    try:
        p = subprocess.run(["pkexec", AYUDANTE, *args], capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as e:
        return str(e)
    if p.returncode == 126:
        return "Autorización cancelada"
    return None if p.returncode == 0 else (p.stderr or p.stdout or "falló").strip().splitlines()[-1][:200]
