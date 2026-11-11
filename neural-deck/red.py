"""Red de NEURAL//DECK sobre NetworkManager (nmcli). Sin GTK: lo usan
NEURAL//AJUSTES (página RED) y, más adelante, el panel rápido.

- Dispositivos (WiFi / Ethernet) y su detalle: IP, puerta de enlace, DNS, MAC, velocidad.
- WiFi: buscar, conectar (las redes nuevas solo se guardan si conectan), desconectar.
- Conexiones guardadas: activar, olvidar y opciones avanzadas (IPv4 manual, DNS,
  IPv6, MAC aleatoria, conexión medida, autoconectar).
- Modo avión: bloquea todas las radios por /dev/rfkill (sin root: ACL de logind).

Las contraseñas nunca van en la línea de órdenes (se verían en la lista de
procesos): se pasan a nmcli por su entrada estándar con `passwd-file`.
Todas las funciones bloquean: llamarlas desde un hilo, no desde GTK."""
from __future__ import annotations

import re
import struct
import subprocess
from pathlib import Path

NET = Path("/sys/class/net")
TIPOS = {"wifi": "WIFI", "ethernet": "ETHERNET"}
TIPO_CON = {"802-11-wireless": "wifi", "802-3-ethernet": "ethernet"}
ESTADOS = {"connected": "CONECTADO", "disconnected": "DESCONECTADO", "unavailable": "NO DISPONIBLE",
           "connecting": "CONECTANDO", "deactivating": "DESCONECTANDO", "unmanaged": "SIN GESTIONAR",
           "connected (externally)": "CONECTADO (EXTERNO)"}
DNS_CONOCIDOS = {  # nombre: (IPv4, IPv6)
    "cloudflare": ("1.1.1.1 1.0.0.1", "2606:4700:4700::1111 2606:4700:4700::1001"),
    "quad9": ("9.9.9.9 149.112.112.112", "2620:fe::fe 2620:fe::9"),
    "google": ("8.8.8.8 8.8.4.4", "2001:4860:4860::8888 2001:4860:4860::8844"),
}
CAMPOS_OPC = ("connection.id", "connection.type", "connection.autoconnect", "connection.metered",
              "ipv4.method", "ipv4.addresses", "ipv4.gateway", "ipv4.dns", "ipv4.ignore-auto-dns",
              "ipv6.method", "ipv6.dns", "ipv6.ignore-auto-dns",
              "802-11-wireless.ssid", "802-11-wireless.cloned-mac-address", "802-3-ethernet.cloned-mac-address")


class RedError(Exception):
    pass


class NecesitaClave(RedError):
    """La red es nueva y tiene seguridad: hay que pedir la contraseña."""


def _nm(*args: str, entrada: str | None = None, timeout: float = 15) -> str:
    """Ejecuta nmcli; devuelve stdout o lanza RedError con el mensaje de nmcli."""
    try:
        r = subprocess.run(["nmcli", *args], input=entrada, capture_output=True, text=True, timeout=timeout,
                           env={"LC_ALL": "C", "PATH": "/usr/bin:/bin:/usr/sbin"})
    except FileNotFoundError as e:
        raise RedError("falta nmcli (NetworkManager)") from e
    except subprocess.TimeoutExpired as e:
        raise RedError("nmcli no respondió a tiempo") from e
    if r.returncode != 0:
        msg = (r.stderr or r.stdout).strip().splitlines()
        raise RedError(re.sub(r"^Error:\s*", "", msg[-1] if msg else f"nmcli falló ({r.returncode})"))
    return r.stdout


def _campos(linea: str) -> list[str]:
    """Separa una línea de `nmcli -t` por los «:» que no están escapados."""
    partes = re.split(r"(?<!\\):", linea)
    return [p.replace("\\:", ":").replace("\\\\", "\\") for p in partes]


def _clave_valor(salida: str) -> dict[str, str]:
    """`nmcli -t device/connection show` = líneas «CLAVE:valor» (las listas como CLAVE[1], CLAVE[2]…)."""
    listas: dict[str, list[str]] = {}
    for linea in salida.splitlines():
        if ":" not in linea:
            continue
        k, v = linea.split(":", 1)
        v = v.replace("\\:", ":").strip()
        if v in ("", "--"):
            v = ""
        listas.setdefault(re.sub(r"\[\d+\]$", "", k), []).append(v)
    return {k: ", ".join(x for x in vs if x) for k, vs in listas.items()}


def _leer(p: Path) -> str:
    try:
        return p.read_text().strip()
    except OSError:
        return ""


