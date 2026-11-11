"""Motor de NEURAL//VM: máquinas virtuales QEMU/KVM sin libvirt.

Cada VM es una carpeta con un vm.json (sus características) y sus discos.
Las carpetas conocidas se guardan en ~/.config/neural-vm/vms.json; además
se recogen solas las de ~/vms/*/vm.json.

Una VM está encendida si hay un qemu-system-x86_64 cuyo directorio de
trabajo es su carpeta (así también se ven las arrancadas a mano).
El control (apagar, pausa, captura, USB en caliente) va por QMP en
<carpeta>/qmp.sock; la pantalla SPICE por <carpeta>/spice.sock.
Redes de NetworkManager, USB del equipo y hugepages: vm_red.py.
No hay GTK aquí: se puede probar desde la terminal (python3 vm_motor.py -c).
"""
from __future__ import annotations

import copy
import json
import os
import random
import re
import shutil
import signal
import socket
import subprocess
import tarfile
import tempfile
import threading
import zlib
import time
from pathlib import Path

import vm_red as R

QEMU = "qemu-system-x86_64"
QEMU_IMG = "qemu-img"
VIRTIOFSD = next((p for p in ("/usr/lib/virtiofsd", "/usr/libexec/virtiofsd") if Path(p).exists()),
                 shutil.which("virtiofsd") or "/usr/lib/virtiofsd")
VISOR = "remote-viewer"
RENDER = next((str(p) for p in sorted(Path("/dev/dri").glob("renderD*"))), "/dev/dri/renderD128") \
    if Path("/dev/dri").is_dir() else "/dev/dri/renderD128"
# Firmware UEFI (código + plantilla de variables, del mismo tamaño): el primero que exista.
# LFS: el que trae QEMU. Arch/CachyOS: paquete edk2-ovmf (sin Secure Boot).
_UEFI_PARES = [
    (Path("/usr/share/qemu/edk2-x86_64-code.fd"), Path("/usr/share/qemu/edk2-i386-vars.fd")),
    (Path("/usr/share/edk2/x64/OVMF_CODE.4m.fd"), Path("/usr/share/edk2/x64/OVMF_VARS.4m.fd")),
    (Path("/usr/share/edk2-ovmf/x64/OVMF_CODE.fd"), Path("/usr/share/edk2-ovmf/x64/OVMF_VARS.fd")),
]
UEFI_CODE, UEFI_VARS = next(((c, v) for c, v in _UEFI_PARES if c.exists() and v.exists()), _UEFI_PARES[0])

VMS_DIR = Path.home() / "vms"
ISOS_DIR = Path.home() / "ISOs"
REGISTRO = Path.home() / ".config/neural-vm/vms.json"

TIPOS = {"linux": "LINUX", "windows": "WINDOWS", "macos": "MACOS", "otro": "OTRO"}

# ---- catálogos de opciones (clave en vm.json → nombre visible)
PANTALLAS = {"gtk": "VENTANA QEMU", "spice": "VENTANA SPICE", "sin_ventana": "SIN VENTANA (SPICE)"}
GRAFICOS = {"auto": "AUTOMÁTICO", "3d": "3D · VIRGL (LINUX)", "virtio": "VIRTIO", "std": "VGA ESTÁNDAR",
            "qxl": "QXL (SPICE)", "vmware": "VMWARE SVGA"}
AUDIOS = {"auto": "AUTOMÁTICO", "hda": "INTEL HDA", "virtio": "VIRTIO", "ninguno": "SIN SONIDO"}
MODOS_RED = {"nat": "NAT", "interna": "RED INTERNA", "puente": "PUENTE (CABLE)",
             "aislada": "SOLO ENTRE VMS", "ninguna": "DESCONECTADA"}
MODELOS_RED = {"auto": "AUTO", "virtio": "VIRTIO", "e1000e": "INTEL E1000E", "rtl8139": "REALTEK 8139"}
BUSES = {"auto": "AUTOMÁTICO", "virtio": "VIRTIO", "sata": "SATA", "nvme": "NVME"}
CACHES = {"writeback": "WRITEBACK (SEGURA)", "none": "DIRECTA · io_uring", "unsafe": "SIN FLUSH (RÁPIDA, ARRIESGADA)"}
CPU_MODELOS = {"host": "HOST (TODA LA CPU)", "oculto": "HOST OCULTO (sin «hipervisor»)",
               "compatible": "COMPATIBLE (genérica)"}
_AMD = "AuthenticAMD" in (Path("/proc/cpuinfo").read_text(errors="ignore")[:4000] if Path("/proc/cpuinfo").exists() else "")
CPU_COMPATIBLE = "EPYC" if _AMD else "Skylake-Client"
# Hyper-V para Windows: mucho menos trabajo de la CPU con KVM
HYPERV = ("hv_relaxed,hv_vapic,hv_spinlocks=0x1fff,hv_time,hv_vpindex,hv_synic,hv_stimer,"
          "hv_frequencies,hv_tlbflush,hv_ipi")

DEFECTO = {
    "nombre": "nueva-vm",
    "tipo": "linux",
    "ram_gb": 4,
    "cpus": 4,
    "disco": "disco.qcow2",
    "disco_fisico": False,  # True: "disco" es un disco entero del equipo (USB), sin qcow2
    "iso": "",
    "arrancar_iso": False,
    "uefi": True,
    "ssh_puerto": 0,     # antiguo: ahora va en "puertos" (se migra solo)
    "extra": [],         # argumentos de QEMU añadidos al final
    # pantalla
    "pantalla": "gtk",   # gtk | spice | sin_ventana
    "graficos": "auto",
    "pantallas": 1,      # monitores (virtio / 3d / qxl)
    "vram_mb": 0,        # VGA estándar, QXL y VMware (0 = auto: 64, macOS 256)
    "audio": "auto",
    # red: tarjetas y puertos reenviados (estos van por la primera tarjeta NAT)
    "redes": [{"modo": "nat", "modelo": "auto", "mac": "", "grupo": 1}],
    "puertos": [],       # [{"proto": "tcp", "host": 2222, "vm": 22, "lan": false}]
    "arrancar_red": False,  # PXE por la primera tarjeta
    # discos
    "bus_disco": "auto",
    "cache_disco": "writeback",
    "discos_extra": [],  # [{"archivo": "extra-1.qcow2", "bus": "virtio"}]
    "drivers_iso": "",   # segundo CD (p. ej. virtio-win.iso)
    # compartir
    "carpetas": [],      # [{"ruta": "/home/…", "etiqueta": "compartida", "solo_lectura": false}]
    "usb": [],           # [{"vid": "046d", "pid": "c52b", "nombre": "…"}] conectados al arrancar
    # rendimiento
    "cpu_modelo": "host",
    "nucleos_fijos": "",  # "4-7" o "2,3,6,7": cada CPU de la VM en un núcleo del equipo
    "hugepages": False,
}


