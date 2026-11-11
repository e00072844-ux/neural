#!/bin/bash
# Ajustes de seguridad elegidos a partir de la auditoría con Lynis.
# Uso:  sudo ~/neural-deck/root/endurecer.sh             aplicar
#       sudo ~/neural-deck/root/endurecer.sh --deshacer  quitar todo
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "ejecútalo con sudo"; exit 1; }

SYSCTL=/etc/sysctl.d/90-neural-seguridad.conf
MODPROBE=/etc/modprobe.d/neural-protocolos.conf
CORE=/etc/systemd/coredump.conf.d/neural.conf

if [ "${1:-}" = "--deshacer" ]; then
    rm -f "$SYSCTL" "$MODPROBE" "$CORE"
    sysctl --system >/dev/null
    systemctl daemon-reload
    echo "✓ Quitado. Reinicia para que los valores vuelvan del todo a los de antes."
    exit 0
fi

cat > "$SYSCTL" <<'EOF'
# Sin autocarga de disciplinas de línea desde usuarios normales
dev.tty.ldisc_autoload = 0
# Más protección contra ataques con archivos en carpetas compartidas (/tmp)
fs.protected_fifos = 2
fs.protected_regular = 2
# Los programas con privilegios no dejan volcado de memoria
fs.suid_dumpable = 0
# Endurece el compilador de BPF del kernel
net.core.bpf_jit_harden = 2
# Esta laptop no es un router
net.ipv4.conf.all.send_redirects = 0
net.ipv4.conf.default.send_redirects = 0
EOF
echo "✓ $SYSCTL"

cat > "$MODPROBE" <<'EOF'
# Protocolos de red que este equipo no usa
install dccp /bin/false
install sctp /bin/false
install rds /bin/false
install tipc /bin/false
EOF
echo "✓ $MODPROBE: dccp, sctp, rds y tipc bloqueados"

mkdir -p "$(dirname "$CORE")"
cat > "$CORE" <<'EOF'
[Coredump]
Storage=none
ProcessSizeMax=0
EOF
echo "✓ $CORE: los volcados de memoria ya no se guardan en disco"

chmod 600 /etc/ssh/sshd_config
echo "✓ /etc/ssh/sshd_config: solo root"

sysctl --system >/dev/null
systemctl daemon-reload
echo "Listo. Reinicia cuando puedas para aplicar también las actualizaciones."
