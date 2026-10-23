#!/usr/bin/env bash
#
# install-tailscale-lxc.sh – Tailscale als Subnet-Router in einem Proxmox-LXC.
#
# Damit erreichst du von unterwegs das ganze Heimnetz (HA, Galerie, Immich, seestar_alp)
# ohne Portfreigabe – auch hinter CGNAT.
#
# Auf dem PROXMOX-HOST (legt bei Bedarf den Container an, richtet TUN ein, installiert Tailscale):
#   bash install-tailscale-lxc.sh host <CTID> [--create] [--template <vztmpl>] [--auth-key <tskey-…>]
#
# Im CONTAINER (wird vom Host-Modus automatisch aufgerufen, geht aber auch einzeln):
#   bash install-tailscale-lxc.sh container [--routes 192.168.52.0/24] [--auth-key <tskey-…>] [--hostname heim]
#
set -euo pipefail

log() { echo -e "\e[1;36m==>\e[0m $*"; }
die() { echo "FEHLER: $*" >&2; exit 1; }

MODE="${1:-}"; shift || true

# ------------------------------------------------------------------ Host
host_mode() {
  local ctid="${1:-}"; shift || true
  [[ -n "$ctid" ]] || die "CTID fehlt"
  command -v pct >/dev/null || die "pct nicht gefunden – bitte auf dem Proxmox-Host ausführen"
  local create=0 template="" pass=()
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --create)   create=1 ;;
      --template) template="$2"; shift ;;
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
    pct create "$ctid" "$template" --hostname tailscale --cores 1 --memory 512 --swap 256 \
      --rootfs local-lvm:4 --net0 name=eth0,bridge=vmbr0,ip=dhcp \
      --unprivileged 1 --onboot 1 --features nesting=1
  fi

  local conf="/etc/pve/lxc/${ctid}.conf"
  if ! grep -q 'dev/net/tun' "$conf"; then
    log "TUN-Device für Container $ctid freigeben"
    cat >> "$conf" <<'CONF'
lxc.cgroup2.devices.allow: c 10:200 rwm
lxc.mount.entry: /dev/net/tun dev/net/tun none bind,create=file
CONF
    if pct status "$ctid" | grep -q running; then pct reboot "$ctid"; else pct start "$ctid"; fi
  elif ! pct status "$ctid" | grep -q running; then
    pct start "$ctid"
  fi
  sleep 5
  log "Installiere Tailscale im Container"
  pct push "$ctid" "$0" /root/install-tailscale-lxc.sh --perms 0755
  pct exec "$ctid" -- bash /root/install-tailscale-lxc.sh container "${pass[@]}"
}

# ------------------------------------------------------------------ Container
container_mode() {
  local routes="" authkey="" hostname="" ; local extra=()
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --routes)   routes="$2"; shift ;;
      --auth-key) authkey="$2"; shift ;;
      --hostname) hostname="$2"; shift ;;
      *)          extra+=("$1") ;;
    esac
    shift
  done
  [[ $EUID -eq 0 ]] || die "bitte als root ausführen"
  [[ -c /dev/net/tun ]] || die "/dev/net/tun fehlt – erst den Host-Modus ausführen"

  if [[ -z "$routes" ]]; then
    routes="$(ip -4 route show dev eth0 proto kernel scope link 2>/dev/null | awk 'NR==1{print $1}')"
    [[ -n "$routes" ]] || die "Heimnetz nicht erkannt – bitte --routes angeben"
  fi
  # shellcheck source=/dev/null
  . /etc/os-release
  log "System: $PRETTY_NAME – Heimnetz: $routes"

  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y curl ca-certificates gnupg ethtool
  if ! command -v tailscale >/dev/null; then
    log "Tailscale-Paketquelle für ${VERSION_CODENAME} einrichten"
    curl -fsSL "https://pkgs.tailscale.com/stable/debian/${VERSION_CODENAME}.noarmor.gpg" \
      -o /usr/share/keyrings/tailscale-archive-keyring.gpg
    curl -fsSL "https://pkgs.tailscale.com/stable/debian/${VERSION_CODENAME}.tailscale-keyring.list" \
      -o /etc/apt/sources.list.d/tailscale.list
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y tailscale
  fi

  log "IP-Forwarding aktivieren"
  cat > /etc/sysctl.d/99-tailscale.conf <<'SYS'
net.ipv4.ip_forward = 1
net.ipv6.conf.all.forwarding = 1
SYS
  sysctl -p /etc/sysctl.d/99-tailscale.conf || log "Hinweis: sysctl im Container eingeschränkt – Forwarding ggf. auf dem Host aktivieren"

  # Durchsatz-Optimierung fuer Subnet-Router (laut Tailscale-Doku), Fehler unkritisch
  ethtool -K eth0 rx-udp-gro-forwarding on rx-gro-list off 2>/dev/null || true

  systemctl enable --now tailscaled
  local args=(--advertise-routes="$routes" --accept-dns=false --reset)
  [[ -n "$authkey" ]]  && args+=(--auth-key="$authkey")
  [[ -n "$hostname" ]] && args+=(--hostname="$hostname")
  log "tailscale up ${args[*]/--auth-key=*/--auth-key=***} ${extra[*]}"
  tailscale up "${args[@]}" "${extra[@]}"

  cat <<TXT

Fertig. Noch zwei Schritte in der Tailscale-Adminkonsole (https://login.tailscale.com/admin/machines):
  1. Beim Gerät "$(hostname)" → Edit route settings → Route $routes freigeben
  2. Optional: "Disable key expiry", damit der Router nicht nach 180 Tagen neu angemeldet werden muss
Auf Handy/Laptop: Tailscale-App installieren, gleich anmelden – dann ist $routes von unterwegs erreichbar.
TXT
}

case "$MODE" in
  host)      host_mode "$@" ;;
  container) container_mode "$@" ;;
  *) sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac
