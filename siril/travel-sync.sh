#!/usr/bin/env bash
#
# travel-sync.sh – Seestar-Frames von unterwegs ueber Tailscale aufs NAS holen.
#
# Laeuft zu Hause im Siril-LXC. Das Seestar haengt unterwegs am GL.iNet-Reiserouter,
# dessen Subnetz per Tailscale ins Heimnetz geroutet ist (docs/04, Szenario B).
# Geholt werden standardmaessig nur Einzelframes (*_sub) der letzten Tage – das spart Volumen.
#
# Aufruf (als root oder siril):
#   travel-sync.sh --ip 192.168.8.XX --size                  # nur anzeigen: Dateien, GB, Dauer
#   travel-sync.sh --ip 192.168.8.XX --since 2d --stack      # holen, danach siril-auto
#   travel-sync.sh --ip 192.168.8.XX --target "M 31" --bwlimit 2M --background
#
# Optionen:
#   --ip <ip>        IP des Seestar im Reiserouter-Netz (Pflicht)
#   --since <dauer>  nur Dateien juenger als <dauer> (rclone: 12h, 2d, 1w; Default 2d)
#   --target <name>  nur dieses Ziel (Ordnername in der App, z. B. "M 31")
#   --all            auch Seestar-eigene Stacks/JPGs, nicht nur *_sub
#   --bwlimit <rate> Bandbreite begrenzen (z. B. 2M = 2 MB/s), schont den Handy-Tarif
#   --size           nur Umfang anzeigen, nichts kopieren
#   --dry-run        rclone --dry-run
#   --stack          danach siril-auto.sh --no-fetch starten
#   --background     als systemd-Unit "seestar-travel-sync" weiterlaufen (SSH darf abreissen)
#
set -euo pipefail

CONFIG="${SIRIL_AUTO_CONFIG:-/etc/siril-auto.env}"
# shellcheck source=/dev/null
source "$CONFIG"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RCLONE_REMOTE="${RCLONE_REMOTE:-seestar}"
LOCK_FILE="${LOCK_FILE:-/run/lock/siril-auto.lock}"
RCLONE="${RCLONE_BIN:-rclone}"
SMB_PORT="${SMB_PORT:-445}"

log() { echo "$(date '+%F %T') $*"; }
die() { echo "FEHLER: $*" >&2; exit 2; }
usage() { sed -n '2,24p' "$0" | sed 's/^# \{0,1\}//'; exit 2; }

IP=""; SINCE="2d"; TARGET=""; ALL=0; BWLIMIT=""; SIZE=0; DRY=0; STACK=0; BG=0
ORIG_ARGS=("$@")
while [[ $# -gt 0 ]]; do
  case "$1" in
    --ip)         IP="${2:-}"; shift ;;
    --since)      SINCE="${2:-}"; shift ;;
    --target)     TARGET="${2:-}"; shift ;;
    --all)        ALL=1 ;;
    --bwlimit)    BWLIMIT="${2:-}"; shift ;;
    --size)       SIZE=1 ;;
    --dry-run)    DRY=1 ;;
    --stack)      STACK=1 ;;
    --background) BG=1 ;;
    -h|--help)    usage ;;
    *)            die "unbekannte Option: $1" ;;
  esac
  shift
done
[[ "$IP" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "--ip <Seestar-IP im Reiserouter-Netz> fehlt"
[[ "$SINCE" =~ ^[0-9]+(ms|s|m|h|d|w|M|y)?$ || "$SINCE" == "off" ]] || die "--since: z. B. 12h, 2d, 1w oder off"

# ---------------------------------------------------------------- Hintergrund
if (( BG )); then
  command -v systemd-run >/dev/null || die "systemd-run fehlt"
  rest=(); for a in "${ORIG_ARGS[@]}"; do [[ "$a" == "--background" ]] || rest+=("$a"); done
  unit_args=(--unit=seestar-travel-sync --collect --property=Nice=10 \
    "--setenv=SIRIL_AUTO_CONFIG=$CONFIG")
  [[ $EUID -eq 0 ]] && unit_args+=(--uid=siril --gid=siril)
  systemd-run "${unit_args[@]}" "$HERE/travel-sync.sh" "${rest[@]}"
  log "Läuft im Hintergrund. Fortschritt: journalctl -u seestar-travel-sync -f"
  exit 0
fi

# ---------------------------------------------------------------- Erreichbarkeit
if ! timeout 5 bash -c ">/dev/tcp/${IP}/${SMB_PORT}" 2>/dev/null; then
  cat >&2 <<TXT
FEHLER: Seestar unter $IP (SMB, Port $SMB_PORT) nicht erreichbar. Prüfen:
  1. Reiserouter im Tailnet, Route seines Netzes in der Tailscale-Adminkonsole freigegeben
  2. Tailscale-LXC zu Hause mit --accept-routes
  3. Statische Route für das Reiserouter-Netz über den Tailscale-LXC (Fritzbox oder hier im LXC):
       ip route add ${IP%.*}.0/24 via <IP des Tailscale-LXC>
  4. Seestar eingeschaltet und im WLAN des Reiserouters
TXT
  exit 1
fi

src="${RCLONE_REMOTE},host=${IP}:${RCLONE_SRC_PATH}"
filters=()
if [[ -n "$TARGET" ]]; then
  filters+=(--include "${TARGET}_sub/**")
  (( ALL )) && filters+=(--include "${TARGET}/**")
elif (( ! ALL )); then
  filters+=(--include "*_sub/**")
fi
[[ "$SINCE" != "off" ]] && filters+=(--max-age "$SINCE")

if (( SIZE )); then
  log "Umfang auf dem Seestar ($IP, ${TARGET:-alle Ziele}, seit $SINCE):"
  json="$("$RCLONE" size --json "$src" "${filters[@]}")"
  python3 - "$json" "${BWLIMIT:-}" <<'PY'
import json, re, sys
d = json.loads(sys.argv[1]); b = d.get("bytes", 0)
print(f"  {d.get('count', 0)} Dateien, {b / 1e9:.2f} GB")
for rate in ([sys.argv[2]] if sys.argv[2] else ["1M", "5M"]):
    m = re.fullmatch(r"(\d+(?:\.\d+)?)([KMG]?)", rate)
    if m:
        bps = float(m[1]) * {"": 1, "K": 1e3, "M": 1e6, "G": 1e9}[m[2]]
        print(f"  bei {rate}B/s ≈ {b / bps / 3600:.1f} h")
PY
  exit 0
fi

exec 9>"$LOCK_FILE"
flock -n 9 || die "siril-auto läuft gerade – später erneut versuchen"

args=(copy "$src" "$MIRROR_DIR" "${filters[@]}" --transfers 2 --checkers 4
      --retries 10 --low-level-retries 20 --contimeout 30s --timeout 5m
      --stats 1m --stats-one-line --log-level NOTICE)
[[ -n "$BWLIMIT" ]] && args+=(--bwlimit "$BWLIMIT")
(( DRY )) && args+=(--dry-run)
log "Hole Frames von $IP (${TARGET:-alle Ziele}, seit $SINCE${BWLIMIT:+, max. ${BWLIMIT}B/s})"
if "$RCLONE" "${args[@]}"; then
  log "Kopieren fertig"
else
  log "WARN: rclone meldete Fehler – erneut aufrufen setzt fort (bereits Kopiertes wird übersprungen)"
  exit 1
fi
flock -u 9

if (( STACK && ! DRY )); then
  log "Starte siril-auto (ohne erneutes Abholen)"
  exec "$HERE/siril-auto.sh" --no-fetch
fi
