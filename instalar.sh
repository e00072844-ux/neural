#!/bin/bash
# NEURAL — instala el escritorio en tu cuenta (Arch Linux y derivadas).
# Uso (como TU usuario, no como root):   ./instalar.sh
# Lo que ya existe se guarda antes como <archivo>.antes-instalar
# No pide sudo. La parte de sistema (plugin neural-holo, ayudantes, polkit,
# PAM del bloqueo) va aparte:
#     sudo ~/neural-deck/root/instalar.sh
# y la pantalla de inicio de sesión (greetd), opcional:
#     sudo ~/neural-deck/greeter/instalar.sh
set -euo pipefail
[ "$(id -u)" != 0 ] || { echo "ejecútalo como tu usuario, sin sudo"; exit 1; }

AQUI="$(cd "$(dirname "$0")" && pwd)"
ok(){ printf '  \e[32m✓\e[0m %s\n' "$1"; }
falta(){ printf '  \e[33m!\e[0m %s\n' "$1"; }

respaldar(){ [ -e "$1" ] && [ ! -e "$1.antes-instalar" ] && cp -a "$1" "$1.antes-instalar" || true; }

poner(){   # poner <origen> <destino>   (carpeta o archivo)
  respaldar "$2"
  mkdir -p "$(dirname "$2")"
  if [ -d "$1" ]; then mkdir -p "$2"; cp -a "$1/." "$2/"; else cp -a "$1" "$2"; fi
}

echo "== Comprobando programas"
for c in wayfire python3 foot wofi nmcli wpctl wl-copy grim slurp swayidle udisksctl flatpak; do
  command -v "$c" >/dev/null || falta "no encuentro «$c» (ver README: dependencias)"
done
python3 - <<'EOF' || falta "faltan módulos de Python (ver README: dependencias)"
import gi, psutil, cryptography
gi.require_version("Gtk", "4.0"); gi.require_version("Gtk4LayerShell", "1.0")
from gi.repository import Gtk, Gtk4LayerShell
EOF

echo "== NEURAL: copiando archivos a $HOME"
poner "$AQUI/neural-deck" "$HOME/neural-deck";                    ok "~/neural-deck"
for f in "$AQUI"/config/*; do
  n=$(basename "$f"); [ "$n" = systemd ] && continue
  poner "$f" "$HOME/.config/$n"
done;                                                             ok "~/.config"
mkdir -p "$HOME/.config/systemd/user"
cp -a "$AQUI"/config/systemd/user/* "$HOME/.config/systemd/user/"; ok "servicios de usuario"
mkdir -p "$HOME/.local/share/applications" "$HOME/bin"
for a in "$AQUI"/applications/*.desktop; do
  sed "s|@HOME@|$HOME|g" "$a" > "$HOME/.local/share/applications/$(basename "$a")"
done;                                                             ok "lanzadores .desktop"
cp -a "$AQUI"/bin/* "$HOME/bin/";                                 ok "~/bin"
mkdir -p "$HOME/.local/share/fonts/neural"
cp -a "$AQUI"/fonts/* "$HOME/.local/share/fonts/neural/"
fc-cache -f "$HOME/.local/share/fonts" >/dev/null;                ok "fuentes (Share Tech Mono, Rajdhani)"
python3 "$HOME/neural-deck/iconos.py" >/dev/null;                 ok "iconos de las apps NEURAL"
mkdir -p "$HOME/.cache"

echo "== Servicios de usuario"
systemctl --user daemon-reload
for u in neural-vigia.service neural-nexo.service neural-cve.timer; do
  systemctl --user enable "$u" >/dev/null 2>&1 && ok "$u activado" || falta "$u no se pudo activar"
done

echo "== Flathub de usuario (NEURAL//PAK usa flatpak --user)"
if command -v flatpak >/dev/null; then
  flatpak remote-add --user --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo &&
    ok "flathub (usuario)"
else
  falta "falta flatpak"
fi

echo "== Plugin neural-holo (Wayfire)"
if pkg-config --exists wayfire wf-config wlroots-0.20; then
  ok "cabeceras de Wayfire presentes: root/instalar.sh lo compila e instala"
else
  falta "faltan las cabeceras de wayfire / wlroots-0.20: el escritorio va igual, sin el efecto holograma"
fi

echo
echo "NEURAL instalado. Para probarlo: inicia sesión en una TTY y escribe  wayfire"
echo "Parte de sistema (holograma, energía, bloqueo, VM, cortafuegos):  sudo ~/neural-deck/root/instalar.sh"
echo "Pantalla de inicio (greetd, opcional):                 sudo ~/neural-deck/greeter/instalar.sh"
