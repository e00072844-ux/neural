#!/usr/bin/env python3
"""Motor de NEURAL//CVE para CachyOS/Arch (sin root).

Descarga los avisos del equipo de seguridad de Arch (security.archlinux.org,
lo mismo que usa arch-audit), los cruza con lo instalado (pacman -Q, vercmp) y
escribe ~/.local/state/neural-cve/ultimo.json (lo lee cve.py) y ultimo.txt.

  pendientes  lo instalado es más viejo que la versión corregida
  a_mano      el aviso no tiene versión corregida: mirar y marcar como revisado
  al_dia      avisos de lo instalado que ya están corregidos (y los revisados)
  sistema     actualizaciones, kernel en uso, cortafuegos, puertos, Flatpak

  --sin-red   no descargar (usa lo guardado; para recalcular tras marcar)
  --callado   no imprimir el informe
  --aviso     notificar si aparece algo nuevo (lo usa el timer diario)
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

BASE = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "neural-cve"
CACHE = BASE / "cache"
JSON = BASE / "ultimo.json"
TXT = BASE / "ultimo.txt"
REVISADOS = BASE / "revisados.tsv"
URL = "https://security.archlinux.org"
AGENTE = "neural-cve/1.0"

GRAV = {"Critical": "CRÍTICO", "High": "ALTO", "Medium": "MEDIO", "Low": "BAJO", "Unknown": "SIN DATO"}
ORDEN = {"CRÍTICO": 0, "ALTO": 1, "MEDIO": 2, "BAJO": 3, "SIN DATO": 4}
TIPOS = {
    "arbitrary code execution": "ejecución de código", "arbitrary command execution": "ejecución de órdenes",
    "denial of service": "denegación de servicio", "privilege escalation": "escalada de privilegios",
    "information disclosure": "fuga de información", "arbitrary filesystem access": "acceso a archivos",
    "multiple issues": "varios fallos", "access restriction bypass": "saltarse restricciones",
    "insufficient validation": "validación insuficiente", "content spoofing": "suplantación de contenido",
    "man-in-the-middle": "intermediario (MITM)", "cross-site scripting": "cross-site scripting",
    "sandbox escape": "escape del sandbox", "signature forgery": "firma falsificada",
    "arbitrary file overwrite": "sobrescribir archivos", "arbitrary file upload": "subir archivos",
    "authentication bypass": "saltarse la autenticación", "incorrect calculation": "cálculo erróneo",
    "url request injection": "inyección de peticiones", "silent downgrade": "degradación silenciosa",
    "directory traversal": "salirse de la carpeta", "certificate verification bypass": "saltarse el certificado",
    "unknown": "sin clasificar",
}


# ------------------------------------------------------------------ utilidades
def _run(*cmd: str, timeout: float = 60) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _bajar(ruta: str, destino: Path, red: bool) -> object:
    """JSON de security.archlinux.org (con copia en cache/ para --sin-red)."""
    if red:
        req = urllib.request.Request(f"{URL}/{ruta}", headers={"User-Agent": AGENTE})
        with urllib.request.urlopen(req, timeout=40) as r:
            datos = r.read()
        json.loads(datos)  # que sea JSON antes de guardarlo
        destino.parent.mkdir(parents=True, exist_ok=True)
        tmp = destino.with_suffix(".tmp")
        tmp.write_bytes(datos)
        tmp.replace(destino)
    return json.loads(destino.read_text(encoding="utf-8"))


def _cve(cid: str, red: bool) -> dict:
    f = CACHE / f"{cid}.json"
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    try:
        return _bajar(f"{cid}.json", f, red) if red else {}
    except Exception:  # noqa: BLE001
        return {}


def vercmp_lote(pares: list[tuple[str, str]]) -> list[int]:
    """vercmp de pacman para muchos pares (en paralelo; cada llamada es un proceso)."""
    def uno(p):
        return int(_run("vercmp", p[0], p[1], timeout=10).stdout.strip() or 0)
    with ThreadPoolExecutor(8) as ex:
        return list(ex.map(uno, pares))


def instalados() -> dict[str, str]:
    r = _run("pacman", "-Q")
    return dict(l.split(None, 1) for l in r.stdout.splitlines() if " " in l)


def actualizaciones(red: bool, previo: dict) -> tuple[dict[str, str], str]:
    """{paquete: versión nueva} según checkupdates (base temporal, sin root)."""
    if not red:
        return previo.get("_actualizaciones", {}), ""
    try:
        r = _run("checkupdates", "--nocolor", timeout=180)
    except (OSError, subprocess.SubprocessError) as e:
        return {}, f"checkupdates: {e}"
    if r.returncode not in (0, 2):  # 2 = no hay nada
        return {}, "checkupdates: " + (r.stderr.strip().splitlines() or ["falló"])[-1]
    res = {}
    for l in r.stdout.splitlines():
        p = l.split()
        if len(p) >= 4 and p[2] == "->":
            res[p[0]] = p[3]
    return res, ""


def leer_revisados() -> dict[str, str]:
    try:
        lineas = REVISADOS.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return {}
    res = {}
    for l in lineas:
        if l and not l.startswith("#") and "\t" in l:
            k, nota = l.split("\t", 1)
            res[k.strip()] = nota.strip()
    return res


# ------------------------------------------------------------------ avisos
def _texto(grupo: dict, cves: list[dict]) -> str:
    partes = [f"{grupo['name']} · {', '.join(grupo['packages'])}",
              f"Afectada: {grupo.get('affected') or '?'}   Corregida: {grupo.get('fixed') or 'sin versión'}"
              f"   Estado en Arch: {grupo.get('status')}"]
    if grupo.get("ticket"):
        partes.append(f"Ticket: https://bugs.archlinux.org/task/{grupo['ticket']}")
    partes.append(f"Ficha: {URL}/{grupo['name']}")
    for c in cves:
        if not c:
            continue
        cab = f"\n■ {c.get('name')}  ({c.get('severity', '?')}, {c.get('vector', '?')})"
        partes.append(cab)
        if c.get("description"):
            partes.append(re.sub(r"\r\n?", "\n", c["description"]).strip())
        for ref in (c.get("references") or [])[:4]:
            partes.append("  " + ref)
    falta = [i for i, c in zip(grupo["issues"], cves) if not c]
    if falta:
        partes.append("\nSin descripción guardada: " + ", ".join(falta))
    return "\n".join(partes)


def revisar_avisos(red: bool, previo: dict) -> tuple[dict, list[str]]:
    errores = []
    try:
        grupos = _bajar("issues/all.json", CACHE / "all.json", red)
    except Exception as e:  # noqa: BLE001
        errores.append(f"no se pudieron bajar los avisos ({e})")
        try:
            grupos = _bajar("", CACHE / "all.json", False)
            errores[-1] += "; uso los guardados"
        except (OSError, ValueError):
            return {"pendientes": [], "a_mano": [], "al_dia": [], "n_avisos": 0, "n_detectados": 0}, errores
    inst = instalados()
    nuevas, err = actualizaciones(red, previo)
    fallo_act = err
    if err:
        errores.append(err)
    revisados = leer_revisados()

    # (grupo, paquete instalado) que tocan a este equipo
    casos = [(g, p) for g in grupos if g.get("status") != "Not affected"
             for p in g.get("packages", []) if p in inst]
    pares = []
    for g, p in casos:
        pares.append((inst[p], g["fixed"] or g.get("affected") or "0"))
    cmp = vercmp_lote(pares)
    hay_nueva = vercmp_lote([(nuevas[p], g["fixed"]) for g, p in casos if g["fixed"] and p in nuevas])
    it_nueva = iter(hay_nueva)

    antes = {i["id"] for k in ("pendientes", "a_mano") for i in previo.get(k, [])}
    hubo_antes = bool(previo)
    res = {"pendientes": [], "a_mano": [], "al_dia": []}
    por_bajar = []
    for (g, p), c in zip(casos, cmp):
        grav = GRAV.get(g.get("severity"), "SIN DATO")
        tipo = TIPOS.get(g.get("type", ""), g.get("type", ""))
        it = {"id": g["name"], "paquete": p, "grav_es": grav, "tipo": tipo, "fecha": tipo,
              "cves": g.get("issues", []), "instalada": inst[p]}
        if g["fixed"]:
            if c < 0:
                arreglo = nuevas.get(p)
                ya = arreglo is not None and next(it_nueva) >= 0
                it["detalle"] = (f"Tienes {inst[p]}; corregido en {g['fixed']}. "
                                 + (f"Ya está en los repos ({arreglo}): sudo pacman -Syu" if ya
                                    else "tu distribución aún no lo ha publicado"))
                cola = "pendientes"
            else:
                if p in nuevas:
                    next(it_nueva)
                it["detalle"] = f"Corregido en {g['fixed']}; tienes {inst[p]}"
                cola = "al_dia"
        elif c < 0:
            it["detalle"] = f"Afecta desde {g['affected']} y tienes {inst[p]} (más vieja)"
            cola = "al_dia"
        else:
            it["detalle"] = (f"{tipo}: el aviso es de {g['affected']} y tienes {inst[p]}. "
                             "Arch no ha anotado versión corregida")
            cola = "a_mano"
        if cola != "al_dia" and g["name"] in revisados:
            it["revisado"] = True
            it["nota"] = revisados[g["name"]]
            it["detalle"] = "Revisado: " + revisados[g["name"]]
            cola = "al_dia"
        if cola != "al_dia":
            it["nuevo"] = hubo_antes and g["name"] not in antes
            por_bajar.append((it, g))
        else:
            it["texto"] = _texto(g, [])
        res[cola].append(it)

    # descripción de cada CVE solo para lo que hay que mirar
    ids = sorted({c for _, g in por_bajar for c in g.get("issues", [])})
    with ThreadPoolExecutor(6) as ex:
        desc = dict(zip(ids, ex.map(lambda c: _cve(c, red), ids)))
    for it, g in por_bajar:
        it["texto"] = _texto(g, [desc.get(c, {}) for c in g.get("issues", [])])
    for it in res["al_dia"]:
        if it.get("revisado"):
            g = next(x for x in grupos if x["name"] == it["id"])
            it["texto"] = _texto(g, [_cve(c, False) for c in g.get("issues", [])])

    def clave(i):
        return (not i.get("nuevo"), ORDEN.get(i["grav_es"], 9), -int(i["id"].split("-")[-1]))
    for k in res:
        res[k].sort(key=clave if k != "al_dia" else lambda i: (not i.get("revisado"), -int(i["id"].split("-")[-1])))
    res["n_avisos"] = len(grupos)
    res["n_detectados"] = len({p for _, p in casos})
    res["_actualizaciones"] = nuevas
    res["_fallo_act"] = fallo_act
    return res, errores


# ------------------------------------------------------------------ sistema
def _kernel() -> dict:
    rel = os.uname().release
    vmlinuz = Path("/usr/lib/modules") / rel / "vmlinuz"
    if not vmlinuz.exists():
        return {"nombre": "kernel", "ok": False, "grav": "ALTO",
                "texto": f"El kernel en uso ({rel}) ya no está instalado: se actualizó. Reinicia para usar el nuevo"}
    paquete = _run("pacman", "-Qqo", str(vmlinuz)).stdout.strip() or "?"
    return {"nombre": "kernel", "ok": True, "grav": "",
            "texto": f"En uso {rel} ({paquete}), el instalado"}


def _cortafuegos() -> dict:
    for s in ("ufw", "firewalld", "nftables"):
        if _run("systemctl", "is-active", s, timeout=5).stdout.strip() == "active":
            return {"nombre": "cortafuegos", "ok": True, "grav": "", "texto": f"{s} activo"}
    return {"nombre": "cortafuegos", "ok": False, "grav": "ALTO",
            "texto": "Ningún cortafuegos activo (ufw, firewalld o nftables)"}


LOCAL = re.compile(r"^(127\.|\[?::1\]?|\[?::ffff:127\.)")
TCP_CONOCIDOS = {"5355": "LLMNR (resolved)", "10.77.0.1:53": "DNS de la red interna de las VMs"}
UDP_CONOCIDOS = {"5353": "mDNS (avahi)", "5355": "LLMNR (resolved)", "67": "DHCP de la red interna de las VMs",
                 "53": "DNS", "546": "DHCPv6", "68": "DHCP"}


def _puertos(cortafuegos: bool) -> dict:
    r = _run("ss", "-Htuln", timeout=10)
    tcp, udp = [], []
    for l in r.stdout.splitlines():
        p = l.split()
        if len(p) < 5:
            continue
        dir_, puerto = p[4].rsplit(":", 1)
        if LOCAL.match(dir_) or "%lo" in dir_:
            continue
        if p[0] == "tcp":
            conocido = TCP_CONOCIDOS.get(puerto) or TCP_CONOCIDOS.get(f"{dir_}:{puerto}")
            (udp if conocido else tcp).append(f"{puerto}/tcp {conocido}" if conocido else f"{dir_}:{puerto}")
        elif puerto in UDP_CONOCIDOS:
            udp.append(f"{puerto} {UDP_CONOCIDOS[puerto]}")
    tcp = sorted(set(tcp))
    udp = sorted(set(udp))
    extra = ("  ·  servicios del sistema: " + ", ".join(udp)) if udp else ""
    if not tcp:
        return {"nombre": "puertos", "ok": True, "grav": "",
                "texto": "Ningún programa acepta conexiones TCP desde la red" + extra}
    return {"nombre": "puertos", "ok": False, "grav": "BAJO" if cortafuegos else "ALTO",
            "texto": "Escuchando hacia la red: " + ", ".join(tcp) + extra}


def _actualizaciones(nuevas: dict, pendientes: list, red: bool, fallo: str = "") -> dict:
    if fallo:
        return {"nombre": "actualizaciones", "ok": False, "grav": "ERROR", "texto": "No se pudo consultar: " + fallo}
    if not nuevas:
        return {"nombre": "actualizaciones", "ok": True, "grav": "",
                "texto": "Todo actualizado" if red else "Todo actualizado (última consulta)"}
    seg = sorted({i["paquete"] for i in pendientes if i["paquete"] in nuevas})
    texto = f"{len(nuevas)} paquetes por actualizar"
    if seg:
        texto += " · con arreglo de seguridad: " + ", ".join(seg)
    return {"nombre": "actualizaciones", "ok": False, "grav": "ALTO" if seg else "MEDIO", "texto": texto,
            "lista": sorted(nuevas)}


def _flatpak(red: bool, previo: dict) -> dict | None:
    if not Path("/usr/bin/flatpak").exists():
        return None
    if not red:
        return next((s for s in previo.get("sistema", []) if s["nombre"] == "flatpak"), None)
    try:
        r = _run("flatpak", "remote-ls", "--updates", "--columns=application", timeout=120)
    except (OSError, subprocess.SubprocessError) as e:
        return {"nombre": "flatpak", "ok": False, "grav": "ERROR", "texto": str(e)}
    if r.returncode:
        return {"nombre": "flatpak", "ok": False, "grav": "ERROR",
                "texto": (r.stderr.strip().splitlines() or ["falló"])[-1]}
    apps = sorted({l.strip() for l in r.stdout.splitlines() if l.strip()})
    if not apps:
        return {"nombre": "flatpak", "ok": True, "grav": "", "texto": "Sin actualizaciones de Flatpak"}
    return {"nombre": "flatpak", "ok": False, "grav": "MEDIO",
            "texto": f"{len(apps)} por actualizar: " + ", ".join(apps[:8]) + ("…" if len(apps) > 8 else "")}


def revisar_sistema(avisos: dict, red: bool, previo: dict) -> list[dict]:
    fw = _cortafuegos()
    res = [_actualizaciones(avisos.get("_actualizaciones", {}), avisos["pendientes"], red, avisos.get("_fallo_act", "")),
           _kernel(), fw, _puertos(fw["ok"])]
    fp = _flatpak(red, previo)
    if fp:
        res.append(fp)
    return res


# ------------------------------------------------------------------ informe
def informe(d: dict) -> str:
    l = [f"NEURAL//CVE — {d['fecha']}", f"{d['n_avisos']} avisos de Arch · {d['n_detectados']} paquetes tuyos con avisos", ""]
    for err in d["errores"]:
        l.append(f"ERROR: {err}")
    for clave, titulo in (("pendientes", "POR ACTUALIZAR"), ("a_mano", "REVISAR A MANO")):
        l.append(f"== {titulo} ({len(d[clave])})")
        for i in d[clave]:
            l.append(f"  [{i['grav_es']}] {i['paquete']} {i['id']} {'(NUEVO) ' if i.get('nuevo') else ''}— {i['detalle']}")
        l.append("")
    l.append("== SISTEMA")
    for s in d["sistema"]:
        l.append(f"  [{'OK' if s['ok'] else s['grav']}] {s['nombre']}: {s['texto']}")
    rev = [i for i in d["al_dia"] if i.get("revisado")]
    l += ["", f"== REVISADOS ({len(rev)})"] + [f"  {i['id']} {i['paquete']}: {i.get('nota', '')}" for i in rev]
    return "\n".join(l) + "\n"


def avisar(d: dict, previo: dict) -> None:
    nuevos = [i for k in ("pendientes", "a_mano") for i in d[k] if i.get("nuevo")]
    antes = {s["nombre"] for s in previo.get("sistema", []) if not s.get("ok")}
    sis = [s for s in d["sistema"] if not s["ok"] and s["nombre"] not in antes and s["grav"] in ("ALTO", "CRÍTICO")]
    if not nuevos and not sis:
        return
    partes = []
    if nuevos:
        partes.append(f"{len(nuevos)} aviso{'s' if len(nuevos) > 1 else ''} nuevo{'s' if len(nuevos) > 1 else ''}: "
                      + ", ".join(sorted({i['paquete'] for i in nuevos})[:6]))
    partes += [s["texto"] for s in sis]
    urg = "critical" if any(i["grav_es"] in ("CRÍTICO", "ALTO") for i in nuevos) or sis else "normal"
    try:
        _run("notify-send", "-a", "NEURAL//CVE", "-i", "org.local.NeuralCVE", "-u", urg,
             "NEURAL//CVE", "\n".join(partes), timeout=10)
    except (OSError, subprocess.SubprocessError):
        pass


def main(argv: list[str]) -> int:
    red = "--sin-red" not in argv
    BASE.mkdir(parents=True, exist_ok=True)
    try:
        previo = json.loads(JSON.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        previo = {}
    avisos, errores = revisar_avisos(red, previo)
    d = {"fecha": dt.datetime.now().strftime("%d-%m-%Y %H:%M"), "libro": "Arch", "errores": errores, **avisos}
    d["sistema"] = revisar_sistema(avisos, red, previo)
    tmp = JSON.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    TXT.write_text(informe(d), encoding="utf-8")
    tmp.replace(JSON)
    if "--aviso" in argv:
        avisar(d, previo)
    if "--callado" not in argv:
        print(informe(d), end="")
    return 1 if errores and not d["n_avisos"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
