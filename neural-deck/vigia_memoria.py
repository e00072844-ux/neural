#!/usr/bin/env python3
"""NEURAL//VIGÍA — vigila la RAM y la memoria de GPU (GTT) para que el LFS
no se congele.

Sin swap y con la raíz en un SSD USB cifrado, cuando la RAM se llena el
kernel se pone a releer ejecutables del disco y TODO se congela (hasta la
tty2) antes de que el OOM killer actúe. Este vigía actúa antes:

- cada 5 s mide MemAvailable, la GTT usada por amdgpu y, por proceso,
  RSS + GTT (fdinfo drm-total-gtt);
- escribe en ~/.cache/vigia-memoria.log una línea por minuto, y cada 5 s
  con los procesos más grandes cuando algo crece rápido o la RAM baja;
- si quedan menos de LIMITE_MB disponibles mata (SIGKILL) al proceso del
  usuario que más ocupa y lo deja anotado.

Corre como servicio de usuario (systemd --user), fuera de la sesión, así
sobrevive a cerrar sesión y ve también una 2ª sesión de Wayfire.
"""
import os
import signal
import time
import glob

LOG = os.path.expanduser("~/.cache/vigia-memoria.log")
LIMITE_MB = 3072          # por debajo de esto, matar al más grande
AVISO_MB = 8192           # por debajo de esto, registro detallado
CRECE_MB = 400            # crecimiento en 5 s que se considera sospechoso
INTERVALO = 5
UID = os.getuid()
YO = os.getpid()


def log(msg):
    try:
        if os.path.exists(LOG) and os.path.getsize(LOG) > 2_000_000:
            os.replace(LOG, LOG + ".1")
        with open(LOG, "a") as f:
            f.write(time.strftime("%H:%M:%S ") + msg + "\n")
    except OSError:
        pass


def meminfo():
    d = {}
    with open("/proc/meminfo") as f:
        for linea in f:
            k, v = linea.split(":", 1)
            d[k] = int(v.split()[0])  # kB
    return d


def gtt_total_mb():
    total = 0
    for p in glob.glob("/sys/class/drm/card*/device/mem_info_gtt_used"):
        try:
            total += int(open(p).read())
        except (OSError, ValueError):
            pass
    return total // (1 << 20)


UNIDAD = {"KiB": 1, "MiB": 1024, "GiB": 1 << 20, "": 1 / 1024}


def gtt_proceso_kb(pid):
    """Suma drm-total-gtt de los clientes DRM del proceso (sin repetir)."""
    vistos, total = set(), 0
    try:
        fds = os.listdir(f"/proc/{pid}/fdinfo")
    except OSError:
        return 0
    for fd in fds:
        try:
            with open(f"/proc/{pid}/fdinfo/{fd}") as f:
                txt = f.read()
        except OSError:
            continue
        if "drm-client-id" not in txt:
            continue
        cid, gtt = None, 0
        for linea in txt.splitlines():
            if linea.startswith("drm-client-id:"):
                cid = linea.split()[1]
            elif linea.startswith(("drm-total-gtt:", "drm-memory-gtt:")):
                partes = linea.split()
                unidad = partes[2] if len(partes) > 2 else ""
                gtt = int(int(partes[1]) * UNIDAD.get(unidad, 1))
        if cid not in vistos:
            vistos.add(cid)
            total += gtt
    return total


def procesos():
    lista = []
    for nombre in os.listdir("/proc"):
        if not nombre.isdigit():
            continue
        pid = int(nombre)
        if pid == YO:
            continue
        try:
            if os.stat(f"/proc/{pid}").st_uid != UID:
                continue
            rss = 0
            with open(f"/proc/{pid}/status") as f:
                for linea in f:
                    if linea.startswith("VmRSS:"):
                        rss = int(linea.split()[1])
                        break
            comm = open(f"/proc/{pid}/comm").read().strip()
            tty = os.readlink(f"/proc/{pid}/fd/0") if os.path.exists(f"/proc/{pid}/fd/0") else "?"
        except OSError:
            continue
        gtt = gtt_proceso_kb(pid)
        lista.append((rss + gtt, pid, comm, rss, gtt, tty))
    lista.sort(reverse=True)
    return lista


def resumen(lista, n=6):
    return " | ".join(f"{c}[{p}] rss={r // 1024}M gtt={g // 1024}M"
                      for _, p, c, r, g, _t in lista[:n])


def main():
    log(f"vigía arrancado (límite {LIMITE_MB} MB)")
    previo = None
    ultimo_normal = 0
    while True:
        m = meminfo()
        disp = m["MemAvailable"] // 1024
        shmem = m.get("Shmem", 0) // 1024
        gtt = gtt_total_mb()
        ahora = time.time()
        sospechoso = previo is not None and previo - disp >= CRECE_MB
        if disp < AVISO_MB or sospechoso:
            lista = procesos()
            log(f"!! disp={disp}M shmem={shmem}M gtt={gtt}M "
                f"(bajó {0 if previo is None else previo - disp}M) :: {resumen(lista)}")
            if disp < LIMITE_MB and lista:
                _, pid, comm, rss, g, _t = lista[0]
                log(f"XX RAM casi llena: mato {comm}[{pid}] "
                    f"rss={rss // 1024}M gtt={g // 1024}M")
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError as e:
                    log(f"   no pude matarlo: {e}")
                time.sleep(2)
        elif ahora - ultimo_normal >= 60:
            lista = procesos()
            log(f"ok disp={disp}M shmem={shmem}M gtt={gtt}M :: {resumen(lista, 4)}")
            ultimo_normal = ahora
        previo = disp
        time.sleep(INTERVALO)


if __name__ == "__main__":
    main()
