#!/bin/bash
# Lo que NEURAL necesita instalar como root (una vez):
#  1. Ayudante de energía de NEURAL//AJUSTES (umbrales de carga de la batería y
#     perfil cuando no hay power-profiles-daemon) + su política de polkit.
#  2. Hook de pacman que recompila el plugin neural-holo al actualizar Wayfire.
#  3. PAM de la pantalla de bloqueo (sin él, bloqueo.py NO bloquea).
#  4. NEURAL//VM: permiso de QEMU para sus puentes de red, USB para la sesión
#     activa y ayudante de hugepages (+ su política de polkit).
# Uso:  sudo ~/neural-deck/root/instalar.sh
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "ejecútalo con sudo"; exit 1; }
AQUI="$(cd "$(dirname "$0")" && pwd)"
DECK="$(dirname "$AQUI")"

install -D -m 755 -o root -g root "$AQUI/neural-energia" /usr/libexec/neural-energia
install -D -m 644 -o root -g root "$AQUI/org.local.neural.energia.policy" \
        /usr/share/polkit-1/actions/org.local.neural.energia.policy
echo "✓ /usr/libexec/neural-energia (+ política polkit): umbrales de carga desde AJUSTES"

# neural-holo: root compila desde SU copia de la fuente, nunca desde la carpeta del usuario
# (si no, cualquiera que escriba en ~/neural-deck ejecutaría código como root en la próxima actualización).
rm -rf /usr/local/src/neural-holo
install -d -m 755 -o root -g root /usr/local/src/neural-holo
for f in neural-holo.cpp neural-holo.xml Makefile; do
    install -m 644 -o root -g root "$DECK/plugins/neural-holo/$f" /usr/local/src/neural-holo/
done
install -D -m 755 -o root -g root "$AQUI/neural-holo-recompilar" /usr/local/bin/neural-holo-recompilar
install -D -m 644 -o root -g root "$AQUI/neural-holo.hook" /etc/pacman.d/hooks/neural-holo.hook
/usr/local/bin/neural-holo-recompilar
echo "✓ neural-holo: fuente en /usr/local/src/neural-holo (de root), compilado sin privilegios e instalado"
echo "  hook de pacman: se recompila solo al actualizar wayfire / wlroots"
echo "  si cambias el plugin en ~/neural-deck, vuelve a ejecutar este instalador para copiarlo"
echo "  (a mano: sudo neural-holo-recompilar · registro: /var/log/neural-holo.log)"

install -D -m 644 -o root -g root "$AQUI/neural-lock.pam" /etc/pam.d/neural-lock
echo "✓ /etc/pam.d/neural-lock: la pantalla de bloqueo ya puede bloquear (al suspender y por inactividad)"

# --- NEURAL//VM
mkdir -p /etc/qemu
touch /etc/qemu/bridge.conf
for br in neuralvm0 neuralbr0; do
    grep -qxF "allow $br" /etc/qemu/bridge.conf || echo "allow $br" >> /etc/qemu/bridge.conf
done
chmod 644 /etc/qemu/bridge.conf
echo "✓ /etc/qemu/bridge.conf: las VMs pueden usar la red interna (neuralvm0) y el puente (neuralbr0)"

install -D -m 644 -o root -g root "$AQUI/70-neural-vm-usb.rules" /etc/udev/rules.d/70-neural-vm-usb.rules
udevadm control --reload
udevadm trigger --subsystem-match=usb --action=change
udevadm trigger --subsystem-match=block --action=change
echo "✓ /etc/udev/rules.d/70-neural-vm-usb.rules: pasar USB y discos USB a las VMs sin ser root"

install -D -m 755 -o root -g root "$AQUI/neural-vm-ayudante" /usr/libexec/neural-vm-ayudante
install -D -m 644 -o root -g root "$AQUI/org.local.neural.vm.policy" \
        /usr/share/polkit-1/actions/org.local.neural.vm.policy
echo "✓ /usr/libexec/neural-vm-ayudante (+ política polkit): hugepages desde NEURAL//VM"

# --- NEURAL//ESCUDO
install -D -m 755 -o root -g root "$AQUI/neural-escudo-ayudante" /usr/libexec/neural-escudo-ayudante
install -D -m 644 -o root -g root "$AQUI/org.local.neural.escudo.policy" \
        /usr/share/polkit-1/actions/org.local.neural.escudo.policy
echo "✓ /usr/libexec/neural-escudo-ayudante (+ política polkit): reglas del cortafuegos desde NEURAL//ESCUDO"
