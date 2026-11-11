#!/usr/bin/env python3
"""Estado y control del sistema para NEURAL//BAR (y luego el centro de control).

Sin GTK, para poder usarlo tambien desde las teclas del teclado
(wayfire.ini, [command]):
    sistema.py vol +5 | vol -5 | vol mute | mic mute | brillo +5 | brillo -5
Despues de cada cambio avisa a la barra (SIGUSR1) para que se redibuje ya.

- Audio: wpctl (PipeWire / WirePlumber).
- Brillo: SetBrightness de systemd-logind en la sesion propia (sin sudo,
  sin regla udev); se lee de /sys/class/backlight.
- Bateria: /sys/class/power_supply.
- WiFi: estado de /sys/class/net y el SSID de nmcli (o `networkctl status`).
- Sesion: salir = cerrar Wayfire; suspender / reiniciar / apagar con systemctl
  (polkit + logind lo permiten al usuario de la sesion activa, sin sudo)."""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

BAR_PROC = "neural-bar"  # nombre de proceso de la barra (ver bar.py), para pkill
SINK, SOURCE = "@DEFAULT_AUDIO_SINK@", "@DEFAULT_AUDIO_SOURCE@"
VOL_MAX = 1.0  # tope de volumen (1.0 = 100 %, sin saturar)
BRILLO_MIN = 0.02  # nunca dejar la pantalla negra del todo


def _run(*cmd: str, timeout: float = 3) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def _read(path: Path) -> str:
    try:
        return path.read_text().strip()
    except OSError:
        return ""


# ---------- audio ----------
def get_audio(dev: str = SINK) -> tuple[float, bool] | None:
    """(volumen 0..1, silenciado) o None si no hay dispositivo."""
    m = re.search(r"Volume:\s*([\d.]+)(\s*\[MUTED\])?", _run("wpctl", "get-volume", dev))
    if not m:
        return None
    return float(m.group(1)), bool(m.group(2))


def cmd_set_volume(dev: str, frac: float) -> list[str]:
    return ["wpctl", "set-volume", "-l", str(VOL_MAX), dev, f"{max(0.0, min(VOL_MAX, frac)):.3f}"]


def step_volume(dev: str, pct: int) -> None:
    sign = "+" if pct >= 0 else "-"
    _run("wpctl", "set-volume", "-l", str(VOL_MAX), dev, f"{abs(pct)}%{sign}")


def cmd_toggle_mute(dev: str) -> list[str]:
    return ["wpctl", "set-mute", dev, "toggle"]



# nombres cortos para los perfiles ALSA (device.profile.description)
NOMBRES_AUDIO = {"Speaker": "Altavoces", "Headphones": "Audífonos (jack)", "Stereo Microphone": "Micrófono (jack)",
                 "Headset Microphone": "Micrófono (jack)", "Digital Microphone": "Micrófono interno",
                 "Internal Microphone": "Micrófono interno", "Line Out": "Salida de línea"}


def get_dispositivos(salida: bool = True) -> list[dict]:
    """Salidas (Audio/Sink) o entradas (Audio/Source) de PipeWire:
    [{id, name, nombre, bt, default, disponible}], la predeterminada primero.
    disponible = False si su conector esta vacio (p. ej. el jack sin nada
    enchufado: WirePlumber no deja usarlo)."""
    import json
    try:
        objs = json.loads(_run("pw-dump", timeout=4) or "[]")
    except json.JSONDecodeError:
        return []
    clase = "Audio/Sink" if salida else "Audio/Source"
    clave = "default.audio.sink" if salida else "default.audio.source"
    default = ""
    out = []
    # rutas sin conectar de cada tarjeta: {(device.id, card.profile.device)}
    vacias = set()
    for o in objs:
        for r in ((o.get("info") or {}).get("params") or {}).get("EnumRoute") or []:
            if r.get("available") == "no":
                for dev in r.get("devices") or []:
                    vacias.add((o.get("id"), dev))
    for o in objs:
        if o.get("type", "").endswith("Metadata") and (o.get("props") or {}).get("metadata.name") == "default":
            for m in o.get("metadata") or []:
                if m.get("key") == clave and isinstance(m.get("value"), dict):
                    default = m["value"].get("name", "")
        props = (o.get("info") or {}).get("props") or {}
        if props.get("media.class") != clase:
            continue
        bt = "api.bluez5.address" in props or str(props.get("node.name", "")).startswith("bluez")
        perfil = str(props.get("device.profile.description", ""))
        if bt:
            nombre = props.get("node.description") or props.get("node.name")
        elif "HDMI" in perfil or "DisplayPort" in perfil:
            nombre = "HDMI / DisplayPort"
        else:
            nombre = NOMBRES_AUDIO.get(perfil) or props.get("node.description") or props.get("node.name")
        libre = (props.get("device.id"), props.get("card.profile.device")) not in vacias
        out.append({"id": o["id"], "name": props.get("node.name", ""), "nombre": str(nombre), "bt": bt,
                    "disponible": libre})
    for d in out:
        d["default"] = d["name"] == default
    out.sort(key=lambda d: (not d["default"], not d["disponible"], not d["bt"], d["nombre"]))
    return out


