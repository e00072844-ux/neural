"""Motor de NEURAL//DISCO: discos, particiones y salud vía UDisks2 (solo lectura).

Discos(on_change).discos() devuelve una lista de discos enteros, cada uno con sus
particiones ordenadas por posición y los huecos libres entre ellas. Un disco está
«protegido» si algo suyo sostiene el sistema (/, /boot, /home, swap activo…): la
interfaz lo enseña pero no deja tocarlo.

Montar, desmontar y expulsar se heredan de usb.Udisks. Las acciones peligrosas
(formatear, particionar) se añadirán aquí más adelante. Prueba rápida:  python3 disco_motor.py
"""
from __future__ import annotations

import os

from gi.repository import GLib

from usb import BUS, I_BLOCK, I_FS, I_PART, I_DRIVE, I_CRYPT, Udisks, _bytes, _limpio, _prop

I_TABLA = BUS + ".PartitionTable"
I_SWAP = BUS + ".Swapspace"
I_ATA = BUS + ".Drive.Ata"
I_NVME = BUS + ".NVMe.Controller"
I_LOOP = BUS + ".Loop"
I_JOB = BUS + ".Job"
I_MANAGER = BUS + ".Manager"
MANAGER = "/org/freedesktop/UDisks2/Manager"
SIN_LIMITE = GLib.MAXINT32  # ms: formatear con ceros un USB lento puede tardar mucho

# formatos que ofrece la ventana: clave → (tipo udisks, nombre, máx. letras de etiqueta, nota)
FORMATOS = {
    "vfat": ("vfat", "FAT32", 11, "Lo lee todo (Windows, Mac, TV, coche). Archivos de 4 GB como máximo."),
    "exfat": ("exfat", "exFAT", 15, "Para USB grandes: sin límite de 4 GB; Windows, Mac y móviles."),
    "ntfs": ("ntfs", "NTFS", 32, "Formato de Windows."),
    "ext4": ("ext4", "ext4", 16, "Linux: permisos y enlaces. Windows no lo lee."),
    "luks": ("ext4", "ext4 cifrado (LUKS2)", 16, "Linux, con contraseña al conectarlo. Sin ella no se lee nada."),
    "btrfs": ("btrfs", "btrfs", 256, "Linux, con instantáneas y compresión."),
}
# tipo de partición que corresponde a cada formato
TIPO_PART = {
    "gpt": {"vfat": "ebd0a0a2-b9e5-4433-87c0-68b6b72699c7", "exfat": "ebd0a0a2-b9e5-4433-87c0-68b6b72699c7",
            "ntfs": "ebd0a0a2-b9e5-4433-87c0-68b6b72699c7", "linux": "0fc63daf-8483-4772-8e79-3d69d8477de4"},
    "dos": {"vfat": "0x0c", "exfat": "0x07", "ntfs": "0x07", "linux": "0x83"},
}

MIB = 1024 * 1024
ESP_GPT = "c12a7328-f81f-11d2-ba4b-00a0c93ec93b"  # tipo de partición EFI (GPT)
ESP_MBR = "0xef"
# puntos de montaje que hacen intocable a un disco
CRITICOS = {"/", "/boot", "/boot/efi", "/efi", "/home", "/usr", "/var"}
# un hueco menor que esto es solo alineación, no espacio útil
HUECO_MIN = 4 * MIB


def tam_txt(n: int) -> str:
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1000 or u == "TB":
            return f"{n:.0f} {u}" if u == "B" else f"{n:.1f} {u}"
        n /= 1000
    return ""


def _swaps_activos() -> set[str]:
    try:
        with open("/proc/swaps") as f:
            return {l.split()[0] for l in f.readlines()[1:] if l.strip()}
    except OSError:
        return set()


def _uso(punto: str):
    try:
        st = os.statvfs(punto)
    except OSError:
        return None
    total = st.f_blocks * st.f_frsize
    libre = st.f_bavail * st.f_frsize
    return (total - libre, total) if total else None


class Discos(Udisks):
    """Conexión, avisos agrupados y montar/desmontar/expulsar vienen de usb.Udisks."""

    # ---------- lectura ----------
    def _volumen(self, obj, swaps: set[str]) -> dict:
        """Lo que hay dentro de un bloque: sistema de archivos, LUKS, swap…"""
        blk = obj.get_interface(I_BLOCK)
        fs = obj.get_interface(I_FS)
        crypt = obj.get_interface(I_CRYPT)
        dev = _bytes(blk.get_cached_property("Device"))
        v = {
            "ruta": obj.get_object_path(),
            "dev": dev,
            "tipo": _prop(blk, "IdType", "") or "",
            "version": _prop(blk, "IdVersion", "") or "",
            "uso_id": _prop(blk, "IdUsage", "") or "",
            "etiqueta": _prop(blk, "IdLabel", "") or "",
            "uuid": _prop(blk, "IdUUID", "") or "",
            "puntos": [_bytes(p) for p in (_prop(fs, "MountPoints", []) if fs else [])],
            "swap": dev in swaps or (obj.get_interface(I_SWAP) is not None
                                     and _prop(obj.get_interface(I_SWAP), "Active", False)),
            "cifrado": crypt is not None,
            "abierto": None,  # si es LUKS desbloqueado: el volumen de dentro
            "uso": None,      # (usado, total) en bytes si está montado
        }
        if crypt is not None:
            claro = _prop(crypt, "CleartextDevice", "/")
            cobj = self.om.get_object(claro) if claro != "/" else None
            if cobj is not None and cobj.get_interface(I_BLOCK) is not None:
                v["abierto"] = self._volumen(cobj, swaps)
        if v["puntos"]:
            v["uso"] = _uso(v["puntos"][0])
        return v

    @staticmethod
    def _critico(v: dict) -> bool:
        if v["swap"] or any(p in CRITICOS for p in v["puntos"]):
            return True
        return bool(v["abierto"]) and Discos._critico(v["abierto"])

    def discos(self) -> list[dict]:
        swaps = _swaps_activos()
        enteros: dict[str, dict] = {}
        partes: list[tuple[str, object]] = []

        for obj in self.om.get_objects():
            blk = obj.get_interface(I_BLOCK)
            if blk is None:
                continue
            if obj.get_interface(I_PART) is not None:
                partes.append((_prop(obj.get_interface(I_PART), "Table", "/"), obj))
                continue
            drive_ruta = _prop(blk, "Drive", "/")
            loop = obj.get_interface(I_LOOP)
            # disco entero = tiene Drive (o es un loop con algo dentro); fuera dm-*, zram, etc.
            if drive_ruta == "/" and loop is None:
                continue
            if loop is not None and _prop(blk, "Size", 0) == 0:
                continue
            drive = self._iface(drive_ruta, I_DRIVE)
            tabla = obj.get_interface(I_TABLA)
            d = {
                "ruta": obj.get_object_path(),
                "dev": _bytes(blk.get_cached_property("Device")),
                "tam": _prop(blk, "Size", 0),
                "modelo": " ".join(x for x in (_prop(drive, "Vendor", ""), _prop(drive, "Model", "")) if x).strip(),
                "serie": _prop(drive, "Serial", "") or "",
                "bus": _prop(drive, "ConnectionBus", "") or ("loop" if loop else "")
                       or ("nvme" if self._iface(drive_ruta, I_NVME) else ""),
                "extraible": bool(_prop(drive, "Removable", False) or _prop(drive, "MediaRemovable", False)),
                "rotacion": _prop(drive, "RotationRate", -1),  # 0 = SSD
                "drive": drive_ruta,
                "apagable": bool(_prop(drive, "CanPowerOff", False)),
                "solo_lectura": bool(_prop(blk, "ReadOnly", False)),
                "tabla": _prop(tabla, "Type", "") if tabla else "",  # gpt / dos / ""
                "particiones": [],
                "huecos": [],
                "contenido": None,  # sin tabla: sistema de archivos directo en el disco
                "salud": self._salud(drive_ruta),
                "protegido": False,
                "motivo": "",
                "aviso": "",  # editable pero con algo delicado (p. ej. otro sistema operativo)
            }
            if tabla is None and (_prop(blk, "IdType", "") or obj.get_interface(I_CRYPT)):
                d["contenido"] = self._volumen(obj, swaps)
            enteros[d["ruta"]] = d

        for tabla_ruta, obj in partes:
            d = enteros.get(tabla_ruta)
            if d is None:
                continue
            p = obj.get_interface(I_PART)
            v = self._volumen(obj, swaps)
            v.update({
                "numero": _prop(p, "Number", 0),
                "inicio": _prop(p, "Offset", 0),
                "tam": _prop(p, "Size", 0),
                "nombre": _prop(p, "Name", "") or "",       # nombre GPT
                "tipo_part": _prop(p, "Type", "") or "",    # GUID o código MBR
                "extendida": bool(_prop(p, "IsContainer", False)),
                "logica": bool(_prop(p, "IsContained", False)),
            })
            d["particiones"].append(v)

        out = []
        for d in enteros.values():
            d["particiones"].sort(key=lambda v: v["inicio"])
            d["huecos"] = self._huecos(d)
            vols = d["particiones"] + ([d["contenido"]] if d["contenido"] else [])
            crit = [v for v in vols if self._critico(v)]
            if crit:
                d["protegido"] = True
                d["motivo"] = "Disco del sistema (" + ", ".join(
                    os.path.basename(v["dev"]) for v in crit) + ")"
            elif d["solo_lectura"]:
                d["protegido"] = True
                d["motivo"] = "Solo lectura"
            if any(v["tipo_part"].lower() in (ESP_GPT, ESP_MBR) for v in d["particiones"]):
                d["aviso"] = "Tiene partición EFI: probablemente lleva un sistema operativo"
            out.append(d)
        # primero los internos, luego USB/extraíbles
        out.sort(key=lambda d: (d["bus"] == "usb" or d["extraible"], d["dev"]))
        return out


    # ---------- formatear (asíncrono; fin(ok, texto)) ----------
    def formatos(self) -> list[str]:
        """Claves de FORMATOS que este sistema sabe crear (pregunta a udisks qué mkfs hay)."""
        man = self._iface(MANAGER, I_MANAGER)
        out = []
        for clave, (tipo, *_r) in FORMATOS.items():
            try:
                ok, _falta = man.call_sync("CanFormat", GLib.Variant("(s)", (tipo,)),
                                           0, 5000, None).unpack()[0] if man else (False, "")
            except GLib.Error:
                ok = False
            if ok:
                out.append(clave)
        return out

    def _disco_de(self, ruta: str) -> dict | None:
        """El disco (recién leído) al que pertenece un bloque: el propio disco o una partición suya."""
        for d in self.discos():
            if d["ruta"] == ruta or any(v["ruta"] == ruta for v in d["particiones"]):
                return d
        return None

    def _seguro(self, ruta: str):
        """Último cerrojo, aquí y no solo en la ventana: nada protegido se formatea."""
        d = self._disco_de(ruta)
        if d is None:
            return None, "El disco ya no está"
        if d["protegido"]:
            return None, f"Bloqueado: {d['motivo']}"
        return d, ""

    def _soltar(self, rutas: set[str], fin):
        """Desmonta y cierra (LUKS) todo lo que cuelga de esos bloques, en orden."""
        pasos = []
        for obj in self.om.get_objects():
            blk = obj.get_interface(I_BLOCK)
            if blk is None:
                continue
            ruta = obj.get_object_path()
            if ruta not in rutas and _prop(blk, "CryptoBackingDevice", "/") not in rutas:
                continue
            fs = obj.get_interface(I_FS)
            if fs is not None and _prop(fs, "MountPoints", []):
                pasos.append((0, ruta, I_FS, "Unmount"))
            crypt = obj.get_interface(I_CRYPT)
            if crypt is not None and _prop(crypt, "CleartextDevice", "/") != "/":
                pasos.append((1, ruta, I_CRYPT, "Lock"))
        pasos.sort()

        def siguiente(ok=True, texto=""):
            if not ok:
                fin(False, texto)
            elif not pasos:
                fin(True, "")
            else:
                _o, r, iface, metodo = pasos.pop(0)
                self._llamar(r, iface, metodo, _opts(), siguiente)
        siguiente()

    def _llamar_largo(self, ruta, iface, metodo, params, fin):
        proxy = self._iface(ruta, iface)
        if proxy is None:
            fin(False, "El dispositivo ya no está")
            return

        def hecho(p, res):
            try:
                r = p.call_finish(res)
                fin(True, r.unpack()[0] if r is not None and r.n_children() else "")
            except GLib.Error as e:
                fin(False, _limpio(e.message))
        proxy.call(metodo, params, 0, SIN_LIMITE, None, hecho)

    def formatear_disco(self, ruta: str, tabla: str, formato: str, etiqueta: str,
                        clave: str, ceros: bool, fin):
        """Borra el disco entero: tabla nueva (gpt/dos) + una partición que lo ocupa todo."""
        d, motivo = self._seguro(ruta)
        if d is None or d["ruta"] != ruta:
            fin(False, motivo or "Eso no es un disco entero")
            return
        if tabla not in ("gpt", "dos"):
            fin(False, "Tabla desconocida")
            return
        solo_tabla = formato == "vacia"
        tipo = "" if solo_tabla else FORMATOS[formato][0]
        familia = tipo if tipo in ("vfat", "exfat", "ntfs") else "linux"
        # 1 MiB al principio (alineación) y, en GPT, 1 MiB al final (copia de la tabla)
        tam = (d["tam"] - MIB - (MIB if tabla == "gpt" else 0)) // MIB * MIB
        rutas = {ruta} | {v["ruta"] for v in d["particiones"]}

        def tras_soltar(ok, texto):
            if not ok:
                fin(False, texto)
                return
            extra = {"erase": "zero"} if ceros else {}
            self._llamar_largo(ruta, I_BLOCK, "Format", _opts_v(tabla, **extra), tras_tabla)

        def tras_tabla(ok, texto):
            if not ok or solo_tabla:
                fin(ok, "" if ok else texto)
                return
            # la tabla nueva tarda un instante en aparecer en udisks: esperar hasta 10 s
            intentos = [0]

            def esperar():
                if self._iface(ruta, I_TABLA) is not None:
                    crear()
                    return GLib.SOURCE_REMOVE
                intentos[0] += 1
                if intentos[0] > 40:
                    fin(False, "La tabla nueva no aparece; desenchufa y vuelve a enchufar el disco")
                    return GLib.SOURCE_REMOVE
                return GLib.SOURCE_CONTINUE
            GLib.timeout_add(250, esperar)

        def crear():
            params = GLib.Variant("(ttssa{sv}sa{sv})", (
                MIB, tam, TIPO_PART[tabla][familia], "", _dict(),
                tipo, _dict_fmt(formato, etiqueta, clave)))
            self._llamar_largo(ruta, I_TABLA, "CreatePartitionAndFormat", params,
                               lambda ok, t: fin(ok, "" if ok else t))

        self._soltar(rutas, tras_soltar)

    def formatear_parte(self, ruta: str, formato: str, etiqueta: str, clave: str, ceros: bool, fin):
        """Borra una partición y le pone un sistema de archivos nuevo (el resto del disco no se toca)."""
        d, motivo = self._seguro(ruta)
        if d is None or d["ruta"] == ruta:
            fin(False, motivo or "Eso es un disco entero, no una partición")
            return
        tipo = FORMATOS[formato][0]
        opts = _dict_fmt(formato, etiqueta, clave, ceros)
        opts["update-partition-type"] = GLib.Variant("b", True)

        def tras_soltar(ok, texto):
            if not ok:
                fin(False, texto)
                return
            self._llamar_largo(ruta, I_BLOCK, "Format", GLib.Variant("(sa{sv})", (tipo, opts)), fin)
        self._soltar({ruta}, tras_soltar)

    # ---------- particionar (asíncrono; fin(ok, texto)) ----------
    def crear_parte(self, ruta_disco: str, inicio: int, tam: int, formato: str | None,
                    etiqueta: str, clave: str, fin):
        """Partición nueva en un hueco libre. formato None = sin formato."""
        d, motivo = self._seguro(ruta_disco)
        if d is None or d["ruta"] != ruta_disco:
            fin(False, motivo or "Eso no es un disco entero")
            return
        if not d["tabla"]:
            fin(False, "El disco no tiene tabla de particiones")
            return
        if d["tabla"] == "dos" and sum(1 for v in d["particiones"] if not v["logica"]) >= 4:
            fin(False, "MBR solo admite 4 particiones: formatea el disco en GPT para tener más")
            return
        # alinear a 1 MiB y no salirse del hueco
        ini = -(-inicio // MIB) * MIB
        hueco = next(((i, t) for i, t in d["huecos"] if i <= inicio < i + t), None)
        if hueco is None:
            fin(False, "Ese espacio ya no está libre")
            return
        tope = hueco[0] + hueco[1]
        tam = min(tam, tope - ini) // MIB * MIB
        if tam < MIB:
            fin(False, "Tamaño demasiado pequeño")
            return
        tabla = d["tabla"]
        if formato is None:
            params = GLib.Variant("(ttssa{sv})", (ini, tam, TIPO_PART[tabla]["linux"], "", _dict()))
            self._llamar_largo(ruta_disco, I_TABLA, "CreatePartition", params,
                               lambda ok, t: fin(ok, "" if ok else t))
            return
        tipo = FORMATOS[formato][0]
        familia = tipo if tipo in ("vfat", "exfat", "ntfs") else "linux"
        params = GLib.Variant("(ttssa{sv}sa{sv})", (
            ini, tam, TIPO_PART[tabla][familia], "", _dict(), tipo, _dict_fmt(formato, etiqueta, clave)))
        self._llamar_largo(ruta_disco, I_TABLA, "CreatePartitionAndFormat", params,
                           lambda ok, t: fin(ok, "" if ok else t))

    def borrar_parte(self, ruta: str, fin):
        d, motivo = self._seguro(ruta)
        if d is None or d["ruta"] == ruta:
            fin(False, motivo or "Eso es un disco entero, no una partición")
            return

        def tras_soltar(ok, texto):
            if not ok:
                fin(False, texto)
                return
            self._llamar_largo(ruta, I_PART, "Delete", _opts(), fin)
        self._soltar({ruta}, tras_soltar)

    def etiquetar(self, ruta: str, etiqueta: str, fin):
        """Cambia la etiqueta del sistema de archivos de una partición (o del de dentro de su LUKS)."""
        d, motivo = self._seguro(ruta)
        if d is None:
            fin(False, motivo)
            return
        destino = ruta
        crypt = self._iface(ruta, I_CRYPT)
        if crypt is not None:
            destino = _prop(crypt, "CleartextDevice", "/")
            if destino == "/":
                fin(False, "Está cifrada y cerrada: ábrela primero")
                return
        blk = self._iface(destino, I_BLOCK)
        if (_prop(blk, "IdType", "") or "") == "vfat":
            etiqueta = etiqueta.upper()
        self._llamar_largo(destino, I_FS, "SetLabel", GLib.Variant("(sa{sv})", (etiqueta, _dict())), fin)

    def progreso(self, ruta_disco: str) -> float | None:
        """Avance (0-1) del trabajo de udisks sobre ese disco o sus particiones, si informa."""
        d = next((x for x in self.discos() if x["ruta"] == ruta_disco), None)
        rutas = {ruta_disco} | ({v["ruta"] for v in d["particiones"]} if d else set())
        for obj in self.om.get_objects():
            job = obj.get_interface(I_JOB)
            if job is None or not set(_prop(job, "Objects", [])) & rutas:
                continue
            if _prop(job, "ProgressValid", False):
                return _prop(job, "Progress", 0.0)
        return None

    @staticmethod
    def _huecos(d: dict) -> list[tuple[int, int]]:
        """Espacio sin particionar (inicio, tamaño) entre particiones de primer nivel."""
        if not d["tabla"]:
            return []
        fin_util = d["tam"] - (MIB if d["tabla"] == "gpt" else 0)  # GPT copia su tabla al final
        pos = MIB  # la primera partición suele empezar en 1 MiB
        out = []
        for v in d["particiones"]:
            if v["logica"]:
                continue
            if v["inicio"] - pos >= HUECO_MIN:
                out.append((pos, v["inicio"] - pos))
            pos = max(pos, v["inicio"] + v["tam"])
        if fin_util - pos >= HUECO_MIN:
            out.append((pos, fin_util - pos))
        return out

    def _salud(self, drive_ruta: str) -> dict | None:
        ata = self._iface(drive_ruta, I_ATA)
        if ata is not None and _prop(ata, "SmartSupported", False):
            k = _prop(ata, "SmartTemperature", 0.0)
            return {
                "tipo": "ata",
                "ok": not _prop(ata, "SmartFailing", False),
                "temp_c": round(k - 273.15) if k else None,
                "horas": (_prop(ata, "SmartPowerOnSeconds", 0) or 0) // 3600 or None,
                "aviso": "SMART avisa de fallo" if _prop(ata, "SmartFailing", False) else "",
            }
        nv = self._iface(drive_ruta, I_NVME)
        if nv is not None:
            k = _prop(nv, "SmartTemperature", 0)
            warn = _prop(nv, "SmartCriticalWarning", []) or []
            return {
                "tipo": "nvme",
                "ok": not warn,
                "temp_c": round(k - 273.15) if k else None,
                "horas": _prop(nv, "SmartPowerOnHours", 0) or None,
                "aviso": ", ".join(warn),
            }
        return None


def _dict(**extra) -> dict:
    d = {"auth.no_user_interaction": GLib.Variant("b", False)}
    d.update(extra)
    return d


def _opts() -> GLib.Variant:
    return GLib.Variant("(a{sv})", (_dict(),))


def _opts_v(tipo: str, **cadenas) -> GLib.Variant:
    return GLib.Variant("(sa{sv})", (tipo, _dict(**{k: GLib.Variant("s", v) for k, v in cadenas.items()})))


def _dict_fmt(formato: str, etiqueta: str, clave: str, ceros: bool = False) -> dict:
    """Opciones de Format para udisks: etiqueta, dueño, cifrado, borrado con ceros."""
    d = _dict()
    if etiqueta:
        d["label"] = GLib.Variant("s", etiqueta.upper() if formato == "vfat" else etiqueta)
    if FORMATOS[formato][0] in ("ext4", "btrfs"):
        d["take-ownership"] = GLib.Variant("b", True)  # que la raíz sea tuya y no de root
    if formato == "luks":
        d["encrypt.passphrase"] = GLib.Variant("s", clave)
        d["encrypt.type"] = GLib.Variant("s", "luks2")
    if ceros:
        d["erase"] = GLib.Variant("s", "zero")
    return d


# ---------- prueba por terminal ----------
def _linea_vol(v: dict, sangria: str) -> str:
    partes = [os.path.basename(v["dev"])]
    if "tam" in v:
        partes.append(tam_txt(v["tam"]))
    partes.append(v["tipo"] or "—")
    if v["etiqueta"]:
        partes.append(f'«{v["etiqueta"]}»')
    if v.get("extendida"):
        partes.append("[extendida]")
    if v["puntos"]:
        partes.append("→ " + ", ".join(v["puntos"]))
    if v["uso"]:
        u, t = v["uso"]
        partes.append(f"({u * 100 // t}% usado)")
    if v["swap"]:
        partes.append("[swap activo]")
    s = sangria + "  ".join(partes)
    if v["abierto"]:
        s += "\n" + _linea_vol(v["abierto"], sangria + "   └ ")
    return s


def main():
    for d in Discos().discos():
        cab = f'{d["dev"]}  {tam_txt(d["tam"])}  {d["modelo"] or "?"}  bus={d["bus"] or "?"}  tabla={d["tabla"] or "ninguna"}'
        if d["salud"]:
            s = d["salud"]
            cab += f'  salud={"OK" if s["ok"] else "¡MAL!"} {s["temp_c"] or "?"}°C {s["horas"] or "?"}h'
        print(cab)
        print("   " + ("🔒 " + d["motivo"] if d["protegido"] else "✎ editable"))
        if d["aviso"] and not d["protegido"]:
            print("   ⚠ " + d["aviso"])
        if d["contenido"]:
            print(_linea_vol(d["contenido"], "   "))
        for v in d["particiones"]:
            print(_linea_vol(v, "   " + ("   " if v["logica"] else "")))
        for ini, t in d["huecos"]:
            print(f"   (libre: {tam_txt(t)} en {ini // MIB} MiB)")
        print()


if __name__ == "__main__":
    main()
