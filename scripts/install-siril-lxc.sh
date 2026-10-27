#!/usr/bin/env bash
#
# install-siril-lxc.sh – Siril + siril-auto in einem Proxmox-LXC (Debian 13) einrichten.
#
# Auf dem PROXMOX-HOST (legt bei Bedarf den Container an, bindet das NAS-Volume ein,
# installiert alles im Container):
#   bash install-siril-lxc.sh host <CTID> [--create] [--template <vztmpl>] [--storage local-lvm]
#                                  [--astro /<host-pfad>/astro] [--chown] [Container-Optionen …]
#
# Im CONTAINER (wird vom Host-Modus automatisch aufgerufen, geht aber auch einzeln):
#   bash install-siril-lxc.sh container [--seestar-ip <ip>] [--astrometry] [--astrometry-wide] [--enable-timer] [--branch main]
#   bash install-siril-lxc.sh update    # Repo aktualisieren, systemd-Units neu installieren
#
# Wiederholbar: vorhandene Configs in /etc bleiben unangetastet.
#
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/ChristophCaina/seestar-astro-pipeline}"
REPO_DIR="${REPO_DIR:-/opt/seestar-astro-pipeline}"
SIRIL_USER="siril"
SIRIL_UID="${SIRIL_UID:-1500}"        # fest, damit die Host-UID planbar ist (100000 + 1500)
ASTRO_ROOT="${ASTRO_ROOT:-/data/astro}"
OPENNGC="https://raw.githubusercontent.com/mattiaverga/OpenNGC/master/database_files"
ASTROMETRY_INDEX="http://data.astrometry.net/4100"

log() { echo -e "\e[1;35m==>\e[0m $*"; }
die() { echo "FEHLER: $*" >&2; exit 1; }

MODE="${1:-}"; shift || true

# ------------------------------------------------------------------ Host
host_mode() {
  local ctid="${1:-}"; shift || true
  [[ "$ctid" =~ ^[0-9]+$ ]] || die "CTID fehlt (z. B. 132)"
  command -v pct >/dev/null || die "pct nicht gefunden – bitte auf dem Proxmox-Host ausführen"
  local create=0 template="" storage="local-lvm" astro="" do_chown=0 pass=()
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --create)   create=1 ;;
      --template) template="$2"; shift ;;
      --storage)  storage="$2"; shift ;;
      --astro)    astro="$2"; shift ;;
      --chown)    do_chown=1 ;;
      *)          pass+=("$1") ;;
    esac
    shift
  done

  if ! pct status "$ctid" &>/dev/null; then
    (( create )) || die "Container $ctid existiert nicht (mit --create anlegen)"
    if [[ -z "$template" ]]; then
      template="$(pveam list local 2>/dev/null | awk '/debian-13-standard/ {print $1}' | sort | tail -1)"
      [[ -n "$template" ]] || die "kein Debian-13-Template gefunden (pveam download local …)"
    fi
    log "Lege Container $ctid aus $template an"
    pct create "$ctid" "$template" --hostname siril --cores 4 --memory 8192 --swap 1024 \
      --rootfs "${storage}:32" --net0 name=eth0,bridge=vmbr0,ip=dhcp \
      --unprivileged 1 --onboot 1 --features nesting=1
  fi

  if [[ -n "$astro" ]]; then
    [[ -d "$astro" ]] || die "Host-Pfad $astro existiert nicht (erst in Samba/NAS anlegen)"
    local conf="/etc/pve/lxc/${ctid}.conf"
    if grep -q "mp=${ASTRO_ROOT}" "$conf"; then
      log "NAS-Volume ist bereits als ${ASTRO_ROOT} eingebunden"
    else
      local mp=0
      while grep -q "^mp${mp}:" "$conf"; do mp=$((mp + 1)); done
      log "Binde $astro als ${ASTRO_ROOT} ein (mp${mp})"
      pct set "$ctid" "-mp${mp}" "${astro},mp=${ASTRO_ROOT}"
      pct status "$ctid" | grep -q running && pct reboot "$ctid"
    fi
    local host_uid=$((100000 + SIRIL_UID))
    if (( do_chown )); then
      log "Schreibrechte: $astro → UID/GID $host_uid (= $SIRIL_USER im Container)"
      chown "$host_uid:$host_uid" "$astro"
      chmod 2775 "$astro"
    else
      log "Hinweis: $SIRIL_USER braucht Schreibrechte auf $astro (Host-UID $host_uid) – ggf. mit --chown"
    fi
  fi

  pct status "$ctid" | grep -q running || pct start "$ctid"
  sleep 5
  log "Installiere Siril + siril-auto im Container"
  pct push "$ctid" "$0" /root/install-siril-lxc.sh --perms 0755
  pct exec "$ctid" -- bash /root/install-siril-lxc.sh container "${pass[@]}"
}

