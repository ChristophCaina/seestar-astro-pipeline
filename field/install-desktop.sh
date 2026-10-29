#!/usr/bin/env bash
# install-desktop.sh – Feld-Laptop einrichten: Dienste, Desktop-Starter, kein Autostart.
#
#   bash ~/Astro/seestar-astro-pipeline/field/install-desktop.sh [--server-alp 192.168.1.XX:5555]
#
# Legt an:
#   ~/.config/systemd/user/seestar-*.service|timer   (aus field/systemd)
#   Desktop:  "Astro-Session starten" / "Astro-Session beenden"
#   Menue:    dieselben Eintraege unter Bildung/Wissenschaft
# Die Dienste starten danach NICHT mehr automatisch beim Anmelden, nur ueber den Starter.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVER_ALP=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --server-alp) SERVER_ALP="$2"; shift 2 ;;
    *) echo "unbekannte Option: $1" >&2; exit 2 ;;
  esac
done

UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
APP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
DESK_DIR="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"
CONF="${XDG_CONFIG_HOME:-$HOME/.config}/astro-session.env"
mkdir -p "$UNIT_DIR" "$APP_DIR" "$DESK_DIR"

# Dienste: aktualisieren, Autostart beim Anmelden aus (der Starter entscheidet)
cp "$HERE"/systemd/* "$UNIT_DIR"/
systemctl --user daemon-reload
systemctl --user disable seestar-alp.service seestar-indi.service seestar-location.timer >/dev/null 2>&1 || true
chmod +x "$HERE/astro-session.sh" "$HERE/seestar_location_sync.py"

if [[ ! -f "$CONF" ]]; then
  cat > "$CONF" <<EOF
# Einstellungen fuer astro-session.sh
SERVER_ALP="$SERVER_ALP"        # seestar_alp im Heimnetz (host:port); laeuft er, wird vor dem Start gefragt
WEBUI_URL="http://localhost:5432"
OPEN_STELLARIUM=1
OPEN_BROWSER=1
EOF
fi

entry() {   # Name, Kommentar, Aktion, Icon
  cat <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=$1
Comment=$2
Exec=$HERE/astro-session.sh $3
Icon=$4
Terminal=false
Categories=Education;Science;Astronomy;
EOF
}

for spec in "start|Astro-Session starten|seestar_alp, INDI und Standort starten, Stellarium und Web-UI öffnen|applications-science" \
            "stop|Astro-Session beenden|seestar_alp und INDI beenden|process-stop"; do
  IFS='|' read -r action name comment icon <<< "$spec"
  file="astro-session-$action.desktop"
  entry "$name" "$comment" "$action" "$icon" > "$APP_DIR/$file"
  cp "$APP_DIR/$file" "$DESK_DIR/$file"
  chmod +x "$DESK_DIR/$file" "$APP_DIR/$file"
  # Starter als vertrauenswuerdig markieren (sonst fragt der Dateimanager beim ersten Klick)
  gio set "$DESK_DIR/$file" metadata::trusted true 2>/dev/null || true
done

echo "Fertig: Starter auf dem Desktop ($DESK_DIR) und im Menü."
echo "Einstellungen: $CONF"
