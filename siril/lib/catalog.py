"""Deep-Sky-Katalog (OpenNGC) laden und Objekte fuer ein Bildfeld auswaehlen.

OpenNGC: https://github.com/mattiaverga/OpenNGC  (Lizenz CC-BY-SA 4.0)
Die Datei NGC.csv wird bei der Installation heruntergeladen, siehe siril/README.md.
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

TYPE_DE = {
    "PN": "Planetarischer Nebel", "HII": "Emissionsnebel (H-II-Region)", "EmN": "Emissionsnebel",
    "RfN": "Reflexionsnebel", "Neb": "Nebel", "SNR": "Supernova-Überrest", "DrkN": "Dunkelnebel",
    "Cl+N": "Sternhaufen mit Nebel", "OCl": "Offener Sternhaufen", "GCl": "Kugelsternhaufen",
    "*Ass": "Sternassoziation", "GPair": "Galaxienpaar", "GTrpl": "Galaxientripel", "GGroup": "Galaxiengruppe",
    "*": "Stern", "**": "Doppelstern", "Nova": "Nova", "Other": "Objekt",
}
KIND = {
    "G": "galaxy", "GPair": "galaxy", "GTrpl": "galaxy", "GGroup": "galaxy",
    "PN": "nebula", "HII": "nebula", "EmN": "nebula", "RfN": "nebula", "Neb": "nebula", "SNR": "nebula",
    "DrkN": "nebula", "Cl+N": "nebula", "OCl": "cluster", "GCl": "cluster", "*Ass": "cluster",
}
SKIP_TYPES = {"Dup", "NonEx", "*", "**", "Nova", "Other"}


def norm(name: str) -> str:
    """'M 31' / 'M031' / 'NGC 0224' / 'ngc224' -> 'M31' / 'NGC224'"""
    s = re.sub(r"\s+", "", name.upper())
    m = re.match(r"^(M|NGC|IC)0*(\d+)([A-Z]?)$", s)
    return f"{m.group(1)}{m.group(2)}{m.group(3)}" if m else s


def display(name: str) -> str:
    """'NGC0224' -> 'NGC 224', 'M031' -> 'M 31'"""
    m = re.match(r"^(M|NGC|IC)\s*0*(\d+)\s*([A-Z]?)$", name.strip().upper())
    return f"{m.group(1)} {m.group(2)}{m.group(3)}" if m else name


def _hms(s: str) -> float:
    h, m, sec = (float(v) for v in s.split(":"))
    return (h + m / 60 + sec / 3600) * 15


def _dms(s: str) -> float:
    sign = -1 if s.strip().startswith("-") else 1
    d, m, sec = (abs(float(v)) for v in s.strip().lstrip("+-").split(":"))
    return sign * (d + m / 60 + sec / 3600)


def _f(v):
    try:
        return float(v) if v not in ("", None) else None
    except ValueError:
        return None


def type_de(t: str, hubble: str = "") -> str:
    if t == "G":
        h = (hubble or "").strip()
        if h.startswith("S0") or h.startswith("SB0"):
            base = "Linsenförmige Galaxie"
        elif h.startswith("S") or h.startswith("SB"):
            base = "Spiralgalaxie"
        elif h.startswith("E"):
            base = "Elliptische Galaxie"
        elif h.startswith("I"):
            base = "Irreguläre Galaxie"
        else:
            base = "Galaxie"
        return f"{base} ({h})" if h else base
    return TYPE_DE.get(t, t)


class Catalog:
    def __init__(self, csv_paths: list[Path], extra: Path | None = None):
        self.objects: list[dict] = []
        self.by_key: dict[str, dict] = {}
        self.extra = json.loads(Path(extra).read_text(encoding="utf-8")) if extra and Path(extra).exists() else {}
        for p in csv_paths:
            if Path(p).exists():
                self._load(Path(p))

    def _load(self, path: Path) -> None:
        with open(path, encoding="utf-8") as f:
            for r in csv.DictReader(f, delimiter=";"):
                t = r.get("Type", "")
                if t in SKIP_TYPES or not r.get("RA") or not r.get("Dec"):
                    continue
                name = r["Name"]
                mnum = (r.get("M") or "").strip()
                o = {
                    "id": norm(name), "messier": f"M{int(mnum)}" if mnum.isdigit() else None,
                    "name": display(f"M{int(mnum)}") if mnum.isdigit() else display(name),
                    "alt": display(name) if mnum.isdigit() else "",
                    "type_code": t, "type": type_de(t, r.get("Hubble", "")), "kind": KIND.get(t, "other"),
                    "ra": _hms(r["RA"]), "dec": _dms(r["Dec"]),
                    "major": _f(r.get("MajAx")) or 0.0, "minor": _f(r.get("MinAx")) or _f(r.get("MajAx")) or 0.0,
                    "pa": _f(r.get("PosAng")) or 0.0,
                    "mag": _f(r.get("V-Mag")) if _f(r.get("V-Mag")) is not None else _f(r.get("B-Mag")),
                    "common": (r.get("Common names") or "").split(",")[0].strip(),
                }
                ex = self.extra.get(o["messier"] or "") or self.extra.get(o["id"]) or {}
                if ex.get("common"):
                    o["common"] = ex["common"]
                o["dist"] = ex.get("dist")
                self.objects.append(o)
                for k in filter(None, [o["id"], o["messier"], norm(o["common"]) if o["common"] else None]):
                    self.by_key.setdefault(k, o)
                for cn in (r.get("Common names") or "").split(","):
                    if cn.strip():
                        self.by_key.setdefault(norm(cn.strip()), o)

    def find(self, name: str) -> dict | None:
        return self.by_key.get(norm(name))

    def in_field(self, wcs: dict, w: int, h: int, world_to_pixel, margin_arcmin: float = 60.0,
                 max_mag: float = 12.5, min_size: float = 1.0, scale_arcsec: float = 2.39):
        """Objekte im Bild (und bis margin ausserhalb) – sortiert nach Helligkeit."""
        margin_px = margin_arcmin * 60 / scale_arcsec
        out = []
        for o in self.objects:
            p = world_to_pixel(o["ra"], o["dec"], wcs)
            if p is None:
                continue
            x, y = p
            if not (-margin_px <= x <= w + margin_px and -margin_px <= y <= h + margin_px):
                continue
            inside = 0 <= x <= w and 0 <= y <= h
            bright = o["messier"] or (o["mag"] is not None and o["mag"] <= max_mag) or o["major"] >= min_size * 5
            if inside and not (bright or o["major"] >= min_size):
                continue
            if not inside and not (o["messier"] or (o["mag"] is not None and o["mag"] <= 9)):
                continue
            out.append(o)
        out.sort(key=lambda o: (o["mag"] if o["mag"] is not None else 99))
        return out
