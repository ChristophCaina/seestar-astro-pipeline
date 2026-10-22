#!/usr/bin/env bash
#
# siril-auto.sh – Seestar-Frames abholen, pro Ziel/Filter/Nacht mit Siril stacken,
# Master ueber alle Naechte bilden, plate-solven, auf dem NAS ablegen,
# fuer Immich exportieren und die Galerie fuettern.
#
#   1. Seestar erreichbar?  -> rclone copy (SMB) nach MIRROR_DIR  (nur ergaenzen, nie loeschen)
#   2. Alle *_sub/*.fit nach Ziel + Filter + Nacht gruppieren (Alias-Liste fuer Zielnamen)
#   3. Neue / gewachsene Naechte stacken            -> <ziel>_<nacht>_<filter>_siril
#   4. Ziele mit >= 2 Naechten: Master-Stack        -> <ziel>_master_<filter>_siril
#   5. Je Stack: platesolve, JPG mit Aufnahmedatum (Immich), meta.json (Galerie)
#   6. Status per MQTT (optional), Galerie neu bauen (optional)
#
# Aufruf:  siril-auto.sh [--no-fetch] [--force] [--dry-run] [--master-full]
#   --master-full  Master diesmal aus ALLEN Einzelframes statt aus den Nacht-Stacks
#
set -euo pipefail

CONFIG="${SIRIL_AUTO_CONFIG:-/etc/siril-auto.env}"
# shellcheck source=/dev/null
source "$CONFIG"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB_DIR="${LIB_DIR:-$HERE/lib}"
SIRIL_SCRIPT="${SIRIL_SCRIPT:-$HERE/seestar_stack.ssf}"
SIRIL_MASTER_SCRIPT="${SIRIL_MASTER_SCRIPT:-$HERE/seestar_master.ssf}"
SIRIL_SOLVE_SCRIPT="${SIRIL_SOLVE_SCRIPT:-$HERE/seestar_solve.ssf}"
CATALOG="${CATALOG:-$HERE/data/NGC.csv}"
ALIAS_FILE="${ALIAS_FILE:-/etc/siril-auto.aliases}"
MIN_FRAMES="${MIN_FRAMES:-20}"
MASTER_MIN_NIGHTS="${MASTER_MIN_NIGHTS:-2}"
MASTER_STACK_METHOD="${MASTER_STACK_METHOD:-rej n}"
PLATESOLVE="${PLATESOLVE:-1}"
SOLVE_ARGS="${SOLVE_ARGS:--noflip}"
WRITE_META="${WRITE_META:-1}"
DEFAULT_EXPOSURE="${DEFAULT_EXPOSURE:-10}"
LOCK_FILE="${LOCK_FILE:-/run/lock/siril-auto.lock}"

NO_FETCH=0; FORCE=0; DRY_RUN=0; MASTER_FULL=0
for arg in "$@"; do
  case "$arg" in
    --no-fetch)    NO_FETCH=1 ;;
    --force)       FORCE=1 ;;
    --dry-run)     DRY_RUN=1 ;;
    --master-full) MASTER_FULL=1 ;;
    *) echo "Unbekannte Option: $arg" >&2; exit 2 ;;
  esac
done

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "Laeuft bereits – Abbruch."; exit 0
fi

log() { echo "$(date '+%F %T') $*"; }

# ---------------------------------------------------------------- MQTT
mqtt_args() { MQ=(-h "$MQTT_HOST" -p "${MQTT_PORT:-1883}"); [[ -n "${MQTT_USER:-}" ]] && MQ+=(-u "$MQTT_USER" -P "$MQTT_PASS"); return 0; }

mqtt_pub() {   # mqtt_pub <subtopic> <payload> [retain]
  [[ "${MQTT_ENABLED:-0}" == "1" ]] || return 0
  command -v mosquitto_pub >/dev/null || return 0
  mqtt_args; local args=("${MQ[@]}" -t "${MQTT_TOPIC}/$1" -m "$2")
  [[ "${3:-}" == "retain" ]] && args+=(-r)
  mosquitto_pub "${args[@]}" 2>/dev/null || log "WARN: MQTT-Publish fehlgeschlagen"
}

