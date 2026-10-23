#!/usr/bin/env bash
#
# install-astro-pi.sh – Raspberry Pi (4/5, Raspberry Pi OS 64 Bit mit Desktop) als Astro-Steuerung einrichten.
#
#   * INDI-Server mit Treibern fuer Montierung und Kamera (systemd)
#   * Astro-Pi Web-App (Port 8080) – am Tablet "Zum Startbildschirm hinzufuegen"
#   * KStars/Ekos + noVNC (Port 6080) fuer alles Weitere (Polausrichtung, Guiding)
#   * astrometry.net fuer Plate-Solving, optional Tailscale und WLAN-Hotspot als Rueckfall
#
# Aufruf als root auf dem Pi:
#   sudo bash install-astro-pi.sh [--mount pmc8|celestron|simulator] [--camera canon|simulator]
#                                 [--lat 48.7 --lon 8.9] [--tailscale] [--hotspot] [--no-desktop]
#   sudo bash install-astro-pi.sh update     # Repo aktualisieren, Dienste neu starten
#
# Wiederholbar; vorhandene Configs in /etc bleiben unangetastet.
#
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/ChristophCaina/seestar-astro-pipeline}"
REPO_DIR="${REPO_DIR:-/opt/seestar-astro-pipeline}"
ASTRO_USER="${ASTRO_USER:-astro}"
CAPTURE_DIR="${CAPTURE_DIR:-/data/captures}"
INDEX_URL="http://data.astrometry.net/4100"

log() { echo -e "\e[1;34m==>\e[0m $*"; }
die() { echo "FEHLER: $*" >&2; exit 1; }
[[ $EUID -eq 0 ]] || die "bitte mit sudo ausführen"

install_units() {
  install -m 0644 "$REPO_DIR"/pi/systemd/{indiserver,astro-pi,novnc}.service /etc/systemd/system/
  sed -i "s/^User=astro/User=$ASTRO_USER/; s/^Group=astro/Group=$ASTRO_USER/" \
    /etc/systemd/system/{indiserver,astro-pi,novnc}.service
  systemctl daemon-reload
}

if [[ "${1:-}" == "update" ]]; then
  git -C "$REPO_DIR" pull -q --ff-only
  install_units
  systemctl restart indiserver astro-pi
  log "Aktuell: $(git -C "$REPO_DIR" log -1 --format='%h %s')"
  exit 0
fi

MOUNT="pmc8"; CAMERA="canon"; LAT=""; LON=""; TAILSCALE=0; HOTSPOT=0; DESKTOP=1
while [[ $# -gt 0 ]]; do
  case "$1" in
    --mount)      MOUNT="$2"; shift ;;
    --camera)     CAMERA="$2"; shift ;;
    --lat)        LAT="$2"; shift ;;
    --lon)        LON="$2"; shift ;;
    --tailscale)  TAILSCALE=1 ;;
    --hotspot)    HOTSPOT=1 ;;
    --no-desktop) DESKTOP=0 ;;
    *) die "unbekannte Option: $1" ;;
  esac
  shift
done
case "$MOUNT" in
  pmc8)      MOUNT_DRV="indi_pmc8_telescope" ;;      # Explore Scientific iEXOS-100 / EXOS-2 (PMC-Eight)
  celestron) MOUNT_DRV="indi_celestron_gps" ;;       # Celestron NexStar (auch aeltere Handbox per Seriell)
  simulator) MOUNT_DRV="indi_simulator_telescope" ;;
  *) die "--mount: pmc8 | celestron | simulator" ;;
esac
case "$CAMERA" in
  canon)     CAM_DRV="indi_canon_ccd" ;;              # Canon EOS per USB (gphoto)
  simulator) CAM_DRV="indi_simulator_ccd" ;;
  *) die "--camera: canon | simulator" ;;
esac

[[ "$(uname -m)" == "aarch64" ]] || log "Hinweis: kein 64-Bit-ARM – gedacht für Raspberry Pi OS 64 Bit"

log "Pakete installieren"
apt-get update
pkgs=(git curl ca-certificates indi-bin indi-gphoto gphoto2 astrometry.net mosquitto-clients
      python3 python3-aiohttp python3-numpy python3-pil)
(( DESKTOP )) && pkgs+=(kstars novnc websockify tigervnc-scraping-server)
DEBIAN_FRONTEND=noninteractive apt-get install -y "${pkgs[@]}"
command -v "$MOUNT_DRV" >/dev/null || die "Treiber $MOUNT_DRV fehlt nach der Installation"
command -v "$CAM_DRV" >/dev/null || die "Treiber $CAM_DRV fehlt (Paket indi-gphoto?)"

log "User $ASTRO_USER und Rechte (seriell, USB-Kamera)"
id "$ASTRO_USER" &>/dev/null || adduser --disabled-password --gecos "" "$ASTRO_USER"
usermod -aG dialout,plugdev,video "$ASTRO_USER"
# GNOME/GVFS greift die Kamera sonst selbst ab und blockiert gphoto
if [[ -f /usr/share/dbus-1/services/org.gtk.vfs.GPhoto2VolumeMonitor.service ]]; then
  chmod -x /usr/lib/gvfs/gvfs-gphoto2-volume-monitor 2>/dev/null || true
fi
mkdir -p "$CAPTURE_DIR" && chown "$ASTRO_USER:" "$CAPTURE_DIR"