def lista_nucleos(texto: str) -> list[int]:
    """"4-7,10" → [4, 5, 6, 7, 10] (solo núcleos que existen)."""
    res = []
    for parte in (texto or "").replace(" ", "").split(","):
        if not parte:
            continue
        a, _, b = parte.partition("-")
        try:
            ini, fin = int(a), int(b or a)
        except ValueError:
            raise ValueError(f"núcleos mal escritos: «{parte}»") from None
        res += [n for n in range(ini, fin + 1) if n not in res]
    validos = os.sched_getaffinity(0)
    return [n for n in res if n in validos]


def mac_nueva() -> str:
    return "52:54:00:" + ":".join(f"{random.randint(0, 255):02x}" for _ in range(3))


def buscar_virtio_win() -> str:
    """La ISO de drivers virtio-win más nueva de ~/ISOs (o "")."""
    isos = sorted(ISOS_DIR.glob("virtio-win*.iso"), key=lambda p: p.stat().st_mtime, reverse=True) \
        if ISOS_DIR.is_dir() else []
    return str(isos[0]) if isos else ""


# CPU que macOS acepta (Haswell disfrazado de Intel), copiada de quickemu
CPU_MAC = ("Haswell-v2,vendor=GenuineIntel,-pdpe1gb,+avx,+sse,+sse2,+ssse3,vmware-cpuid-freq=on,+avx2,"
           "+sse4.2,+abm,+adx,+aes,+apic,+arat,+bmi1,+bmi2,+clflush,+cmov,+cx8,+cx16,+de,+erms,+f16c,+fma,"
           "+fsgsbase,+fxsr,+invpcid,+invtsc,+lahf-lm,+lm,+mca,+mce,+mmx,+movbe,+msr,+mtrr,+nx,+pae,+pat,"
           "-pcid,+pge,+pse,+popcnt,+pse36,+rdrand,+rdtscp,+sep,+smep,+syscall,+tsc,+vaes,+vpclmulqdq,"
           "+xgetbv1,+xsave,+xsaveopt,+tsc-deadline")
OSK = "ourhardworkbythesewordsguardedpleasedontsteal(c)AppleComputerInc"


