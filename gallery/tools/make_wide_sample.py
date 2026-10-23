#!/usr/bin/env python3
"""Erzeugt ein simuliertes Weitwinkel-Beispiel (Milchstraße im Schwan, EOS 6D + 24 mm) fuer die Galerie.

Bild: Milchstraßenband entlang der galaktischen Ebene, Sterne aus sky.json plus Zufallssterne,
dunkle Wolken (Cygnus Rift). WCS und meta.json kommen wie bei echten Stacks aus build_meta().

Aufruf:  python3 gallery/tools/make_wide_sample.py
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "gallery" / "build"))
sys.path.insert(0, str(ROOT / "siril" / "lib"))
from catalog import Catalog  # noqa: E402
from write_meta import build_meta, write_json  # noqa: E402
import sky  # noqa: E402

W, H = 1800, 1200
SCALE = 74.0 * 3600 / W               # "/px: 24 mm an Vollformat ≈ 74° × 50°
OUT = ROOT / "gallery" / "sample-data" / "stacks" / "Milchstrasse_Schwan"
NAME = "Milchstrasse_Schwan_2026-08-15_WIDE_eos6d_siril"


def wcs_for(ra_deg, dec_deg, rot_deg):
    s = SCALE / 3600
    r = math.radians(rot_deg)
    # Nord oben, Ost links, um rot gedreht: xi = -s*x', eta = -s*y'
    cd = [[-s * math.cos(r), -s * math.sin(r)], [s * math.sin(r), -s * math.cos(r)]]
    return {"crval": [ra_deg, dec_deg], "crpix": [W / 2, H / 2], "cd": cd}


def pix2world_grid(wcs):
    y, x = np.mgrid[0:H, 0:W].astype(np.float64)
    dx, dy = x - wcs["crpix"][0], y - wcs["crpix"][1]
    (a, b), (c, d) = wcs["cd"]
    xi, eta = np.radians(a * dx + b * dy), np.radians(c * dx + d * dy)
    ra0, dec0 = map(math.radians, wcs["crval"])
    den = math.cos(dec0) - eta * math.sin(dec0)
    ra = ra0 + np.arctan2(xi, den)
    dec = np.arctan2(math.sin(dec0) + eta * math.cos(dec0), np.hypot(xi, den))
    return ra, dec


def galactic(ra, dec):
    """Aequatorial (rad) -> galaktisch (Grad), J2000."""
    ra_gp, dec_gp, l_ncp = math.radians(192.85948), math.radians(27.12825), math.radians(122.93192)
    sb = np.sin(dec) * math.sin(dec_gp) + np.cos(dec) * math.cos(dec_gp) * np.cos(ra - ra_gp)
    b = np.arcsin(np.clip(sb, -1, 1))
    y = np.cos(dec) * np.sin(ra - ra_gp)
    x = np.sin(dec) * math.cos(dec_gp) - np.cos(dec) * math.sin(dec_gp) * np.cos(ra - ra_gp)
    l = l_ncp - np.arctan2(y, x)
    return np.degrees(l) % 360, np.degrees(b)


def render(wcs, rng):
    ra, dec = pix2world_grid(wcs)
    l, b = galactic(ra, dec)
    # Band: heller zum galaktischen Zentrum hin, Breite ~10°
    band = np.exp(-(b / 9.0) ** 2) * (0.55 + 0.45 * np.cos(np.radians(l)) ** 2)
    noise = rng.normal(0, 1, (H // 8, W // 8))
    clouds = np.array(Image.fromarray(((noise - noise.min()) / np.ptp(noise) * 255).astype(np.uint8))
                      .resize((W, H), Image.BICUBIC).filter(ImageFilter.GaussianBlur(18)), dtype=np.float64) / 255
    rift = np.exp(-((b - 1.5) / 2.2) ** 2) * np.exp(-((l - 62) / 18) ** 2)   # Cygnus Rift
    glow = band * (0.6 + 0.6 * clouds) * (1 - 0.55 * rift)
    img = np.zeros((H, W, 3))
    img += 0.05                                                      # Himmelshintergrund
    img[..., 0] += glow * 0.42
    img[..., 1] += glow * 0.38
    img[..., 2] += glow * 0.33
    # H-alpha-Rot (umgebaute 6D) im Nordamerika-/Pelikangebiet
    ha = np.exp(-(((l - 85) / 2.5) ** 2 + ((b + 0.5) / 2.0) ** 2))
    img[..., 0] += ha * 0.35
    # Sterne
    stars = np.zeros((H, W))
    n = 9000
    xs, ys = rng.uniform(0, W, n), rng.uniform(0, H, n)
    mags = rng.exponential(1.0, n)
    bb = galactic(*[np.radians(v) for v in _world(wcs, xs, ys)])[1]
    keep = rng.random(n) < (0.35 + 0.65 * np.exp(-(bb / 15) ** 2))
    for x, y, m in zip(xs[keep], ys[keep], mags[keep]):
        stars[int(y) % H, int(x) % W] += 0.25 * math.exp(-m)
    from wcs import world_to_pixel
    for s in sky.load()["stars"]:
        p = world_to_pixel(s["ra"], s["dec"], wcs)
        if p and 0 <= p[0] < W and 0 <= p[1] < H:
            stars[int(p[1]), int(p[0])] += 10 ** (-0.4 * (s["mag"] - 1.0)) * 3
    st = np.array(Image.fromarray(np.clip(stars * 255, 0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(1.1)),
                  dtype=np.float64) / 255 * 3
    for c, k in zip(range(3), (1.0, 0.97, 1.05)):
        img[..., c] += st * k
    img = 1 - np.exp(-img * 1.6)                                    # weiches Clipping
    return Image.fromarray((np.clip(img, 0, 1) * 255).astype(np.uint8))


def _world(wcs, xs, ys):
    from wcs import pixel_to_world
    pts = [pixel_to_world(x, y, wcs) for x, y in zip(xs, ys)]
    return np.array([p[0] for p in pts]), np.array([p[1] for p in pts])


def main():
    rng = np.random.default_rng(7)
    wcs = wcs_for(305.0, 38.0, -22)                 # Schwan, Deneb – Albireo schräg im Bild
    OUT.mkdir(parents=True, exist_ok=True)
    jpg = OUT / f"{NAME}.jpg"
    render(wcs, rng).save(jpg, "JPEG", quality=88)
    cat = Catalog([ROOT / "tests" / "fixtures" / "ngc_subset.csv"], ROOT / "siril" / "lib" / "objects_de.json")
    meta = build_meta(wcs=wcs, width=W, height=H, target="Milchstraße im Schwan", catalog=cat, jpg=jpg,
                      out=OUT / f"{NAME}.meta.json", stack="night", filter_="WIDE", exposure=12, gain=None,
                      nights=[{"date": "2026-08-15", "frames": 60}],
                      caption="Simuliert: EOS 6D (astromodifiziert) mit 24 mm, 60 × 12 s auf der iEXOS-100 als Nachführung.",
                      device={"id": "eos6d", "label": "Canon EOS 6D"})
    meta["sample"] = True
    write_json(OUT / f"{NAME}.meta.json", meta)
    print(f"{jpg.name}: {len(meta['constellations'])} Sternbilder, "
          f"{sum(o['category'] == 'star' for o in meta['objects'])} Sterne, "
          f"{sum(o['category'] == 'dso' for o in meta['objects'])} DSO")


if __name__ == "__main__":
    main()
