#!/bin/bash
# Instala NEURAL//LOGIN para greetd (Arch / CachyOS).
# Uso:  sudo pacman -S greetd   y luego   sudo ~/neural-deck/greeter/instalar.sh
# No activa nada al arrancar: eso es `sudo systemctl enable greetd` tras probarlo.
# Lo que ya existe en /etc se guarda antes como <archivo>.antes-neural
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "ejecútalo con sudo"; exit 1; }
USUARIO="${SUDO_USER:?ejecútalo con sudo desde tu usuario, no como root directamente}"
CASA="$(getent passwd "$USUARIO" | cut -d: -f6)"
AQUI="$(cd "$(dirname "$0")" && pwd)"
DECK="$(dirname "$AQUI")"
command -v greetd >/dev/null || { echo "falta greetd:  sudo pacman -S greetd"; exit 1; }
command -v wayfire >/dev/null || { echo "falta wayfire:  sudo pacman -S wayfire"; exit 1; }
command -v tuigreet >/dev/null || { echo "falta tuigreet (respaldo en modo texto):  sudo pacman -S greetd-tuigreet"; exit 1; }

respaldar(){ [ -e "$1" ] && [ ! -e "$1.antes-neural" ] && cp -a "$1" "$1.antes-neural" || true; }

# usuario del greeter (el paquete lo crea con sysusers; por si acaso)
getent group greeter >/dev/null || groupadd -r greeter
getent passwd greeter >/dev/null || useradd -r -g greeter -G video -d /var/lib/greetd -s /bin/false -c "greetd greeter" greeter
install -d -o greeter -g greeter -m 750 /var/lib/greetd

# el programa + estilo + temas + fuentes
D=/usr/local/share/neural-greeter
install -d -m 755 "$D" "$D/web" "$D/web/fonts"
install -m 644 "$AQUI/neural-greeter.py" "$D/neural-greeter.py"
install -m 644 "$DECK/estilo.py" "$D/estilo.py"
install -m 644 "$DECK/web/themes.js" "$D/web/themes.js"
install -m 644 "$DECK"/web/fonts/* "$D/web/fonts/"

# arranque con respaldo (neural-login → tuigreet) y sesión NEURAL elegible en tuigreet
install -m 755 "$AQUI/neural-login" /usr/local/bin/neural-login
install -m 755 "$AQUI/neural-sesion" /usr/local/bin/neural-sesion
install -m 644 "$AQUI/neural.desktop" /usr/share/wayland-sessions/neural.desktop
install -d -o greeter -g greeter -m 755 /var/cache/tuigreet   # --remember / --remember-session

# configuración de greetd
install -d -m 755 /etc/greetd /etc/greetd/neural
for f in config.toml wayfire-greeter.ini; do
  respaldar "/etc/greetd/$f"
  install -m 644 "$AQUI/$f" "/etc/greetd/$f"
done
TEMA=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("theme","red"))' \
       "$CASA/.config/live-wallpaper/board.json" 2>/dev/null || echo red)
echo "$TEMA" > /etc/greetd/neural/tema

# PAM (formato Arch: system-local-login) y servicio (VT 7, sin quitar tty1)
respaldar /etc/pam.d/greetd
respaldar /etc/pam.d/greetd-greeter
install -m 644 "$AQUI/pam-greetd" /etc/pam.d/greetd
install -m 644 "$AQUI/pam-greetd-greeter" /etc/pam.d/greetd-greeter
install -m 644 "$AQUI/greetd.service" /etc/systemd/system/greetd.service
systemctl daemon-reload

echo "NEURAL//LOGIN instalado (tema: $TEMA)."
echo "Probar sin reiniciar:  sudo systemctl start greetd    (salta a la VT 7)"
echo "Si algo va mal:        Ctrl+Alt+F1, entra y: sudo systemctl stop greetd"
echo "Si sale en negro:      sudo $AQUI/diagnostico.sh"
echo "Probar el respaldo:    en la pantalla de inicio, Ctrl+Alt+Retroceso → tuigreet (F3 elige sesión)"
echo "Registro del respaldo: /var/lib/greetd/neural-login.log"
echo "Para que salga siempre: sudo systemctl enable greetd"
