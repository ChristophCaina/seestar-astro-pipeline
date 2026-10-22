#!/usr/bin/env bash
#
# siril-auto.sh – Seestar-Frames abholen, pro Ziel und Nacht mit Siril stacken,
# Ergebnisse auf dem NAS ablegen und fuer Immich exportieren.
#
#   1. Seestar erreichbar?  -> rclone copy (SMB) nach MIRROR_DIR  (nur ergaenzen, nie loeschen)
#   2. Alle *_sub/*.fit im Mirror nach Ziel + Nacht gruppieren
#   3. Neue / gewachsene Naechte mit siril-cli stacken
#   4. FIT/TIF -> STACK_DIR, JPG (mit Aufnahmedatum) -> EXPORT_DIR (Immich)
#   5. Status per MQTT an Home Assistant (optional, mit Auto-Discovery)
#
# Aufruf:  siril-auto.sh [--no-fetch] [--force] [--dry-run]
#
set -euo pipefail

CONFIG="${SIRIL_AUTO_CONFIG:-/etc/siril-auto.env}"
# shellcheck source=/dev/null
source "$CONFIG"

NO_FETCH=0; FORCE=0; DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --no-fetch) NO_FETCH=1 ;;
    --force)    FORCE=1 ;;
    --dry-run)  DRY_RUN=1 ;;
    *) echo "Unbekannte Option: $arg" >&2; exit 2 ;;
  esac
done

LOCK_FILE="/run/lock/siril-auto.lock"
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "Laeuft bereits – Abbruch."; exit 0
fi

log() { echo "$(date '+%F %T') $*"; }

# ---------------------------------------------------------------- MQTT
mqtt_pub() {   # mqtt_pub <subtopic> <payload> [retain]
  [[ "${MQTT_ENABLED:-0}" == "1" ]] || return 0
  command -v mosquitto_pub >/dev/null || return 0
  local args=(-h "$MQTT_HOST" -p "${MQTT_PORT:-1883}" -t "${MQTT_TOPIC}/$1" -m "$2")
  [[ -n "${MQTT_USER:-}" ]] && args+=(-u "$MQTT_USER" -P "$MQTT_PASS")
  [[ "${3:-}" == "retain" ]] && args+=(-r)
  mosquitto_pub "${args[@]}" 2>/dev/null || log "WARN: MQTT-Publish fehlgeschlagen"
}

