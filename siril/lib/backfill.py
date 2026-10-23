#!/usr/bin/env python3
"""Nachhol-Job: aeltere Seestar-App-Stacks aus Immich nachtraeglich plate-solven und beschriften.

    backfill.py --since 2026-01-01 [--until …] [--album "Seestar"] [--limit 20] [--dry-run]

Ablauf je App-Stack (Dateiname passt zu IMMICH_APP_PATTERN):
  1. Original aus Immich laden          -> <stacks>/_app/<ziel>/<datei>.jpg
  2. astrometry.net solve-field          (Skalenhinweis Seestar S50: 2,2–2,6 "/px)
  3. meta.json (source "app") schreiben  -> erscheint in der Galerie
  4. beschriftete Version                -> <export>/<ziel>/<datei>_beschriftet.jpg
     (landet ueber die External Library in Immich, immich_sync stapelt sie zum Original)

Bereits verarbeitete Assets merkt sich <stacks>/../.state/backfill.json.
Benoetigt: astrometry.net (apt install astrometry.net) plus Indexdateien fuer
ca. 0,7–1,3° Bildfeld (z. B. 4107–4110).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent.parent / "gallery" / "build"))
from annotate import annotate  # noqa: E402
from catalog import Catalog  # noqa: E402
from fitsheader import read_header, wcs_from_header  # noqa: E402
from immich import from_env  # noqa: E402
from write_meta import build_meta, write_json  # noqa: E402
import sky  # noqa: E402

from PIL import Image  # noqa: E402


def slugify(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.+-]", "", s.replace(" ", "_").replace("/", "_"))


def night_of(local_dt: str) -> str:
    """'2026-10-19T01:15:00.000Z' (lokale Zeit laut Immich) -> Nacht '2026-10-18'"""
    dt = datetime.fromisoformat(local_dt.replace("Z", "").split(".")[0])
    return (dt - timedelta(days=1) if dt.hour < 12 else dt).strftime("%Y-%m-%d")


def solve(jpg: Path, work: Path, solve_field: str, lo: float, hi: float) -> Path | None:
    cmd = [solve_field, "--overwrite", "--no-plots", "--downsample", "2", "--cpulimit", "120",
           "--scale-units", "arcsecperpix", "--scale-low", str(lo), "--scale-high", str(hi),
           "--dir", str(work), "--new-fits", "none", str(jpg)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    wcs = work / (jpg.stem + ".wcs")
    if r.returncode != 0 or not wcs.exists():
        return None
    return wcs


def pick_target(wcs: dict, w: int, h: int, cat: Catalog) -> str:
    """Hauptobjekt = groesstes/hellstes Katalogobjekt nahe der Bildmitte.
    Weitwinkel (z. B. Milchstrassen-Modus des S30 Pro): Sternbild nahe der Bildmitte."""
    from wcs import pixscale_arcsec, world_to_pixel
    if sky.is_wide(wcs, w, h, pixscale_arcsec):
        best, dmin = None, 1e9
        for c in sky.constellations(wcs, w, h, world_to_pixel, margin_frac=0):
            if not c.get("label"):
                continue
            x, y = world_to_pixel(c["label"][0], c["label"][1], wcs)
            dist = math.hypot(x - w / 2, y - h / 2)
            if dist < dmin:
                best, dmin = c["name"], dist
        return f"Sternfeld {best}" if best else "Sternfeld"
    best, score = None, -1e9
    for o in cat.in_field(wcs, w, h, world_to_pixel, margin_arcmin=0, scale_arcsec=pixscale_arcsec(wcs)):
        x, y = world_to_pixel(o["ra"], o["dec"], wcs)
        dist = ((x - w / 2) ** 2 + (y - h / 2) ** 2) ** 0.5 / max(w, h)
        s = (o["major"] or 0) - 20 * dist + (5 if o["messier"] else 0) - (o["mag"] or 15)
        if s > score:
            best, score = o, s
    return best["name"] if best else ""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stacks", default=os.environ.get("STACK_DIR", "/data/astro/stacks"))
    ap.add_argument("--export", default=os.environ.get("EXPORT_DIR", "/data/astro/export"))
    ap.add_argument("--since", required=True, help="YYYY-MM-DD")
    ap.add_argument("--until", default=None, help="YYYY-MM-DD (Default: heute)")
    ap.add_argument("--limit", type=int, default=0, help="max. Anzahl pro Lauf (0 = alle)")
    ap.add_argument("--catalog", action="append", default=[])
    ap.add_argument("--solve-field", default=os.environ.get("SOLVE_FIELD", "solve-field"))
    ap.add_argument("--scale-low", type=float, default=2.2)
    ap.add_argument("--scale-high", type=float, default=2.6)
    ap.add_argument("--flip-y", action="store_true", help="WCS ist bottom-up (Overlay vertikal gespiegelt?)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    if not shutil.which(a.solve_field) and not a.dry_run:
        sys.exit(f"{a.solve_field} nicht gefunden – apt install astrometry.net")
    stacks, export = Path(a.stacks), Path(a.export)
    state_path = stacks.parent / ".state" / "backfill.json"
    done = set(json.loads(state_path.read_text())) if state_path.exists() else set()
    lib = os.environ.get("IMMICH_LIBRARY_ID", "")
    app_re = re.compile(os.environ.get("IMMICH_APP_PATTERN", "seestar|stack"), re.I)
    catalogs = a.catalog or [str(HERE.parent / "data" / "NGC.csv"), str(HERE.parent / "data" / "addendum.csv")]
    cat = Catalog([Path(c) for c in catalogs], HERE / "objects_de.json")
    im = from_env()

    until = a.until or datetime.now().strftime("%Y-%m-%d")
    assets = [x for x in im.search(takenAfter=f"{a.since}T00:00:00Z", takenBefore=f"{until}T23:59:59Z", type="IMAGE")
              if app_re.search(x.get("originalFileName", "")) and x["id"] not in done
              and not (lib and x.get("libraryId") == lib)]
    if a.limit:
        assets = assets[:a.limit]
    print(f"{len(assets)} App-Stacks zu verarbeiten")
    ok = 0
    for x in assets:
        name = x["originalFileName"]
        night = night_of(x.get("localDateTime") or x.get("fileCreatedAt"))
        print(f"-> {name} ({night})", end=" ")
        if a.dry_run:
            print("[dry-run]"); continue
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            src = im.download_original(x["id"], tmp / name)
            if src.suffix.lower() not in (".jpg", ".jpeg", ".png", ".tif", ".tiff"):
                print("übersprungen (Format)"); done.add(x["id"]); continue
            wcs_file = solve(src, tmp, a.solve_field, a.scale_low, a.scale_high)
            if not wcs_file:
                print("nicht lösbar"); done.add(x["id"]); continue
            with Image.open(src) as img:
                w, h = img.size
            hdr = read_header(wcs_file)
            if not hdr.get("NAXIS2"):                 # .wcs von solve-field ist reiner Header
                hdr["NAXIS2"] = hdr.get("IMAGEH") or h
            wcs = wcs_from_header(hdr, flip_y=a.flip_y)
            target = pick_target(wcs, w, h, cat) or Path(name).stem
            slug = slugify(target)
            dest = stacks / "_app" / slug / f"{Path(name).stem}.jpg"
            dest.parent.mkdir(parents=True, exist_ok=True)
            with Image.open(src) as img:
                kw = {"quality": 95}
                if img.info.get("exif"):
                    kw["exif"] = img.info["exif"]
                img.convert("RGB").save(dest, "JPEG", **kw)
            meta_path = dest.with_suffix(".meta.json")
            meta = build_meta(wcs=wcs, width=w, height=h, target=target, catalog=cat, jpg=dest, out=meta_path,
                              stack="night", nights=[{"date": night, "frames": 0}], source="app",
                              caption="Seestar-App-Stack, nachträglich plate-gelöst.")
            meta["id"] = f"app_{slugify(Path(name).stem).lower()}"
            write_json(meta_path, meta)
            ann = annotate(meta_path, export / slug / f"{Path(name).stem}_beschriftet.jpg")
            os.utime(ann, (dest.stat().st_mtime, dest.stat().st_mtime))
            print(f"gelöst: {target}, {len(meta['objects'])} Objekte")
            done.add(x["id"]); ok += 1
    if not a.dry_run:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(sorted(done)))
    print(f"{ok} gelöst")
    return 0


if __name__ == "__main__":
    sys.exit(main())
