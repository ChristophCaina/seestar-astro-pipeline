#!/usr/bin/env bash
# astro-session.sh – Astro-Session auf dem Feld-Laptop starten/beenden (Desktop-Starter).
#
#   astro-session.sh start    seestar_alp + INDI + Standort + Astro-Station starten,
#                             Astro-Station, Live-Fenster (wartet auf Bilder) und Stellarium oeffnen
#   astro-session.sh stop     alles wieder beenden
#   astro-session.sh status   kurzer Status
#
# Einstellungen (optional) in ~/.config/astro-session.env:
#   SERVER_ALP="192.168.1.XX:5555"   # seestar_alp im Heimnetz; laeuft er, wird vor dem Start gefragt
#   ALP_URL="http://localhost:5432"   # Web-UI von seestar_alp (Bereitschaft pruefen)
#   STATION_URL="http://localhost:8070"
#   OPEN_STELLARIUM=1                 # 0 = Stellarium nicht oeffnen
#   OPEN_BROWSER=1                    # 0 = Astro-Station nicht im Browser oeffnen
#   OPEN_LIVEWIN=1                    # 0 = kein Live-Fenster
set -u

CONF="${XDG_CONFIG_HOME:-$HOME/.config}/astro-session.env"
# shellcheck source=/dev/null
[[ -f "$CONF" ]] && . "$CONF"
SERVER_ALP="${SERVER_ALP:-}"
ALP_URL="${ALP_URL:-${WEBUI_URL:-http://localhost:5432}}"   # WEBUI_URL: alter Name
STATION_URL="${STATION_URL:-http://localhost:8070}"
OPEN_STELLARIUM="${OPEN_STELLARIUM:-1}"
OPEN_BROWSER="${OPEN_BROWSER:-1}"
OPEN_LIVEWIN="${OPEN_LIVEWIN:-1}"
STATION_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../station" && pwd)"
SERVICES=(seestar-alp.service seestar-indi.service astro-station.service)
TITLE="Astro-Session"

say() {   # Meldung im Terminal und als Desktop-Benachrichtigung
  echo "[astro] $*"
  command -v notify-send >/dev/null && notify-send -a "$TITLE" -i applications-science "$TITLE" "$*" 2>/dev/null
  return 0
}

ask() {   # Ja/Nein-Frage; ohne grafische Oberflaeche: im Terminal
  if command -v zenity >/dev/null && [[ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]]; then
    zenity --question --title="$TITLE" --width=420 --text="$1" 2>/dev/null
  else
    read -r -p "$1 [j/N] " a; [[ "$a" =~ ^[jJyY] ]]
  fi
}

port_open() {   # host:port erreichbar?
  local host="${1%:*}" port="${1##*:}"
  timeout 2 bash -c "exec 3<>/dev/tcp/$host/$port" 2>/dev/null
}

wait_for_webui() {
  local i
  for i in $(seq 1 60); do
    curl -fsS -o /dev/null --max-time 2 "$ALP_URL" 2>/dev/null && return 0
    sleep 1
    (( i == 15 )) && say "seestar_alp startet noch …"
  done
  return 1
}

start() {
  if systemctl --user is-active --quiet seestar-alp.service; then
    systemctl --user start astro-station.service   # nach einem Update evtl. noch nicht gestartet
    say "Läuft schon."
  else
    if [[ -n "$SERVER_ALP" ]] && port_open "$SERVER_ALP"; then
      ask "seestar_alp läuft auch auf dem Server ($SERVER_ALP).

Zwei seestar_alp am selben Seestar stören sich gegenseitig.
Den Dienst auf dem Server vorher stoppen:
  pct exec 210 -- systemctl stop seestar

Trotzdem hier starten?" || { say "Abgebrochen."; exit 1; }
    fi
    say "Starte seestar_alp, INDI und Standort …"
    systemctl --user start "${SERVICES[@]}" seestar-location.timer || {
      say "Start fehlgeschlagen – Details: journalctl --user -u seestar-alp -n 30"; exit 1; }
    if wait_for_webui; then
      systemctl --user start seestar-location.service >/dev/null 2>&1 &
      say "Bereit: Astro-Station $STATION_URL, seestar_alp, INDI (localhost:7624)"
    else
      say "seestar_alp antwortet nicht – Details: journalctl --user -u seestar-alp -n 30"
    fi
  fi
  if [[ "$OPEN_LIVEWIN" == 1 ]] && ! pgrep -f "astro_station[.]livewin" >/dev/null; then
    if python3 -c "import gi; gi.require_version('Gtk', '3.0'); gi.require_foreign('cairo')" 2>/dev/null; then
      (cd "$STATION_DIR" && setsid python3 -m astro_station.livewin --station "$STATION_URL" >/dev/null 2>&1 &)
    else
      say "Live-Fenster braucht python3-gi-cairo: sudo apt install python3-gi-cairo"
    fi
  fi
  if [[ "$OPEN_BROWSER" == 1 ]]; then xdg-open "$STATION_URL" >/dev/null 2>&1 & fi
  if [[ "$OPEN_STELLARIUM" == 1 ]] && command -v stellarium >/dev/null && ! pgrep -x stellarium >/dev/null; then
    setsid stellarium >/dev/null 2>&1 &
  fi
}

stop() {
  pkill -f "astro_station[.]livewin" 2>/dev/null
  systemctl --user stop seestar-location.timer "${SERVICES[@]}"
  say "Astro-Session beendet."
}

status() {
  local s
  for s in "${SERVICES[@]}" seestar-location.timer; do
    printf '%-24s %s\n' "$s" "$(systemctl --user is-active "$s")"
  done
  printf '%-24s %s\n' "Live-Fenster" "$(pgrep -f "astro_station[.]livewin" >/dev/null && echo active || echo inactive)"
}

case "${1:-start}" in
  start) start ;;
  stop) stop ;;
  status) status ;;
  *) echo "Aufruf: $0 start|stop|status" >&2; exit 2 ;;
esac
