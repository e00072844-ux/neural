#!/bin/bash
# Diagnóstico del login negro. Uso (desde tty1): sudo ~/neural-deck/greeter/diagnostico.sh
# Arranca greetd guardando el log de Wayfire, salta solo a tty7 durante 10 s,
# vuelve a tty1 y muestra los logs. Deja greetd parado y la config como estaba.
[ "$(id -u)" = 0 ] || { echo "ejecútalo con sudo"; exit 1; }
USUARIO="${SUDO_USER:?ejecútalo con sudo desde tu usuario}"
SAL="$(getent passwd "$USUARIO" | cut -d: -f6)/greeter-diagnostico.txt"
systemctl stop greetd
"$(dirname "$0")/instalar.sh" >/dev/null
cp -a /etc/greetd/config.toml /etc/greetd/config.toml.diag
sed -i 's|^command = .*|command = "sh -c '"'"'WAYLAND_DEBUG=0 wayfire -d -c /etc/greetd/wayfire-greeter.ini > /var/lib/greetd/wayfire.log 2>\&1'"'"'"|' /etc/greetd/config.toml
grep ^command /etc/greetd/config.toml
rm -f /var/lib/greetd/wayfire.log /var/lib/greetd/neural-greeter.log
systemctl start greetd
sleep 3
chvt 7; sleep 10
S=$(loginctl list-sessions --no-legend | awk '$3=="greeter" && $6=="tty7"{print $1}')
pkill -USR1 -u greeter -f neural-greeter.py; sleep 1
ACT=$(loginctl show-session "$S" -p Active -p State 2>&1)
chvt 1; sleep 1
{
  echo "== sesión greeter en tty7 mientras estaba en tty7: $ACT"
  echo "== VT activa al volver: $(cat /sys/class/tty/tty0/active)"
  echo "== procesos greeter"; pgrep -af -u greeter
  echo "== neural-greeter.log"; cat /var/lib/greetd/neural-greeter.log
  echo "== wayfire.log (filtrado)"; grep -v -E 'DEBUG.*(wf::|input|pointer|keyboard)' /var/lib/greetd/wayfire.log | grep -i -E 'EE|WW|error|fail|output|drm|seat|session|libseat|monitor|eDP|NOOP|backend|denied|vt' | head -80
  echo "== journal greetd"; journalctl -b -u greetd --no-pager | tail -15
} > "$SAL" 2>&1
cp /var/lib/greetd/wayfire.log "${SAL%.txt}-wayfire-completo.log"
chown "$USUARIO" "$SAL" "${SAL%.txt}-wayfire-completo.log"
systemctl stop greetd
mv /etc/greetd/config.toml.diag /etc/greetd/config.toml
echo "Listo: $SAL"
