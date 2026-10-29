"""'Heute Nacht': gut sichtbare Ziele mit Zeitfenster, Kulmination und Hoehe jetzt; Zielsuche.

Grundliste: data/highlights.json (aus OpenNGC, CC-BY-SA 4.0, mit deutschen Namen). Die Suche nutzt den
vollen OpenNGC-Katalog, wenn siril/data/NGC.csv vorhanden ist (siehe siril/README.md), sonst die Grundliste.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import astro

ROOT = Path(__file__).resolve().parents[2]
HIGHLIGHTS = Path(__file__).with_name("data") / "highlights.json"
PLANETS_DE = ["Venus", "Mars", "Jupiter", "Saturn", "Uranus", "Neptun"]
TAG = {"galaxy": "Galaxie", "cluster": "Haufen", "nebula": "Nebel", "planet": "Planet", "moon": "Mond"}
MIN_ALT = 30.0


def hm(t: datetime | None) -> str:
    return t.astimezone().strftime("%H:%M") if t else "–"


def label(o: dict) -> str:
    return f"{o['name']} – {o['common']}" if o.get("common") else o["name"]


class Sky:
    def __init__(self, catalog_paths: list[str] | None = None):
        self.highlights = json.loads(HIGHLIGHTS.read_text(encoding="utf-8"))
        paths = [Path(p).expanduser() for p in (catalog_paths or [])] or \
                [ROOT / "siril" / "data" / "NGC.csv", ROOT / "siril" / "data" / "addendum.csv"]
        self.catalog = None
        if any(p.exists() for p in paths):
            sys.path.insert(0, str(ROOT / "siril" / "lib"))
            from catalog import Catalog
            self.catalog = Catalog([p for p in paths if p.exists()], ROOT / "siril" / "lib" / "objects_de.json")
        self._cache: dict = {}

    # ------------------------------------------------------------ Nacht
    def night(self, lat: float, lon: float, now: datetime | None = None) -> astro.Night:
        now = now or datetime.now(timezone.utc)
        key = ("night", round(lat, 2), round(lon, 2), astro.night_start(now))
        if key not in self._cache:
            self._cache[key] = astro.night(lat, lon, now)
        return self._cache[key]

    def summary(self, lat, lon, now=None) -> dict:
        now = now or datetime.now(timezone.utc)
        n = self.night(lat, lon, now)
        up = astro.moon_alt(lat, lon, now) > 0
        return {"astro": [hm(n.astro[0]), hm(n.astro[1])], "nautical": [hm(n.nautical[0]), hm(n.nautical[1])],
                "dark_now": bool(n.astro[0] and n.astro[1] and n.astro[0] <= now <= n.astro[1]),
                "moon": {"illum": round(n.moon_illum * 100), "waxing": n.moon_waxing, "up": up,
                         "rise": hm(n.moon_rise), "set": hm(n.moon_set)}}

    # ------------------------------------------------------------ Ziele
    def _entry(self, o: dict, lat, lon, n, now, kind=None) -> dict | None:
        v = astro.visibility(o["ra"], o["dec"], lat, lon, n, now, MIN_ALT)
        if not v or v["hours"] < 1.0:
            return None
        whole = v["from"] and v["to"] and v["from"] <= n.nautical[0] + timedelta(minutes=10) \
            and v["to"] >= n.nautical[1] - timedelta(minutes=20)
        kind = kind or o.get("kind")
        tag = "LP-Filter" if o.get("lp") else TAG.get(kind, "Objekt")
        later = v["from"] and v["from"] > now + timedelta(minutes=60)
        up_now = v["from"] and v["from"] <= now <= v["to"] and v["alt"] >= MIN_ALT
        return {"id": o["id"], "name": label(o), "short": o["name"], "kind": kind, "lp": bool(o.get("lp")),
                "ra": o["ra"], "dec": o["dec"], "planet": o.get("planet"), "type": o.get("type", ""),
                "window": "ganze Nacht" if whole else f"{hm(v['from'])}–{hm(v['to'])}",
                "peak": f"max. {round(v['peak_alt'])}° um {hm(v['peak_time'])}",
                "alt": round(v["alt"]), "up": bool(up_now), "tag": "später" if later else tag,
                "from": v["from"].isoformat() if v["from"] else None}

    def tonight(self, lat, lon, planets: dict | None = None, now=None, limit: int = 24) -> list[dict]:
        """planets: {"Saturn": (ra_jnow, dec_jnow)} – vom Seestar (seestar_alp) nachgeschlagen."""
        now = now or datetime.now(timezone.utc)
        n = self.night(lat, lon, now)
        order = lambda e: (not e["up"], -e["alt"] if e["up"] else 0, e["from"] or "")  # noqa: E731
        planets_out = []
        for name, (ra, dec) in (planets or {}).items():
            ra2, dec2 = astro.date_to_j2000(ra, dec, now)
            e = self._entry({"id": name.lower(), "name": name, "common": "", "kind": "planet", "planet": name,
                             "ra": ra2, "dec": dec2}, lat, lon, n, now)
            if e:
                planets_out.append(e)
        dso = [e for e in (self._entry(o, lat, lon, n, now) for o in self.highlights) if e]
        out = sorted(planets_out, key=order) + sorted(dso, key=order)[:limit]   # Planeten immer oben
        mr, md = astro.moon_radec(now)
        malt = astro.moon_alt(lat, lon, now)
        if n.moon_illum > 0.08:
            out.append({"id": "moon", "name": f"Mond ({round(n.moon_illum * 100)} %)", "short": "Mond", "kind": "moon",
                        "lp": False, "ra": None, "dec": None, "planet": None, "type": "Mond",
                        "window": f"Aufgang {hm(n.moon_rise)}" if n.moon_rise else "die ganze Nacht",
                        "peak": "Suchlauf am Seestar", "alt": round(malt), "up": malt > 10, "tag": "Mond", "from": None,
                        "radec_now": [mr, md]})
        return out

    def search(self, q: str, lat=None, lon=None, limit: int = 12, now=None) -> list[dict]:
        now = now or datetime.now(timezone.utc)
        k = q.strip().lower()
        if not k:
            return []
        res = []
        for p in PLANETS_DE:
            if p.lower().startswith(k):
                res.append({"id": p.lower(), "name": p, "short": p, "kind": "planet", "planet": p, "tag": "Planet",
                            "window": "Position live vom Seestar", "peak": "", "alt": None})
        if "mond".startswith(k) or "moon".startswith(k):
            res.append({"id": "moon", "name": "Mond", "short": "Mond", "kind": "moon", "tag": "Mond",
                        "window": "Suchlauf am Seestar", "peak": "", "alt": None})
        objs = []
        if self.catalog:
            from catalog import norm
            nk = norm(q)
            exact = self.catalog.find(q)
            for o in self.catalog.objects:
                keys = [o["id"], norm(o["name"]), norm(o.get("alt") or ""), norm(o.get("common") or "")]
                if o is exact:
                    s = 0
                elif any(x.startswith(nk) for x in keys if x):
                    s = 1
                elif any(nk in x for x in keys if x):
                    s = 2
                else:
                    continue
                objs.append((s, 0 if o["messier"] else 1, o["mag"] if o["mag"] is not None else 99, o))
            objs.sort(key=lambda t: t[:3])
            lp_types = {"HII", "EmN", "SNR", "PN", "Cl+N", "Neb"}
            objs = [{"id": o["id"], "name": o["name"], "common": o["common"], "kind": o["kind"], "type": o["type"],
                     "lp": o.get("type_code") in lp_types, "ra": o["ra"] / 15, "dec": o["dec"]}
                    for *_, o in objs[:limit]]
        else:
            objs = [o for o in self.highlights if k.replace(" ", "") in (o["name"] + o["common"] + o["alt"]).lower().replace(" ", "")][:limit]
        n = self.night(lat, lon, now) if lat is not None else None
        for o in objs:
            e = self._entry(o, lat, lon, n, now) if n else None
            if not e:
                alt = round(astro.altaz(o["ra"], o["dec"], lat, lon, now)[0]) if lat is not None else None
                e = {"id": o["id"], "name": label(o), "short": o["name"], "kind": o["kind"], "lp": bool(o.get("lp")),
                     "ra": o["ra"], "dec": o["dec"], "type": o.get("type", ""), "window": "heute kaum über 30°",
                     "peak": "", "alt": alt, "up": False, "tag": "tief"}
            res.append(e)
        return res[:limit]
