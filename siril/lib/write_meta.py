#!/usr/bin/env python3
"""Schreibt die meta.json fuer einen Stack (Format: gallery/SCHEMA.md).

    write_meta.py --fits result_linear.fit --jpg export/M_31/M_31_master_IRCUT_siril.jpg \
        --target "M 31" --filter IRCUT --exposure 10 --stack master \
        --nights 2026-10-18:212,2026-10-19:247 --catalog NGC.csv --out stacks/M_31/....meta.json

Die WCS kommt aus dem FITS-Header (nach Siril `platesolve`). Fehlt sie, entsteht eine
meta.json ohne WCS – die Galerie zeigt das Bild dann ohne Annotationen.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent.parent / "gallery" / "build"))
from catalog import Catalog, display, norm  # noqa: E402
from fitsheader import read_header, wcs_from_header  # noqa: E402
from wcs import pixscale_arcsec, world_to_pixel  # noqa: E402

MAX_OBJECTS = 25


def sort_key(name: str) -> int:
    n = norm(name)
    for prefix, base in (("M", 0), ("NGC", 1000), ("IC", 10000)):
        if n.startswith(prefix) and n[len(prefix):].isdigit():
            return base + int(n[len(prefix):])
    return 99999


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fits", required=True)
    ap.add_argument("--jpg", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--target", required=True)
    ap.add_argument("--filter", default="")
    ap.add_argument("--exposure", type=float, default=None)
    ap.add_argument("--stack", choices=["night", "master"], default="night")
    ap.add_argument("--nights", required=True, help="YYYY-MM-DD:frames[,…]")
    ap.add_argument("--catalog", action="append", default=[], help="OpenNGC-CSV (mehrfach möglich)")
    ap.add_argument("--extra", default=str(HERE / "objects_de.json"))
    ap.add_argument("--no-flip-y", action="store_true", help="FITS ist bereits top-down gespeichert")
    ap.add_argument("--caption", default="")
    a = ap.parse_args()

    hdr = read_header(a.fits)
    w, h = int(hdr.get("NAXIS1", 0)), int(hdr.get("NAXIS2", 0))
    wcs = wcs_from_header(hdr, flip_y=not a.no_flip_y)
    cat = Catalog([Path(p) for p in a.catalog], Path(a.extra))
    main_obj = cat.find(a.target)

    objects = []
    if wcs:
        scale = pixscale_arcsec(wcs)
        found = cat.in_field(wcs, w, h, world_to_pixel, scale_arcsec=scale)
        if main_obj and main_obj not in found:
            found.insert(0, main_obj)
        for o in found[:MAX_OBJECTS]:
            objects.append({
                "name": o["name"], "alt": o["alt"] or o["common"], "type": o["type"],
                "ra": round(o["ra"], 6), "dec": round(o["dec"], 6),
                "major": o["major"], "minor": o["minor"], "pa": o["pa"],
                "mag": o["mag"], "dist": o.get("dist"), "category": "dso",
                "main": o is main_obj,
            })
        if objects and not any(o["main"] for o in objects):
            objects[0]["main"] = True
        if main_obj:      # Hauptobjekt nach vorne
            objects.sort(key=lambda o: not o["main"])

    nights = []
    for part in a.nights.split(","):
        d, f = part.split(":")
        nights.append({"date": d, "frames": int(f)})

    name_disp = main_obj["name"] if main_obj else a.target
    common = (main_obj or {}).get("common") or ""
    if main_obj and common == a.target:
        common = ""
    jpg_rel = os.path.relpath(Path(a.jpg).resolve(), Path(a.out).resolve().parent)
    meta = {
        "schema": 1, "sample": False,
        "id": Path(a.jpg).stem.lower(),
        "target": {"name": name_disp, "common": common, "kind": (main_obj or {}).get("kind", "other"),
                   "sort": sort_key(name_disp)},
        "stack": a.stack, "filter": a.filter,
        "exposure_s": a.exposure if a.exposure is not None else hdr.get("EXPTIME"),
        "gain": hdr.get("GAIN"),
        "nights": nights,
        "image": jpg_rel, "width": w, "height": h,
        "wcs": wcs, "objects": objects, "caption": a.caption,
        "versions": [],
    }
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(a.out).with_suffix(".tmp")
    tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(a.out)
    print(f"{a.out}: {len(objects)} Objekte, WCS {'ja' if wcs else 'nein'}, Ziel {display(name_disp)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
