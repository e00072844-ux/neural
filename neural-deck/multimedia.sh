#!/bin/sh
# NEURAL: teclas multimedia (y botones de auriculares bluetooth) -> reproductor por MPRIS (D-Bus).
#   multimedia.sh play-pause | next | previous | stop
# Elige el reproductor que esté sonando; si ninguno, el primero que haya.
case "$1" in
  play-pause) m=PlayPause ;; next) m=Next ;; previous) m=Previous ;; stop) m=Stop ;;
  *) echo "uso: $0 play-pause|next|previous|stop" >&2; exit 2 ;;
esac
P=/org/mpris/MediaPlayer2
I=org.mpris.MediaPlayer2.Player
elegido=""
for n in $(busctl --user list --no-legend 2>/dev/null | awk '$1 ~ /^org\.mpris\.MediaPlayer2\./ {print $1}'); do
  [ -z "$elegido" ] && elegido=$n
  if busctl --user get-property "$n" $P $I PlaybackStatus 2>/dev/null | grep -q '"Playing"'; then
    elegido=$n; break
  fi
done
[ -n "$elegido" ] || exit 0   # no hay reproductor: no hacer nada
exec busctl --user call "$elegido" $P $I "$m"