def cmd_set_default(node_id: int) -> list[str]:
    """WirePlumber mueve solo los sonidos que ya suenan a la nueva salida."""
    return ["wpctl", "set-default", str(node_id)]

# ---------- brillo ----------
def backlight() -> Path | None:
    devs = sorted(Path("/sys/class/backlight").glob("*"))
    return devs[0] if devs else None


def get_brillo() -> float | None:
    dev = backlight()
    if dev is None:
        return None
    try:
        return int(_read(dev / "brightness")) / int(_read(dev / "max_brightness"))
    except (ValueError, ZeroDivisionError):
        return None


def cmd_set_brillo(frac: float) -> list[str] | None:
    dev = backlight()
    if dev is None:
        return None
    try:
        top = int(_read(dev / "max_brightness"))
    except ValueError:
        return None
    value = round(max(BRILLO_MIN, min(1.0, frac)) * top)
    return ["busctl", "call", "org.freedesktop.login1", "/org/freedesktop/login1/session/auto",
            "org.freedesktop.login1.Session", "SetBrightness", "ssu", "backlight", dev.name, str(value)]


def step_brillo(pct: int) -> None:
    cur = get_brillo()
    cmd = None if cur is None else cmd_set_brillo(cur + pct / 100)
    if cmd:
        _run(*cmd)


# ---------- bateria ----------
def get_bateria() -> dict | None:
    """{capacity 0..100, status (Charging, Discharging, Full...), ac} o None."""
    supplies = Path("/sys/class/power_supply")
    bat = next((p for p in sorted(supplies.glob("*")) if _read(p / "type") == "Battery"), None)
    if bat is None:
        return None
    try:
        cap = int(_read(bat / "capacity"))
    except ValueError:
        return None
    ac = any(_read(p / "online") == "1" for p in supplies.glob("*") if _read(p / "type") == "Mains")
    return {"capacity": cap, "status": _read(bat / "status"), "ac": ac}


# ---------- red ----------
def wifi_iface() -> str | None:
    return next((p.name for p in sorted(Path("/sys/class/net").glob("*")) if (p / "wireless").exists()), None)


def get_wifi() -> dict | None:
    """{iface, up, ssid, ip} o None si no hay tarjeta WiFi."""
    iface = wifi_iface()
    if iface is None:
        return None
    up = _read(Path("/sys/class/net") / iface / "operstate") == "up"
    ssid = ip = ""
    if up:
        # NetworkManager (Arch/CachyOS): GENERAL.CONNECTION = nombre de la conexión (el SSID por defecto)
        out = _run("nmcli", "-t", "-g", "GENERAL.CONNECTION,IP4.ADDRESS", "device", "show", iface)
        if out.strip():
            lineas = out.splitlines() + ["", ""]
            ssid = lineas[0].strip()
            ip = lineas[1].split("/", 1)[0].strip()
        else:  # systemd-networkd (el LFS)
            out = _run("networkctl", "status", "--no-pager", iface)
            m = re.search(r"Wi-Fi access point:\s*(.+?)\s*\(", out)
            ssid = m.group(1) if m else ""
            m = re.search(r"Address:\s*(\d+\.\d+\.\d+\.\d+)", out)
            ip = m.group(1) if m else ""
    return {"iface": iface, "up": up, "ssid": ssid, "ip": ip}


# ---------- radio WiFi (rfkill) ----------
RFKILL_TYPE_WLAN, RFKILL_OP_CHANGE_ALL = 1, 3


