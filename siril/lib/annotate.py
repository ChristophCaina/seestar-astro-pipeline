#!/usr/bin/env python3
"""Erzeugt aus einem Stack-JPG + meta.json eine beschriftete Version (fuer Immich).

    annotate.py --meta stacks/M_31/M_31_master_IRCUT_siril.meta.json [--out …_beschriftet.jpg]

Zeichnet Objektumrisse und Namen (aus der WCS berechnet), Hinweise auf Objekte
knapp ausserhalb, Kompass, Massstab und eine Fusszeile mit den Aufnahmedaten.
Die EXIF-Daten (Aufnahmedatum) des Originals werden uebernommen.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "gallery" / "build"))
from wcs import pixel_to_world, pixscale_arcsec, screen_angle_of_pa, world_to_pixel  # noqa: E402

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

GOLD = (233, 201, 124)
WHITE = (226, 229, 238)
SHADOW = (0, 0, 0)
CONST = (120, 150, 230)
CONST_TXT = (170, 195, 255)
FONT_DIRS = ["/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/dejavu", "/usr/share/fonts/TTF"]


def font(size: int, serif: bool = False):
    name = "DejaVuSerif.ttf" if serif else "DejaVuSans.ttf"
    for d in FONT_DIRS:
        p = Path(d) / name
        if p.exists():
            return ImageFont.truetype(str(p), size)
    try:
        return ImageFont.load_default(size=size)
    except TypeError:            # Pillow < 10.1
        return ImageFont.load_default()


def ellipse_points(cx, cy, rx, ry, rot_deg, n=96):
    r = math.radians(rot_deg)
    return [(cx + rx * math.cos(t) * math.cos(r) - ry * math.sin(t) * math.sin(r),
             cy + rx * math.cos(t) * math.sin(r) + ry * math.sin(t) * math.cos(r))
            for t in (2 * math.pi * i / n for i in range(n + 1))]


def text(draw, xy, s, f, fill=GOLD, anchor="ls"):
    draw.text(xy, s, font=f, fill=fill, anchor=anchor, stroke_width=max(2, f.size // 12), stroke_fill=SHADOW)


def fmt_int(seconds: float) -> str:
    h, m = int(seconds // 3600), round((seconds % 3600) / 60)
    return f"{h} h {m:02d} min" if h else f"{m} min"


def annotate(meta_path: Path, out: Path) -> Path:
    m = json.loads(meta_path.read_text(encoding="utf-8"))
    src = meta_path.parent / m["image"]
    im = Image.open(src)
    exif = im.info.get("exif")
    im = im.convert("RGB")
    w, h = im.size
    k = w / 1920
    d = ImageDraw.Draw(im)
    f_lbl, f_small = font(round(34 * k), serif=True), font(round(20 * k))
    wcs = m.get("wcs")
    if wcs:
        sx = w / (m.get("width") or w)                      # meta-WCS bezieht sich auf width/height
        wcs = {"crval": wcs["crval"], "crpix": [wcs["crpix"][0] * sx, wcs["crpix"][1] * sx],
               "cd": [[c / sx for c in row] for row in wcs["cd"]]}
        px_per_arcmin = 60 / pixscale_arcsec(wcs)
        # Weitwinkel: Sternbildlinien und -namen zuerst (liegen unter den Objekten)
        f_con = font(round(30 * k), serif=True)
        for c in m.get("constellations", []):
            for seg in c.get("lines", []):
                pts = []
                for r, dd in seg:
                    p = world_to_pixel(r, dd, wcs)
                    if p is None or abs(p[0]) > 4 * w or abs(p[1]) > 4 * h:
                        if len(pts) > 1:
                            d.line(pts, fill=CONST, width=max(1, round(2 * k)))
                        pts = []
                        continue
                    pts.append(p)
                if len(pts) > 1:
                    d.line(pts, fill=CONST, width=max(1, round(2 * k)))
            if c.get("label"):
                p = world_to_pixel(c["label"][0], c["label"][1], wcs)
                if p and 0 <= p[0] <= w and 40 * k <= p[1] <= h - 80 * k:
                    text(d, p, c["name"], f_con, fill=CONST_TXT, anchor="mm")
        placed: list[tuple] = []
        for o in m.get("objects", []):
            p = world_to_pixel(o["ra"], o["dec"], wcs)
            if p is None:
                continue
            x, y = p
            if m.get("wide") and y > h - 70 * k:
                continue                                     # nicht unter die Fusszeile
            if 0 <= x <= w and 0 <= y <= h and (o.get("category") == "star" or m.get("wide")):
                star = o.get("category") == "star"
                mk = o.get("major") or 0
                r = 10 * k if star else max(12 * k, mk / 2 * px_per_arcmin)
                col = WHITE if star else GOLD
                d.ellipse([x - r, y - r, x + r, y + r], outline=col, width=max(1, round(1.5 * k)))
                name = o["name"] if star or not o.get("alt") else f"{o['name']} ({o['alt']})"
                tw = d.textlength(name, font=f_small)
                # freie Position fuer das Label suchen (rechts oben, rechts unten, links oben, links unten)
                for ox, oy, anc in ((r + 4 * k, -r - 2 * k, "ls"), (r + 4 * k, r + 20 * k, "ls"),
                                    (-r - 4 * k, -r - 2 * k, "rs"), (-r - 4 * k, r + 20 * k, "rs")):
                    bx0 = x + ox - (tw if anc == "rs" else 0)
                    box = (bx0, y + oy - f_small.size, bx0 + tw, y + oy + 4 * k)
                    if not any(box[0] < b[2] and b[0] < box[2] and box[1] < b[3] and b[1] < box[3] for b in placed):
                        placed.append(box)
                        text(d, (x + ox, y + oy), name, f_small, fill=col, anchor=anc)
                        break
            elif 0 <= x <= w and 0 <= y <= h:
                mk = o.get("marker") or [o.get("major") or 0, o.get("minor") or o.get("major") or 0]
                rx, ry = mk[0] / 2 * px_per_arcmin, mk[1] / 2 * px_per_arcmin
                rot = screen_angle_of_pa(o.get("pa") or 0, wcs)
                if rx > 2:
                    d.line(ellipse_points(x, y, rx, ry, rot), fill=GOLD, width=max(1, round(2 * k)))
                else:
                    r = 16 * k
                    d.ellipse([x - r, y - r, x + r, y + r], outline=GOLD, width=max(1, round(2 * k)))
                lx = x + min(max(rx * 0.7, 40 * k), 260 * k)
                ly = y - min(max(ry * 0.9, 60 * k), 220 * k)
                lx, ly = min(max(lx, 10), w - 10), min(max(ly, 40 * k), h - 80 * k)
                d.line([(x, y), (lx, ly + 6 * k)], fill=GOLD, width=1)
                anchor = "rs" if lx > w - 300 * k else "ls"
                text(d, (lx, ly), o["name"], f_lbl, anchor=anchor)
            elif not o.get("main") and not m.get("wide"):
                dx, dy = max(0.0, -x, x - w), max(0.0, -y, y - h)
                dist = round(math.hypot(dx, dy) / px_per_arcmin)
                ex, ey = min(max(x, 60 * k), w - 60 * k), min(max(y, 60 * k), h - 90 * k)
                arrow = "↓" if y > h else "↑" if y < 0 else ("→" if x > w else "←")
                text(d, (ex, ey), f"{arrow} {o['name']} · {dist}′", f_small,
                     anchor="rs" if ex > w / 2 else "ls")
        # Kompass oben rechts
        ra_c, dec_c = pixel_to_world(w / 2, h / 2, wcs)
        cx, cy = w / 2, h / 2
        xn, yn = world_to_pixel(ra_c, dec_c + 0.05, wcs)
        xe, ye = world_to_pixel(ra_c + 0.05 / max(math.cos(math.radians(dec_c)), 1e-6), dec_c, wcs)
        ox, oy, L = w - 110 * k, 110 * k, 70 * k
        for lab, (vx, vy), ln in (("N", (xn - cx, yn - cy), L), ("O", (xe - cx, ye - cy), L * 0.7)):
            n = math.hypot(vx, vy) or 1
            ux, uy = vx / n, vy / n
            d.line([(ox, oy), (ox + ux * ln, oy + uy * ln)], fill=WHITE, width=max(1, round(2 * k)))
            text(d, (ox + ux * (ln + 16 * k), oy + uy * (ln + 16 * k)), lab, f_small, fill=WHITE, anchor="mm")
        # Massstab unten links (Weitwinkel: 5°)
        wide = bool(m.get("wide"))
        bar = (300 if wide else 10) * px_per_arcmin
        y0 = h - 70 * k
        d.line([(40 * k, y0), (40 * k + bar, y0)], fill=WHITE, width=max(1, round(3 * k)))
        text(d, (40 * k, y0 - 10 * k), "5°" if wide else "10′", f_small, fill=WHITE)

    # Fusszeile
    t = m["target"]
    nights = m.get("nights", [])
    frames = sum(n.get("frames", 0) for n in nights)
    parts = [t["name"] + (f" – {t['common']}" if t.get("common") else "")]
    if frames and m.get("exposure_s"):
        parts.append(f"{frames} × {m['exposure_s']:g} s = {fmt_int(frames * m['exposure_s'])}")
    if len(nights) > 1:
        parts.append(f"{len(nights)} Nächte")
    elif nights:
        parts.append(nights[0]["date"])
    if m.get("filter"):
        parts.append(m["filter"])
    dev = (m.get("device") or {}).get("label") or "Seestar S50"
    parts.append(f"{dev} · Siril" if m.get("source", "siril") == "siril" else f"{dev} · App-Stack")
    text(d, (w - 30 * k, h - 26 * k), "  ·  ".join(parts), f_small, fill=WHITE, anchor="rs")

    out.parent.mkdir(parents=True, exist_ok=True)
    kw = {"quality": 90, "optimize": True}
    if exif:
        kw["exif"] = exif
    im.save(out, "JPEG", **kw)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--meta", required=True)
    ap.add_argument("--out", help="Default: <bild>_beschriftet.jpg neben dem Original")
    a = ap.parse_args()
    meta = Path(a.meta)
    m = json.loads(meta.read_text(encoding="utf-8"))
    src = meta.parent / m["image"]
    out = Path(a.out) if a.out else src.with_name(src.stem + "_beschriftet.jpg")
    print(annotate(meta, out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
