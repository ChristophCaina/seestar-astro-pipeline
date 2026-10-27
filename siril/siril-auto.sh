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
#   5. Je Stack: platesolve, JPG mit Aufnahmedatum (Immich), meta.json (Galerie),
#      beschriftete Version (*_beschriftet.jpg)
#   6. Status per MQTT, Galerie neu bauen, Immich-Stacks/Album (jeweils optional)
#
# Mehrere Geraete (DEVICES="s50 s30"): je Geraet eigene Quelle, eigenes Rohdaten-Archiv,
# eigene Stacks. Naechte und Master werden nie zwischen Geraeten gemischt. Das erste
# Geraet ist das Hauptgeraet (Dateinamen ohne Zusatz), weitere bekommen _<id> angehaengt.
#
# Aufruf:  siril-auto.sh [--no-fetch] [--force] [--dry-run] [--master-full]
#   --master-full  Master diesmal aus ALLEN Einzelframes statt aus den Nacht-Stacks
#
set -euo pipefail

CONFIG="${SIRIL_AUTO_CONFIG:-/etc/siril-auto.env}"
set -a   # alle Variablen auch fuer die Python-Helfer exportieren
# shellcheck source=/dev/null
source "$CONFIG"
set +a

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB_DIR="${LIB_DIR:-$HERE/lib}"
SIRIL_SCRIPT="${SIRIL_SCRIPT:-$HERE/seestar_stack.ssf}"
SIRIL_MASTER_SCRIPT="${SIRIL_MASTER_SCRIPT:-$HERE/seestar_master.ssf}"
SIRIL_SOLVE_SCRIPT="${SIRIL_SOLVE_SCRIPT:-$HERE/seestar_solve.ssf}"
SIRIL_CALIB_SCRIPT="${SIRIL_CALIB_SCRIPT:-$HERE/dslr_calib.ssf}"
SIRIL_WIDE_SCRIPT="${SIRIL_WIDE_SCRIPT:-$HERE/wide_stack.ssf}"
WIDE_FILTERS="${WIDE_FILTERS:-WIDE}"          # Filter-Kuerzel im Dateinamen, die als Weitwinkel gelten
WIDE_FOREGROUND="${WIDE_FOREGROUND:-1}"       # zusaetzlich Vordergrund-Stack ohne Registrierung
WIDE_SOLVE="${WIDE_SOLVE:-1}"                 # Plate-Solving per astrometry.net (solve-field)
WIDE_SCALE_LOW="${WIDE_SCALE_LOW:-8}"         # Bildbreite in Grad (Suchbereich)
WIDE_SCALE_HIGH="${WIDE_SCALE_HIGH:-130}"
WIDE_SOLVE_FLIP_Y="${WIDE_SOLVE_FLIP_Y:-0}"
SOLVE_FIELD="${SOLVE_FIELD:-solve-field}"
CATALOG="${CATALOG:-$HERE/data/NGC.csv}"
ALIAS_FILE="${ALIAS_FILE:-/etc/siril-auto.aliases}"
MIN_FRAMES="${MIN_FRAMES:-20}"
MASTER_MIN_NIGHTS="${MASTER_MIN_NIGHTS:-2}"
MASTER_STACK_METHOD="${MASTER_STACK_METHOD:-rej n}"
PLATESOLVE="${PLATESOLVE:-1}"
SOLVE_ARGS="${SOLVE_ARGS:--noflip}"
WRITE_META="${WRITE_META:-1}"
ANNOTATE="${ANNOTATE:-1}"
IMMICH_SYNC="${IMMICH_SYNC:-0}"
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
  [[ "${DRY_RUN:-0}" == "1" ]] && return 0     # Probelauf meldet nichts an Home Assistant
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

