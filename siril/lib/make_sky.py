#!/usr/bin/env python3
"""Erzeugt siril/lib/sky.json (Sternbildlinien, Sternbildnamen, helle Sterne) aus d3-celestial.

    python3 make_sky.py <ordner mit constellations.lines.json, constellations.json, stars.6.json, starnames.json>

Quelle: https://github.com/ofrohn/d3-celestial (data/), Copyright (c) 2015 Olaf Frohn, BSD-3-Clause.
Nur noetig, wenn die Daten aktualisiert werden sollen – sky.json liegt im Repo.
"""
import json
import sys
from pathlib import Path

MAG_LIMIT = 4.0          # benannte/Bayer-Sterne bis zu dieser Helligkeit
GREEK = "αβγδεζηθικλμνξοπρστυφχψω"


def ra(lon: float) -> float:
    return round(lon % 360, 4)


def main():
    src = Path(sys.argv[1])
    lines = json.loads((src / "constellations.lines.json").read_text())
    meta = {f["id"]: f for f in json.loads((src / "constellations.json").read_text())["features"]}
    stars = json.loads((src / "stars.6.json").read_text())["features"]
    names = json.loads((src / "starnames.json").read_text())

    cons = []
    for f in lines["features"]:
        m = meta.get(f["id"], {})
        p = m.get("properties", {})
        lon, lat = m.get("geometry", {}).get("coordinates", [None, None])
        cons.append({
            "id": f["id"], "name": (p.get("de") or p.get("name") or f["id"]).replace("\u2005", " "),
            "ra": ra(lon) if lon is not None else None, "dec": lat, "rank": int(p.get("rank", 3)),
            "lines": [[[ra(a), round(b, 4)] for a, b in seg] for seg in f["geometry"]["coordinates"]],
        })
    out_stars = []
    for s in stars:
        mag = s["properties"]["mag"]
        if mag > MAG_LIMIT:
            continue
        n = names.get(str(s["id"]), {})
        label = (n.get("de") or n.get("name") or "").replace("\u2005", " ")
        bayer = f"{n.get('bayer', '')} {n.get('c', '')}".strip() if n.get("bayer") in GREEK else ""
        if not label and not bayer:
            continue
        lon, lat = s["geometry"]["coordinates"]
        out_stars.append({"name": label or bayer, "bayer": bayer if label else "", "ra": ra(lon), "dec": round(lat, 4),
                          "mag": mag})
    out_stars.sort(key=lambda x: x["mag"])
    data = {"source": "d3-celestial (c) 2015 Olaf Frohn, BSD-3-Clause, https://github.com/ofrohn/d3-celestial",
            "constellations": cons, "stars": out_stars}
    dest = Path(__file__).with_name("sky.json")
    dest.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"{dest}: {len(cons)} Sternbilder, {len(out_stars)} Sterne")


if __name__ == "__main__":
    main()