def wifi_radio_on() -> bool | None:
    """False si la radio WiFi esta apagada por software o por el interruptor; None si no hay."""
    vistos = False
    for r in Path("/sys/class/rfkill").glob("rfkill*"):
        try:
            if (r / "type").read_text().strip() != "wlan":
                continue
            vistos = True
            if (r / "soft").read_text().strip() == "1" or (r / "hard").read_text().strip() == "1":
                return False
        except OSError:
            continue
    return True if vistos else None


def wifi_radio(encender: bool) -> None:
    """Apaga/enciende TODAS las radios WiFi (como `rfkill block wlan`). /dev/rfkill es del
    usuario de la sesion activa (ACL de logind): no hace falta root."""
    import struct
    with open("/dev/rfkill", "wb", buffering=0) as f:
        f.write(struct.pack("=IBBBB", 0, RFKILL_TYPE_WLAN, RFKILL_OP_CHANGE_ALL, 0 if encender else 1, 0))


# ---------- luz del teclado (cualquier LED *::kbd_backlight) ----------
TECLADO_GUARDADO = Path.home() / ".config" / "live-wallpaper" / "teclado.json"


def teclado_led() -> Path | None:
    return next(iter(sorted(Path("/sys/class/leds").glob("*::kbd_backlight"))), None)


def get_teclado() -> tuple[int, int] | None:
    """(nivel, maximo) de la luz del teclado, o None si no hay."""
    led = teclado_led()
    if led is None:
        return None
    try:
        return int((led / "brightness").read_text()), int((led / "max_brightness").read_text())
    except (OSError, ValueError):
        return None


def set_teclado(nivel: int) -> bool:
    """Pone la luz del teclado por logind (Session.SetBrightness): sin root, solo para
    la sesion activa. True si se pudo."""
    led, actual = teclado_led(), get_teclado()
    if led is None or actual is None:
        return False
    nivel = max(0, min(actual[1], int(nivel)))
    from gi.repository import Gio, GLib
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        bus.call_sync("org.freedesktop.login1", "/org/freedesktop/login1/session/auto",
                      "org.freedesktop.login1.Session", "SetBrightness",
                      GLib.Variant("(ssu)", ("leds", led.name, nivel)), None,
                      Gio.DBusCallFlags.NONE, 2000, None)
        return True
    except GLib.Error as e:
        print(f"teclado: no pude poner el nivel {nivel}: {e.message}", file=sys.stderr, flush=True)
        return False


def teclado_guardado() -> int | None:
    import json
    try:
        return int(json.loads(TECLADO_GUARDADO.read_text())["nivel"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def guardar_teclado(nivel: int) -> None:
    import json
    try:
        TECLADO_GUARDADO.parent.mkdir(parents=True, exist_ok=True)
        tmp = TECLADO_GUARDADO.with_suffix(".tmp")
        tmp.write_text(json.dumps({"nivel": int(nivel)}) + "\n")
        tmp.replace(TECLADO_GUARDADO)
    except OSError:
        pass


def bluez_instalado() -> bool:
    return any(Path(d, "bluetoothctl").exists() for d in ("/usr/bin", "/usr/local/bin"))


# ---------- sesion ----------
def cmd_sesion(accion: str) -> list[str]:
    return {
        "salir": ["pkill", "-x", "wayfire"],
        "suspender": ["systemctl", "suspend"],  # logind lo permite al usuario de la sesion activa
        "reiniciar": ["systemctl", "reboot"],  # polkit/logind: permitido a la sesion activa
        "apagar": ["systemctl", "poweroff"],
    }[accion]


def uptime() -> str:
    try:
        secs = int(float(_read(Path("/proc/uptime")).split()[0]))
    except (ValueError, IndexError):
        return "--"
    h, m = divmod(secs // 60, 60)
    return f"{h // 24}d {h % 24:02d}h" if h >= 24 else f"{h:02d}h {m:02d}m"


def avisar_barra() -> None:
    _run("pkill", "-USR1", "-x", BAR_PROC)


# ---------- linea de comandos (teclas) ----------
def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    que, arg = argv
    if que in ("vol", "mic"):
        dev = SINK if que == "vol" else SOURCE
        if arg == "mute":
            _run(*cmd_toggle_mute(dev))
        else:
            step_volume(dev, int(arg))
    elif que == "brillo":
        step_brillo(int(arg))
    else:
        print(__doc__, file=sys.stderr)
        return 2
    avisar_barra()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
