#!/usr/bin/env python3
"""Baut die statische Astro-Galerie aus den meta.json-Dateien der Stacks.

    python3 gallery_build.py --stacks /data/astro/stacks --out /data/astro/web

* sucht rekursiv nach *.meta.json (Format: gallery/SCHEMA.md)
* erzeugt Web-Bild (max. 1920 px) und Vorschau (640 px) unter <out>/img/
* rechnet Objektpositionen aus RA/Dec ueber die WCS in Pixel um
* schreibt <out>/catalog.json und kopiert das Frontend (gallery/site) nach <out>

Abhaengigkeit: Pillow  (Debian: apt install python3-pil)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from wcs import fmt_dec, fmt_ra, pixel_to_world, pixscale_arcsec, screen_angle_of_pa, world_to_pixel  # noqa: E402

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    sys.exit("Pillow fehlt: apt install python3-pil  (oder pip install pillow)")

SITE_DEFAULT = Path(__file__).resolve().parent.parent / "site"
SCHEMA = 1


def de_num(v: float, nd: int = 1) -> str:
    s = f"{v:.{nd}f}".rstrip("0").rstrip(".") if nd else f"{v:.0f}"
    return s.replace(".", ",")


def size_text(major: float, minor: float) -> str:
    if not major:
        return "–"
    if abs(major - minor) < 1e-6:
        return f"{de_num(major)}′"
    return f"{de_num(major)}′ × {de_num(minor)}′"


def resize(src: Path, dst: Path, width: int, quality: int) -> tuple[int, int]:
    """Skaliert auf maximale Breite; ueberspringt, wenn dst aktueller ist."""
    with Image.open(src) as im:
        w, h = im.size
        if w > width:
            h = round(h * width / w); w = width
        if not dst.exists() or dst.stat().st_mtime < src.stat().st_mtime:
            dst.parent.mkdir(parents=True, exist_ok=True)
            im.convert("RGB").resize((w, h), Image.LANCZOS).save(dst, "JPEG", quality=quality, optimize=True, progressive=True)
    return w, h


def scale_wcs(wcs: dict, k: float) -> dict:
    """WCS fuer ein um Faktor k skaliertes Bild (Pixelmitte-genau genug fuer die Anzeige)."""
    return {"crval": wcs["crval"], "crpix": [wcs["crpix"][0] * k, wcs["crpix"][1] * k],
            "cd": [[c / k for c in row] for row in wcs["cd"]]}


def unit(dx: float, dy: float) -> list[float]:
    n = math.hypot(dx, dy) or 1
    return [round(dx / n, 4), round(dy / n, 4)]


def place_labels(annotations: list[dict], img_w: int) -> None:
    """Weitwinkel: Label-Versatz so waehlen, dass sich Beschriftungen nicht ueberlappen (wie app.js gross)."""
    k = img_w / 1920
    placed = []
    for a in annotations:
        fs = (23 if a["category"] == "star" else 27) * k
        tw, r = len(a["name"]) * fs * 0.58, max(a["rx"], 14 * k)
        for dx, dy in ((r + 16 * k, -r - 10 * k), (r + 16 * k, r + fs + 6 * k),
                       (-r - 16 * k, -r - 10 * k), (-r - 16 * k, r + fs + 6 * k)):
            x0 = a["x"] + dx - (tw if dx < 0 else 0)
            box = (x0, a["y"] + dy - fs, x0 + tw, a["y"] + dy + 4 * k)
            if not any(box[0] < b[2] and b[0] < box[2] and box[1] < b[3] and b[1] < box[3] for b in placed):
                placed.append(box)
                a["label"] = [round(dx, 1), round(dy, 1)]
                break


def build_entry(meta_path: Path, out: Path, web_w: int, thumb_w: int) -> dict:
    m = json.loads(meta_path.read_text(encoding="utf-8"))
    if m.get("schema") != SCHEMA:
        raise ValueError(f"unbekanntes schema {m.get('schema')}")
    src = meta_path.parent / m["image"]
    if not src.exists():
        raise FileNotFoundError(src)

    eid = m["id"]
    w, h = resize(src, out / "img" / f"{eid}.jpg", web_w, 86)
    resize(src, out / "img" / f"{eid}_t.jpg", thumb_w, 80)
    k = w / (m.get("width") or w)
    nights = sorted(m.get("nights", []), key=lambda n: n["date"])
    frames = sum(n["frames"] for n in nights)
    base = {
        "id": eid, "target": m["target"], "stack": m.get("stack", "night"), "filter": m.get("filter", ""),
        "exposure_s": m.get("exposure_s"), "gain": m.get("gain"),
        "nights": nights, "frames": frames, "seconds": frames * (m.get("exposure_s") or 0),
        "last": nights[-1]["date"] if nights else None,
        "image": {"web": f"img/{eid}.jpg", "thumb": f"img/{eid}_t.jpg", "w": w, "h": h},
        "caption": m.get("caption", ""), "versions": m.get("versions", []), "sample": bool(m.get("sample")),
        "source": m.get("source", "siril"),
        "device": m.get("device") or {"id": "s50", "label": "Seestar S50"},
    }
    if not m.get("wcs"):            # Plate-Solving fehlgeschlagen: Bild ohne Annotationen
        return {**base, "wcs": None, "solve": None, "annotations": [], "offscreen": [], "constellations": []}
    wcs = scale_wcs(m["wcs"], k)
    scale = pixscale_arcsec(wcs)                     # "/px im Web-Bild
    px_per_arcmin = 60 / scale

    # Orientierung in der Bildmitte
    ra_c, dec_c = pixel_to_world(w / 2, h / 2, wcs)
    xc, yc = w / 2, h / 2
    xn, yn = world_to_pixel(ra_c, dec_c + 0.05, wcs)
    xe, ye = world_to_pixel(ra_c + 0.05 / max(math.cos(math.radians(dec_c)), 1e-6), dec_c, wcs)
    north, east = unit(xn - xc, yn - yc), unit(xe - xc, ye - yc)
    north_deg = math.degrees(math.atan2(north[0], -north[1])) % 360
    north_txt = "oben" if min(north_deg, 360 - north_deg) < 3 else f"{north_deg:.0f}° im Uhrzeigersinn"

    annotations, offscreen = [], []
    for o in m.get("objects", []):
        p = world_to_pixel(o["ra"], o["dec"], wcs)
        if p is None:
            continue
        x, y = p
        inside = 0 <= x <= w and 0 <= y <= h
        if not inside:
            dx = max(0.0, -x, x - w); dy = max(0.0, -y, y - h)
            offscreen.append({"name": o["name"], "x": round(x, 1), "y": round(y, 1),
                              "dist_arcmin": round(math.hypot(dx, dy) / px_per_arcmin)})
            continue
        major, minor = o.get("major") or 0, o.get("minor") or o.get("major") or 0
        mk = o.get("marker") or [major, minor]
        annotations.append({
            "name": o["name"], "alt": o.get("alt", ""), "type": o.get("type", ""),
            "category": o.get("category", "dso"), "main": bool(o.get("main")),
            "x": round(x, 1), "y": round(y, 1),
            "rx": round(mk[0] / 2 * px_per_arcmin, 1), "ry": round(mk[1] / 2 * px_per_arcmin, 1),
            "rot": round(screen_angle_of_pa(o.get("pa", 0), wcs), 1),
            "dashed": major * px_per_arcmin > w * 0.8,
            "mag": de_num(o["mag"]) if o.get("mag") is not None else "–",
            "size": size_text(major, minor), "dist": o.get("dist") or "–",
            "label": [v * k for v in o["label"]] if o.get("label") else None,
            "small": bool(m.get("wide")),
        })

    if m.get("wide"):
        place_labels(annotations, w)

    # Weitwinkel: Sternbildlinien in Pixel umrechnen (Teilstuecke hinter der Tangentialebene weglassen)
    constellations = []
    for c in m.get("constellations", []):
        paths = []
        for seg in c.get("lines", []):
            cur = []
            for r, d in seg:
                p = world_to_pixel(r, d, wcs)
                if p is None or abs(p[0]) > 4 * w or abs(p[1]) > 4 * h:
                    if len(cur) > 1:
                        paths.append(cur)
                    cur = []
                    continue
                cur.append([round(p[0], 1), round(p[1], 1)])
            if len(cur) > 1:
                paths.append(cur)
        lab = None
        if c.get("label"):
            q = world_to_pixel(c["label"][0], c["label"][1], wcs)
            lab = [round(q[0], 1), round(q[1], 1)] if q else None
        if paths:
            constellations.append({"name": c["name"], "paths": paths, "label": lab})

    return {**base, "wcs": wcs, "constellations": constellations, "wide": bool(m.get("wide")),
            "solve": {"ra": fmt_ra(ra_c), "dec": fmt_dec(dec_c), "pixscale": round(scale, 2),
                      "fov": [round(w * scale / 3600, 2), round(h * scale / 3600, 2)],
                      "north": north, "east": east, "north_txt": north_txt},
            "annotations": annotations, "offscreen": offscreen}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stacks", default="/data/astro/stacks", help="Ordner mit *.meta.json (rekursiv)")
    ap.add_argument("--out", default="/data/astro/web", help="Zielordner der Website")
    ap.add_argument("--site", default=str(SITE_DEFAULT), help="Frontend-Dateien")
    ap.add_argument("--web-width", type=int, default=1920)
    ap.add_argument("--thumb-width", type=int, default=640)
    a = ap.parse_args()

    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    entries, errors = [], 0
    for meta in sorted(Path(a.stacks).rglob("*.meta.json")):
        try:
            entries.append(build_entry(meta, out, a.web_width, a.thumb_width))
            print(f"ok      {meta.name}")
        except Exception as e:  # einzelne kaputte Datei soll den Rest nicht verhindern
            errors += 1
            print(f"FEHLER  {meta}: {e}", file=sys.stderr)

    catalog = {"schema": SCHEMA, "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "entries": entries}
    tmp = out / "catalog.json.tmp"
    tmp.write_text(json.dumps(catalog, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(out / "catalog.json")

    site = Path(a.site)
    for f in site.iterdir():
        if f.is_file() and f.name != "index.html":
            shutil.copy2(f, out / f.name)
    # Cache-Busting: app.js/styles.css mit Inhalts-Hash, damit Browser nach Updates neu laden
    html = (site / "index.html").read_text(encoding="utf-8")
    for asset in ("app.js", "styles.css"):
        if (site / asset).exists():
            v = hashlib.sha1((site / asset).read_bytes()).hexdigest()[:8]
            html = html.replace(f'"{asset}"', f'"{asset}?v={v}"')
    (out / "index.html").write_text(html, encoding="utf-8")
    print(f"{len(entries)} Stacks -> {out}  ({errors} Fehler)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