device_reachable() {   # device_reachable <ip> [port]
  [[ -n "${1:-}" ]] || return 1
  timeout 3 bash -c ">/dev/tcp/${1}/${2:-445}" 2>/dev/null
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

# ---------------------------------------------------------------- Geraete
read -ra DEVICE_LIST <<< "${DEVICES:-s50}"
PRIMARY_DEVICE="${DEVICE_LIST[0]}"
for d in "${DEVICE_LIST[@]}"; do
  [[ "$d" =~ ^[a-z][a-z0-9]*$ ]] || { echo "Ungueltige Geraete-ID '$d' (nur a-z, 0-9, Buchstabe zuerst)" >&2; exit 2; }
done
# devcfg <id> <KEY> -> Wert von DEV_<id>_<KEY>; das Hauptgeraet faellt auf die alten Einzelvariablen zurueck
devcfg() {
  local v="DEV_${1}_${2}" def=""
  if [[ "$1" == "$PRIMARY_DEVICE" ]]; then
    case "$2" in
      LABEL)  def="Seestar S50" ;;
      IP)     def="${SEESTAR_IP:-}" ;;
      REMOTE) def="${RCLONE_REMOTE:-}" ;;
      SRC)    def="${RCLONE_SRC_PATH:-}" ;;
      MIRROR) def="$MIRROR_DIR" ;;
    esac
  else
    case "$2" in
      LABEL)  def="$1" ;;
      MIRROR) def="${ASTRO_ROOT}/raw/$1" ;;
    esac
  fi
  local t="DEV_${1}_TYPE"
  case "$2" in
    TYPE)   def="seestar" ;;
    PORT)   def="445" ;;
    SCRIPT) if [[ "${!t:-seestar}" == "dslr" ]]; then def="$HERE/dslr_stack.ssf"; else def="$SIRIL_SCRIPT"; fi ;;
    CALIB)  def="${ASTRO_ROOT}/calib/$1" ;;
  esac
  printf '%s' "${!v:-$def}"
}

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
  local denoise="# denoise (DENOISE=0)"
  [[ "${DENOISE:-1}" == "1" ]] && denoise="denoise ${DENOISE_ARGS:--mod=0.8}"
  # Gruenstich: aus, wenn der Filter (bzw. einer der kombinierten) Dualband ist – $filter aus run_stack
  local rmgreen="rmgreen" f
  for f in ${DUALBAND_FILTERS:-LP}; do
    if [[ "+${filter:-}+" == *"+$f+"* ]]; then rmgreen="# rmgreen (Dualband-Filter $f)"; fi
  done
  [[ "${RMGREEN:-1}" == "0" ]] && rmgreen="# rmgreen (RMGREEN=0)"
  local satu="# satu (SATU_ARGS leer)"
  [[ -n "${SATU_ARGS-0.6}" ]] && satu="satu ${SATU_ARGS-0.6}"
  sed -e "s|@SUBSKY@|${SUBSKY_ARGS:--rbf -samples=20 -tolerance=1.0 -smooth=0.5}|g" \
      -e "s|@DENOISE@|${denoise}|g" -e "s|@RMGREEN@|${rmgreen}|g" -e "s|@SATU@|${satu}|g" -e "s|@STRETCH@|${STRETCH_ARGS:--linked -2.8 0.15}|g" \
      -e "s|@STACK_METHOD@|${MASTER_STACK_METHOD}|g" -e "s|@WEIGHT@|${WEIGHT_OPT:-}|g" \
      -e "s|@SOLVE_ARGS@|${SOLVE_ARGS}|g" -e "s|@CALIBRATE@|${CALIBRATE:-calibrate light -cfa -debayer}|g" \
      -e "s|@PRE@|${CAL_PRE:-}|g" -e "s|@SEQ@|${CAL_SEQ:-frame}|g" -e "s|@NORM@|${CAL_NORM:--nonorm}|g" \
      -e "s|^@FG@|${FG_PREFIX-# }|" "$1" > "$2"
}

is_wide_filter() { [[ " $WIDE_FILTERS " == *" $1 "* ]]; }

# Weitwinkel: astrometry.net auf dem Vorschau-JPG (Siril-platesolve scheitert oft an grossen Feldern)
solve_wide() {   # solve_wide <work> -> <work>/solve/result_preview.wcs
  local work="$1"
  command -v "$SOLVE_FIELD" >/dev/null || { log "      Hinweis: $SOLVE_FIELD fehlt – Weitwinkel ohne Beschriftung"; return 1; }
  mkdir -p "$work/solve"
  "$SOLVE_FIELD" --overwrite --no-plots --downsample 4 --cpulimit 180 --scale-units degwidth \
    --scale-low "$WIDE_SCALE_LOW" --scale-high "$WIDE_SCALE_HIGH" --dir "$work/solve" --new-fits none \
    "$work/result_preview.jpg" >> "$work/siril.log" 2>&1 && [[ -f "$work/solve/result_preview.wcs" ]]
}