def slug(texto: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", texto.lower()).strip("-")
    return s or "vm"


# ------------------------------------------------------------------ registro
def _leer_registro() -> list[str]:
    try:
        return list(json.loads(REGISTRO.read_text()).get("carpetas", []))
    except (OSError, ValueError):
        return []


def _guardar_registro(carpetas: list[str]) -> None:
    REGISTRO.parent.mkdir(parents=True, exist_ok=True)
    tmp = REGISTRO.with_suffix(".tmp")
    tmp.write_text(json.dumps({"carpetas": carpetas}, indent=2, ensure_ascii=False))
    tmp.replace(REGISTRO)


def registrar(carpeta: Path) -> None:
    c = str(Path(carpeta).resolve())
    lista = _leer_registro()
    if c not in lista:
        _guardar_registro(lista + [c])


def olvidar(carpeta: Path) -> None:
    c = str(Path(carpeta).resolve())
    _guardar_registro([x for x in _leer_registro() if x != c])


def listar() -> list["VM"]:
    vistas, vms = set(), []
    candidatas = [Path(c) for c in _leer_registro()]
    if VMS_DIR.is_dir():
        candidatas += sorted(p.parent for p in VMS_DIR.glob("*/vm.json"))
    for c in candidatas:
        c = c.resolve()
        if c in vistas or not (c / "vm.json").is_file():
            continue
        vistas.add(c)
        try:
            vms.append(VM(c))
        except (OSError, ValueError):
            continue
    return sorted(vms, key=lambda v: v.nombre.lower())


def procesos_qemu() -> dict[Path, int]:
    """{carpeta de trabajo: pid} de cada QEMU del usuario."""
    res = {}
    for p in Path("/proc").iterdir():
        if not p.name.isdigit():
            continue
        try:
            if os.readlink(p / "exe").endswith("/" + QEMU):
                res[Path(os.readlink(p / "cwd"))] = int(p.name)
        except OSError:
            continue
    return res


# ------------------------------------------------------------------ QMP
class ErrorQMP(Exception):
    pass


def qmp(sock: Path, comando: str, argumentos: dict | None = None, espera: float = 5.0):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(espera)
    try:
        s.connect(str(sock))
        f = s.makefile("rwb")

        def leer():
            while True:
                linea = f.readline()
                if not linea:
                    raise ErrorQMP("QEMU cerró la conexión")
                m = json.loads(linea)
                if "event" in m:
                    continue
                return m

        def enviar(cmd, args=None):
            m = {"execute": cmd}
            if args:
                m["arguments"] = args
            f.write(json.dumps(m).encode() + b"\n")
            f.flush()
            r = leer()
            if "error" in r:
                raise ErrorQMP(r["error"].get("desc", str(r["error"])))
            return r.get("return")

        leer()  # saludo
        enviar("qmp_capabilities")
        return enviar(comando, argumentos)
    except (OSError, ValueError) as e:
        raise ErrorQMP(str(e)) from e
    finally:
        s.close()


# ------------------------------------------------------------------ VM
class VM:
    def __init__(self, carpeta: Path):
        self.dir = Path(carpeta).resolve()
        self.cfg = copy.deepcopy(DEFECTO)
        self.cfg.update(json.loads((self.dir / "vm.json").read_text()))
        # antes el reenvío de SSH era un solo número
        if int(self.cfg.get("ssh_puerto") or 0) and not self.cfg.get("puertos"):
            self.cfg["puertos"] = [{"proto": "tcp", "host": int(self.cfg["ssh_puerto"]), "vm": 22, "lan": False}]
        self.cfg["ssh_puerto"] = 0
        if not isinstance(self.cfg.get("redes"), list):
            self.cfg["redes"] = copy.deepcopy(DEFECTO["redes"])

    # ---- datos
    @property
    def nombre(self) -> str:
        return self.cfg["nombre"]

    @property
    def tipo(self) -> str:
        return self.cfg.get("tipo", "linux")

    @property
    def sock(self) -> Path:
        return self.dir / "qmp.sock"

    @property
    def spice_sock(self) -> Path:
        return self.dir / "spice.sock"

    @property
    def spice(self) -> bool:
        return self.cfg.get("pantalla") in ("spice", "sin_ventana")

    @property
    def log(self) -> Path:
        return self.dir / "neural-vm.log"

    def ruta(self, nombre: str) -> Path:
        p = Path(os.path.expanduser(nombre))
        return p if p.is_absolute() else self.dir / p

    def guardar(self) -> None:
        tmp = self.dir / "vm.json.tmp"
        tmp.write_text(json.dumps(self.cfg, indent=2, ensure_ascii=False) + "\n")
        tmp.replace(self.dir / "vm.json")

    # ---- estado
    def pid(self, procesos: dict[Path, int] | None = None) -> int | None:
        return (procesos if procesos is not None else procesos_qemu()).get(self.dir)

    def estado(self, procesos: dict[Path, int] | None = None) -> str:
        """apagada | encendida | pausada | ajena (encendida sin QMP: arrancada a mano)."""
        if self.pid(procesos) is None:
            return "apagada"
        if not self.sock.exists():
            return "ajena"
        try:
            st = qmp(self.sock, "query-status", espera=1.5)
            return "encendida" if st.get("running") else "pausada"
        except ErrorQMP:
            return "ajena"

    def cambios_pendientes(self) -> bool:
        """¿Está encendida con una configuración distinta de la guardada?
        QEMU fija el hardware al arrancar: solo un proceso nuevo aplica los cambios."""
        pid = self.pid()
        if pid is None:
            return False
        try:
            en_marcha = Path(f"/proc/{pid}/cmdline").read_bytes().decode().split("\0")[:-1]
        except OSError:
            return False
        return en_marcha != self.comando()

    # ---- opciones resueltas (lo "auto" según el sistema)
    def graficos(self) -> str:
        if self.tipo == "macos":
            return "vmware"
        g = self.cfg.get("graficos", "auto")
        if g == "auto":
            return "virtio" if self.tipo == "linux" else "std"
        return g if g in GRAFICOS else "std"

    def vram(self) -> int:
        v = int(self.cfg.get("vram_mb") or 0)
        return max(16, min(512, v)) if v else (256 if self.tipo == "macos" else 64)

    def audio(self) -> str:
        a = self.cfg.get("audio", "auto")
        if a == "auto":
            return "hda" if self.tipo == "windows" else "virtio"
        return a

    @property
    def fisico(self) -> bool:
        return bool(self.cfg.get("disco_fisico"))

    def bus_disco(self) -> str:
        b = self.cfg.get("bus_disco", "auto")
        if b == "auto":
            # un sistema ya instalado trae el driver de donde se instaló: casi siempre NVMe o SATA
            if self.fisico:
                return "nvme"
            return "sata" if self.tipo == "windows" else "virtio"
        return b

    def modelo_red(self, r: dict) -> str:
        m = r.get("modelo", "auto")
        if m == "auto":
            return "e1000e" if self.tipo == "windows" else "virtio-net-pci"
        return {"virtio": "virtio-net-pci"}.get(m, m)

    def _cache(self) -> str:
        return {"none": "cache=none,aio=io_uring", "unsafe": "cache=unsafe"}.get(
            self.cfg.get("cache_disco"), "cache=writeback")

    def fs_sock(self, i: int) -> Path:
        return self.dir / f"fs{i}.sock"

    # ---- comando de QEMU
    def comando(self) -> list[str]:
        c, t = self.cfg, self.tipo
        mac, win = t == "macos", t == "windows"
        ram, cpus = int(c["ram_gb"]), max(1, int(c["cpus"]))
        graf = self.graficos()
        gl = graf == "3d"
        nombre_proc = "vm-" + slug(self.nombre)[:12]
        a = [QEMU, "-name", f"{self.nombre},process={nombre_proc}",
             "-pidfile", "vm.pid",
             "-qmp", f"unix:{self.sock},server=on,wait=off",
             "-m", f"{ram}G"]
        # pantalla: ventana de QEMU (GTK) o servidor SPICE (remote-viewer: portapapeles, USB, resolución)
        if self.spice:
            a += ["-display", "none",
                  "-spice", f"unix=on,addr={self.spice_sock},disable-ticketing=on"
                            + (f",gl=on,rendernode={RENDER}" if gl else ""),
                  "-device", "virtio-serial-pci",
                  "-chardev", "spicevmc,id=vdagent,name=vdagent",
                  "-device", "virtserialport,chardev=vdagent,name=com.redhat.spice.0"]
        else:
            a += ["-display", "gtk,zoom-to-fit=on" + (",gl=on" if gl else "")]
        a += ["-device", "virtio-rng-pci,rng=rng0",
              "-object", "rng-random,id=rng0,filename=/dev/urandom",
              "-device", "qemu-xhci,id=input,p2=8,p3=8",
              "-audiodev", "pipewire,id=audio0"]
        if not c.get("ps2"):  # sin ellos entra el PS/2 de la placa: lo único que ven los sistemas antiguos
            a += ["-device", "usb-kbd,bus=input.0", "-device", "usb-tablet,bus=input.0"]
        if self.spice:  # USB del equipo a la VM desde el menú de remote-viewer
            for i in range(1, 4):
                a += ["-chardev", f"spicevmc,id=ur{i},name=usbredir", "-device", f"usb-redir,chardev=ur{i},bus=input.0"]
        for u in c.get("usb", []):
            a += ["-device", f"usb-host,vendorid=0x{u['vid']},productid=0x{u['pid']},id={usb_id(u)},bus=input.0"]

        # memoria: compartida (carpetas virtiofs) y/o en hugepages
        carpetas = c.get("carpetas", []) if not mac else []
        if carpetas or c.get("hugepages"):
            obj = f"memory-backend-memfd,id=mem,size={ram}G"
            if carpetas:
                obj += ",share=on"
            if c.get("hugepages"):
                obj += ",hugetlb=on,hugetlbsize=2M"
            a += ["-object", obj, "-machine", "memory-backend=mem"]

        # orden de arranque: red (PXE) > ISO > disco
        boot_red = bool(c.get("arrancar_red"))
        iso_arr = bool(c.get("arrancar_iso") and c.get("iso"))
        b_iso, b_disco = (1 if boot_red else 0), (2 if (iso_arr or boot_red) else 0)
        if not iso_arr:
            b_iso = 3

        if mac:
            hilos = 2 if cpus >= 2 and cpus % 2 == 0 else 1
            a += ["-machine", "q35,hpet=off,smm=off,vmport=off,accel=kvm",
                  "-global", "kvm-pit.lost_tick_policy=discard",
                  "-global", "ICH9-LPC.disable_s3=1",
                  "-global", "ICH9-LPC.acpi-pci-hotplug-with-bridge-support=off",
                  "-device", f"isa-applesmc,osk={OSK}",
                  "-cpu", CPU_MAC,
                  "-smp", f"cores={cpus // hilos},threads={hilos},sockets=1",
                  "-device", "virtio-balloon",
                  "-rtc", "base=utc,clock=host",
                  "-vga", "none", "-device", f"vmware-svga,vgamem_mb={self.vram()}"]
            a += self._audio_args()
            a += self._red_args(boot_red)
            a += ["-global", "driver=cfi.pflash01,property=secure,value=on",
                  "-drive", f"if=pflash,format=raw,unit=0,readonly=on,file={self.ruta(c['ovmf_code'])}",
                  "-drive", f"if=pflash,format=raw,unit=1,file={self.ruta(c['ovmf_vars'])}",
                  "-device", "ahci,id=ahci",
                  "-device", "ide-hd,bus=ahci.0,drive=BootLoader,bootindex=0",
                  "-drive", f"id=BootLoader,if=none,format=qcow2,file={self.ruta(c['opencore'])}",
                  "-device", "virtio-blk-pci,drive=SystemDisk",
                  "-drive", f"id=SystemDisk,if=none,format=qcow2,file={self.ruta(c['disco'])},"
                            f"discard=unmap,detect-zeroes=unmap,{self._cache()}" +
                            (",aio=threads" if c.get("cache_disco") not in ("none",) else "")]
            if c.get("arrancar_iso") and c.get("iso"):
                # instalador/recuperación de macOS: imagen .img como disco extra
                a += ["-drive", f"id=cd0,if=none,format=raw,file={self.ruta(c['iso'])}",
                      "-device", "ide-hd,bus=ahci.1,drive=cd0"]
            for i, d in enumerate(c.get("discos_extra", [])):
                a += ["-drive", f"id=dx{i},if=none,format=qcow2,file={self.ruta(d['archivo'])},"
                                f"discard=unmap,{self._cache()}",
                      "-device", f"virtio-blk-pci,drive=dx{i}"]
        else:
            modelo = {"host": "host", "oculto": "host,kvm=off,-hypervisor",
                      "compatible": CPU_COMPATIBLE}.get(c.get("cpu_modelo"), "host")
            if win and c.get("cpu_modelo") != "oculto":
                modelo += "," + HYPERV
            a += ["-machine", "q35,accel=kvm,smm=off",
                  "-cpu", modelo,
                  "-smp", str(cpus),
                  "-device", "virtio-balloon",
                  "-rtc", "base=localtime,clock=host" if win else "base=utc,clock=host"]
            a += self._graficos_args(graf)
            a += self._audio_args()
            a += self._red_args(boot_red)
            if c.get("uefi", True):
                a += ["-drive", f"if=pflash,format=raw,unit=0,readonly=on,file={UEFI_CODE}",
                      "-drive", f"if=pflash,format=raw,unit=1,file={self.dir / 'uefi-vars.fd'}"]
            opts = f"discard=unmap,detect-zeroes=unmap,{self._cache()}"
            if self.fisico:
                # disco entero del equipo: escritura directa (sin caché del anfitrión)
                a += ["-drive", f"id=d0,if=none,format=raw,file={self.ruta(c['disco'])},"
                                "cache=none,aio=io_uring,discard=unmap"]
            else:
                a += ["-drive", f"id=d0,if=none,format=qcow2,file={self.ruta(c['disco'])},{opts}"]
            bus = self.bus_disco()
            if bus == "sata":
                a += ["-device", f"ide-hd,drive=d0,bus=ide.0,bootindex={b_disco}"]
            elif bus == "nvme":
                a += ["-device", f"nvme,drive=d0,serial=NEURALVM{zlib.crc32(self.dir.name.encode()) % 10**6:06d},"
                                 f"bootindex={b_disco}"]
            else:
                a += ["-device", f"virtio-blk-pci,drive=d0,bootindex={b_disco}"]
            if c.get("iso"):
                a += ["-drive", f"id=cd0,if=none,media=cdrom,readonly=on,file={self.ruta(c['iso'])}",
                      "-device", f"ide-cd,drive=cd0,bus=ide.1,bootindex={b_iso}"]
            if c.get("drivers_iso"):
                a += ["-drive", f"id=cd1,if=none,media=cdrom,readonly=on,file={self.ruta(c['drivers_iso'])}",
                      "-device", "ide-cd,drive=cd1,bus=ide.2"]
            sata = 3  # ide.0 disco, ide.1 ISO, ide.2 drivers; quedan ide.3-5
            for i, d in enumerate(c.get("discos_extra", [])):
                a += ["-drive", f"id=dx{i},if=none,format=qcow2,file={self.ruta(d['archivo'])},{opts}"]
                if d.get("bus") == "sata" and sata <= 5:
                    a += ["-device", f"ide-hd,drive=dx{i},bus=ide.{sata}"]
                    sata += 1
                else:
                    a += ["-device", f"virtio-blk-pci,drive=dx{i}"]
            for i, cp in enumerate(carpetas):
                a += ["-chardev", f"socket,id=fs{i},path={self.fs_sock(i)}",
                      "-device", f"vhost-user-fs-pci,chardev=fs{i},tag={etiqueta(cp)}"]
        return a + [str(x) for x in c.get("extra", [])]

    def _graficos_args(self, graf: str) -> list[str]:
        n = max(1, min(4, int(self.cfg.get("pantallas") or 1)))
        vram = self.vram()
        a = ["-vga", "none"]
        if graf == "3d":
            a += ["-device", f"virtio-vga-gl,max_outputs={n}"]
        elif graf == "virtio":
            a += ["-device", f"virtio-vga,max_outputs={n}"]
        elif graf == "qxl":
            a += ["-device", f"qxl-vga,vgamem_mb={vram},vram_size_mb={max(64, vram * 2)}"]
            for _ in range(n - 1):
                a += ["-device", f"qxl,vgamem_mb={vram},vram_size_mb={max(64, vram * 2)}"]
        elif graf == "vmware":
            a += ["-device", f"vmware-svga,vgamem_mb={vram}"]
        else:
            a += ["-device", f"VGA,vgamem_mb={vram}"]
        return a

    def _audio_args(self) -> list[str]:
        au = self.audio()
        if au == "hda":
            return ["-device", "ich9-intel-hda", "-device", "hda-duplex,audiodev=audio0"]
        if au == "virtio":
            return ["-device", "virtio-sound-pci,audiodev=audio0"]
        return []

    def _red_args(self, boot_red: bool) -> list[str]:
        a, primera_nat, primera = [], True, True
        for i, r in enumerate(self.cfg.get("redes", [])):
            modo = r.get("modo", "nat")
            if modo == "ninguna":
                continue
            nd = f"n{i}"
            if modo == "interna":
                nd_args = f"bridge,id={nd},br={R.INTERNA_BR}"
            elif modo == "puente":
                nd_args = f"bridge,id={nd},br={R.PUENTE_BR}"
            elif modo == "aislada":
                g = max(1, min(250, int(r.get("grupo") or 1)))
                nd_args = f"dgram,id={nd},remote.type=inet,remote.host=230.0.77.{g},remote.port={7700 + g}"
            else:
                nd_args = f"user,id={nd},hostname={slug(self.nombre)}"
                if primera_nat:
                    for p in self.cfg.get("puertos", []):
                        dentro = "" if p.get("lan") else "127.0.0.1"
                        nd_args += f",hostfwd={p.get('proto', 'tcp')}:{dentro}:{int(p['host'])}-:{int(p['vm'])}"
                    primera_nat = False
            dev = f"{self.modelo_red(r)},netdev={nd}"
            if r.get("mac"):
                dev += f",mac={r['mac']}"
            if boot_red and primera:
                dev += ",bootindex=0"
            primera = False
            a += ["-netdev", nd_args, "-device", dev]
        return a

    # ---- comprobaciones
    def comprobar(self) -> list[str]:
        """Problemas que impedirían arrancar (archivos que faltan…)."""
        c, mal = self.cfg, []
        req = [("disco", c["disco"])]
        if self.tipo == "macos":
            req += [("firmware", c.get("ovmf_code", "")), ("variables UEFI", c.get("ovmf_vars", "")),
                    ("OpenCore", c.get("opencore", ""))]
        if c.get("iso"):
            req.append(("ISO", c["iso"]))
        if c.get("drivers_iso") and self.tipo != "macos":
            req.append(("ISO de drivers", c["drivers_iso"]))
        req += [("disco extra", d.get("archivo", "")) for d in c.get("discos_extra", [])]
        for que, f in req:
            if not f or not self.ruta(f).exists():
                mal.append(f"falta {que}: {f or '(sin definir)'}")
        if self.fisico and self.ruta(c["disco"]).exists():
            dev = self.ruta(c["disco"])
            if not os.access(dev, os.R_OK | os.W_OK):
                mal.append("sin permiso para el disco USB (sudo ~/neural-deck/root/instalar.sh y vuelve a conectarlo)")
            for m in R.montajes(str(dev)):
                if m["punto"] in R.SISTEMA:
                    mal.append(f"ese disco es parte de este sistema ({m['punto']}): no se puede usar en una VM")
                elif not m["punto"].startswith("/run/media/"):
                    mal.append(f"{m['part']} está montada en {m['punto']}: desmóntala antes")
            for otra in listar():
                if otra.dir != self.dir and otra.fisico and otra.pid() is not None and \
                        os.path.realpath(otra.ruta(otra.cfg["disco"])) == os.path.realpath(dev):
                    mal.append(f"el disco ya lo está usando la VM «{otra.nombre}»")
        elif self.fisico:
            mal[:] = [m for m in mal if not m.startswith("falta disco: ")]
            mal.append(f"el disco USB no está conectado ({Path(c['disco']).name})")
        if not shutil.which(QEMU):
            mal.append(f"falta {QEMU} (sudo pacman -S qemu-desktop)")
        if self.tipo != "macos" and c.get("uefi", True) and not UEFI_CODE.exists():
            mal.append("falta el firmware UEFI (sudo pacman -S edk2-ovmf)")
        if not os.access("/dev/kvm", os.R_OK | os.W_OK):
            mal.append("sin acceso a /dev/kvm")
        if self.spice and not shutil.which(VISOR) and c.get("pantalla") == "spice":
            mal.append("falta remote-viewer (sudo pacman -S virt-viewer)")
        if self.graficos() == "3d" and not Path(RENDER).exists():
            mal.append(f"3D: no hay {RENDER}")
        if self.tipo != "macos":
            for cp in c.get("carpetas", []):
                if not Path(os.path.expanduser(cp.get("ruta", ""))).is_dir():
                    mal.append(f"carpeta compartida que no existe: {cp.get('ruta')}")
            if c.get("carpetas") and not Path(VIRTIOFSD).exists():
                mal.append("falta virtiofsd (sudo pacman -S virtiofsd)")
        modos = {r.get("modo") for r in c.get("redes", [])}
        for modo, br in (("interna", R.INTERNA_BR), ("puente", R.PUENTE_BR)):
            if modo in modos and not R.permitido(br):
                mal.append(f"red {MODOS_RED[modo].lower()}: QEMU no tiene permiso para {br} "
                           "(sudo ~/neural-deck/root/instalar.sh)")
        if c.get("hugepages") and not R.AYUDANTE.exists():
            hp = R.hugepages()
            if hp["libres"] < R.paginas(int(c["ram_gb"])):
                mal.append("hugepages: falta el ayudante (sudo ~/neural-deck/root/instalar.sh)")
        try:
            fijos = lista_nucleos(c.get("nucleos_fijos", ""))
            if fijos and len(fijos) < int(c["cpus"]):
                mal.append(f"núcleos fijos: {len(fijos)} núcleo(s) para {c['cpus']} CPU de la VM (irían "
                           "amontonadas y muy lentas): pon uno por CPU o déjalo vacío")
        except ValueError as e:
            mal.append(str(e))
        return mal

    def avisos(self) -> list[str]:
        """Cosas que no impiden arrancar pero conviene saber."""
        c, av = self.cfg, []
        if self.graficos() == "3d" and self.tipo != "linux":
            av.append("3D (virgl) solo tiene drivers en Linux: en Windows se verá sin aceleración")
        if self.graficos() == "qxl" and not self.spice:
            av.append("QXL rinde mejor con la pantalla SPICE")
        if c.get("carpetas") and self.tipo == "macos":
            av.append("macOS no tiene drivers virtiofs: las carpetas compartidas no se conectan")
        for u in c.get("usb", []):
            disp = [d for d in R.usb_dispositivos() if d["vid"] == u["vid"] and d["pid"] == u["pid"]]
            if disp and not all(d["acceso"] for d in disp):
                av.append(f"USB {u.get('nombre') or u['vid']}: sin permiso (sudo ~/neural-deck/root/instalar.sh)")
        if self.fisico and self.ruta(c["disco"]).exists():
            mont = [m for m in R.montajes(str(self.ruta(c["disco"]))) if m["punto"].startswith("/run/media/")]
            if mont:
                av.append("disco USB montado en este equipo (" + ", ".join(m["punto"] for m in mont) +
                          "): se desmonta solo al arrancar")
            av.append("disco USB: mientras la VM esté encendida no lo montes ni lo desconectes")
        if "puente" in {r.get("modo") for r in c.get("redes", [])} and not R.estado_red("puente")["existe"]:
            av.append("red puente: aún no está creada (pestaña RED)")
        if self.cambios_pendientes():
            av.insert(0, "cambios guardados sin aplicar: pulsa REINICIAR (un reinicio desde dentro de la VM no los aplica)")
        return av

    # ---- acciones
    def _preparar_macs(self) -> None:
        cambio = False
        for r in self.cfg.get("redes", []):
            if not r.get("mac"):
                r["mac"] = mac_nueva()
                cambio = True
        if cambio:
            self.guardar()

    def _preparar_hugepages(self) -> None:
        if not self.cfg.get("hugepages"):
            return
        falta = R.paginas(int(self.cfg["ram_gb"]))
        hp = R.hugepages()
        if hp["libres"] < falta:
            R.reservar_hugepages(hp["total"] - hp["libres"] + falta)
            if R.hugepages()["libres"] < falta:
                raise RuntimeError("no hay memoria libre seguida para las hugepages: prueba sin ellas")

    def _lanzar_carpetas(self, log) -> None:
        carpetas = self.cfg.get("carpetas", []) if self.tipo != "macos" else []
        for i, cp in enumerate(carpetas):
            sock = self.fs_sock(i)
            sock.unlink(missing_ok=True)
            args = [VIRTIOFSD, f"--socket-path={sock}", f"--shared-dir={os.path.expanduser(cp['ruta'])}",
                    "--sandbox=namespace"]
            if cp.get("solo_lectura"):
                args.append("--readonly")
            log.write(f"=== virtiofsd: {' '.join(args)}\n")
            log.flush()
            subprocess.Popen(args, cwd=self.dir, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                             start_new_session=True)
        fin = time.monotonic() + 8
        while time.monotonic() < fin and not all(self.fs_sock(i).exists() for i in range(len(carpetas))):
            time.sleep(0.1)
        if not all(self.fs_sock(i).exists() for i in range(len(carpetas))):
            raise RuntimeError("virtiofsd no arrancó (mira el LOG de la VM)")

    def arrancar(self) -> None:
        mal = self.comprobar()
        if mal:
            raise RuntimeError("; ".join(mal))
        if self.fisico:
            disco = str(self.ruta(self.cfg["disco"]))
            R.desmontar(disco)
            if R.montajes(disco):
                raise RuntimeError("el disco USB sigue montado: desmóntalo antes")
        modos = {r.get("modo") for r in self.cfg.get("redes", [])}
        if "interna" in modos:
            R.asegurar_interna()
        if "puente" in modos and not R.estado_red("puente")["existe"]:
            raise RuntimeError("la red puente no está creada: créala en la pestaña RED")
        self._preparar_macs()
        self._preparar_hugepages()
        if self.tipo != "macos" and self.cfg.get("uefi", True):
            vars_ = self.dir / "uefi-vars.fd"
            if not vars_.exists():
                shutil.copyfile(UEFI_VARS, vars_)
        self.sock.unlink(missing_ok=True)
        self.spice_sock.unlink(missing_ok=True)
        with open(self.log, "a") as log:
            self._lanzar_carpetas(log)
            log.write(f"\n=== arranque: {' '.join(self.comando())}\n")
            log.flush()
            subprocess.Popen(self.comando(), cwd=self.dir, stdin=subprocess.DEVNULL,
                             stdout=log, stderr=log, start_new_session=True)
        threading.Thread(target=self._tras_arrancar, daemon=True).start()

    def _esperar(self, p: Path, s: float = 15) -> bool:
        fin = time.monotonic() + s
        while time.monotonic() < fin:
            if p.exists():
                return True
            time.sleep(0.2)
        return False

    def _tras_arrancar(self) -> None:
        """Ya con QEMU en marcha: fijar núcleos y abrir la ventana SPICE."""
        if self._esperar(self.sock):
            try:
                self.fijar_nucleos()
            except (ErrorQMP, OSError, ValueError):
                pass
        if self.cfg.get("pantalla") == "spice":
            try:
                self.ver()
            except RuntimeError:
                pass

    def fijar_nucleos(self) -> None:
        nucleos = lista_nucleos(self.cfg.get("nucleos_fijos", ""))
        if not nucleos:
            return
        for i, cpu in enumerate(qmp(self.sock, "query-cpus-fast") or []):
            tid = cpu.get("thread-id")
            if tid:
                os.sched_setaffinity(int(tid), {nucleos[i % len(nucleos)]})

    def ver(self) -> None:
        """Abre (otra) ventana de remote-viewer sobre la pantalla SPICE."""
        if not self.spice:
            raise RuntimeError("esta VM usa la ventana de QEMU (pestaña PANTALLA → SPICE)")
        if not self._esperar(self.spice_sock, 10):
            raise RuntimeError("la pantalla SPICE no responde")
        subprocess.Popen([VISOR, "--hotkeys=toggle-fullscreen=shift+f11,release-cursor=ctrl+alt", "--title", f"{self.nombre} · NEURAL//VM", f"spice+unix://{self.spice_sock}"],
                         cwd=self.dir, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)

    def apagar(self) -> None:
        """Pide al sistema invitado que se apague (como pulsar el botón)."""
        qmp(self.sock, "system_powerdown")

    def forzar(self) -> None:
        """Corta la corriente. Si no hay QMP (arrancada a mano), SIGTERM."""
        try:
            qmp(self.sock, "quit")
        except ErrorQMP:
            pid = self.pid()
            if pid:
                os.kill(pid, signal.SIGTERM)

    def pausar(self) -> None:
        qmp(self.sock, "stop")

    def reanudar(self) -> None:
        qmp(self.sock, "cont")

    def reiniciar(self) -> None:
        qmp(self.sock, "system_reset")

    def reiniciar_aplicando(self, espera: float = 40) -> None:
        """Apaga (si no responde, corta la corriente) y arranca un QEMU nuevo con lo guardado."""
        try:
            self.apagar()
        except ErrorQMP:
            pass
        fin = time.monotonic() + espera
        while time.monotonic() < fin and self.pid() is not None:
            time.sleep(0.5)
        if self.pid() is not None:
            self.forzar()
            fin = time.monotonic() + 15
            while time.monotonic() < fin and self.pid() is not None:
                time.sleep(0.2)
            if self.pid() is not None:
                raise RuntimeError("QEMU no terminó: inténtalo con FORZAR")
        self.arrancar()

    def captura(self, destino: Path) -> None:
        qmp(self.sock, "screendump", {"filename": str(destino), "format": "png"}, espera=8)

    # ---- USB en caliente
    def usb_conectados(self) -> set[str]:
        """ids de los usb-host enchufados ahora mismo a la VM encendida."""
        try:
            hijos = qmp(self.sock, "qom-list", {"path": "/machine/peripheral"}, espera=2) or []
        except ErrorQMP:
            return set()
        return {h["name"] for h in hijos if h.get("type") == "child<usb-host>"}

    def usb_conectar(self, u: dict) -> None:
        qmp(self.sock, "device_add", {"driver": "usb-host", "id": usb_id(u), "bus": "input.0",
                                      "vendorid": int(u["vid"], 16), "productid": int(u["pid"], 16)})

    def usb_desconectar(self, u: dict) -> None:
        qmp(self.sock, "device_del", {"id": usb_id(u)})

    # ---- discos (con la VM apagada)
    def _apagada(self) -> None:
        if self.pid() is not None:
            raise RuntimeError("apaga la VM antes")

    def info_disco(self, archivo: str) -> dict:
        ruta = self.ruta(archivo)
        if ruta.is_block_device():
            try:
                r = subprocess.run(["lsblk", "-bdno", "SIZE", str(ruta)], capture_output=True, text=True, timeout=5)
                n = int(r.stdout.strip() or 0)
            except (OSError, ValueError, subprocess.SubprocessError):
                n = 0
            return {"virtual": n, "real": n, "snapshots": 0}
        try:
            r = subprocess.run([QEMU_IMG, "info", "-U", "--output=json", str(self.ruta(archivo))],
                               capture_output=True, text=True, timeout=10)
            j = json.loads(r.stdout)
            return {"virtual": j.get("virtual-size", 0), "real": j.get("actual-size", 0),
                    "snapshots": len(j.get("snapshots", []))}
        except (OSError, ValueError, subprocess.SubprocessError):
            return {"virtual": 0, "real": 0, "snapshots": 0}

    def disco_info(self) -> dict:
        return self.info_disco(self.cfg["disco"])

    def ampliar(self, archivo: str, gb: int) -> None:
        if self.ruta(archivo).is_block_device() or (self.fisico and archivo == self.cfg["disco"]):
            raise RuntimeError("es un disco físico: su tamaño es el del disco")
        """Agranda un disco (solo crecer). Dentro de la VM hay que ampliar la partición."""
        self._apagada()
        info = self.info_disco(archivo)
        if gb * 1024 ** 3 <= info["virtual"]:
            raise RuntimeError("el tamaño nuevo debe ser mayor que el actual")
        _qemu_img("resize", str(self.ruta(archivo)), f"{gb}G")

    def compactar(self, archivo: str) -> None:
        if self.fisico and archivo == self.cfg["disco"]:
            raise RuntimeError("es un disco físico: no se compacta")
        """Reescribe el qcow2 sin los huecos (necesita espacio libre como lo que ocupa)."""
        self._apagada()
        if self.info_disco(archivo)["snapshots"]:
            raise RuntimeError("tiene snapshots: bórralas antes (compactar las perdería)")
        orig = self.ruta(archivo)
        tmp = orig.with_name(orig.name + ".compactando")
        try:
            _qemu_img("convert", "-O", "qcow2", str(orig), str(tmp), timeout=7200)
            tmp.replace(orig)
        finally:
            tmp.unlink(missing_ok=True)

    def crear_disco_extra(self, gb: int, bus: str = "virtio") -> str:
        self._apagada()
        n = 1
        while (self.dir / f"extra-{n}.qcow2").exists():
            n += 1
        nombre = f"extra-{n}.qcow2"
        _qemu_img("create", "-f", "qcow2", str(self.dir / nombre), f"{gb}G")
        self.cfg.setdefault("discos_extra", []).append({"archivo": nombre, "bus": bus})
        self.guardar()
        return nombre

    def usar_disco_existente(self, origen: str, principal: bool, bus: str = "sata") -> None:
        """Copia un disco virtual (vmdk, vdi, vhd, qcow2, img…) a qcow2 dentro de la VM; el original no se toca.
        Como principal, el anterior se queda en DISCOS EXTRA para no perder nada."""
        self._apagada()
        if self.fisico:
            raise RuntimeError("esta VM usa un disco físico")
        src = Path(os.path.expanduser(origen))
        if not src.is_file():
            raise RuntimeError(f"no existe {src}")
        n = 1
        while (self.dir / f"existente-{n}.qcow2").exists():
            n += 1
        archivo = f"existente-{n}.qcow2"
        _qemu_img("convert", "-O", "qcow2", str(src), str(self.dir / archivo), timeout=7200)
        if principal:
            self.cfg["discos_extra"] = [{"archivo": self.cfg["disco"], "bus": "sata"}] + self.cfg.get("discos_extra", [])
            # sata: lo traen casi todos los sistemas ya instalados, también los antiguos (virtio no)
            self.cfg.update(disco=archivo, bus_disco="sata", arrancar_iso=False)
            if self.tipo != "macos":
                self.cfg["uefi"] = _es_gpt(self.dir / archivo)
        else:
            self.cfg.setdefault("discos_extra", []).append({"archivo": archivo, "bus": bus})
        self.guardar()

    def quitar_disco_extra(self, i: int, borrar: bool) -> None:
        self._apagada()
        d = self.cfg["discos_extra"].pop(i)
        self.guardar()
        if borrar:
            self.ruta(d["archivo"]).unlink(missing_ok=True)

    # ---- snapshots (solo con la VM apagada: con qemu-img es lo seguro)
    def snapshots(self) -> list[dict]:
        if self.fisico:
            return []
        try:
            r = subprocess.run([QEMU_IMG, "snapshot", "-U", "-l", str(self.ruta(self.cfg["disco"]))],
                               capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.SubprocessError):
            return []
        res = []
        for linea in r.stdout.splitlines():
            # ID  TAG  VM_SIZE(“0 B”)  FECHA HORA  VM_CLOCK  ICOUNT
            m = re.match(r"\s*(\d+)\s+(\S+)\s+.*?(\d{4}-\d\d-\d\d \d\d:\d\d)", linea)
            if m:
                res.append({"id": m[1], "nombre": m[2], "fecha": m[3]})
        return res

    def _snap(self, op: str, nombre: str) -> None:
        if self.fisico:
            raise RuntimeError("un disco físico no admite snapshots")
        if self.pid() is not None:
            raise RuntimeError("apaga la VM antes de tocar snapshots")
        r = subprocess.run([QEMU_IMG, "snapshot", op, nombre, str(self.ruta(self.cfg["disco"]))],
                           capture_output=True, text=True, timeout=600)
        if r.returncode:
            raise RuntimeError(r.stderr.strip() or f"qemu-img salió con {r.returncode}")

    def crear_snapshot(self, nombre: str) -> None:
        self._snap("-c", nombre)

    def restaurar_snapshot(self, nombre: str) -> None:
        self._snap("-a", nombre)

    def borrar_snapshot(self, nombre: str) -> None:
        self._snap("-d", nombre)

    # ---- borrar
    def tamano_carpeta(self) -> int:
        """Bytes que ocupa de verdad en el SSD la carpeta de la VM."""
        total = 0
        for raiz, _dirs, archivos in os.walk(self.dir):
            for a in archivos:
                try:
                    total += os.lstat(os.path.join(raiz, a)).st_blocks * 512
                except OSError:
                    pass
        return total

    def borrar(self) -> None:
        """Borra del SSD la carpeta entera de la VM (discos, snapshots, UEFI, log).
        Un disco físico (VM desde USB) y lo que esté fuera de la carpeta no se tocan."""
        self._apagada()
        casa, vms = Path.home().resolve(), VMS_DIR.resolve()
        if self.dir in (casa, vms) or self.dir in casa.parents or self.dir in vms.parents:
            raise RuntimeError(f"no se borra {self.dir}: no es una carpeta exclusiva de la VM")
        olvidar(self.dir)
        # renombrar primero: la VM desaparece de golpe y no queda a medio borrar en la lista
        tmp = self.dir.with_name(f".borrando-{self.dir.name}")
        self.dir.rename(tmp)
        (tmp / "vm.json").unlink()
        shutil.rmtree(tmp)

    # ---- clonar
    def clonar(self, nuevo: str) -> "VM":
        """Copia entera de la VM (discos con reflink si el sistema de archivos lo permite)."""
        self._apagada()
        if self.fisico:
            raise RuntimeError("usa un disco físico: no se puede clonar")
        destino = VMS_DIR / slug(nuevo)
        if destino.exists():
            raise RuntimeError(f"ya existe {destino}")
        destino.mkdir(parents=True)
        fuera = {"vm.pid", "qmp.sock", "spice.sock", "neural-vm.log", "vm.json"}
        try:
            for f in self.dir.iterdir():
                if f.name in fuera or f.suffix == ".sock" or f.is_dir():
                    continue
                r = subprocess.run(["cp", "--reflink=auto", "--sparse=always", "-p", str(f), str(destino / f.name)],
                                   capture_output=True, text=True)
                if r.returncode:
                    raise RuntimeError(r.stderr.strip())
            cfg = copy.deepcopy(self.cfg)
            viejo = str(self.dir)

            def cambiar(v):  # rutas absolutas dentro de la carpeta vieja → la nueva
                if isinstance(v, str) and (v == viejo or v.startswith(viejo + "/")):
                    return str(destino) + v[len(viejo):]
                if isinstance(v, list):
                    return [cambiar(x) for x in v]
                if isinstance(v, dict):
                    return {k: cambiar(x) for k, x in v.items()}
                return v
            cfg = cambiar(cfg)
            cfg["nombre"] = nuevo
            for r in cfg.get("redes", []):
                r["mac"] = ""
            (destino / "vm.json").write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n")
        except Exception:
            shutil.rmtree(destino, ignore_errors=True)
            raise
        return VM(destino)


def usb_id(u: dict) -> str:
    return f"usb-{u['vid']}-{u['pid']}"


def etiqueta(cp: dict) -> str:
    """Nombre con el que se monta la carpeta dentro de la VM (máx. 36 bytes)."""
    return (slug(cp.get("etiqueta") or Path(cp.get("ruta", "")).name) or "compartida")[:36]


def _qemu_img(*args: str, timeout: float = 600) -> None:
    r = subprocess.run([QEMU_IMG, *args], capture_output=True, text=True, timeout=timeout)
    if r.returncode:
        raise RuntimeError(r.stderr.strip() or f"qemu-img salió con {r.returncode}")


def _es_gpt(qcow: Path) -> bool:
    """Un disco GPT se arranca con UEFI; uno MBR (sistemas antiguos), con BIOS."""
    with tempfile.TemporaryDirectory() as t:
        salida = Path(t) / "lba"
        _qemu_img("dd", "-f", "qcow2", "-O", "raw", "bs=512", "count=2", f"if={qcow}", f"of={salida}")
        return salida.read_bytes()[512:520] == b"EFI PART"


def _carpeta_nueva(nombre: str) -> Path:
    carpeta = VMS_DIR / slug(nombre)
    if carpeta.exists():
        raise RuntimeError(f"ya existe {carpeta}")
    carpeta.mkdir(parents=True)
    return carpeta


def crear(nombre: str, tipo: str, ram_gb: int, cpus: int, disco_gb: int, iso: str = "") -> VM:
    carpeta = _carpeta_nueva(nombre)
    cfg = dict(copy.deepcopy(DEFECTO), nombre=nombre, tipo=tipo, ram_gb=ram_gb, cpus=cpus, iso=iso,
               # con la ISO detrás del disco, el instalador arranca mientras el disco esté vacío
               # y el reinicio final entra ya en el sistema instalado (macOS la necesita marcada)
               arrancar_iso=bool(iso) and tipo == "macos", pantalla="spice")
    if tipo == "windows":
        cfg["drivers_iso"] = buscar_virtio_win()
    try:
        _qemu_img("create", "-f", "qcow2", str(carpeta / cfg["disco"]), f"{disco_gb}G")
    except RuntimeError:
        shutil.rmtree(carpeta, ignore_errors=True)
        raise
    (carpeta / "vm.json").write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n")
    return VM(carpeta)


EFI_GUID = "c12a7328-f81f-11d2-ba4b-00a0c93ec93b"


def desde_disco(dev: str, nombre: str, tipo: str, ram_gb: int, cpus: int) -> VM:
    """VM que arranca un sistema ya instalado en un disco USB, usando el disco entero
    (los cambios se guardan en el disco). Firmware UEFI si el disco tiene partición EFI."""
    disco = next((d for d in R.discos_usb() if d["dev"] == dev or d["id"] == dev), None)
    if disco is None:
        raise RuntimeError(f"{dev} no es un disco USB conectado")
    if disco["sistema"]:
        raise RuntimeError("ese disco es parte de este sistema")
    try:
        r = subprocess.run(["lsblk", "-no", "PARTTYPE", disco["dev"]], capture_output=True, text=True, timeout=5)
        uefi = EFI_GUID in r.stdout.lower()
    except (OSError, subprocess.SubprocessError):
        uefi = True
    carpeta = _carpeta_nueva(nombre)
    cfg = dict(copy.deepcopy(DEFECTO), nombre=nombre, tipo=tipo, ram_gb=ram_gb, cpus=cpus, uefi=uefi,
               disco=disco["id"], disco_fisico=True, pantalla="spice")
    (carpeta / "vm.json").write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n")
    return VM(carpeta)


FORMATOS_IMPORTAR = ("*.ova", "*.vdi", "*.vmdk", "*.vhd", "*.vhdx", "*.qcow2", "*.img", "*.raw")


def importar(origen: str, nombre: str, tipo: str, ram_gb: int, cpus: int, uefi: bool) -> VM:
    """VM nueva desde un disco de VirtualBox / VMware / Hyper-V (vdi, vmdk, vhd(x)) o un .ova.
    El disco se convierte a qcow2 (el original no se toca)."""
    src = Path(os.path.expanduser(origen))
    if not src.is_file():
        raise RuntimeError(f"no existe {src}")
    carpeta = _carpeta_nueva(nombre)
    try:
        discos = [src]
        tmp = carpeta / "importando"
        if src.suffix.lower() == ".ova":
            tmp.mkdir()
            with tarfile.open(src) as tar:
                miembros = [m for m in tar.getmembers() if m.isfile() and
                            Path(m.name).suffix.lower() in (".vmdk", ".vdi", ".vhd", ".img", ".qcow2")]
                if not miembros:
                    raise RuntimeError("el .ova no trae ningún disco")
                for m in miembros:
                    m.name = Path(m.name).name  # sin rutas raras
                    tar.extract(m, tmp, filter="data")
            discos = sorted(tmp.iterdir(), key=lambda p: p.stat().st_size, reverse=True)
        cfg = dict(copy.deepcopy(DEFECTO), nombre=nombre, tipo=tipo, ram_gb=ram_gb, cpus=cpus,
                   uefi=uefi, pantalla="spice", bus_disco="sata")
        if tipo == "windows":
            cfg["drivers_iso"] = buscar_virtio_win()
        for i, d in enumerate(discos):
            archivo = "disco.qcow2" if i == 0 else f"extra-{i}.qcow2"
            _qemu_img("convert", "-O", "qcow2", str(d), str(carpeta / archivo), timeout=7200)
            if i:
                cfg["discos_extra"].append({"archivo": archivo, "bus": "sata" if tipo == "windows" else "virtio"})
        shutil.rmtree(tmp, ignore_errors=True)
        (carpeta / "vm.json").write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n")
    except Exception:
        shutil.rmtree(carpeta, ignore_errors=True)
        raise
    return VM(carpeta)


if __name__ == "__main__":
    import sys
    pr = procesos_qemu()
    for v in listar():
        print(f"{v.nombre:24} {v.tipo:8} {v.estado(pr):10} {v.dir}")
        if "-c" in sys.argv:
            print("  " + " ".join(v.comando()))
            for x in v.comprobar() + v.avisos():
                print("  ! " + x)
