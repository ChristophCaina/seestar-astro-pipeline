#!/usr/bin/env python3
"""Erzeugt homeassistant/packages/astro_goto.yaml: Zielliste (Name -> RA/Dec) fuer GoTo im Dashboard.

Quelle: OpenNGC (NGC.csv, CC-BY-SA 4.0, https://github.com/mattiaverga/OpenNGC) fuer die Koordinaten,
siril/lib/objects_de.json fuer die deutschen Namen, dazu einige Milchstrassen-Regionen fuer Weitwinkel.

    python3 homeassistant/tools/make_goto_targets.py /pfad/zu/NGC.csv

Teleskope fuer die Auswahl/Knoepfe: TELESKOPE unten (id wie in der KONFIGURATION von astro_audit.yaml).
"""
from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "homeassistant" / "packages" / "astro_goto.yaml"
TELESKOPE = [("s50", "S50"), ("s30", "S30 Pro")]

# Zusaetzlich zu objects_de.json (nur Ziele mit Namen dort kommen sonst rein)
EXTRA = ["M3", "M11", "M35", "M52", "M92", "M106", "NGC891", "NGC2392", "NGC6543", "NGC7331"]
EXTRA_NAMES = {"M3": "Kugelsternhaufen", "M11": "Wildentenhaufen", "M35": "Offener Haufen",
               "M52": "Offener Haufen", "M92": "Kugelsternhaufen", "M106": "Galaxie",
               "NGC891": "Silbernadel-Galaxie", "NGC2392": "Eskimonebel", "NGC6543": "Katzenaugennebel",
               "NGC7331": "Galaxie"}
# Milchstrassen-Regionen (Bildmitte fuer Weitwinkel), RA in Stunden, Dec in Grad
MILCHSTRASSE = [
    ("Milchstraße: Schwan", 20.40, 40.0),
    ("Milchstraße: Kassiopeia", 1.00, 60.0),
    ("Milchstraße: Fuhrmann", 5.50, 40.0),
    ("Milchstraße: Adler", 19.50, 8.0),
    ("Milchstraße: Zentrum (Schütze)", 17.76, -29.0),
]


def hms(s: str) -> float:
    h, m, sec = (float(x) for x in s.split(":"))
    return h + m / 60 + sec / 3600


def dms(s: str) -> float:
    sign = -1 if s.strip().startswith("-") else 1
    d, m, sec = (abs(float(x)) for x in s.strip().lstrip("+-").split(":"))
    return sign * (d + m / 60 + sec / 3600)


def key(name: str) -> str:
    """OpenNGC-Name -> Schluessel wie in objects_de.json (NGC0224 -> NGC224)."""
    m = re.match(r"^(NGC|IC)0*(\d+)$", name)
    return f"{m[1]}{m[2]}" if m else name


def main(csv_path: str) -> None:
    de = {k: v for k, v in json.loads((ROOT / "siril/lib/objects_de.json").read_text()).items() if k != "_hinweis"}
    want = {k: (v.get("common") or "") for k, v in de.items()}
    for k in EXTRA:
        want.setdefault(k, EXTRA_NAMES.get(k, ""))
    found: dict[str, tuple[float, float]] = {}
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f, delimiter=";"):
            if not row["RA"] or not row["Dec"]:
                continue
            ids = {key(row["Name"])}
            if row.get("M"):
                ids.add(f"M{int(row['M'])}")
            for i in ids & want.keys():
                found.setdefault(i, (hms(row["RA"]), dms(row["Dec"])))
    # M45/M44 u. a. fehlen in OpenNGC als NGC – feste Werte
    found.setdefault("M45", (3.7833, 24.1167))
    found.setdefault("M44", (8.6700, 19.6667))
    missing = sorted(want.keys() - found.keys())
    if missing:
        print("ohne Koordinaten:", ", ".join(missing), file=sys.stderr)

    def sort_key(k: str):
        m = re.match(r"^(M|NGC|IC)(\d+)$", k)
        return ({"M": 0, "NGC": 1, "IC": 2}[m[1]], int(m[2])) if m else (9, 0)

    ziele = []
    for k in sorted(found, key=sort_key):
        if not want.get(k) and k in ("M32", "M110"):
            continue
        label = re.sub(r"^(M|NGC|IC)(\d+)$", r"\1 \2", k)
        name = f"{label} – {want[k]}" if want.get(k) else label
        ra, dec = found[k]
        ziele.append((name, round(ra, 4), round(dec, 3)))
    ziele += [(n, ra, dec) for n, ra, dec in MILCHSTRASSE]

    L = []
    w = L.append
    w("# =============================================================================")
    w("# Astro-GoTo – Ziel auswaehlen und per Knopf anfahren (je Teleskop)")
    w("#")
    w("# ERZEUGT von homeassistant/tools/make_goto_targets.py – Ziele dort ergaenzen und neu erzeugen,")
    w("# oder unten in BEIDEN Listen (options + ziele) von Hand nachtragen.")
    w("# Koordinaten: OpenNGC (CC-BY-SA 4.0), J2000; RA in Stunden, Dec in Grad.")
    w("# Braucht astro_audit.yaml (script.astro_goto, KONFIGURATION der Teleskope).")
    w("# =============================================================================")
    w("")
    w("input_select:")
    for tid, tname in TELESKOPE:
        w(f"  astro_ziel_{tid}:")
        w(f"    name: Astro – Ziel {tname}")
        w("    icon: mdi:target")
        w("    options:")
        for n, _, _ in ziele:
            w(f'      - "{n}"')
    w("")
    w("script:")
    for tid, tname in TELESKOPE:
        w(f"  astro_goto_{tid}:")
        w(f"    alias: Astro – {tname} GoTo")
        w("    icon: mdi:crosshairs-gps")
        w("    mode: single")
        w("    variables:")
        w("      ziele: &astro_ziele" if tid == TELESKOPE[0][0] else "      ziele: *astro_ziele")
        if tid == TELESKOPE[0][0]:
            for n, ra, dec in ziele:
                w(f'        "{n}": [{ra}, {dec}]')
        w(f"      ziel: \"{{{{ states('input_select.astro_ziel_{tid}') }}}}\"")
        w("    sequence:")
        w("      - action: script.astro_goto")
        w("        data:")
        w(f"          teleskop: {tid}")
        w("          name: \"{{ ziel.split(' – ')[0] }}\"")
        w("          ra: \"{{ ziele[ziel][0] }}\"")
        w("          dec: \"{{ ziele[ziel][1] }}\"")
    OUT.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"{OUT.relative_to(ROOT)}: {len(ziele)} Ziele")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "siril/data/NGC.csv")