# ---------- dispositivos ----------
def dispositivos() -> list[dict]:
    """[{dev, tipo ('wifi'|'ethernet'), estado, con}] solo de WiFi y Ethernet."""
    out = []
    for linea in _nm("-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device").splitlines():
        c = _campos(linea)
        if len(c) >= 4 and c[1] in TIPOS:
            out.append({"dev": c[0], "tipo": c[1], "estado": c[2], "con": c[3]})
    return out


def detalle(dev: str) -> dict:
    """Estado legible de un dispositivo: ip, puerta, dns, mac, velocidad, ipv6."""
    d = _clave_valor(_nm("-t", "-f", "GENERAL,IP4,IP6", "device", "show", dev))
    estado = re.sub(r"^\d+\s*\((.*)\)$", r"\1", d.get("GENERAL.STATE", ""))
    vel = _leer(NET / dev / "speed")
    velocidad = f"{vel} Mb/s" if vel.isdigit() and int(vel) > 0 else ""
    ip6 = [a for a in d.get("IP6.ADDRESS", "").split(", ") if a and not a.startswith("fe80")]
    return {
        "dev": dev, "tipo": d.get("GENERAL.TYPE", ""), "estado": ESTADOS.get(estado, estado.upper()),
        "con": d.get("GENERAL.CONNECTION", ""), "mac": d.get("GENERAL.HWADDR", ""),
        "ip": d.get("IP4.ADDRESS", ""), "puerta": d.get("IP4.GATEWAY", ""), "dns": d.get("IP4.DNS", ""),
        "ip6": ", ".join(ip6), "velocidad": velocidad, "driver": d.get("GENERAL.DRIVER", ""),
    }


def wifi_dev() -> str | None:
    return next((d["dev"] for d in dispositivos() if d["tipo"] == "wifi"), None)


# ---------- WiFi ----------
def wifi_lista(buscar: bool = False) -> list[dict]:
    """Redes visibles, una por SSID (la de más señal): [{ssid, señal, seguridad, canal, en_uso, banda}]."""
    salida = _nm("-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY,CHAN,FREQ", "device", "wifi", "list",
                 "--rescan", "yes" if buscar else "auto", timeout=25)
    redes: dict[str, dict] = {}
    for linea in salida.splitlines():
        c = _campos(linea)
        if len(c) < 6 or not c[1]:
            continue  # redes ocultas: sin nombre
        r = {"ssid": c[1], "señal": int(c[2] or 0), "seguridad": c[3] if c[3] not in ("", "--") else "",
             "canal": c[4], "en_uso": c[0] == "*", "banda": "5 GHz" if c[5].startswith("5") else
             ("6 GHz" if c[5].startswith("6") else "2.4 GHz")}
        prev = redes.get(r["ssid"])
        if prev is None or r["en_uso"] or (not prev["en_uso"] and r["señal"] > prev["señal"]):
            redes[r["ssid"]] = r
    return sorted(redes.values(), key=lambda r: (not r["en_uso"], -r["señal"]))


def _ssid_de(uuid: str) -> str:
    return _clave_valor(_nm("-t", "-f", "802-11-wireless.ssid", "connection", "show", uuid)).get(
        "802-11-wireless.ssid", "")


def guardada_para(ssid: str) -> str | None:
    """UUID de la conexión guardada para ese SSID, o None."""
    for g in guardadas():
        if g["tipo"] == "wifi" and (g["nombre"] == ssid or _ssid_de(g["uuid"]) == ssid):
            return g["uuid"]
    return None


def _gestion_clave(seguridad: str) -> str | None:
    """key-mgmt de NetworkManager según lo que anuncia la red; None = abierta."""
    s = seguridad.upper()
    if not s:
        return None
    if "802.1X" in s:
        raise RedError("redes empresariales (802.1X) aún no soportadas desde aquí")
    if "WPA3" in s and "WPA2" not in s and "WPA1" not in s:
        return "sae"
    if "WEP" in s:
        return "none"
    return "wpa-psk"


