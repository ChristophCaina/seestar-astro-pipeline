"""Zielsuche im OpenNGC-Katalog (gleicher Katalog wie Galerie und siril-auto) plus helle Sterne/Planeten-frei."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "siril" / "lib"))
from catalog import Catalog, norm  # noqa: E402


class Targets:
    def __init__(self, csv_paths: list[Path], extra: Path | None = None):
        extra = extra or ROOT / "siril" / "lib" / "objects_de.json"
        self.cat = Catalog([p for p in csv_paths if Path(p).exists()], extra)

    def search(self, q: str, limit: int = 20, altitude=None) -> list[dict]:
        """Treffer nach Relevanz: exakte Bezeichnung, dann Praefix, dann Teilstring (Name, Alt-Name, Trivialname)."""
        k = norm(q)
        if not k:
            return []
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
        return out
