#!/usr/bin/env python3
"""NEURAL//RESPALDO: copias cifradas e incrementales con restic a un disco externo.

Residente ([autostart]; respaldo_motor.py --fondo): al ver el disco configurado lo
monta y respalda si toca. La ventana (respaldo.py) lee estado.json y pide un
respaldo creando el archivo "pedir". Solo Linux, sin red ni servidores.
Nunca escribe en un disco que esté usando una máquina virtual."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

BASE = Path(os.environ.get("RESPALDO_HOME", Path.home() / ".local/state/neural-respaldo"))
RAIZ = Path(os.environ.get("RESPALDO_RAIZ", Path.home()))
CFG, EST, CLAVE, PEDIR = BASE / "config.json", BASE / "estado.json", BASE / "clave", BASE / "pedir"
SUBDIR = "NEURAL-RESPALDO"
RESTAURADO = RAIZ / "Restaurado"
MIN_CLAVE = 12

RUTAS = ["Documentos", "Proyectos", "Escritorio", "Imágenes", "Música", "Vídeos", "neural-deck",
         "bin", ".config", ".ssh", ".gnupg", ".local/share/keyrings",
         ".local/state/neural-nexo", ".local/state/neural-boveda", ".local/state/neural-respaldo/config.json",
         ".zshrc", ".profile"]
EXCLUIR = ["**/.cache", "**/__pycache__", "**/.venv", "**/venv", "**/node_modules", "**/Cache", "**/Code Cache",
           "**/GPUCache", "**/CacheStorage", "**/Service Worker/CacheStorage", "**/cache2", "*.qcow2", "*.iso",
           "**/.nexo-parcial"]
DEFECTO = {"uuid": "", "ruta": "", "rutas": RUTAS, "cada_h": 24, "ultimo_check": 0}


class ErrorRespaldo(Exception):
    pass


def _leer(p: Path, defecto: dict) -> dict:
    try:
        return {**defecto, **json.loads(p.read_text())}
    except (OSError, ValueError):
        return dict(defecto)


def _escribir(p: Path, d: dict) -> None:
    BASE.mkdir(parents=True, exist_ok=True, mode=0o700)
    t = p.with_name(p.name + ".tmp")
    t.write_text(json.dumps(d))
    os.replace(t, p)


def config() -> dict:
    return _leer(CFG, DEFECTO)


def estado() -> dict:
    return _leer(EST, {"fase": "reposo", "pct": 0, "ultimo_ok": 0, "msg": "", "error": ""})


def _bloques() -> list[dict]:
    try:
        s = subprocess.run(["lsblk", "-Jpo", "PATH,PKNAME,UUID,FSTYPE,LABEL,MOUNTPOINT,SIZE,RM,TRAN,MODEL"],
                           capture_output=True, text=True, timeout=5).stdout
        return json.loads(s)["blockdevices"]
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        return []


def _aplanar(ds, padre=None):
    for d in ds:
        d["_disco"] = padre or d
        yield d
        yield from _aplanar(d.get("children", []), padre or d)


def disponibles() -> list[dict]:
    """Volúmenes montados en discos externos que pueden ser destino."""
    r = []
    for d in _aplanar(_bloques()):
        raiz = d["_disco"]
        if d.get("uuid") and d.get("mountpoint") and (raiz.get("tran") == "usb" or raiz.get("rm")):
            u = shutil.disk_usage(d["mountpoint"])
            r.append({"uuid": d["uuid"], "punto": d["mountpoint"], "fs": d.get("fstype"), "label": d.get("label") or "",
                      "modelo": raiz.get("model") or "", "total": u.total, "libre": u.free})
    return r


def _buscar_volumen(uuid: str) -> dict | None:
    return next((d for d in _aplanar(_bloques()) if d.get("uuid") == uuid), None)


def discos_de_vm() -> set[str]:
    """Discos físicos (/dev/sdX) abiertos por algún proceso qemu."""
    usados = set()
    for p in Path("/proc").iterdir():
        if not p.name.isdigit():
            continue
        try:
            if "qemu" not in (p / "comm").read_text():
                continue
            for fd in (p / "fd").iterdir():
                dst = os.readlink(fd)
                if dst.startswith("/dev/") and not dst.startswith(("/dev/null", "/dev/kvm", "/dev/net", "/dev/pts")):
                    usados.add(dst)
        except OSError:
            pass
    return usados


def en_uso_por_vm(uuid: str, usados: set[str] | None = None) -> bool:
    d = _buscar_volumen(uuid) if uuid else None
    if not d:
        return False
    usados = discos_de_vm() if usados is None else usados
    raiz = d["_disco"]
    nombres = {raiz["path"], d["path"]} | {h["path"] for h, in ((c,) for c in _aplanar(raiz.get("children", [])))}
    return bool(nombres & usados)


def montar(uuid: str) -> str | None:
    d = _buscar_volumen(uuid)
    if not d:
        return None
    if d.get("mountpoint"):
        return d["mountpoint"]
    subprocess.run(["udisksctl", "mount", "--no-user-interaction", "-b", d["path"]], capture_output=True, timeout=30)
    d = _buscar_volumen(uuid)
    return d.get("mountpoint") if d else None


def carpeta_destino(c: dict, montando: bool = True) -> Path | None:
    if c.get("ruta"):
        return Path(c["ruta"])
    if not c.get("uuid"):
        return None
    if en_uso_por_vm(c["uuid"]):
        return None
    if montando:
        m = montar(c["uuid"])
    else:
        d = _buscar_volumen(c["uuid"])
        m = d.get("mountpoint") if d else None
    return Path(m) / SUBDIR if m else None


def _env(repo: Path, clave: Path = CLAVE) -> dict:
    return {**os.environ, "RESTIC_REPOSITORY": str(repo), "RESTIC_PASSWORD_FILE": str(clave), "RESTIC_PROGRESS_FPS": "2"}


def restic(repo: Path, *args: str, clave: Path = CLAVE, timeout: int = 7200) -> subprocess.CompletedProcess:
    return subprocess.run(["restic", *args], env=_env(repo, clave), capture_output=True, text=True, timeout=timeout)


def _error(p: subprocess.CompletedProcess) -> str:
    return (p.stderr or p.stdout or "error desconocido").strip().splitlines()[-1][:300]


def configurar(clave: str, uuid: str = "", ruta: str = "") -> bool:
    """Prueba la clave o crea el repositorio. True si se creó uno nuevo."""
    if len(clave) < MIN_CLAVE:
        raise ErrorRespaldo(f"La clave necesita al menos {MIN_CLAVE} caracteres")
    c = {**config(), "uuid": uuid, "ruta": ruta}
    repo = carpeta_destino(c)
    if repo is None:
        raise ErrorRespaldo("El disco no está disponible (o lo usa una máquina virtual)")
    BASE.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = BASE / "clave.nueva"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(clave)
    try:
        nuevo = not (repo / "config").exists()
        if nuevo:
            repo.mkdir(parents=True, exist_ok=True)
            p = restic(repo, "init", clave=tmp)
        else:
            p = restic(repo, "cat", "config", clave=tmp)
        if p.returncode != 0:
            raise ErrorRespaldo("Esa no es la clave de este respaldo" if not nuevo else _error(p))
        os.replace(tmp, CLAVE)
    finally:
        tmp.unlink(missing_ok=True)
    _escribir(CFG, c)
    return nuevo


def lista_rutas(c: dict) -> list[str]:
    return [str(RAIZ / r) for r in c["rutas"] if (RAIZ / r).exists()]


def _base_backup(c: dict) -> list[str]:
    a = ["backup", "--json", "--tag", "neural", "--exclude-caches", "--skip-if-unchanged"]
    for e in EXCLUIR:
        a += ["--exclude", e]
    return a + lista_rutas(c)


def _guardar_fase(est: dict, **k) -> None:
    est.update(k)
    _escribir(EST, est)


def respaldar(c: dict | None = None, est: dict | None = None) -> dict:
    """Respaldo completo. Devuelve el estado final; nunca deja la fase a medias."""
    c = c or config()
    est = est if est is not None else estado()
    _guardar_fase(est, fase="preparando", pct=0, error="", msg="Comprobando el disco")
    try:
        repo = carpeta_destino(c)
        if repo is None or not (repo / "config").exists():
            raise ErrorRespaldo("Disco no disponible o en uso por una máquina virtual")
        if not CLAVE.exists():
            raise ErrorRespaldo("Falta configurar la clave")
        p = restic(repo, *_base_backup(c)[:1], "--dry-run", *_base_backup(c)[1:])
        if p.returncode not in (0, 3):
            raise ErrorRespaldo(_error(p))
        fin = next((json.loads(x) for x in reversed(p.stdout.splitlines()) if '"summary"' in x), {})
        nuevo = fin.get("data_added_packed") or fin.get("data_added", 0)
        libre = shutil.disk_usage(repo).free
        if nuevo * 1.1 + 64 * 1024 * 1024 > libre:
            raise ErrorRespaldo(f"No cabe: hacen falta {nuevo >> 20} MiB y hay {libre >> 20} MiB libres")
        _guardar_fase(est, fase="copiando", msg="Copiando")
        pr = subprocess.Popen(["restic", *_base_backup(c)], env=_env(repo), stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True, bufsize=1)
        ultimo = 0.0
        for linea in pr.stdout:
            try:
                d = json.loads(linea)
            except ValueError:
                continue
            if d.get("message_type") == "status" and time.monotonic() - ultimo > 0.5:
                ultimo = time.monotonic()
                _guardar_fase(est, pct=round(d.get("percent_done", 0) * 100),
                              msg=f"{d.get('files_done', 0)} / {d.get('total_files', 0)} archivos")
        err = pr.stderr.read()
        cod = pr.wait()
        if cod not in (0, 3):
            raise ErrorRespaldo((err.strip().splitlines() or ["restic falló"])[-1][:300])
        _guardar_fase(est, fase="limpiando", msg="Aplicando retención", pct=100)
        p = restic(repo, "forget", "--tag", "neural", "--keep-daily", "7", "--keep-weekly", "4", "--keep-monthly", "12", "--prune")
        if p.returncode != 0:
            raise ErrorRespaldo(_error(p))
        if time.time() - c.get("ultimo_check", 0) > 7 * 86400:
            _guardar_fase(est, fase="verificando", msg="Verificando la copia")
            p = restic(repo, "check", "--read-data-subset=10%")
            if p.returncode != 0:
                raise ErrorRespaldo("LA VERIFICACIÓN FALLÓ: " + _error(p))
            c["ultimo_check"] = time.time()
            _escribir(CFG, c)
        aviso = " (algunos archivos no se pudieron leer)" if cod == 3 else ""
        _guardar_fase(est, fase="reposo", pct=100, ultimo_ok=time.time(), error="", msg="Respaldo completo" + aviso)
        _notificar("Respaldo completo", "Ya puedes retirar el disco desde NEURAL//DISCO." + aviso)
    except (ErrorRespaldo, OSError, subprocess.SubprocessError) as e:
        _guardar_fase(est, fase="reposo", pct=0, error=str(e), msg="", ultimo_fallo=time.time())
        _notificar("El respaldo falló", str(e))
    return est


def _notificar(titulo: str, cuerpo: str) -> None:
    try:
        subprocess.Popen(["notify-send", "-a", "NEURAL//RESPALDO", titulo, cuerpo], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    except OSError:
        pass


def instantaneas(c: dict | None = None) -> list[dict]:
    repo = carpeta_destino(c or config())
    if repo is None:
        raise ErrorRespaldo("Conecta el disco de respaldo")
    p = restic(repo, "snapshots", "--json", timeout=120)
    if p.returncode != 0:
        raise ErrorRespaldo(_error(p))
    return sorted(json.loads(p.stdout or "[]"), key=lambda s: s["time"], reverse=True)


def restaurar(snap: str, incluir: str = "", c: dict | None = None) -> Path:
    """Siempre a una carpeta nueva: restaurar no pisa nada de lo que tienes."""
    repo = carpeta_destino(c or config())
    if repo is None:
        raise ErrorRespaldo("Conecta el disco de respaldo")
    n, dst = 0, RESTAURADO / f"{time.strftime('%Y-%m-%d_%H%M')}_{snap[:8]}"
    while dst.exists():
        n += 1
        dst = dst.with_name(f"{dst.name}_{n}")
    dst.mkdir(parents=True)
    a = ["restore", snap, "--target", str(dst)]
    if incluir.strip():
        a += ["--include", incluir.strip()]
    p = restic(repo, *a)
    if p.returncode != 0:
        raise ErrorRespaldo(_error(p))
    return dst


def _vigilar() -> None:
    PEDIR.unlink(missing_ok=True)
    ciclo = 0
    while True:
        c, pedido = config(), PEDIR.exists()
        PEDIR.unlink(missing_ok=True)
        if pedido or (ciclo % 6 == 0 and (c.get("uuid") or c.get("ruta"))):
            est = estado()
            ahora = time.time()
            toca = ahora - est.get("ultimo_ok", 0) >= c["cada_h"] * 3600 \
                and ahora - est.get("ultimo_fallo", 0) >= 3600
            if CLAVE.exists() and (pedido or toca) and (c.get("ruta") or _buscar_volumen(c.get("uuid", ""))):
                respaldar(c, est)
        ciclo += 1
        time.sleep(2)


if __name__ == "__main__":
    import ctypes

    ctypes.CDLL(None).prctl(15, b"neural-respaldo", 0, 0, 0)
    _vigilar()