def conectar_wifi(ssid: str, seguridad: str = "", clave: str | None = None) -> str:
    """Conecta a una red. Si ya está guardada, la activa; si es nueva, la crea y,
    si no conecta (p. ej. contraseña mala), la borra. Devuelve un texto para mostrar."""
    uuid = guardada_para(ssid)
    if uuid and clave is None:
        _nm("--wait", "30", "connection", "up", "uuid", uuid, timeout=40)
        return f"CONECTADO A {ssid}"
    dev = wifi_dev()
    if dev is None:
        raise RedError("no hay tarjeta WiFi")
    gestion = _gestion_clave(seguridad)
    if gestion is not None and not clave:
        raise NecesitaClave("esta red necesita contraseña")
    nueva = uuid is None
    if nueva:
        args = ["connection", "add", "type", "wifi", "ifname", dev, "con-name", ssid, "ssid", ssid]
        if gestion is not None:
            args += ["wifi-sec.key-mgmt", gestion]
        out = _nm(*args)
        m = re.search(r"\(([0-9a-f-]{36})\)", out)
        if not m:
            raise RedError("no se pudo crear la conexión")
        uuid = m.group(1)
    campo = "802-11-wireless-security.wep-key0" if gestion == "none" else "802-11-wireless-security.psk"
    try:
        if gestion is None:
            _nm("--wait", "30", "connection", "up", "uuid", uuid, timeout=40)
        else:
            _nm("--wait", "30", "connection", "up", "uuid", uuid, "passwd-file", "/dev/stdin",
                entrada=f"{campo}:{clave}\n", timeout=40)
    except RedError:
        if nueva:
            try:
                _nm("connection", "delete", "uuid", uuid)
            except RedError:
                pass
        raise
    return f"CONECTADO A {ssid}"


def desconectar(dev: str) -> None:
    _nm("device", "disconnect", dev)


def wifi_radio_on() -> bool:
    return _nm("radio", "wifi").strip() == "enabled"


def wifi_radio(encender: bool) -> None:
    _nm("radio", "wifi", "on" if encender else "off")


# ---------- modo avión (todas las radios: WiFi, Bluetooth, WWAN) ----------
RFKILL_TYPE_ALL, RFKILL_OP_CHANGE_ALL = 0, 3


def modo_avion() -> bool | None:
    """True si todas las radios están bloqueadas por software; None si no hay radios."""
    radios = list(Path("/sys/class/rfkill").glob("rfkill*"))
    if not radios:
        return None
    return all(_leer(r / "soft") == "1" for r in radios)


def poner_modo_avion(activar: bool) -> None:
    try:
        with open("/dev/rfkill", "wb", buffering=0) as f:
            f.write(struct.pack("=IBBBB", 0, RFKILL_TYPE_ALL, RFKILL_OP_CHANGE_ALL, 1 if activar else 0, 0))
    except OSError as e:
        raise RedError(f"/dev/rfkill: {e.strerror}") from e
    if not activar:  # NetworkManager recuerda su propio interruptor de WiFi
        try:
            wifi_radio(True)
        except RedError:
            pass


# ---------- conexiones guardadas ----------
def guardadas() -> list[dict]:
    """[{nombre, uuid, tipo ('wifi'|'ethernet'), auto (bool), dev (activa en…)}]"""
    out = []
    for linea in _nm("-t", "-f", "NAME,UUID,TYPE,AUTOCONNECT,DEVICE", "connection", "show").splitlines():
        c = _campos(linea)
        if len(c) >= 5 and c[2] in TIPO_CON:
            out.append({"nombre": c[0], "uuid": c[1], "tipo": TIPO_CON[c[2]], "auto": c[3] == "yes",
                        "dev": c[4]})
    return sorted(out, key=lambda g: (not g["dev"], g["tipo"], g["nombre"].lower()))


def activar(uuid: str) -> None:
    _nm("--wait", "30", "connection", "up", "uuid", uuid, timeout=40)


def olvidar(uuid: str) -> None:
    _nm("connection", "delete", "uuid", uuid)


def opciones(uuid: str) -> dict:
    """Opciones avanzadas de una conexión, ya interpretadas."""
    d = _clave_valor(_nm("-t", "-f", ",".join(CAMPOS_OPC), "connection", "show", uuid))
    tipo = TIPO_CON.get(d.get("connection.type", ""), "")
    dns4 = " ".join(d.get("ipv4.dns", "").replace(",", " ").split())
    dns = "auto"
    if d.get("ipv4.ignore-auto-dns") == "yes" or dns4:
        dns = next((k for k, (v4, _v6) in DNS_CONOCIDOS.items() if v4 == dns4), "propio")
    mac = d.get("802-11-wireless.cloned-mac-address" if tipo == "wifi" else "802-3-ethernet.cloned-mac-address", "")
    direccion = d.get("ipv4.addresses", "").split(",")[0].strip()
    return {
        "uuid": uuid, "nombre": d.get("connection.id", ""), "tipo": tipo,
        "auto": d.get("connection.autoconnect", "yes") == "yes",
        "medida": {"yes": "si", "no": "no"}.get(d.get("connection.metered", ""), "auto"),
        "ipv4": "manual" if d.get("ipv4.method") == "manual" else "auto",
        "direccion": direccion, "puerta": d.get("ipv4.gateway", "").strip(),
        "dns": dns, "dns_propio": dns4 if dns == "propio" else "",
        "ipv6": "off" if d.get("ipv6.method") in ("disabled", "ignore") else "auto",
        "mac": mac if mac in ("random", "stable") else "permanente",
    }