json_escape() { local s=${1//\\/\\\\}; s=${s//\"/\\\"}; printf '%s' "$s"; }

status() {   # status <state> [target] [night] [frames] [message]
  mqtt_pub status "$(printf '{"state":"%s","target":"%s","night":"%s","frames":%s,"message":"%s","timestamp":"%s"}' \
    "$1" "$(json_escape "${2:-}")" "${3:-}" "${4:-0}" "$(json_escape "${5:-}")" "$(date -Iseconds)")" retain
}

mqtt_discovery() {
  [[ "${MQTT_ENABLED:-0}" == "1" ]] || return 0
  command -v mosquitto_pub >/dev/null || return 0
  local dev='"device":{"identifiers":["siril_auto"],"name":"Siril Auto-Stacking","manufacturer":"siril-auto.sh"}'
  mqtt_args
  mosquitto_pub "${MQ[@]}" -r -t "homeassistant/sensor/siril_auto_status/config" \
    -m "{\"name\":\"Status\",\"unique_id\":\"siril_auto_status\",\"state_topic\":\"${MQTT_TOPIC}/status\",\"value_template\":\"{{ value_json.state }}\",\"json_attributes_topic\":\"${MQTT_TOPIC}/status\",\"icon\":\"mdi:telescope\",${dev}}" 2>/dev/null || true
  mosquitto_pub "${MQ[@]}" -r -t "homeassistant/sensor/siril_auto_last/config" \
    -m "{\"name\":\"Letztes Ergebnis\",\"unique_id\":\"siril_auto_last\",\"state_topic\":\"${MQTT_TOPIC}/last\",\"value_template\":\"{{ value_json.target }} ({{ value_json.night }})\",\"json_attributes_topic\":\"${MQTT_TOPIC}/last\",\"icon\":\"mdi:image-filter-hdr\",${dev}}" 2>/dev/null || true
}

# ---------------------------------------------------------------- Helfer
slugify() { printf '%s' "$1" | tr ' /' '__' | tr -cd 'A-Za-z0-9_.+-'; }

seestar_reachable() {
  [[ -n "${SEESTAR_IP:-}" ]] || return 1
  timeout 3 bash -c ">/dev/tcp/${SEESTAR_IP}/445" 2>/dev/null
}

declare -A ALIAS=()
if [[ -f "$ALIAS_FILE" ]]; then
  while IFS='=' read -r k v; do
    k="$(echo "$k" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"; v="$(echo "$v" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
    [[ -z "$k" || "$k" == \#* || -z "$v" ]] && continue
    ALIAS["$k"]="$v"
  done < "$ALIAS_FILE"
fi
canon_target() { local t="$1"; printf '%s' "${ALIAS[$t]:-$t}"; }

# Liefert "<filter>\t<belichtung>\t<nacht YYYY-MM-DD>\t<zeit YYYY-MM-DD HH:MM:SS>".
# Seestar-Dateinamen: Light_<Ziel>_10.0s_IRCUT_20261018-213045.fit
# Frames vor 12 Uhr zaehlen zur Nacht des Vortags. Fallback: Aenderungszeit.
frame_info() {
  local f b d t filter="UNK" exp="$DEFAULT_EXPOSURE"
  f="$1"; b="$(basename "$f")"
  if [[ "$b" =~ _([0-9]+(\.[0-9]+)?)s_([A-Za-z0-9]+)_(20[0-9]{6})-([0-9]{6}) ]]; then
    exp="${BASH_REMATCH[1]}"; filter="${BASH_REMATCH[3]^^}"; d="${BASH_REMATCH[4]}"; t="${BASH_REMATCH[5]}"
  elif [[ "$b" =~ _(20[0-9]{6})-([0-9]{6}) ]]; then
    d="${BASH_REMATCH[1]}"; t="${BASH_REMATCH[2]}"
  else
    d=$(date -r "$f" +%Y%m%d); t=$(date -r "$f" +%H%M%S)
  fi
  exp="${exp%.0}"
  local ts="${d:0:4}-${d:4:2}-${d:6:2} ${t:0:2}:${t:2:2}:${t:4:2}" night
  if (( 10#${t:0:2} < 12 )); then night="$(date -d "@$(( $(date -d "$d" +%s) - 86400 ))" +%F)"
  else night="$(date -d "$d" +%F)"; fi
  printf '%s\t%s\t%s\t%s\n' "$filter" "$exp" "$night" "$ts"
}

# Siril-Version -> Gewichtungsoption fuer den Master
weight_option() {
  local v
  v="$("$SIRIL_BIN" --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+' | head -1 || true)"
  case "$v" in
    1.0|1.1|1.2) echo "-weight_from_nbstack" ;;
    *)           echo "-weight=nbstack" ;;
  esac
}

render_script() {   # render_script <vorlage> <ziel>
  sed -e "s|@STACK_METHOD@|${MASTER_STACK_METHOD}|g" -e "s|@WEIGHT@|${WEIGHT_OPT:-}|g" \
      -e "s|@SOLVE_ARGS@|${SOLVE_ARGS}|g" "$1" > "$2"
}

# ---------------------------------------------------------------- Stack-Lauf
# run_stack <art night|master> <ziel> <filter> <label> <belichtung> <naechte-spec> <zeitstempel> <frames> <vorlage> <dateien…>
run_stack() {
  local kind="$1" target="$2" filter="$3" label="$4" exp="$5" spec="$6" ts="$7" frames="$8" tmpl="$9"; shift 9
  local slug work base jpg
  slug="$(slugify "$target")"
  base="${slug}_${label}_${filter}_siril"
  work="$WORK_DIR/$base"
  rm -rf "$work"; mkdir -p "$work/light"
  local i=0
  for f in "$@"; do
    i=$((i+1))
    if [[ "$kind" == "master" ]]; then cp "$f" "$work/light/stack_$(printf '%03d' $i).fit"
    else ln "$f" "$work/light/" 2>/dev/null || cp "$f" "$work/light/"; fi
  done
  render_script "$tmpl" "$work/run.ssf"

  if ! "$SIRIL_BIN" -d "$work" -s "$work/run.ssf" > "$work/siril.log" 2>&1 \
     || [[ ! -f "$work/result_linear.fit" || ! -f "$work/result_preview.jpg" ]]; then
    log "FEHLER $target $label $filter – siehe $work/siril.log"
    [[ "${KEEP_WORK_ON_ERROR:-1}" == "1" ]] || rm -rf "$work"
    return 1
  fi

  if [[ "$PLATESOLVE" == "1" ]]; then
    render_script "$SIRIL_SOLVE_SCRIPT" "$work/solve.ssf"
    if "$SIRIL_BIN" -d "$work" -s "$work/solve.ssf" >> "$work/siril.log" 2>&1; then
      log "      Plate-Solving ok"
    else
      log "      WARN: Plate-Solving fehlgeschlagen – Bild ohne Annotationen"
    fi
  fi

  mkdir -p "$STACK_DIR/$slug" "$EXPORT_DIR/$slug"
  cp "$work/result_linear.fit" "$STACK_DIR/$slug/$base.fit"
  [[ -f "$work/result_preview.tif" ]] && cp "$work/result_preview.tif" "$STACK_DIR/$slug/$base.tif"
  cp "$work/siril.log" "$STACK_DIR/$slug/$base.log"
  jpg="$EXPORT_DIR/$slug/$base.jpg"
  cp "$work/result_preview.jpg" "$jpg"

  # Aufnahmedatum setzen, damit Immich das Bild in die richtige Nacht einsortiert
  if command -v exiftool >/dev/null; then
    exiftool -q -overwrite_original \
      "-DateTimeOriginal=${ts//-/:}" "-CreateDate=${ts//-/:}" \
      "-ImageDescription=${target} – ${frames} Frames – ${filter} – Siril" \
      "-Software=Siril (siril-auto)" "$jpg" || true
  fi
  touch -d "$ts" "$jpg"

  if [[ "$WRITE_META" == "1" ]]; then
    local cat_args=()
    [[ -f "$CATALOG" ]] && cat_args+=(--catalog "$CATALOG")
    [[ -f "${CATALOG%/*}/addendum.csv" ]] && cat_args+=(--catalog "${CATALOG%/*}/addendum.csv")
    [[ "${NO_FLIP_Y:-0}" == "1" ]] && cat_args+=(--no-flip-y)
    python3 "$LIB_DIR/write_meta.py" --fits "$STACK_DIR/$slug/$base.fit" --jpg "$jpg" \
      --out "$STACK_DIR/$slug/$base.meta.json" --target "$target" --filter "$filter" \
      --exposure "$exp" --stack "$kind" --nights "$spec" "${cat_args[@]}" \
      || log "      WARN: meta.json konnte nicht geschrieben werden"
  fi

  rm -rf "$work"
  log "OK    $target $label $filter -> $jpg"
  mqtt_pub last "$(printf '{"target":"%s","night":"%s","filter":"%s","stack":"%s","frames":%s,"jpg":"%s","fit":"%s","timestamp":"%s"}' \
    "$(json_escape "$target")" "$label" "$filter" "$kind" "$frames" "$(json_escape "$jpg")" \
    "$(json_escape "$STACK_DIR/$slug/$base.fit")" "$(date -Iseconds)")" retain
  return 0
}

# ---------------------------------------------------------------- Start
SIRIL_BIN="${SIRIL_BIN:-$(command -v siril-cli || true)}"
if [[ -z "$SIRIL_BIN" && $DRY_RUN -eq 0 ]]; then
  log "FEHLER: siril-cli nicht gefunden"; status error "" "" 0 "siril-cli fehlt"; exit 1
fi
[[ $DRY_RUN -eq 0 ]] && WEIGHT_OPT="$(weight_option)"

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

# 2) Index: Ziel <TAB> Filter <TAB> Belichtung <TAB> Nacht <TAB> Zeit <TAB> Pfad ----
INDEX="$(mktemp)"; trap 'rm -f "$INDEX" "$INDEX".*' EXIT
while IFS= read -r -d '' f; do
  dir="$(basename "$(dirname "$f")")"
  target="$(canon_target "${dir%_sub}")"
  printf '%s\t%s\t%s\n' "$target" "$(frame_info "$f")" "$f" >> "$INDEX"
done < <(find "$MIRROR_DIR" -type f -path '*_sub/*' \( -iname '*.fit' -o -iname '*.fits' \) -print0)

if [[ ! -s "$INDEX" ]]; then
  log "Keine Einzelframes gefunden."; status idle; exit 0
fi
cut -f1,2,4 "$INDEX" | sort -u > "$INDEX.nights"      # Ziel, Filter, Nacht
cut -f1,2 "$INDEX" | sort -u > "$INDEX.targets"       # Ziel, Filter

# 3) Naechte ------------------------------------------------------------
processed=0; failed=0
while IFS=$'\t' read -r target filter night; do
  slug="$(slugify "$target")"
  key="${slug}_${filter}_${night}"
  mapfile -t files < <(awk -F'\t' -v t="$target" -v fl="$filter" -v n="$night" '$1==t && $2==fl && $4==n {print $6}' "$INDEX" | sort)
  count=${#files[@]}
  first_ts="$(awk -F'\t' -v t="$target" -v fl="$filter" -v n="$night" '$1==t && $2==fl && $4==n {print $5}' "$INDEX" | sort | head -1)"
  exp="$(awk -F'\t' -v t="$target" -v fl="$filter" -v n="$night" '$1==t && $2==fl && $4==n {print $3}' "$INDEX" | sort | uniq -c | sort -rn | awk 'NR==1{print $2}')"

  if (( count < MIN_FRAMES )); then
    log "SKIP $target $filter $night: nur $count Frames (< $MIN_FRAMES)"; continue
  fi
  state_file="$STATE_DIR/$key.done"; fail_file="$STATE_DIR/$key.failed"
  [[ $FORCE -eq 0 && -f "$state_file" && "$(cat "$state_file")" == "$count" ]] && continue
  [[ $FORCE -eq 0 && -f "$fail_file" && "$(cat "$fail_file")" == "$count" ]] && continue

  log "STACK $target $filter $night: $count Frames à ${exp}s (Start $first_ts)"
  if [[ $DRY_RUN -eq 1 ]]; then processed=$((processed+1)); continue; fi
  status processing "$target" "$night" "$count"
  if run_stack night "$target" "$filter" "$night" "$exp" "$night:$count" "$first_ts" "$count" "$SIRIL_SCRIPT" "${files[@]}"; then
    echo "$count" > "$state_file"; rm -f "$fail_file"; processed=$((processed+1))
  else
    echo "$count" > "$fail_file"; failed=$((failed+1))
    status error "$target" "$night" "$count" "Siril fehlgeschlagen"
  fi
done < "$INDEX.nights"

# 4) Master je Ziel + Filter ----------------------------------------------
while IFS=$'\t' read -r target filter; do
  slug="$(slugify "$target")"
  mkey="${slug}_${filter}_master"
  spec=""; nights=(); fits=(); frames=0
  for sf in "$STATE_DIR/${slug}_${filter}"_20*.done; do
    [[ -f "$sf" ]] || continue
    n="$(basename "$sf" .done)"; n="${n##*_}"; c="$(cat "$sf")"
    fit="$STACK_DIR/$slug/${slug}_${n}_${filter}_siril.fit"
    [[ -f "$fit" || $DRY_RUN -eq 1 ]] || continue
    nights+=("$n"); fits+=("$fit"); frames=$((frames + c)); spec+="${spec:+,}$n:$c"
  done
  (( ${#nights[@]} >= MASTER_MIN_NIGHTS )) || continue
  mode="nights"; [[ $MASTER_FULL -eq 1 ]] && mode="full"
  state_file="$STATE_DIR/$mkey.done"; fail_file="$STATE_DIR/$mkey.failed"
  old="$(cat "$state_file" 2>/dev/null || true)"
  # Neu rechnen, wenn sich die Naechte geaendert haben – oder --master-full / --force
  if [[ $FORCE -eq 0 && $MASTER_FULL -eq 0 && "${old#*|}" == "$spec" ]]; then continue; fi
  [[ $FORCE -eq 0 && $MASTER_FULL -eq 0 && "$(cat "$fail_file" 2>/dev/null || true)" == "$spec" ]] && continue

  last_night="${nights[-1]}"
  ts="$(awk -F'\t' -v t="$target" -v fl="$filter" -v n="$last_night" '$1==t && $2==fl && $4==n {print $5}' "$INDEX" | sort | head -1)"
  exp="$(awk -F'\t' -v t="$target" -v fl="$filter" '$1==t && $2==fl {print $3}' "$INDEX" | sort | uniq -c | sort -rn | awk 'NR==1{print $2}')"
  log "MASTER $target $filter ($mode): ${#nights[@]} Nächte, $frames Frames"
  if [[ $DRY_RUN -eq 1 ]]; then processed=$((processed+1)); continue; fi
  status processing "$target" "master" "$frames"

  if [[ "$mode" == "full" ]]; then
    mapfile -t subs < <(for n in "${nights[@]}"; do awk -F'\t' -v t="$target" -v fl="$filter" -v n="$n" '$1==t && $2==fl && $4==n {print $6}' "$INDEX"; done | sort)
    ok=0; run_stack master "$target" "$filter" master "$exp" "$spec" "${ts:-${last_night} 22:00:00}" "$frames" "$SIRIL_SCRIPT" "${subs[@]}" && ok=1
  else
    ok=0; run_stack master "$target" "$filter" master "$exp" "$spec" "${ts:-${last_night} 22:00:00}" "$frames" "$SIRIL_MASTER_SCRIPT" "${fits[@]}" && ok=1
  fi
  if (( ok )); then echo "$mode|$spec" > "$state_file"; rm -f "$fail_file"; processed=$((processed+1))
  else echo "$spec" > "$fail_file"; failed=$((failed+1)); status error "$target" "master" "$frames" "Master fehlgeschlagen"; fi
done < "$INDEX.targets"

log "Fertig: $processed verarbeitet, $failed Fehler"

# 5) Galerie neu bauen, wenn sich etwas geaendert hat (optional)
if (( processed > 0 )) && [[ -n "${GALLERY_BUILD:-}" && $DRY_RUN -eq 0 ]]; then
  log "Baue Galerie neu"
  python3 "$GALLERY_BUILD" --stacks "$STACK_DIR" --out "${GALLERY_OUT:-$ASTRO_ROOT/web}" || log "WARN: Galerie-Build fehlgeschlagen"
fi
if (( failed == 0 )); then status idle "" "" 0 "$processed verarbeitet"; fi
exit $(( failed > 0 ? 1 : 0 ))