if [[ -d "$REPO_DIR/.git" ]]; then
  log "Repo aktualisieren"; git -C "$REPO_DIR" pull -q --ff-only
else
  log "Repo klonen nach $REPO_DIR"; git clone -q "$REPO_URL" "$REPO_DIR"
fi
data="$REPO_DIR/siril/data"; mkdir -p "$data"
for f in NGC.csv addendum.csv; do
  [[ -s "$data/$f" ]] || curl -fsSL -o "$data/$f" "https://raw.githubusercontent.com/mattiaverga/OpenNGC/master/database_files/$f"
done

log "astrometry.net-Indexdateien (Bildfeld ca. 1–8°: 4110–4117)"
idx=/usr/share/astrometry; mkdir -p "$idx"
for i in 4110 4111 4112 4113 4114 4115 4116 4117; do
  [[ -s "$idx/index-$i.fits" ]] || curl -fsSL -o "$idx/index-$i.fits" "$INDEX_URL/index-$i.fits"
done

log "Konfiguration"
[[ -f /etc/default/astro-pi-indi ]] || echo "INDI_DRIVERS=\"$MOUNT_DRV $CAM_DRV\"" > /etc/default/astro-pi-indi
if [[ ! -f /etc/astro-pi.toml ]]; then
  install -m 0640 -o root -g "$ASTRO_USER" "$REPO_DIR/pi/astro-pi.toml.example" /etc/astro-pi.toml
  sed -i "s|^dir = .*|dir = \"$CAPTURE_DIR\"|" /etc/astro-pi.toml
  [[ -n "$LAT" ]] && sed -i "s/^lat = .*/lat = $LAT/" /etc/astro-pi.toml
  [[ -n "$LON" ]] && sed -i "s/^lon = .*/lon = $LON/" /etc/astro-pi.toml
fi

install_units
systemctl enable --now indiserver astro-pi

if (( DESKTOP )); then
  log "Desktop: X11 statt Wayland (fuer VNC), Autologin, VNC-Freigabe + noVNC"
  if command -v raspi-config >/dev/null; then
    raspi-config nonint do_wayland W1 || true          # X11
    raspi-config nonint do_boot_behaviour B4 || true   # Desktop mit Autologin
  fi
  home="$(getent passwd "$ASTRO_USER" | cut -d: -f6)"
  install -d -o "$ASTRO_USER" "$home/.vnc" "$home/.config/autostart"
  if [[ ! -f "$home/.vnc/passwd" ]]; then
    pw="$(tr -dc 'a-zA-Z0-9' < /dev/urandom | head -c 8)"
    echo "$pw" | vncpasswd -f > "$home/.vnc/passwd"; chown "$ASTRO_USER:" "$home/.vnc/passwd"; chmod 600 "$home/.vnc/passwd"
    echo "$pw" > /root/astro-pi-vnc-passwort.txt; chmod 600 /root/astro-pi-vnc-passwort.txt
  fi
  cat > "$home/.config/autostart/x0vncserver.desktop" <<DESK
[Desktop Entry]
Type=Application
Name=VNC-Freigabe
Exec=x0vncserver -display :0 -rfbauth $home/.vnc/passwd -localhost=1
DESK
  chown "$ASTRO_USER:" "$home/.config/autostart/x0vncserver.desktop"
  systemctl enable --now novnc
fi

if (( TAILSCALE )); then
  log "Tailscale"
  command -v tailscale >/dev/null || curl -fsSL https://tailscale.com/install.sh | sh
  tailscale up --hostname astro-pi --accept-routes || log "tailscale up bitte manuell abschliessen"
fi

if (( HOTSPOT )) && command -v nmcli >/dev/null; then
  log "WLAN-Hotspot 'Astro-Pi' als Rueckfall (nur wenn kein bekanntes WLAN da ist)"
  if ! nmcli -t -f NAME con show | grep -qx astro-hotspot; then
    hpw="$(tr -dc 'a-zA-Z0-9' < /dev/urandom | head -c 12)"
    nmcli con add type wifi ifname wlan0 con-name astro-hotspot autoconnect yes ssid Astro-Pi \
      802-11-wireless.mode ap 802-11-wireless.band bg ipv4.method shared \
      wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$hpw" connection.autoconnect-priority -10 >/dev/null
    echo "$hpw" > /root/astro-pi-hotspot-passwort.txt; chmod 600 /root/astro-pi-hotspot-passwort.txt
  fi
fi

ip="$(hostname -I | awk '{print $1}')"
cat <<TXT

Fertig.
  Web-App:   http://${ip}:8080   (oder http://$(hostname).local:8080) – am Tablet: Menü → "Zum Startbildschirm"
  Ekos:      http://${ip}:6080/vnc.html   (VNC-Passwort: /root/astro-pi-vnc-passwort.txt)
  Treiber:   $(cat /etc/default/astro-pi-indi)   → ändern in /etc/default/astro-pi-indi, dann: systemctl restart indiserver
  Standort:  /etc/astro-pi.toml ([location] lat/lon), MQTT zu Home Assistant: [mqtt] host

In KStars/Ekos ein Profil mit "Remote" localhost:7624 anlegen – der INDI-Server läuft bereits als Dienst,
Web-App und Ekos können gleichzeitig verbunden sein. Nach dem ersten Mal: Neustart (X11/Autologin).
TXT
