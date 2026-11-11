# NEURAL

Escritorio completo con estética HUD / cyberpunk sobre **Wayfire**, escrito en Python + GTK4.
Todo es local: sin cuentas, sin nube y sin telemetría.

> Para **Arch Linux y derivadas** (CachyOS, EndeavourOS…). Todos los paquetes están en los
> repositorios oficiales de Arch. Usa `pacman`, NetworkManager y PipeWire; en otras
> distribuciones NEURAL//CVE, el hook de pacman y la pantalla de inicio no funcionan.

## Qué trae

| Pieza | Qué hace |
|---|---|
| **DECK** (`app.py`) | Fondo interactivo con paneles (reloj, CPU, red, batería, apps, terminal) |
| **DOCK**, **BAR**, panel rápido | Dock, barra superior, WiFi, Bluetooth, sonido, brillo y avisos |
| **AJUSTES** | Red, pantalla (luz nocturna), sonido, teclado y ratón, energía, apariencia, temas |
| **FILES** · **TERM** · **WEB** · **VISOR** | Archivos, terminal (VTE), navegador (WebKitGTK), imágenes y PDF |
| **PAK** | Tienda de Flatpak (Flathub, instalación de usuario) |
| **NEXO** | Pasar archivos entre equipos por la LAN o Bluetooth, cifrado de extremo a extremo |
| **CLIP** | Historial del portapapeles (`Super+V`) |
| **BÓVEDA** | Contraseñas, códigos 2FA y notas cifrados (Argon2id + AES-GCM) |
| **RESPALDO** | Copias de seguridad con restic en un disco USB |
| **PULSO** | Monitor del sistema |
| **ESCUDO** | Cortafuegos (ufw) y puertos expuestos |
| **DISCO** | Discos, particiones y salud (UDisks2) |
| **VM** | Máquinas virtuales QEMU/KVM sin libvirt |
| **CVE** | Avisos de seguridad de Arch contra lo instalado |
| **LOGIN** · bloqueo | Pantalla de inicio para greetd y bloqueo de sesión |
| `neural-holo` | Plugin de Wayfire con efecto holograma al abrir y cerrar ventanas |

## Dependencias (Arch)

```sh
sudo pacman -S --needed wayfire wf-config wlroots0.20 \
  python python-gobject python-cairo python-psutil python-cryptography python-dbus \
  gtk4 gtk4-layer-shell vte4 webkitgtk-6.0 poppler-glib graphene gdk-pixbuf2 polkit \
  foot wofi kanshi grim slurp wl-clipboard swayidle wlsunset libnotify xdg-user-dirs \
  networkmanager wireplumber udisks2 bluez bluez-utils power-profiles-daemon gnome-keyring \
  flatpak iproute2 pacman-contrib
```

Opcionales, según lo que vayas a usar:

- **RESPALDO**: `restic`
- **ESCUDO**: `ufw`
- **VM**: `qemu-desktop` y `edk2-ovmf`
- **LOGIN**: `greetd`
- **PULSO** con GPU AMD: `amdgpu_top`

## Instalar

```sh
git clone <este repo> neural && cd neural
./instalar.sh                          # como tu usuario
sudo ~/neural-deck/root/instalar.sh    # plugin holograma, energía, PAM del bloqueo, VM y cortafuegos
sudo ~/neural-deck/greeter/instalar.sh # opcional: pantalla de inicio con greetd
```

Después, desde una TTY: `wayfire`.

`instalar.sh` guarda una copia de todo lo que reemplaza (`*.antes-instalar`).

El plugin `neural-holo` se compila desde una copia de root en `/usr/local/src/neural-holo`,
como el usuario `nobody`, y un hook de pacman lo recompila al actualizar Wayfire. Nunca se
compila como root desde tu carpeta personal.

## Estructura

```
neural-deck/     programas (se copian a ~/neural-deck)
  root/          ayudantes de sistema con polkit, PAM y hook de pacman
  greeter/       NEURAL//LOGIN para greetd
  plugins/       plugin neural-holo de Wayfire (C++)
  web/           fondo interactivo (HTML/JS)
config/          ~/.config: wayfire.ini, foot, gtk, portales, servicios de usuario
applications/    lanzadores .desktop
bin/             utilidades (menú de salida, etc.)
fonts/           Share Tech Mono y Rajdhani (SIL Open Font License, ver fonts/OFL.txt)
etc-greetd/      referencia de /etc/greetd
```

## Datos del usuario

NEURAL guarda tus datos fuera del repo, en `~/.local/state/neural-*`, `~/.config/live-wallpaper`
y `~/vms`. No los subas si haces un fork.

## Licencia

MIT, ver [LICENSE](LICENSE). Las fuentes de `fonts/` y `neural-deck/web/fonts/` tienen su propia
licencia (SIL Open Font License 1.1).
