"""NEURAL//BÓVEDA: archivo cifrado de contraseñas, 2FA y notas. Sin GTK.

Formato: una línea JSON de cabecera (v, salt, iter, mem, lanes, nonce) + "\\n" + AES-256-GCM
del contenido JSON, con la cabecera como dato autenticado. Clave: Argon2id de la
contraseña maestra. Se guarda de forma atómica y conserva la versión anterior en .bak."""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import secrets
import string
import struct
import time
import uuid
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.argon2 import Argon2id

BASE = Path(os.environ.get("BOVEDA_HOME", Path.home() / ".local/state/neural-boveda"))
RUTA = BASE / "boveda.nxv"
MIN_MAESTRA = 12
KDF = {"iter": 3, "mem": 262144, "lanes": 4}  # 256 MiB


class ErrorBoveda(Exception):
    pass


def _clave(maestra: str, salt: bytes, p: dict) -> bytes:
    return Argon2id(salt=salt, length=32, iterations=p["iter"], lanes=p["lanes"], memory_cost=p["mem"]).derive(
        maestra.encode())


class Boveda:
    def __init__(self, ruta: Path = RUTA):
        self.ruta = ruta
        self.clave: bytes | None = None
        self.params: dict = {}
        self.entradas: list[dict] = []

    def existe(self) -> bool:
        return self.ruta.exists()

    def abierta(self) -> bool:
        return self.clave is not None

    def crear(self, maestra: str) -> None:
        if self.existe():
            raise ErrorBoveda("Ya existe una bóveda")
        self._poner_clave(maestra)
        self.entradas = []
        self.guardar()

    def _poner_clave(self, maestra: str) -> None:
        if len(maestra) < MIN_MAESTRA:
            raise ErrorBoveda(f"La contraseña maestra necesita al menos {MIN_MAESTRA} caracteres")
        salt = os.urandom(16)
        self.params = {**KDF, "salt": base64.b64encode(salt).decode()}
        self.clave = _clave(maestra, salt, self.params)

    def abrir(self, maestra: str) -> None:
        try:
            cab_b, cifrado = self.ruta.read_bytes().split(b"\n", 1)
            cab = json.loads(cab_b)
            salt, nonce = base64.b64decode(cab["salt"]), base64.b64decode(cab["nonce"])
            if cab.get("v") != 1 or not (cab["mem"] <= 4194304 and cab["iter"] <= 64 and 0 < cab["lanes"] <= 64):
                raise ValueError("cabecera")
            clave = _clave(maestra, salt, cab)
        except (OSError, ValueError, KeyError, TypeError, binascii.Error) as e:
            raise ErrorBoveda("El archivo de la bóveda está dañado") from e
        try:
            plano = AESGCM(clave).decrypt(nonce, cifrado, cab_b)
        except InvalidTag as e:
            raise ErrorBoveda("Contraseña incorrecta (o archivo alterado)") from e
        self.entradas = json.loads(plano)["entradas"]
        self.clave, self.params = clave, {k: cab[k] for k in ("iter", "mem", "lanes", "salt")}

    def bloquear(self) -> None:
        self.clave = None
        self.params = {}
        self.entradas = []

    def guardar(self) -> None:
        if self.clave is None:
            raise ErrorBoveda("La bóveda está bloqueada")
        self.ruta.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        nonce = os.urandom(12)
        cab = json.dumps({"v": 1, **self.params, "nonce": base64.b64encode(nonce).decode()},
                         separators=(",", ":")).encode()
        cif = AESGCM(self.clave).encrypt(nonce, json.dumps({"entradas": self.entradas}).encode(), cab)
        tmp = self.ruta.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(cab + b"\n" + cif)
            f.flush()
            os.fsync(f.fileno())
        if self.ruta.exists():
            os.replace(self.ruta, self.ruta.with_suffix(".bak"))
        os.replace(tmp, self.ruta)

    def cambiar_maestra(self, actual: str, nueva: str) -> None:
        guardadas = self.entradas
        prueba = Boveda(self.ruta)
        prueba.abrir(actual)
        self._poner_clave(nueva)
        self.entradas = guardadas
        self.guardar()
        self.ruta.with_suffix(".bak").unlink(missing_ok=True)  # la copia vieja abría con la clave anterior

    def poner(self, e: dict) -> dict:
        e = {**e, "ts": time.time()}
        if not e.get("id"):
            e["id"] = uuid.uuid4().hex
            self.entradas.append(e)
        else:
            self.entradas = [e if x["id"] == e["id"] else x for x in self.entradas]
        self.guardar()
        return e

    def borrar(self, eid: str) -> None:
        self.entradas = [x for x in self.entradas if x["id"] != eid]
        self.guardar()

    def buscar(self, q: str = "") -> list[dict]:
        palabras = q.lower().split()
        def coincide(e):
            t = " ".join(str(e.get(k, "")) for k in ("titulo", "usuario", "url")).lower()
            return all(p in t for p in palabras)
        return sorted((e for e in self.entradas if coincide(e)), key=lambda e: e.get("titulo", "").lower())


def generar(largo: int = 24, simbolos: bool = True) -> str:
    abc = string.ascii_letters + string.digits + ("!@#$%^&*-_=+?" if simbolos else "")
    while True:
        c = "".join(secrets.choice(abc) for _ in range(largo))
        if any(x.isdigit() for x in c) and any(x.islower() for x in c) and any(x.isupper() for x in c):
            return c


def secreto_valido(s: str) -> bool:
    try:
        totp(s, 0)
        return True
    except ErrorBoveda:
        return False


def totp(secreto: str, ahora: float | None = None, paso: int = 30, digitos: int = 6) -> str:
    try:
        s = secreto.replace(" ", "").replace("-", "").upper()
        if not s:
            raise ValueError
        k = base64.b32decode(s + "=" * (-len(s) % 8))
    except (ValueError, binascii.Error) as e:
        raise ErrorBoveda("Secreto 2FA no válido") from e
    n = int((time.time() if ahora is None else ahora) // paso)
    h = hmac.new(k, struct.pack(">Q", n), hashlib.sha1).digest()
    o = h[-1] & 15
    return f"{(struct.unpack('>I', h[o:o + 4])[0] & 0x7fffffff) % 10 ** digitos:0{digitos}d}"
