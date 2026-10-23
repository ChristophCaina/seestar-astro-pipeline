"""Zielsuche im OpenNGC-Katalog (gleicher Katalog wie Galerie und siril-auto) plus helle Sterne/Planeten-frei."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "siril" / "lib"))
from catalog import Catalog, norm  # noqa: E402
import sky  # noqa: E402

# Zusaetzliche Weitwinkel-Ziele (RA/Dec in Grad)
REGIONS = [
    {"name": "Milchstraßenzentrum", "alt": "Sgr A*, Schütze", "ra": 266.42, "dec": -29.0},
    {"name": "Milchstraße im Schwan", "alt": "Cygnus-Region, Deneb", "ra": 305.0, "dec": 40.0},
    {"name": "Sommerdreieck", "alt": "Wega, Deneb, Atair", "ra": 295.0, "dec": 25.0},
    {"name": "Milchstraße Kassiopeia–Perseus", "alt": "Doppelhaufen", "ra": 30.0, "dec": 58.0},
    {"name": "Wintersechseck", "alt": "Orion, Zwillinge, Fuhrmann", "ra": 90.0, "dec": 15.0},
]


class Targets:
    def __init__(self, csv_paths: list[Path], extra: Path | None = None):
        extra = extra or ROOT / "siril" / "lib" / "objects_de.json"
        self.cat = Catalog([p for p in csv_paths if Path(p).exists()], extra)
        self.wide = [{"name": c["name"], "alt": c["id"], "common": "", "type": "Sternbild", "kind": "constellation",
                      "mag": None, "major": 0, "minor": 0, "ra": c["ra"], "dec": c["dec"]}
                     for c in sky.load()["constellations"] if c.get("ra") is not None]
        self.wide += [{**r, "common": "", "type": "Himmelsregion", "kind": "region", "mag": None, "major": 0, "minor": 0}
                      for r in REGIONS]

    def search(self, q: str, limit: int = 20, altitude=None, wide: bool = False) -> list[dict]:
        """Treffer nach Relevanz: exakte Bezeichnung, dann Praefix, dann Teilstring (Name, Alt-Name, Trivialname).
        wide=True: zuerst Sternbilder und Himmelsregionen, dann Katalogobjekte."""
        k = norm(q)
        if not k:
            return []
        front = []
        if wide:
            for o in self.wide:
                keys = [norm(o["name"]), norm(o["alt"])]
                if any(x.startswith(k) for x in keys) or any(k in x for x in keys):
                    d = {kk: o[kk] for kk in ("name", "alt", "common", "type", "kind", "mag", "major", "minor")}
                    d["ra"], d["dec"] = o["ra"] / 15, o["dec"]
                    if altitude:
                        d["altitude"] = altitude(d["ra"], d["dec"])
                    front.append(d)
            if len(front) >= limit:
                return front[:limit]
            limit -= len(front)
        exact = self.cat.find(q)
        scored = []
        for o in self.cat.objects:
            keys = [o["id"], norm(o["name"]), norm(o.get("alt") or ""), norm(o.get("common") or "")]
            if o is exact:
                s = 0
            elif any(x.startswith(k) for x in keys if x):
                s = 1
            elif any(k in x for x in keys if x):
                s = 2
            else:
                continue
            scored.append((s, 0 if o["messier"] else 1, o["mag"] if o["mag"] is not None else 99, o))
        scored.sort(key=lambda t: t[:3])
        out = []
        for *_, o in scored[:limit]:
            d = {k2: o[k2] for k2 in ("name", "alt", "common", "type", "kind", "mag", "major", "minor")}
            d["ra"], d["dec"] = o["ra"] / 15, o["dec"]          # RA in Stunden fuer INDI
            if altitude:
                d["altitude"] = altitude(d["ra"], d["dec"])
            out.append(d)
        return front + out
