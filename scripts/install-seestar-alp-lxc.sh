#!/usr/bin/env bash
#
# install-seestar-alp-lxc.sh – seestar_alp in einem Debian-13-LXC (amd64) installieren.
#
# Nutzt das offizielle raspberry_pi/setup.sh von smart-underworld/seestar_alp und
# passt es fuer amd64/LXC an (siehe docs/01-seestar-alp-lxc.md).
#
# Aufruf als root im Container:
#   bash install-seestar-alp-lxc.sh                 # komplette Installation
#   bash install-seestar-alp-lxc.sh --update        # seestar_alp aktualisieren (statt raspberry_pi/update.sh)
#   bash install-seestar-alp-lxc.sh --fix-services  # nur systemd-Fix
#
set -euo pipefail

SEESTAR_USER="${SEESTAR_USER:-seestar}"
SETUP_URL="https://raw.githubusercontent.com/smart-underworld/seestar_alp/main/raspberry_pi/setup.sh"

log() { echo -e "\e[1;33m==>\e[0m $*"; }

[[ $EUID -eq 0 ]] || { echo "Bitte als root ausfuehren."; exit 1; }

fix_services() {
  log "systemd: Services an multi-user.target haengen"
  local changed=0
  for svc in seestar INDI; do
    local f="/etc/systemd/system/${svc}.service"
    [[ -f "$f" ]] || continue
    if ! grep -q '^WantedBy=multi-user.target' "$f"; then
      systemctl disable "$svc" 2>/dev/null || true
      sed -i 's/^WantedBy=.*/WantedBy=multi-user.target/' "$f"
      changed=1
    fi
  done
  systemctl daemon-reload
  systemctl enable seestar INDI 2>/dev/null || systemctl enable seestar
  (( changed )) && log "Service-Dateien angepasst" || log "Service-Dateien waren bereits korrekt"
}

if [[ "${1:-}" == "--fix-services" ]]; then
  fix_services; systemctl restart seestar; exit 0
fi

# update.sh liest raspberry_pi/setup.sh aus dem Repo – dort steht wieder der aarch64-Check.
# Darum: sauber pullen, setup.sh fuer den Lauf anpassen, update.sh --force, Aenderung zuruecknehmen.
if [[ "${1:-}" == "--update" ]]; then
  src="$(getent passwd "$SEESTAR_USER" | cut -d: -f6)/seestar_alp"
  [[ -d "$src/.git" ]] || { echo "Kein seestar_alp unter $src"; exit 1; }
  as_user() { su - "$SEESTAR_USER" -c "cd '$src' && $1"; }
  log "seestar_alp: $(as_user 'git describe --tags --always') → aktualisieren"
  as_user 'git checkout -- raspberry_pi/setup.sh && git pull --ff-only'
  as_user "sed -i -e 's/!= \"aarch64\"/!= \"\$(arch)\"/' -e 's/sudo sysctl -p\$/sudo sysctl -p || true/' raspberry_pi/setup.sh"
  rc=0; as_user 'raspberry_pi/update.sh --force' || rc=$?
  as_user 'git checkout -- raspberry_pi/setup.sh'
  fix_services; systemctl restart seestar
  log "seestar_alp jetzt: $(as_user 'git describe --tags --always')"
  (( rc == 0 )) || log "update.sh meldete Fehler ($rc) – Ausgabe oben pruefen"
  exit "$rc"
fi

log "System aktualisieren und Pakete installieren"
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get full-upgrade -y
DEBIAN_FRONTEND=noninteractive apt-get install -y sudo curl git locales libglib2.0-0t64

log "Locale en_US.UTF-8 erzeugen"
sed -i 's/^# *en_US.UTF-8/en_US.UTF-8/' /etc/locale.gen
locale-gen >/dev/null

if ! id "$SEESTAR_USER" &>/dev/null; then
  log "User $SEESTAR_USER anlegen (Passwort wird abgefragt)"
  adduser --gecos "" "$SEESTAR_USER"
fi
usermod -aG sudo "$SEESTAR_USER"
echo "$SEESTAR_USER ALL=(ALL) NOPASSWD:ALL" > "/etc/sudoers.d/$SEESTAR_USER"
chmod 440 "/etc/sudoers.d/$SEESTAR_USER"

log "Setup-Script laden und fuer amd64/LXC anpassen"
home="$(getent passwd "$SEESTAR_USER" | cut -d: -f6)"
curl -fsSL -o "$home/setup.sh" "$SETUP_URL"
sed -i 's/!= "aarch64"/!= "$(arch)"/' "$home/setup.sh"          # Architektur-Check aus
sed -i 's/sudo sysctl -p$/sudo sysctl -p || true/' "$home/setup.sh" # sysctl im LXC tolerieren
chown "$SEESTAR_USER:" "$home/setup.sh"

log "seestar_alp installieren (dauert: pyenv baut Python aus dem Quellcode)"
# Das Setup bricht am Ende ab, wenn der Service nicht sofort laeuft – das pruefen wir selbst.
su - "$SEESTAR_USER" -c "bash ~/setup.sh" || log "Setup meldete einen Fehler – pruefe den Service-Status unten"

fix_services
systemctl restart seestar
sleep 5

if systemctl is-active --quiet seestar; then
  ip="$(hostname -I | awk '{print $1}')"
  log "Fertig. Web-UI: http://${ip}:5432  ·  Alpaca: ${ip}:5555"
else
  log "seestar-Service laeuft nicht. Log:"
  journalctl -u seestar -n 40 --no-pager
  exit 1
fi
