#!/usr/bin/env bash
#
# repreview.sh – Vorschau-JPG eines vorhandenen Stacks mit anderen Einstellungen neu rechnen,
# ohne neu zu stacken. Zum Ausprobieren von Gradientenentfernung, Entrauschen und Streckung.
#
#   repreview.sh <stack.fit> [ausgabe.jpg]
#
# Einstellungen wie in /etc/siril-auto.env (SUBSKY_ARGS, DENOISE, DENOISE_ARGS, STRETCH_ARGS,
# RMGREEN, DUALBAND_FILTERS, SATU_ARGS),
# einzeln ueberschreibbar, z. B.:
#   SUBSKY_ARGS="1" DENOISE=0 repreview.sh /data/astro/stacks/M_101/M_101_kombi_IRCUT+LP_siril.fit
#   STRETCH_ARGS="-linked -3 0.10" repreview.sh … /tmp/dunkler.jpg
#
# Das lineare FIT (result_linear, bereits mit subsky) wird nicht veraendert. Fuer einen fairen
# Vergleich verschiedener SUBSKY_ARGS ist das FIT also schon einmal "entgradiert" – fuer grobe
# Tests reicht das; endgueltig wirkt eine Aenderung beim naechsten Stack (siril-auto --force).
set -euo pipefail
[[ $# -ge 1 && -f "$1" ]] || { sed -n '3,17p' "$0"; exit 1; }
override_env() { local v; for v in SUBSKY_ARGS DENOISE DENOISE_ARGS STRETCH_ARGS RMGREEN DUALBAND_FILTERS SATU_ARGS; do [[ -n "${!v+x}" ]] && printf '%s=%q\n' "$v" "${!v}"; done; return 0; }
saved="$(override_env)"
CONFIG="${SIRIL_AUTO_CONFIG:-/etc/siril-auto.env}"
# shellcheck source=/dev/null
[[ -r "$CONFIG" ]] && . "$CONFIG"
eval "$saved"          # Werte von der Kommandozeile gewinnen

fit="$(readlink -f "$1")"
out="${2:-${fit%.fit}_test.jpg}"
work="$(mktemp -d)"; trap 'rm -rf "$work"' EXIT
cp "$fit" "$work/in.fit"

denoise="# denoise (DENOISE=0)"
[[ "${DENOISE:-1}" == "1" ]] && denoise="denoise ${DENOISE_ARGS:--mod=0.8}"
# Filter aus dem Dateinamen (<ziel>_<nacht|master|kombi>_<filter>_..._siril.fit)
name="$(basename "$fit")"
rmgreen="rmgreen"
for f in ${DUALBAND_FILTERS:-LP}; do
  if [[ "$name" == *"_${f}_"* || "$name" == *"+${f}_"* || "$name" == *"_${f}+"* ]]; then rmgreen="# rmgreen (Dualband-Filter $f)"; fi
done
[[ "${RMGREEN:-1}" == "0" ]] && rmgreen="# rmgreen (RMGREEN=0)"
satu="# satu (SATU_ARGS leer)"
[[ -n "${SATU_ARGS-0.6}" ]] && satu="satu ${SATU_ARGS-0.6}"
cat > "$work/run.ssf" <<EOF
requires 1.2.0
set32bits
load in
subsky ${SUBSKY_ARGS:--rbf -samples=20 -tolerance=1.0 -smooth=0.5}
$denoise
$rmgreen
autostretch ${STRETCH_ARGS:--linked -2.8 0.15}
$satu
savejpg result 95
close
EOF

siril="${SIRIL_BIN:-$(command -v siril-cli)}"
prefix=()
[[ -z "${DISPLAY:-}" ]] && command -v xvfb-run >/dev/null && prefix=(xvfb-run -a)
echo "== $(basename "$fit")"; grep -E '^(subsky|denoise|rmgreen|# rmgreen|autostretch|satu)' "$work/run.ssf" | sed 's/^/   /'
if ! "${prefix[@]}" "$siril" -d "$work" -s "$work/run.ssf" > "$work/siril.log" 2>&1; then
  tail -20 "$work/siril.log"; echo "FEHLER – siehe oben"; exit 1
fi
cp "$work/result.jpg" "$out"
echo "-> $out"