# ---------------------------------------------------------------- DSLR-Kalibrierung
# Rohframes: <archiv>/_calib/{Bias,Dark,Flat}_<belichtung>s_..._<datum>-<zeit>.fits (Astro-Pi-App)
# Masters:   <CALIB>/bias.fit, dark_<belichtung>s.fit, flat.fit (Flats der neuesten Nacht)
build_master() {   # build_master <name> <pre> <seq> <norm> <dateien…>
  local name="$1" pre="$2" seq="$3" norm="$4"; shift 4
  local cnt=$# sf="$STATE_DIR/calib_${CUR_DEVICE}_${name}.done" work="$WORK_DIR/calib_${CUR_DEVICE}_${name}"
  [[ $FORCE -eq 0 && -f "$sf" && "$(cat "$sf")" == "$cnt" && -f "$CAL_DIR/$name.fit" ]] && return 0
  log "MASTER-KALIB ${DEVTAG}$name: $cnt Frames"
  (( DRY_RUN )) && return 0
  rm -rf "$work"; mkdir -p "$work/raw" "$CAL_DIR"
  for f in "$@"; do ln "$f" "$work/raw/" 2>/dev/null || cp "$f" "$work/raw/"; done
  [[ -f "$CAL_DIR/bias.fit" ]] && cp "$CAL_DIR/bias.fit" "$work/bias.fit"
  CAL_PRE="$pre" CAL_SEQ="$seq" CAL_NORM="$norm" render_script "$SIRIL_CALIB_SCRIPT" "$work/run.ssf"
  if "${SIRIL_PREFIX[@]}" "$SIRIL_BIN" -d "$work" -s "$work/run.ssf" > "$work/siril.log" 2>&1 && [[ -f "$work/master.fit" ]]; then
    cp "$work/master.fit" "$CAL_DIR/$name.fit"; cp "$work/siril.log" "$CAL_DIR/$name.log"
    echo "$cnt" > "$sf"; rm -rf "$work"
  else
    log "FEHLER Master $name – siehe $work/siril.log"
  fi
}

