"""NEURAL//NEXO: identidad y canal cifrado. Mismo protocolo por TCP y Bluetooth.

Conexión (lo tiene que implementar igual el cliente Android):
  1. Cada lado envía en claro  "NXO1" + clave efímera P-256 (65 B, sin comprimir)
     + 32 B aleatorios. I = quien marcó, R = quien atendió.
  2. th = SHA-256("NEXO-v1" + mensaje_I + mensaje_R)
     HKDF-SHA256(ECDH, salt=th, info="nexo keys", 64 B) → clave I→R, clave R→I
  3. Desde aquí, tramas: u32 BE largo + AES-256-GCM(nonce = 0000 + contador u64 BE
     propio de cada dirección). Texto de la trama: u32 BE largo_json + json + datos.
  4. Primera trama de cada lado:
     {"t":"hello","pub":SPKI DER b64,"sig":ECDSA-SHA256 DER b64 de
      "nexo-auth" + rol ("I"/"R") + th,"name","kind","port","v":1}
     El id de un dispositivo es sha256(SPKI DER) en hex, primeros 32 caracteres.
  5. Emparejar: ambos muestran SHA-256("nexo-sas" + th)[:4] mod 10^6 y la persona
     confirma en los dos que coincide (un intermediario no puede igualar th).
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import struct
from pathlib import Path

from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

MAGIC = b"NXO1"
LARGO_SALUDO = 4 + 65 + 32
MAX_TRAMA = 2 * 1024 * 1024
VERSION = 1


class CanalRoto(Exception):
    pass


def id_de(pub_der: bytes) -> str:
    return hashlib.sha256(pub_der).hexdigest()[:32]


class Identidad:
    """Clave P-256 del dispositivo: se crea una vez y es su identidad."""

    def __init__(self, ruta: Path):
        if ruta.exists():
            self.clave = serialization.load_pem_private_key(ruta.read_bytes(), None)
        else:
            self.clave = ec.generate_private_key(ec.SECP256R1())
            pem = self.clave.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption())
            fd = os.open(ruta, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(pem)
        self.pub_der = self.clave.public_key().public_bytes(serialization.Encoding.DER,
                                                            serialization.PublicFormat.SubjectPublicKeyInfo)
        self.id = id_de(self.pub_der)

    def firmar(self, datos: bytes) -> bytes:
        return self.clave.sign(datos, ec.ECDSA(hashes.SHA256()))


class Canal:
    def __init__(self, r: asyncio.StreamReader, w: asyncio.StreamWriter, k_tx: bytes, k_rx: bytes):
        self.r, self.w = r, w
        self.tx, self.rx = AESGCM(k_tx), AESGCM(k_rx)
        self.n_tx = self.n_rx = 0
        self.candado = asyncio.Lock()
        self.peer_id = ""
        self.hello: dict = {}
        self.sas = ""

    async def enviar(self, msg: dict, datos: bytes = b"") -> None:
        cuerpo = json.dumps(msg, separators=(",", ":")).encode()
        plano = struct.pack(">I", len(cuerpo)) + cuerpo + datos
        async with self.candado:  # el contador y el orden en el cable van juntos
            nonce = b"\0\0\0\0" + struct.pack(">Q", self.n_tx)
            self.n_tx += 1
            cifrado = self.tx.encrypt(nonce, plano, None)
            self.w.write(struct.pack(">I", len(cifrado)) + cifrado)
            await self.w.drain()

    async def recibir(self) -> tuple[dict, bytes]:
        try:
            largo = struct.unpack(">I", await self.r.readexactly(4))[0]
            if not 16 < largo <= MAX_TRAMA:
                raise CanalRoto("trama fuera de rango")
            cifrado = await self.r.readexactly(largo)
        except (asyncio.IncompleteReadError, ConnectionError, OSError) as e:
            raise CanalRoto(str(e)) from e
        nonce = b"\0\0\0\0" + struct.pack(">Q", self.n_rx)
        self.n_rx += 1
        try:
            plano = self.rx.decrypt(nonce, cifrado, None)
            largo_json = struct.unpack(">I", plano[:4])[0]
            if largo_json > len(plano) - 4:
                raise CanalRoto("json fuera de rango")
            msg = json.loads(plano[4:4 + largo_json])
        except (InvalidTag, ValueError, struct.error) as e:
            raise CanalRoto("trama inválida") from e
        if not isinstance(msg, dict):
            raise CanalRoto("trama inválida")
        return msg, plano[4 + largo_json:]

    def cerrar(self) -> None:
        try:
            self.w.close()
        except (OSError, RuntimeError):
            pass


async def saludo(r, w, ident: Identidad, iniciador: bool, hello: dict) -> Canal:
    """Handshake completo: al volver, canal.peer_id ya está demostrado por firma."""
    efimera = ec.generate_private_key(ec.SECP256R1())
    punto = efimera.public_key().public_bytes(serialization.Encoding.X962,
                                              serialization.PublicFormat.UncompressedPoint)
    mio = MAGIC + punto + os.urandom(32)
    w.write(mio)
    await w.drain()
    try:
        otro = await r.readexactly(LARGO_SALUDO)
    except (asyncio.IncompleteReadError, ConnectionError, OSError) as e:
        raise CanalRoto("saludo cortado") from e
    if otro[:4] != MAGIC:
        raise CanalRoto("no es NEXO")
    primero, segundo = (mio, otro) if iniciador else (otro, mio)
    th = hashlib.sha256(b"NEXO-v1" + primero + segundo).digest()
    try:
        pub_otro = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), otro[4:69])
    except ValueError as e:
        raise CanalRoto("clave efímera inválida") from e
    claves = HKDF(hashes.SHA256(), 64, salt=th, info=b"nexo keys").derive(efimera.exchange(ec.ECDH(), pub_otro))
    k_ir, k_ri = claves[:32], claves[32:]
    canal = Canal(r, w, k_ir if iniciador else k_ri, k_ri if iniciador else k_ir)

    rol, rol_otro = (b"I", b"R") if iniciador else (b"R", b"I")
    firma = ident.firmar(b"nexo-auth" + rol + th)
    await canal.enviar({**hello, "t": "hello", "v": VERSION, "pub": base64.b64encode(ident.pub_der).decode(),
                        "sig": base64.b64encode(firma).decode()})
    msg, _ = await canal.recibir()
    if msg.get("t") != "hello":
        raise CanalRoto("falta hello")
    try:
        pub_der = base64.b64decode(msg["pub"], validate=True)
        firma_otro = base64.b64decode(msg["sig"], validate=True)
        clave = serialization.load_der_public_key(pub_der)
        if not isinstance(clave, ec.EllipticCurvePublicKey) or not isinstance(clave.curve, ec.SECP256R1):
            raise ValueError("curva")
        clave.verify(firma_otro, b"nexo-auth" + rol_otro + th, ec.ECDSA(hashes.SHA256()))
    except (KeyError, TypeError, ValueError, InvalidSignature) as e:
        raise CanalRoto("identidad inválida") from e
    canal.peer_id = id_de(pub_der)
    canal.hello = msg
    canal.sas = f"{int.from_bytes(hashlib.sha256(b'nexo-sas' + th).digest()[:4], 'big') % 1_000_000:06d}"
    return canal
