"""Weitwinkel-Beschriftung: Sternbildlinien, Sternbildnamen und helle Sterne im Bildfeld.

Daten: sky.json (aus d3-celestial, (c) 2015 Olaf Frohn, BSD-3-Clause; Erzeugung: make_sky.py).
Alle Koordinaten in Grad (RA 0–360).
"""
from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path

SKY = Path(__file__).with_name("sky.json")
WIDE_FOV_DEG = 10.0         # ab dieser Bildbreite gilt ein Bild als Weitwinkel (S30 Pro Tele: 4,6° bleibt Deep-Sky)


@lru_cache(maxsize=1)
def load(path: str = str(SKY)) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def fov_deg(wcs: dict, width: int, height: int, pixscale_arcsec) -> tuple[float, float]:
    s = pixscale_arcsec(wcs) / 3600
    return width * s, height * s


def is_wide(wcs: dict | None, width: int, height: int, pixscale_arcsec) -> bool:
    return bool(wcs) and fov_deg(wcs, width, height, pixscale_arcsec)[0] >= WIDE_FOV_DEG


def _inside(p, w, h, margin):
    return p is not None and -margin <= p[0] <= w + margin and -margin <= p[1] <= h + margin


def _densify(a, b, step_deg=2.0):
    """Grosskreis-Segment in Stuetzpunkte zerlegen, damit die TAN-Projektion gekruemmte Linien zeigt."""
    ra1, d1, ra2, d2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    v1 = (math.cos(d1) * math.cos(ra1), math.cos(d1) * math.sin(ra1), math.sin(d1))
    v2 = (math.cos(d2) * math.cos(ra2), math.cos(d2) * math.sin(ra2), math.sin(d2))
    ang = math.acos(max(-1.0, min(1.0, sum(x * y for x, y in zip(v1, v2)))))
    n = max(1, int(math.degrees(ang) / step_deg))
    out = []
    for i in range(n + 1):
        t = i / n
        if ang < 1e-9:
            v = v1
        else:
            s1, s2 = math.sin((1 - t) * ang) / math.sin(ang), math.sin(t * ang) / math.sin(ang)
            v = tuple(s1 * x + s2 * y for x, y in zip(v1, v2))
        out.append([math.degrees(math.atan2(v[1], v[0])) % 360, math.degrees(math.asin(max(-1, min(1, v[2]))))])
    return out


def constellations(wcs: dict, w: int, h: int, world_to_pixel, margin_frac: float = 0.15) -> list[dict]:
    """Sternbilder, deren Linien das Bild beruehren – als RA/Dec-Polylinien (fuer meta.json)."""
    margin = margin_frac * max(w, h)
    out = []
    for c in load()["constellations"]:
        segs = []
        for line in c["lines"]:
            pts = []
            for a, b in zip(line, line[1:]):
                dense = _densify(a, b)
                pts.extend(dense if not pts else dense[1:])
            if any(_inside(world_to_pixel(r, d, wcs), w, h, margin) for r, d in pts):
                segs.append([[round(r, 3), round(d, 3)] for r, d in pts])
        if segs:
            lp = world_to_pixel(c["ra"], c["dec"], wcs) if c.get("ra") is not None else None
            out.append({"id": c["id"], "name": c["name"], "lines": segs,
                        "label": [round(c["ra"], 3), round(c["dec"], 3)] if _inside(lp, w, h, 0) else None})
    return out


def bright_stars(wcs: dict, w: int, h: int, world_to_pixel, fov_w: float, limit: int = 40) -> list[dict]:
    """Benannte Sterne im Bild als meta.json-Objekte (category "star"). Grenzhelligkeit je nach Bildfeld."""
    max_mag = 3.0 if fov_w > 60 else 3.5 if fov_w > 30 else 4.0
    out = []
    for s in load()["stars"]:
        if s["mag"] > max_mag:
            continue
        p = world_to_pixel(s["ra"], s["dec"], wcs)
        if not _inside(p, w, h, 0):
            continue
        out.append({"name": s["name"], "alt": s.get("bayer", ""), "type": "Stern", "ra": s["ra"], "dec": s["dec"],
                    "major": 0, "minor": 0, "pa": 0, "mag": s["mag"], "dist": None, "category": "star", "main": False})
        if len(out) >= limit:
            break
    return out