build_calibration() {
  local raw="$MIRROR_DIR/_calib" f b e list
  [[ -d "$raw" ]] || return 0
  local -a bias=() flats=(); local -A darks=() flatnight=()
  while IFS= read -r -d '' f; do
    b="$(basename "$f")"
    case "${b%%_*}" in
      Bias) bias+=("$f") ;;
      Dark) [[ "$b" =~ _([0-9]+(\.[0-9]+)?)s_ ]] && { e="${BASH_REMATCH[1]}"; e="${e%.0}"; darks[$e]+="$f"$'\n'; } ;;
      Flat) flats+=("$f"); flatnight["$(frame_info "$f" | cut -f3)"]+="$f"$'\n' ;;
    esac
  done < <(find "$raw" -type f \( -iname '*.fit' -o -iname '*.fits' \) -print0 | sort -z)
  (( ${#bias[@]} >= 3 )) && build_master bias "" frame -nonorm "${bias[@]}"
  for e in "${!darks[@]}"; do
    mapfile -t list < <(printf '%s' "${darks[$e]}")
    (( ${#list[@]} >= 3 )) && build_master "dark_${e}s" "" frame -nonorm "${list[@]}"
  done
  if (( ${#flats[@]} >= 3 )); then
    local newest; newest="$(printf '%s\n' "${!flatnight[@]}" | sort | tail -1)"
    mapfile -t list < <(printf '%s' "${flatnight[$newest]}")
    if [[ -f "$CAL_DIR/bias.fit" ]]; then
      build_master flat "calibrate frame -bias=../bias.fit -cfa" pp_frame -norm=mul "${list[@]}"
    else
      build_master flat "" frame -norm=mul "${list[@]}"
    fi
  fi
}

# Kalibrier-Befehl fuer eine DSLR-Nacht (Masters ins Arbeitsverzeichnis kopieren)
dslr_calibrate() {   # dslr_calibrate <work> <belichtung> -> setzt CALIBRATE
  local work="$1" exp="$2" calargs=""
  if [[ -f "$CAL_DIR/dark_${exp}s.fit" ]]; then
    cp "$CAL_DIR/dark_${exp}s.fit" "$work/dark.fit"; calargs+=" -dark=../dark.fit -cc=dark"
  elif [[ -f "$CAL_DIR/bias.fit" ]]; then
    cp "$CAL_DIR/bias.fit" "$work/bias.fit"; calargs+=" -bias=../bias.fit"
  fi
  if [[ -f "$CAL_DIR/flat.fit" ]]; then
    cp "$CAL_DIR/flat.fit" "$work/flat.fit"; calargs+=" -flat=../flat.fit"
  fi
  if [[ -n "$calargs" ]]; then
    CALIBRATE="calibrate light${calargs} -cfa -equalize_cfa -debayer"
  else                       # ohne Masters nur debayern (Sequenz heisst trotzdem pp_light)
    CALIBRATE='cd ../light\nconvert pp_light -debayer -out=../process\ncd ../process'
  fi
  [[ -n "$calargs" ]] && log "      Kalibrierung:${calargs//..\//}"
  return 0
}

# ---------------------------------------------------------------- Stack-Lauf
# run_stack <art night|master> <ziel> <filter> <label> <belichtung> <naechte-spec> <zeitstempel> <frames> <vorlage> <dateien…>
run_stack() {
  local kind="$1" target="$2" filter="$3" label="$4" exp="$5" spec="$6" ts="$7" frames="$8" tmpl="$9"; shift 9
  local slug work base jpg
  slug="$(slugify "$target")"
  base="${slug}_${label}_${filter}${DEV_SUFFIX}_siril"
  work="$WORK_DIR/$base"
  rm -rf "$work"; mkdir -p "$work/light"
  local i=0
  for f in "$@"; do
    i=$((i+1))
    if [[ "$kind" == "master" ]]; then cp "$f" "$work/light/stack_$(printf '%03d' $i).fit"
    else ln "$f" "$work/light/" 2>/dev/null || cp "$f" "$work/light/"; fi
  done
  CALIBRATE=""; FG_PREFIX="# "
  local wide=0
  if is_wide_filter "$filter" && [[ "$kind" == "night" ]]; then
    wide=1; tmpl="$SIRIL_WIDE_SCRIPT"; [[ "$WIDE_FOREGROUND" == "1" ]] && FG_PREFIX=""
    dslr_calibrate "$work" "$exp"
  elif [[ "$CUR_TYPE" == "dslr" && "$tmpl" == "$CUR_SCRIPT" ]]; then
    dslr_calibrate "$work" "$exp"
  fi
  render_script "$tmpl" "$work/run.ssf"

  if ! "${SIRIL_PREFIX[@]}" "$SIRIL_BIN" -d "$work" -s "$work/run.ssf" > "$work/siril.log" 2>&1 \
     || [[ ! -f "$work/result_linear.fit" || ! -f "$work/result_preview.jpg" ]]; then
    log "FEHLER $target $label $filter – siehe $work/siril.log"
    [[ "${KEEP_WORK_ON_ERROR:-1}" == "1" ]] || rm -rf "$work"
    return 1
  fi

  local wcs_args=()
  if (( wide )); then
    if [[ "$WIDE_SOLVE" == "1" ]] && solve_wide "$work"; then
      log "      Plate-Solving (astrometry.net) ok"
      wcs_args=(--wcs-file "$work/solve/result_preview.wcs")
      [[ "$WIDE_SOLVE_FLIP_Y" == "1" ]] && wcs_args+=(--wcs-flip-y)
    else
      log "      WARN: Weitwinkel-Plate-Solving fehlgeschlagen – Bild ohne Beschriftung"
    fi
  elif [[ "$PLATESOLVE" == "1" ]]; then
    render_script "$SIRIL_SOLVE_SCRIPT" "$work/solve.ssf"
    if "${SIRIL_PREFIX[@]}" "$SIRIL_BIN" -d "$work" -s "$work/solve.ssf" >> "$work/siril.log" 2>&1; then
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
  if (( wide )) && [[ -f "$work/result_foreground.jpg" ]]; then
    cp "$work/result_foreground.jpg" "$EXPORT_DIR/$slug/${base}_vordergrund.jpg"
    touch -d "$ts" "$EXPORT_DIR/$slug/${base}_vordergrund.jpg"
    log "      Vordergrund-Stack: ${base}_vordergrund.jpg (zum Zusammenfügen mit dem Himmel)"
  fi

  if [[ "$WRITE_META" == "1" ]]; then
    local cat_args=()
    [[ -f "$CATALOG" ]] && cat_args+=(--catalog "$CATALOG")
    [[ -f "${CATALOG%/*}/addendum.csv" ]] && cat_args+=(--catalog "${CATALOG%/*}/addendum.csv")
    [[ "${NO_FLIP_Y:-0}" == "1" ]] && cat_args+=(--no-flip-y)
    python3 "$LIB_DIR/write_meta.py" --fits "$STACK_DIR/$slug/$base.fit" --jpg "$jpg" \
      --out "$STACK_DIR/$slug/$base.meta.json" --target "$target" --filter "$filter" \
      --exposure "$exp" --stack "$kind" --nights "$spec" "${cat_args[@]}" \
      --device-id "$CUR_DEVICE" --device-label "$CUR_LABEL" "${wcs_args[@]}" \
      || log "      WARN: meta.json konnte nicht geschrieben werden"
    if [[ "$ANNOTATE" == "1" && -f "$STACK_DIR/$slug/$base.meta.json" ]]; then
      if python3 "$LIB_DIR/annotate.py" --meta "$STACK_DIR/$slug/$base.meta.json" \
           --out "$EXPORT_DIR/$slug/${base}_beschriftet.jpg" >/dev/null; then
        touch -d "$ts" "$EXPORT_DIR/$slug/${base}_beschriftet.jpg"
      else
        log "      WARN: Beschriftung fehlgeschlagen"
      fi
    fi
  fi

  rm -rf "$work"
  log "OK    $target $label $filter -> $jpg"
  # Galerie-Adressen: Bild img/<id>.jpg, Detailseite #<group> (wie gallery/site/app.js)
  local gid="${base,,}" gname="$target"
  if [[ -f "$STACK_DIR/$slug/$base.meta.json" ]]; then
    gname="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["target"]["name"])' \
      "$STACK_DIR/$slug/$base.meta.json" 2>/dev/null || echo "$target")"
  fi
  local group; group="$(printf '%s' "$gname" | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9]+/-/g; s/^-+|-+$//g')"
  # erst nach dem Galerie-Build senden (Abschnitt 5), damit das Vorschaubild schon existiert
  PENDING_LAST+=("$(printf '{"target":"%s","night":"%s","filter":"%s","stack":"%s","frames":%s,"id":"%s","group":"%s","device":"%s","device_label":"%s","jpg":"%s","fit":"%s","timestamp":"%s"}' \
    "$(json_escape "$target")" "$label" "$filter" "$kind" "$frames" "$gid" "$group" "$CUR_DEVICE" "$(json_escape "$CUR_LABEL")" "$(json_escape "$jpg")" \
    "$(json_escape "$STACK_DIR/$slug/$base.fit")" "__TS__")")
  return 0
}

# ---------------------------------------------------------------- Start
SIRIL_BIN="${SIRIL_BIN:-$(command -v siril-cli || true)}"
if [[ -z "$SIRIL_BIN" && $DRY_RUN -eq 0 ]]; then
  log "FEHLER: siril-cli nicht gefunden"; status error "" "" 0 "siril-cli fehlt"; exit 1
fi
# siril-cli (Debian 1.2.x) initialisiert GTK und bricht ohne Bildschirm ab ("cannot open
# display") – ohne DISPLAY daher ueber einen virtuellen X-Server (Paket xvfb) starten.
SIRIL_PREFIX=()
if [[ -z "${DISPLAY:-}" && $DRY_RUN -eq 0 ]]; then
  if command -v xvfb-run >/dev/null; then SIRIL_PREFIX=(xvfb-run -a)
  else log "WARN: kein DISPLAY und kein xvfb-run – siril-cli bricht evtl. ab (apt install xvfb xauth)"; fi
fi
[[ $DRY_RUN -eq 0 ]] && WEIGHT_OPT="$(weight_option)"

PENDING_LAST=()   # Ergebnis-Meldungen, gesendet nach dem Galerie-Build
mkdir -p "$WORK_DIR" "$STACK_DIR" "$EXPORT_DIR" "$STATE_DIR"
mqtt_discovery

# 1) Abholen (je Geraet) -------------------------------------------------
if [[ $NO_FETCH -eq 0 ]]; then
  for dev in "${DEVICE_LIST[@]}"; do
    ip="$(devcfg "$dev" IP)"; remote="$(devcfg "$dev" REMOTE)"; src="$(devcfg "$dev" SRC)"; mirror="$(devcfg "$dev" MIRROR)"
    [[ -n "$remote" ]] || { log "[$dev] keine Quelle (DEV_${dev}_REMOTE) – nur vorhandenes Archiv"; continue; }
    if device_reachable "$ip" "$(devcfg "$dev" PORT)"; then
      log "[$dev] erreichbar – hole neue Dateien"
      status fetching
      if [[ $DRY_RUN -eq 0 ]]; then
        mkdir -p "$mirror"
        rclone copy "${remote}:${src}" "$mirror" \
          --transfers 4 --checkers 8 --log-level NOTICE \
          || log "[$dev] WARN: rclone meldete Fehler – verarbeite, was da ist"
      fi
    else
      log "[$dev] nicht erreichbar – verarbeite nur vorhandenes Archiv"
    fi
  done
fi

# 2)–4) je Geraet: indexieren, Naechte stacken, Master bilden ------------
process_device() {   # process_device <id>
  CUR_DEVICE="$1"; CUR_LABEL="$(devcfg "$1" LABEL)"; CUR_SCRIPT="$(devcfg "$1" SCRIPT)"
  CUR_TYPE="$(devcfg "$1" TYPE)"; CAL_DIR="$(devcfg "$1" CALIB)"
  MIRROR_DIR="$(devcfg "$1" MIRROR)"
  if [[ "$1" == "$PRIMARY_DEVICE" ]]; then DEV_SUFFIX=""; else DEV_SUFFIX="_$1"; fi
  DEVTAG=""; (( ${#DEVICE_LIST[@]} > 1 )) && DEVTAG="[$1] "
  [[ -d "$MIRROR_DIR" ]] || { log "[$1] kein Archiv unter $MIRROR_DIR"; return 0; }
  local INDEX
  [[ "$CUR_TYPE" == "dslr" ]] && build_calibration
  # 2) Index: Ziel <TAB> Filter <TAB> Belichtung <TAB> Nacht <TAB> Zeit <TAB> Pfad ----
  INDEX="$(mktemp)"; INDEXES+=("$INDEX")
  while IFS= read -r -d '' f; do
    dir="$(basename "$(dirname "$f")")"
    target="$(canon_target "${dir%_sub}")"
    printf '%s\t%s\t%s\n' "$target" "$(frame_info "$f")" "$f" >> "$INDEX"
  done < <(find "$MIRROR_DIR" -type f -path '*_sub/*' \( -iname '*.fit' -o -iname '*.fits' \) -print0)

  if [[ ! -s "$INDEX" ]]; then
    log "[$CUR_DEVICE] keine Einzelframes gefunden."; return 0
  fi
  cut -f1,2,4 "$INDEX" | sort -u > "$INDEX.nights"      # Ziel, Filter, Nacht
  cut -f1,2 "$INDEX" | sort -u > "$INDEX.targets"       # Ziel, Filter

  # 3) Naechte ------------------------------------------------------------
  while IFS=$'\t' read -r target filter night; do
    slug="$(slugify "$target")"
    key="${slug}_${filter}${DEV_SUFFIX}_${night}"
    mapfile -t files < <(awk -F'\t' -v t="$target" -v fl="$filter" -v n="$night" '$1==t && $2==fl && $4==n {print $6}' "$INDEX" | sort)
    count=${#files[@]}
    first_ts="$(awk -F'\t' -v t="$target" -v fl="$filter" -v n="$night" '$1==t && $2==fl && $4==n {print $5}' "$INDEX" | sort | head -1)"
    exp="$(awk -F'\t' -v t="$target" -v fl="$filter" -v n="$night" '$1==t && $2==fl && $4==n {print $3}' "$INDEX" | sort | uniq -c | sort -rn | awk 'NR==1{print $2}')"

    if (( count < MIN_FRAMES )); then
      log "SKIP $target $filter $night: nur $count Frames (< $MIN_FRAMES)"; continue
    fi
    state_file="$STATE_DIR/$key.done"; fail_file="$STATE_DIR/$key.failed"
    [[ $FORCE -eq 0 && -f "$state_file" && "$(cat "$state_file")" == "$count" ]] && continue
    if [[ $FORCE -eq 0 && -f "$fail_file" && "$(cat "$fail_file")" == "$count" ]]; then
      log "SKIP $target $filter $night: früher fehlgeschlagen – $fail_file löschen oder --force"; continue
    fi

    log "STACK ${DEVTAG}$target $filter $night: $count Frames à ${exp}s (Start $first_ts)"
    if [[ $DRY_RUN -eq 1 ]]; then processed=$((processed+1)); continue; fi
    status processing "$target" "$night" "$count"
    if run_stack night "$target" "$filter" "$night" "$exp" "$night:$count" "$first_ts" "$count" "$CUR_SCRIPT" "${files[@]}"; then
      echo "$count" > "$state_file"; rm -f "$fail_file"; processed=$((processed+1))
    else
      echo "$count" > "$fail_file"; failed=$((failed+1))
      status error "$target" "$night" "$count" "Siril fehlgeschlagen"
    fi
  done < "$INDEX.nights"

  # 4) Master je Ziel + Filter ----------------------------------------------
  while IFS=$'\t' read -r target filter; do
    slug="$(slugify "$target")"
    mkey="${slug}_${filter}${DEV_SUFFIX}_master"
    spec=""; nights=(); fits=(); frames=0
    for sf in "$STATE_DIR/${slug}_${filter}${DEV_SUFFIX}"_20*.done; do
      [[ -f "$sf" ]] || continue
      n="$(basename "$sf" .done)"; n="${n##*_}"; c="$(cat "$sf")"
      fit="$STACK_DIR/$slug/${slug}_${n}_${filter}${DEV_SUFFIX}_siril.fit"
      [[ -f "$fit" || $DRY_RUN -eq 1 ]] || continue
      nights+=("$n"); fits+=("$fit"); frames=$((frames + c)); spec+="${spec:+,}$n:$c"
    done
    (( ${#nights[@]} >= MASTER_MIN_NIGHTS )) || continue
    is_wide_filter "$filter" && continue          # Weitwinkel: jede Nacht anders gerahmt -> kein Master
    mode="nights"; [[ $MASTER_FULL -eq 1 ]] && mode="full"
    state_file="$STATE_DIR/$mkey.done"; fail_file="$STATE_DIR/$mkey.failed"
    old="$(cat "$state_file" 2>/dev/null || true)"
    # Neu rechnen, wenn sich die Naechte geaendert haben – oder --master-full / --force
    if [[ $FORCE -eq 0 && $MASTER_FULL -eq 0 && "${old#*|}" == "$spec" ]]; then continue; fi
    if [[ $FORCE -eq 0 && $MASTER_FULL -eq 0 && "$(cat "$fail_file" 2>/dev/null || true)" == "$spec" ]]; then
      log "SKIP Master $target $filter: früher fehlgeschlagen – $fail_file löschen oder --force"; continue
    fi

    last_night="${nights[-1]}"
    ts="$(awk -F'\t' -v t="$target" -v fl="$filter" -v n="$last_night" '$1==t && $2==fl && $4==n {print $5}' "$INDEX" | sort | head -1)"
    exp="$(awk -F'\t' -v t="$target" -v fl="$filter" '$1==t && $2==fl {print $3}' "$INDEX" | sort | uniq -c | sort -rn | awk 'NR==1{print $2}')"
    log "MASTER ${DEVTAG}$target $filter ($mode): ${#nights[@]} Nächte, $frames Frames"
    if [[ $DRY_RUN -eq 1 ]]; then processed=$((processed+1)); continue; fi
    status processing "$target" "master" "$frames"

    if [[ "$mode" == "full" ]]; then
      mapfile -t subs < <(for n in "${nights[@]}"; do awk -F'\t' -v t="$target" -v fl="$filter" -v n="$n" '$1==t && $2==fl && $4==n {print $6}' "$INDEX"; done | sort)
      ok=0; run_stack master "$target" "$filter" master "$exp" "$spec" "${ts:-${last_night} 22:00:00}" "$frames" "$CUR_SCRIPT" "${subs[@]}" && ok=1
    else
      ok=0; run_stack master "$target" "$filter" master "$exp" "$spec" "${ts:-${last_night} 22:00:00}" "$frames" "$SIRIL_MASTER_SCRIPT" "${fits[@]}" && ok=1
    fi
    if (( ok )); then echo "$mode|$spec" > "$state_file"; rm -f "$fail_file"; processed=$((processed+1))
    else echo "$spec" > "$fail_file"; failed=$((failed+1)); status error "$target" "master" "$frames" "Master fehlgeschlagen"; fi
  done < "$INDEX.targets"

  # 5) Kombination ueber alle Filter je Ziel (COMBINE_FILTERS=1) -------------
  # Je Filter das beste Ergebnis (Master, sonst die juengste Nacht), registriert und
  # gewichtet zusammengefuehrt – z. B. IRCUT (Sterne/Farbe) + LP (Nebel-Signal).
  [[ "${COMBINE_FILTERS:-1}" == "1" ]] || return 0
  while read -r target; do
    slug="$(slugify "$target")"
    local cfits=() cfilters=() cspec="" nspecs="" cframes=0 last=""
    while read -r filter; do
      is_wide_filter "$filter" && continue
      local mstate="$STATE_DIR/${slug}_${filter}${DEV_SUFFIX}_master.done"
      local mfit="$STACK_DIR/$slug/${slug}_master_${filter}${DEV_SUFFIX}_siril.fit" fspec=""
      if [[ -f "$mstate" && ( -f "$mfit" || $DRY_RUN -eq 1 ) ]]; then
        fspec="$(cut -d'|' -f2- "$mstate")"; cfits+=("$mfit")
      else
        local sf newest=""
        for sf in "$STATE_DIR/${slug}_${filter}${DEV_SUFFIX}"_20*.done; do [[ -f "$sf" ]] && newest="$sf"; done
        [[ -n "$newest" ]] || continue
        n="$(basename "$newest" .done)"; n="${n##*_}"
        local nfit="$STACK_DIR/$slug/${slug}_${n}_${filter}${DEV_SUFFIX}_siril.fit"
        [[ -f "$nfit" || $DRY_RUN -eq 1 ]] || continue
        fspec="$n:$(cat "$newest")"; cfits+=("$nfit")
      fi
      cfilters+=("$filter"); cspec+="${cspec:+;}$filter=$fspec"; nspecs+="${nspecs:+,}$fspec"
    done < <(awk -F'\t' -v t="$target" '$1==t {print $2}' "$INDEX.targets" | sort -u)
    (( ${#cfilters[@]} >= 2 )) || continue

    # Naechte aller Filter zusammenfassen (gleiche Nacht -> Frames addieren)
    local nights_spec
    nights_spec="$(tr ',' '\n' <<< "$nspecs" | awk -F: '{s[$1]+=$2} END {for (d in s) print d":"s[d]}' | sort | paste -sd, -)"
    cframes="$(tr ',' '\n' <<< "$nights_spec" | awk -F: '{s+=$2} END {print s+0}')"
    last="$(tr ',' '\n' <<< "$nights_spec" | tail -1 | cut -d: -f1)"
    local fstr; fstr="$(IFS=+; echo "${cfilters[*]}")"
    local ckey="${slug}${DEV_SUFFIX}_combined" 
    state_file="$STATE_DIR/$ckey.done"; fail_file="$STATE_DIR/$ckey.failed"
    [[ $FORCE -eq 0 && "$(cat "$state_file" 2>/dev/null || true)" == "$cspec" ]] && continue
    if [[ $FORCE -eq 0 && "$(cat "$fail_file" 2>/dev/null || true)" == "$cspec" ]]; then
      log "SKIP Kombination $target: früher fehlgeschlagen – $fail_file löschen oder --force"; continue
    fi
    exp="$(awk -F'\t' -v t="$target" '$1==t {print $3}' "$INDEX" | sort | uniq -c | sort -rn | awk 'NR==1{print $2}')"
    log "KOMBI ${DEVTAG}$target $fstr: $cframes Frames"
    if [[ $DRY_RUN -eq 1 ]]; then processed=$((processed+1)); continue; fi
    status processing "$target" "kombi" "$cframes"
    if run_stack master "$target" "$fstr" kombi "${exp:-$DEFAULT_EXPOSURE}" "$nights_spec" "$last 22:00:00" "$cframes" "$SIRIL_MASTER_SCRIPT" "${cfits[@]}"; then
      echo "$cspec" > "$state_file"; rm -f "$fail_file"; processed=$((processed+1))
    else
      echo "$cspec" > "$fail_file"; failed=$((failed+1)); status error "$target" "kombi" "$cframes" "Kombination fehlgeschlagen"
    fi
  done < <(cut -f1 "$INDEX.targets" | sort -u)
}

processed=0; failed=0; INDEXES=()
cleanup_index() { local x; for x in "${INDEXES[@]}"; do rm -f "$x" "$x".*; done; }
trap cleanup_index EXIT
for dev in "${DEVICE_LIST[@]}"; do process_device "$dev"; done

log "Fertig: $processed verarbeitet, $failed Fehler"

# 5) Galerie neu bauen, wenn sich etwas geaendert hat (optional)
if (( processed > 0 )) && [[ -n "${GALLERY_BUILD:-}" && $DRY_RUN -eq 0 ]]; then
  log "Baue Galerie neu"
  python3 "$GALLERY_BUILD" --stacks "$STACK_DIR" --out "${GALLERY_OUT:-$ASTRO_ROOT/web}" || log "WARN: Galerie-Build fehlgeschlagen"
fi
for payload in "${PENDING_LAST[@]}"; do mqtt_pub last "${payload//__TS__/$(date -Iseconds)}" retain; done
# 6) Immich: Library-Scan + Versionen stapeln (optional)
if (( processed > 0 )) && [[ "$IMMICH_SYNC" == "1" && $DRY_RUN -eq 0 ]]; then
  log "Immich-Sync"
  python3 "$LIB_DIR/immich_sync.py" --stacks "$STACK_DIR" --state "$STATE_DIR/immich.json" || log "WARN: Immich-Sync fehlgeschlagen"
fi
if (( failed == 0 )); then status idle "" "" 0 "$processed verarbeitet"; fi
exit $(( failed > 0 ? 1 : 0 ))