# ------------------------------------------------------------------ Container
install_units() {
  log "systemd-Units installieren"
  install -m 0644 "$REPO_DIR/siril/systemd/siril-auto.service" /etc/systemd/system/
  install -m 0644 "$REPO_DIR/siril/systemd/siril-auto.timer" /etc/systemd/system/
  systemctl daemon-reload
}

fetch_catalog() {
  local data="$REPO_DIR/siril/data"
  mkdir -p "$data"
  for f in NGC.csv addendum.csv; do
    if [[ ! -s "$data/$f" ]]; then
      log "OpenNGC: $f laden (CC-BY-SA 4.0)"
      curl -fsSL -o "$data/$f" "$OPENNGC/$f"
    fi
  done
}

container_mode() {
  local seestar_ip="" astrometry=0 astro_wide=0 enable_timer=0 branch="main"
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --seestar-ip)   seestar_ip="$2"; shift ;;
      --astrometry)   astrometry=1 ;;
      --astrometry-wide) astrometry=1; astro_wide=1 ;;   # zusaetzlich Indexdateien fuer Weitwinkel
      --enable-timer) enable_timer=1 ;;
      --branch)       branch="$2"; shift ;;
      *)              die "unbekannte Option: $1" ;;
    esac
    shift
  done
  [[ $EUID -eq 0 ]] || die "bitte als root ausführen"

  log "Pakete installieren"
  apt-get update
  local pkgs=(siril rclone libimage-exiftool-perl mosquitto-clients smbclient python3 python3-pil
              fonts-dejavu-core git curl ca-certificates xvfb xauth locales)
  (( astrometry )) && pkgs+=(astrometry.net)
  # ohne Recommends: sonst kommen u. a. udisks2/gvfs mit, die im unprivilegierten LXC nur Fehler melden
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "${pkgs[@]}"
  # Locales, die pct exec vom Host mitbringt (sonst Warnungen von GTK/perl)
  sed -i -E 's/^# *((en_US|de_DE)\.UTF-8 UTF-8)/\1/' /etc/locale.gen && locale-gen >/dev/null || true
  siril-cli --version 2>/dev/null | head -1 || log "WARN: siril-cli --version schlug fehl"

  if [[ -d "$REPO_DIR/.git" ]]; then
    log "Repo aktualisieren ($REPO_DIR)"
    git -C "$REPO_DIR" fetch -q origin && git -C "$REPO_DIR" checkout -q "$branch" && git -C "$REPO_DIR" pull -q --ff-only
  else
    log "Repo klonen nach $REPO_DIR"
    git clone -q -b "$branch" "$REPO_URL" "$REPO_DIR"
  fi
  fetch_catalog

  if ! id "$SIRIL_USER" &>/dev/null; then
    log "System-User $SIRIL_USER (UID $SIRIL_UID) anlegen"
    groupadd -r -g "$SIRIL_UID" "$SIRIL_USER" 2>/dev/null || groupadd -r "$SIRIL_USER"
    useradd -r -m -u "$SIRIL_UID" -g "$SIRIL_USER" -s /usr/sbin/nologin "$SIRIL_USER" 2>/dev/null \
      || useradd -r -m -g "$SIRIL_USER" -s /usr/sbin/nologin "$SIRIL_USER"
  fi
  local uid; uid="$(id -u "$SIRIL_USER")"

  for f in env aliases; do
    if [[ ! -f "/etc/siril-auto.$f" ]]; then
      log "/etc/siril-auto.$f aus Vorlage anlegen"
      install -m 0640 -o root -g "$SIRIL_USER" "$REPO_DIR/siril/siril-auto.$f.example" "/etc/siril-auto.$f"
    fi
  done
  if [[ -n "$seestar_ip" ]]; then
    sed -i "s/^SEESTAR_IP=.*/SEESTAR_IP=\"$seestar_ip\"/" /etc/siril-auto.env
    if ! su -s /bin/bash "$SIRIL_USER" -c 'rclone listremotes' | grep -q '^seestar:'; then
      log "rclone-Remote 'seestar' → //$seestar_ip (Gast)"
      su -s /bin/bash "$SIRIL_USER" -c "rclone config create seestar smb host=$seestar_ip user=guest" >/dev/null
    fi
    log "Freigaben auf dem Seestar:"
    smbclient -L "//$seestar_ip" -N 2>/dev/null | awk '/Disk/ {print "    " $1}' || log "Seestar nicht erreichbar"
  fi

  if [[ -d "$ASTRO_ROOT" ]]; then
    if su -s /bin/bash "$SIRIL_USER" -c "mkdir -p $ASTRO_ROOT/{raw/seestar,work,stacks,export,web,.state}" 2>/dev/null; then
      log "Ordnerstruktur in $ASTRO_ROOT angelegt"
    else
      log "WARN: $SIRIL_USER kann nicht nach $ASTRO_ROOT schreiben (Host-UID $((100000 + uid)) braucht Rechte)"
    fi
  else
    log "WARN: $ASTRO_ROOT fehlt – NAS-Volume auf dem Host einbinden (host-Modus mit --astro)"
  fi

  if (( astrometry )); then
    local idx=/usr/share/astrometry
    mkdir -p "$idx"
    local idx_list=(4107 4108 4109 4110)
    (( astro_wide )) && idx_list+=(4111 4112 4113 4114 4115 4116 4117 4118 4119)
    for i in "${idx_list[@]}"; do
      [[ -s "$idx/index-$i.fits" ]] || { log "astrometry.net-Index $i laden"; curl -fsSL -o "$idx/index-$i.fits" "$ASTROMETRY_INDEX/index-$i.fits"; }
    done
  fi

  install_units
  if (( enable_timer )); then
    systemctl enable --now siril-auto.timer
    log "Timer aktiv (alle 30 min)"
  fi

  cat <<TXT

Fertig. Nächste Schritte:
  1. /etc/siril-auto.env anpassen (SEESTAR_IP, RCLONE_SRC_PATH, MQTT_*, ggf. IMMICH_*)
  2. Testlauf:   sudo -u $SIRIL_USER $REPO_DIR/siril/siril-auto.sh --dry-run
  3. Timer an:   systemctl enable --now siril-auto.timer
Update später: bash $0 update
TXT
}

update_mode() {
  [[ -d "$REPO_DIR/.git" ]] || die "$REPO_DIR ist kein Git-Repo – erst 'container' ausführen"
  log "Repo aktualisieren"
  git -C "$REPO_DIR" pull -q --ff-only
  fetch_catalog
  install_units
  systemctl is-enabled --quiet siril-auto.timer && systemctl restart siril-auto.timer
  log "Aktuell: $(git -C "$REPO_DIR" log -1 --format='%h %s')"
}

case "$MODE" in
  host)      host_mode "$@" ;;
  container) container_mode "$@" ;;
  update)    update_mode ;;
  *) sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac
