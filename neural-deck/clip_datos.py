"""NEURAL//CLIP: historial del portapapeles (SQLite local) y lectura de wl-paste. Sin GTK."""
from __future__ import annotations

import hashlib
import os
import sqlite3
import subprocess
import time
from pathlib import Path

BASE = Path(os.environ.get("CLIP_HOME", Path.home() / ".local/state/neural-clip"))
MAX_TEXTO = 2 * 1024 * 1024
MAX_IMAGEN = 32 * 1024 * 1024
MAX_ENTRADAS = 400
MAX_IMAGENES = 256 * 1024 * 1024
LIMITE_VISTA = 150

# Las apps de contraseñas marcan así lo que no debe guardarse. NEURAL//BÓVEDA usará la primera.
SECRETOS = {"application/x-neural-secret", "x-kde-passwordManagerHint", "org.nspasteboard.ConcealedType",
            "application/x-nspasteboard-concealed-type"}
TEXTOS = ("text/plain;charset=utf-8", "UTF8_STRING", "text/plain", "STRING", "TEXT")
SIN_AUTO = BASE / "sin_autocopia"  # si existe, seleccionar no copia
OMITIR = BASE / "omitir"  # hasta qué momento no guardar: lo escribe NEURAL//BÓVEDA antes de copiar
IMAGENES = ("image/png", "image/webp", "image/jpeg", "image/avif", "image/gif", "image/bmp", "image/tiff")


def marcar_omitir(segundos: float = 3) -> None:
    """Lo siguiente que se copie en este plazo no entra al historial."""
    BASE.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = OMITIR.with_suffix(".tmp")
    tmp.write_text(str(time.time() + segundos))
    os.replace(tmp, OMITIR)


def omitiendo() -> bool:
    try:
        return time.time() < float(OMITIR.read_text())
    except (OSError, ValueError):
        return False


def elegir_tipo(tipos: list[str]) -> tuple[str, str] | None:
    """(clase, mime) de lo que hay que guardar, o None si no se debe o no se puede."""
    if SECRETOS & set(tipos):
        return None
    if "text/uri-list" in tipos:
        return "archivos", "text/uri-list"
    for t in TEXTOS:
        if t in tipos:
            return "texto", t
    for t in IMAGENES:
        if t in tipos:
            return "imagen", t
    return None


def _wl_paste(*args: str, limite: int) -> bytes | None:
    """Salida de wl-paste, o None si falla o se pasa del límite (sin leerlo todo)."""
    try:
        p = subprocess.Popen(["wl-paste", "--no-newline", *args], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except OSError:
        return None
    try:
        datos = p.stdout.read(limite + 1)
        if len(datos) > limite:
            p.kill()
            return None
        return datos if p.wait(timeout=5) == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        p.kill()
        return None
    finally:
        p.stdout.close()
        p.wait()


def leer_portapapeles() -> tuple[str, str, bytes] | None:
    """(clase, mime, datos) del portapapeles actual, si hay que guardarlo."""
    try:
        s = subprocess.run(["wl-paste", "--list-types"], capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if s.returncode != 0:
        return None
    elegido = elegir_tipo([t.strip() for t in s.stdout.splitlines() if t.strip()])
    if not elegido:
        return None
    clase, mime = elegido
    datos = _wl_paste("--type", mime, limite=MAX_IMAGEN if clase == "imagen" else MAX_TEXTO)
    if not datos or (clase != "imagen" and not datos.strip()):
        return None
    return clase, mime, datos


def leer_seleccion() -> bytes | None:
    """Texto seleccionado ahora mismo (selección primaria), si se puede copiar."""
    try:
        s = subprocess.run(["wl-paste", "--primary", "--list-types"], capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if s.returncode != 0:
        return None
    elegido = elegir_tipo([t.strip() for t in s.stdout.splitlines() if t.strip()])
    if not elegido or elegido[0] != "texto":
        return None
    datos = _wl_paste("--primary", "--type", elegido[1], limite=MAX_TEXTO)
    return datos if datos and datos.strip() else None


def copiar(mime: str, datos: bytes) -> bool:
    orden = ["wl-copy"] if mime in TEXTOS else ["wl-copy", "--type", mime]
    try:
        return subprocess.run(orden, input=datos, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              timeout=5).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


class Historial:
    def __init__(self, base: Path = BASE):
        base.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(base, 0o700)
        ruta = base / "historial.db"
        self.db = sqlite3.connect(ruta, isolation_level=None)
        os.chmod(ruta, 0o600)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA secure_delete=ON")  # lo borrado no queda en el archivo
        self.db.execute("""CREATE TABLE IF NOT EXISTS clips(
            id INTEGER PRIMARY KEY, sha TEXT UNIQUE NOT NULL, clase TEXT NOT NULL, mime TEXT NOT NULL,
            texto TEXT NOT NULL DEFAULT '', datos BLOB NOT NULL, miniatura BLOB, tam INTEGER NOT NULL,
            ts REAL NOT NULL, fijado INTEGER NOT NULL DEFAULT 0)""")

    def agregar(self, clase: str, mime: str, datos: bytes, miniatura: bytes | None = None) -> int:
        """Lo repetido no se duplica: sube al principio."""
        sha = hashlib.sha256(clase.encode() + b"\0" + datos).hexdigest()
        ahora = time.time()
        texto = "" if clase == "imagen" else datos.decode("utf-8", "replace")
        self.db.execute("""INSERT INTO clips(sha,clase,mime,texto,datos,miniatura,tam,ts) VALUES(?,?,?,?,?,?,?,?)
                           ON CONFLICT(sha) DO UPDATE SET ts=excluded.ts, mime=excluded.mime""",
                        (sha, clase, mime, texto, datos, miniatura, len(datos), ahora))
        self.podar()
        return self.db.execute("SELECT id FROM clips WHERE sha=?", (sha,)).fetchone()[0]

    def podar(self) -> None:
        self.db.execute("""DELETE FROM clips WHERE fijado=0 AND id NOT IN
                           (SELECT id FROM clips WHERE fijado=0 ORDER BY ts DESC LIMIT ?)""", (MAX_ENTRADAS,))
        suma = 0
        for f in self.db.execute("SELECT id,tam FROM clips WHERE clase='imagen' AND fijado=0 ORDER BY ts DESC").fetchall():
            suma += f["tam"]
            if suma > MAX_IMAGENES:
                self.db.execute("DELETE FROM clips WHERE id=?", (f["id"],))

    def buscar(self, consulta: str = "", filtro: str = "todo") -> list[sqlite3.Row]:
        donde, args = [], []
        for palabra in consulta.split():
            donde.append(r"texto LIKE ? ESCAPE '\'")
            args.append("%" + palabra.replace("\\", r"\\").replace("%", r"\%").replace("_", r"\_") + "%")
        if filtro == "fijados":
            donde.append("fijado=1")
        elif filtro in ("texto", "imagen", "archivos"):
            donde.append("clase=?")
            args.append(filtro)
        sql = "SELECT id,clase,mime,substr(texto,1,4000) AS texto,miniatura,tam,ts,fijado FROM clips"
        if donde:
            sql += " WHERE " + " AND ".join(donde)
        return self.db.execute(sql + " ORDER BY fijado DESC, ts DESC LIMIT ?", (*args, LIMITE_VISTA)).fetchall()

    def contenido(self, cid: int) -> sqlite3.Row | None:
        return self.db.execute("SELECT clase,mime,datos FROM clips WHERE id=?", (cid,)).fetchone()

    def fijar(self, cid: int) -> None:
        self.db.execute("UPDATE clips SET fijado=1-fijado WHERE id=?", (cid,))

    def borrar(self, cid: int) -> None:
        self.db.execute("DELETE FROM clips WHERE id=?", (cid,))

    def vaciar(self) -> None:
        self.db.execute("DELETE FROM clips WHERE fijado=0")
        self.db.execute("VACUUM")