_IPV4 = re.compile(r"^(\d{1,3}\.){3}\d{1,3}$")


def _ip_valida(ip: str) -> bool:
    return bool(_IPV4.match(ip)) and all(int(p) <= 255 for p in ip.split("."))


def guardar_opciones(o: dict) -> str:
    """Escribe las opciones (mismo formato que opciones()) y, si la conexión está
    activa, la vuelve a levantar para aplicarlas. Devuelve un texto para mostrar."""
    args = ["connection", "modify", "uuid", o["uuid"],
            "connection.autoconnect", "yes" if o["auto"] else "no",
            "connection.metered", {"si": "yes", "no": "no"}.get(o["medida"], "unknown")]
    if o["ipv4"] == "manual":
        dire = o["direccion"].strip()
        if "/" not in dire:
            dire += "/24"
        ip, _, pref = dire.partition("/")
        if not _ip_valida(ip) or not pref.isdigit() or not 0 < int(pref) <= 32:
            raise RedError("dirección IPv4 no válida (ej.: 192.168.1.50/24)")
        if o["puerta"] and not _ip_valida(o["puerta"].strip()):
            raise RedError("puerta de enlace no válida (ej.: 192.168.1.1)")
        args += ["ipv4.method", "manual", "ipv4.addresses", dire, "ipv4.gateway", o["puerta"].strip()]
    else:
        args += ["ipv4.method", "auto", "ipv4.addresses", "", "ipv4.gateway", ""]
    if o["dns"] == "auto":
        args += ["ipv4.dns", "", "ipv4.ignore-auto-dns", "no", "ipv6.dns", "", "ipv6.ignore-auto-dns", "no"]
    else:
        if o["dns"] == "propio":
            v4 = " ".join(o["dns_propio"].replace(",", " ").split())
            if not v4 or not all(_ip_valida(x) for x in v4.split()):
                raise RedError("DNS propio no válido (ej.: 1.1.1.1 9.9.9.9)")
            v6 = ""
        else:
            v4, v6 = DNS_CONOCIDOS[o["dns"]]
        if o["ipv6"] == "off":
            v6 = ""  # con IPv6 desactivado NetworkManager no admite DNS de IPv6
        args += ["ipv4.dns", v4, "ipv4.ignore-auto-dns", "yes", "ipv6.dns", v6,
                 "ipv6.ignore-auto-dns", "yes" if v6 else "no"]
    if o["ipv6"] == "off":  # con «disabled» ipv6.dns tiene que quedar vacío
        i = args.index("ipv6.dns")
        args[i + 1] = ""
    args += ["ipv6.method", "disabled" if o["ipv6"] == "off" else "auto"]
    if o["tipo"] in ("wifi", "ethernet"):
        campo = "802-11-wireless.cloned-mac-address" if o["tipo"] == "wifi" else "802-3-ethernet.cloned-mac-address"
        args += [campo, "" if o["mac"] == "permanente" else o["mac"]]
    _nm(*args)
    activa = any(g["uuid"] == o["uuid"] and g["dev"] for g in guardadas())
    if activa:
        activar(o["uuid"])
        return "GUARDADO Y APLICADO"
    return "GUARDADO (se aplica la próxima vez que conecte)"


def barras(señal: int) -> str:
    return "▂▄▆█"[: max(1, min(4, (señal + 24) // 25))].ljust(4, "·")


def guardadas_wifi() -> dict[str, str]:
    """{SSID: UUID} de las redes WiFi guardadas (el nombre de la conexión puede no ser el SSID)."""
    out = {}
    for g in guardadas():
        if g["tipo"] == "wifi":
            try:
                ssid = _ssid_de(g["uuid"])
            except RedError:  # se borró mientras la leíamos
                continue
            out[ssid or g["nombre"]] = g["uuid"]
    return out


def texto_error(err: Exception) -> str:
    """Mensaje corto y en español para errores típicos de nmcli."""
    msg = str(err).strip() or err.__class__.__name__
    if "Secrets were required" in msg or "802-1X supplicant" in msg or "psk: property is invalid" in msg:
        return "contraseña incorrecta"
    if "No network with SSID" in msg:
        return "la red ya no está a la vista"
    if "Timeout" in msg or "no respondió" in msg:
        # con una contraseña mala NetworkManager no avisa: reintenta hasta agotar el tiempo
        return "no conectó a tiempo (¿contraseña incorrecta o señal débil?)"
    return msg[:120]