json_escape() { local s=${1//\\/\\\\}; s=${s//\"/\\\"}; printf '%s' "$s"; }

status() {   # status <state> [target] [night] [frames] [message]
  local payload
  payload=$(printf '{"state":"%s","target":"%s","night":"%s","frames":%s,"message":"%s","timestamp":"%s"}' \
    "$1" "$(json_escape "${2:-}")" "${3:-}" "${4:-0}" "$(json_escape "${5:-}")" "$(date -Iseconds)")
  mqtt_pub status "$payload" retain
}

mqtt_discovery() {
  [[ "${MQTT_ENABLED:-0}" == "1" ]] || return 0
  local dev='"device":{"identifiers":["siril_auto"],"name":"Siril Auto-Stacking","manufacturer":"siril-auto.sh"}'
  mqtt_pub_raw() { # direkt auf homeassistant/... publizieren
    local args=(-h "$MQTT_HOST" -p "${MQTT_PORT:-1883}" -t "$1" -m "$2" -r)
    [[ -n "${MQTT_USER:-}" ]] && args+=(-u "$MQTT_USER" -P "$MQTT_PASS")
    mosquitto_pub "${args[@]}" 2>/dev/null || true
  }
  command -v mosquitto_pub >/dev/null || return 0
  mqtt_pub_raw "homeassistant/sensor/siril_auto_status/config" \
    "{\"name\":\"Status\",\"unique_id\":\"siril_auto_status\",\"state_topic\":\"${MQTT_TOPIC}/status\",\"value_template\":\"{{ value_json.state }}\",\"json_attributes_topic\":\"${MQTT_TOPIC}/status\",\"icon\":\"mdi:telescope\",${dev}}"
  mqtt_pub_raw "homeassistant/sensor/siril_auto_last/config" \
    "{\"name\":\"Letztes Ergebnis\",\"unique_id\":\"siril_auto_last\",\"state_topic\":\"${MQTT_TOPIC}/last\",\"value_template\":\"{{ value_json.target }} ({{ value_json.night }})\",\"json_attributes_topic\":\"${MQTT_TOPIC}/last\",\"icon\":\"mdi:image-filter-hdr\",${dev}}"
}

# ---------------------------------------------------------------- Helfer
slugify() { printf '%s' "$1" | tr ' /' '__' | tr -cd 'A-Za-z0-9_.+-'; }

seestar_reachable() {
  [[ -n "${SEESTAR_IP:-}" ]] || return 1
  timeout 3 bash -c ">/dev/tcp/${SEESTAR_IP}/445" 2>/dev/null
}

# Liefert "<nacht YYYY-MM-DD>\t<zeit YYYY-MM-DD HH:MM:SS>" fuer eine Datei.
# Seestar-Dateinamen enthalten ..._YYYYMMDD-HHMMSS.fit; Frames vor 12 Uhr
# zaehlen zur Nacht des Vortags. Fallback: Datei-Aenderungszeit.
night_of() {
  local f="$1" d t
  if [[ "$(basename "$f")" =~ _(20[0-9]{6})-([0-9]{6}) ]]; then
    d="${BASH_REMATCH[1]}"; t="${BASH_REMATCH[2]}"
  else
    d=$(date -r "$f" +%Y%m%d); t=$(date -r "$f" +%H%M%S)
  fi
  local ts="${d:0:4}-${d:4:2}-${d:6:2} ${t:0:2}:${t:2:2}:${t:4:2}"
  if (( 10#${t:0:2} < 12 )); then
    printf '%s\t%s\n' "$(date -d "@$(( $(date -d "$d" +%s) - 86400 ))" +%F)" "$ts"
  else
    printf '%s\t%s\n' "$(date -d "$d" +%F)" "$ts"
  fi
}

# ---------------------------------------------------------------- Start
SIRIL_BIN="$(command -v siril-cli || true)"
if [[ -z "$SIRIL_BIN" && $DRY_RUN -eq 0 ]]; then
  log "FEHLER: siril-cli nicht gefunden"; status error "" "" 0 "siril-cli fehlt"; exit 1
fi

mkdir -p "$MIRROR_DIR" "$WORK_DIR" "$STACK_DIR" "$EXPORT_DIR" "$STATE_DIR"
mqtt_discovery

# 1) Abholen ------------------------------------------------------------
if [[ $NO_FETCH -eq 0 ]]; then
  if seestar_reachable; then
    log "Seestar erreichbar – hole neue Dateien"
    status fetching
    if [[ $DRY_RUN -eq 0 ]]; then
      rclone copy "${RCLONE_REMOTE}:${RCLONE_SRC_PATH}" "$MIRROR_DIR" \
        --transfers 4 --checkers 8 --log-level NOTICE \
        || log "WARN: rclone meldete Fehler – verarbeite, was da ist"
    fi
  else
    log "Seestar nicht erreichbar – verarbeite nur vorhandenes Archiv"
  fi
fi

# 2) Index: Ziel <TAB> Nacht <TAB> Zeit <TAB> Pfad ------------------------
INDEX="$(mktemp)"; trap 'rm -f "$INDEX" "$INDEX.groups"' EXIT
while IFS= read -r -d '' f; do
  dir="$(basename "$(dirname "$f")")"
  target="${dir%_sub}"
  printf '%s\t%s\t%s\n' "$target" "$(night_of "$f")" "$f" >> "$INDEX"
done < <(find "$MIRROR_DIR" -type f -path '*_sub/*' \( -iname '*.fit' -o -iname '*.fits' \) -print0)

if [[ ! -s "$INDEX" ]]; then
  log "Keine Einzelframes gefunden."; status idle; exit 0
fi

cut -f1,2 "$INDEX" | sort -u > "$INDEX.groups"

# 3) Verarbeiten ----------------------------------------------------------
processed=0; failed=0
while IFS=$'\t' read -r target night; do
  slug="$(slugify "$target")"
  key="${slug}_${night}"
  mapfile -t files < <(awk -F'\t' -v t="$target" -v n="$night" '$1==t && $2==n {print $4}' "$INDEX" | sort)
  count=${#files[@]}
  first_ts="$(awk -F'\t' -v t="$target" -v n="$night" '$1==t && $2==n {print $3}' "$INDEX" | sort | head -1)"

  if (( count < MIN_FRAMES )); then
    log "SKIP $target $night: nur $count Frames (< $MIN_FRAMES)"; continue
  fi
  state_file="$STATE_DIR/$key.done"
  if [[ $FORCE -eq 0 && -f "$state_file" && "$(cat "$state_file")" == "$count" ]]; then
    continue   # bereits mit gleicher Frame-Anzahl verarbeitet
  fi
  fail_file="$STATE_DIR/$key.failed"
  if [[ $FORCE -eq 0 && -f "$fail_file" && "$(cat "$fail_file")" == "$count" ]]; then
    continue   # mit dieser Frame-Anzahl schon fehlgeschlagen – erst mit --force erneut
  fi

  log "STACK $target $night: $count Frames (Start $first_ts)"
  if [[ $DRY_RUN -eq 1 ]]; then processed=$((processed+1)); continue; fi
  status processing "$target" "$night" "$count"

  work="$WORK_DIR/$key"
  rm -rf "$work"; mkdir -p "$work/light"
  for f in "${files[@]}"; do
    ln "$f" "$work/light/" 2>/dev/null || cp "$f" "$work/light/"
  done

  if "$SIRIL_BIN" -d "$work" -s "$SIRIL_SCRIPT" > "$work/siril.log" 2>&1 \
     && [[ -f "$work/result_linear.fit" && -f "$work/result_preview.jpg" ]]; then

    mkdir -p "$STACK_DIR/$slug" "$EXPORT_DIR/$slug"
    base="${slug}_${night}_siril"
    cp "$work/result_linear.fit" "$STACK_DIR/$slug/$base.fit"
    [[ -f "$work/result_preview.tif" ]] && cp "$work/result_preview.tif" "$STACK_DIR/$slug/$base.tif"
    cp "$work/siril.log" "$STACK_DIR/$slug/$base.log"
    jpg="$EXPORT_DIR/$slug/$base.jpg"
    cp "$work/result_preview.jpg" "$jpg"

    # Aufnahmedatum setzen, damit Immich das Bild in die richtige Nacht einsortiert
    if command -v exiftool >/dev/null; then
      exiftool -q -overwrite_original \
        "-DateTimeOriginal=${first_ts//-/:}" "-CreateDate=${first_ts//-/:}" \
        "-ImageDescription=${target} – ${count} Frames – Siril" \
        "-Software=Siril (siril-auto)" "$jpg" || true
    fi
    touch -d "$first_ts" "$jpg"

    echo "$count" > "$state_file"; rm -f "$fail_file"
    rm -rf "$work"
    processed=$((processed+1))
    log "OK    $target $night -> $jpg"
    mqtt_pub last "$(printf '{"target":"%s","night":"%s","frames":%s,"jpg":"%s","fit":"%s","timestamp":"%s"}' \
      "$(json_escape "$target")" "$night" "$count" "$(json_escape "$jpg")" \
      "$(json_escape "$STACK_DIR/$slug/$base.fit")" "$(date -Iseconds)")" retain
  else
    failed=$((failed+1)); echo "$count" > "$fail_file"
    log "FEHLER $target $night – siehe $work/siril.log"
    status error "$target" "$night" "$count" "Siril fehlgeschlagen"
    [[ "${KEEP_WORK_ON_ERROR:-1}" == "1" ]] || rm -rf "$work"
  fi
done < "$INDEX.groups"

log "Fertig: $processed verarbeitet, $failed Fehler"

# Galerie neu bauen, wenn sich etwas geaendert hat (optional)
if (( processed > 0 )) && [[ -n "${GALLERY_BUILD:-}" && $DRY_RUN -eq 0 ]]; then
  log "Baue Galerie neu"
  python3 "$GALLERY_BUILD" --stacks "$STACK_DIR" --out "${GALLERY_OUT:-$ASTRO_ROOT/web}" || log "WARN: Galerie-Build fehlgeschlagen"
fi
if (( failed == 0 )); then status idle "" "" 0 "$processed verarbeitet"; fi
exit $(( failed > 0 ? 1 : 0 ))
