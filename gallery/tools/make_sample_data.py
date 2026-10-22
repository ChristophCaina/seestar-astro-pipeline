#!/usr/bin/env python3
"""Erzeugt Beispieldaten fuer die Galerie (gallery/sample-data/).

Jedes Beispiel bekommt eine echte TAN-WCS (Seestar S50: 1920x1080, 2,39"/px) und
Objekte mit realen Koordinaten. Die Pixelpositionen rechnet der Builder spaeter
aus RA/Dec – genau wie bei echten Stacks. Zusaetzlich schreibt das Script
render.json, damit tools/render_samples.mjs die simulierten Bilder passend zeichnet.

Aufruf:  python3 gallery/tools/make_sample_data.py
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "build"))
from wcs import parse_dec, parse_ra, screen_angle_of_pa, world_to_pixel  # noqa: E402

OUT = HERE.parent / "sample-data" / "stacks"
W, H, SCALE = 1920, 1080, 2.39            # Seestar S50
S = 60 / SCALE                            # px pro Bogenminute


def make_wcs(ra: str, dec: str, crpix, beta_deg: float) -> dict:
    """Bildgeometrie: Nord oben/Ost links, dann um beta im Uhrzeigersinn gedreht."""
    b = math.radians(beta_deg)
    cols = []
    for dX, dY in ((1, 0), (0, 1)):
        X, Y = dX / S, dY / S                         # Bogenminuten auf dem Bild
        x = X * math.cos(b) + Y * math.sin(b)         # Drehung rueckgaengig
        y = -X * math.sin(b) + Y * math.cos(b)
        cols.append((-x / 60, -y / 60))               # xi = -x, eta = -y (Grad)
    cd = [[cols[0][0], cols[1][0]], [cols[0][1], cols[1][1]]]
    return {"crval": [round(parse_ra(ra), 7), round(parse_dec(dec), 7)], "crpix": list(crpix), "cd": cd}


def obj(name, ra, dec, type_, major, minor=None, pa=0, mag=None, dist=None, alt="", cat="dso", **kw):
    return {"name": name, "alt": alt, "type": type_, "ra": round(parse_ra(ra), 6), "dec": round(parse_dec(dec), 6),
            "major": major, "minor": minor if minor is not None else major, "pa": pa,
            "mag": mag, "dist": dist, "category": cat, **kw}


SAMPLES = [
    dict(slug="M_31", stack="master", target=dict(name="M 31", common="Andromeda-Galaxie", kind="galaxy", sort=1031),
         filter="IRCUT", nights=[("2026-10-18", 212), ("2026-10-19", 247), ("2026-10-21", 186), ("2026-10-22", 167)],
         wcs=make_wcs("00 42 44.3", "+41 16 09", (960 + 8 * S, 540), 121),
         caption="Die Seestar-Bildfeldbreite reicht nicht für die ganze Galaxie – M 110 liegt knapp unterhalb.",
         objects=[
             obj("M 31", "00 42 44.3", "+41 16 09", "Spiralgalaxie (Sb)", 190, 60, screen=-7, mag=3.4, dist="2,5 Mio. Lj",
                 alt="NGC 224", main=True, marker=[84, 22], label=[60, -120]),
             obj("M 32", "00 42 41.8", "+40 51 55", "Elliptische Zwerggalaxie (E2)", 8.7, 6.5, pa=170, mag=8.1,
                 dist="2,5 Mio. Lj", alt="NGC 221", label=[-40, -120]),
             obj("NGC 206", "00 40 31.3", "+40 44 00", "Sternassoziation", 4.4, 2.8, screen=-7, dist="2,5 Mio. Lj",
                 alt="Sternwolke in M 31", label=[40, 110]),
             obj("M 110", "00 40 22.1", "+41 41 07", "Elliptische Zwerggalaxie (E5)", 21.9, 11, pa=170, mag=8.5,
                 dist="2,7 Mio. Lj", alt="NGC 205"),
         ]),
    dict(slug="M_42", stack="master", target=dict(name="M 42", common="Orionnebel", kind="nebula", sort=1042),
         filter="IRCUT", nights=[("2026-10-21", 154), ("2026-10-22", 198)],
         wcs=make_wcs("05 35 16.5", "-05 23 23", (960, 540), 0),
         caption="Kurze Belichtung pro Frame hält den hellen Kern um das Trapez nicht ausgebrannt.",
         objects=[
             obj("M 42", "05 35 17.3", "-05 23 28", "Emissionsnebel (H-II-Region)", 85, 60, mag=4.0, dist="1.340 Lj",
                 alt="NGC 1976", main=True, marker=[60, 40], label=[300, 250]),
             obj("M 43", "05 35 31.0", "-05 16 03", "Emissionsnebel", 20, 15, mag=9.0, dist="1.340 Lj",
                 alt="De Mairans Nebel", marker=[6.8, 5.6], label=[-60, -90]),
             obj("θ¹ Ori (Trapez)", "05 35 16.5", "-05 23 23", "Mehrfachstern", 0, mag=5.1, dist="1.340 Lj", cat="star"),
             obj("NGC 1977", "05 35 16.0", "-04 49 00", "Emissions-/Reflexionsnebel", 20, 10, dist="1.500 Lj",
                 alt="Running-Man-Nebel"),
         ]),
    dict(slug="M_13", stack="master", target=dict(name="M 13", common="Herkuleshaufen", kind="cluster", sort=1013),
         filter="IRCUT", nights=[("2026-09-26", 143), ("2026-10-02", 120)],
         wcs=make_wcs("16 41 41.2", "+36 27 35", (960 + 6 * S, 540 + 6 * S), 0),
         caption="Mit im Bild: die kleine Spiralgalaxie NGC 6207 nordöstlich des Haufens.",
         objects=[
             obj("M 13", "16 41 41.2", "+36 27 35", "Kugelsternhaufen", 20, mag=5.8, dist="22.200 Lj", alt="NGC 6205",
                 main=True, label=[220, -230]),
             obj("NGC 6207", "16 43 03.9", "+36 49 57", "Spiralgalaxie (Sc)", 3.0, 1.1, pa=15, mag=11.6,
                 dist="≈ 30 Mio. Lj", label=[-30, -60]),
         ]),
    dict(slug="M_27", stack="master", target=dict(name="M 27", common="Hantelnebel", kind="nebula", sort=1027),
         filter="IRCUT", nights=[("2026-09-27", 260), ("2026-10-03", 205)],
         wcs=make_wcs("19 59 36.3", "+22 43 16", (960, 540), 0),
         caption="Dichtes Sternfeld der Milchstraße im Füchschen – der Nebel selbst ist klein im Seestar-Bild.",
         objects=[obj("M 27", "19 59 36.3", "+22 43 16", "Planetarischer Nebel", 8.0, 5.6, screen=30, mag=7.5,
                      dist="1.360 Lj", alt="NGC 6853", main=True, label=[120, -150])]),
    dict(slug="M_51", stack="night", target=dict(name="M 51", common="Whirlpool-Galaxie", kind="galaxy", sort=1051),
         filter="IRCUT", nights=[("2026-09-14", 96)],
         wcs=make_wcs("13 29 52.7", "+47 11 43", (960, 540), 0),
         caption="Nur eine kurze Nacht, tief im Nordwesten – Kandidat für mehr Belichtung im Frühjahr.",
         objects=[
             obj("M 51", "13 29 52.7", "+47 11 43", "Spiralgalaxie (Sc)", 11, 7, pa=163, mag=8.4, dist="23 Mio. Lj",
                 alt="NGC 5194", main=True, label=[170, 90]),
             obj("NGC 5195", "13 29 59.6", "+47 15 58", "Zwerggalaxie", 5.8, 4.6, pa=79, mag=9.6, dist="23 Mio. Lj",
                 alt="Begleiter", label=[-60, -90]),
         ]),
    dict(slug="NGC_7000", stack="master", target=dict(name="NGC 7000", common="Nordamerikanebel", kind="nebula", sort=7000),
         filter="LP", nights=[("2026-09-28", 330), ("2026-10-05", 288), ("2026-10-06", 301)],
         wcs=make_wcs("20 58 40", "+44 22 00", (960, 540), 0),
         caption="Mit LP-Filter. Ein Seestar-Bildfeld zeigt nur die Region um die Cygnus-Wall – der ganze Nebel bräuchte ein Mosaik.",
         objects=[
             obj("NGC 7000", "20 59 17", "+44 31 44", "Emissionsnebel (H-II-Region)", 120, 100, mag=4.0, dist="2.600 Lj",
                 alt="Nordamerikanebel", main=True, marker=[68, 36], label=[-300, -200]),
             obj("Cygnus-Wall", "20 58 40", "+44 22 00", "Ionisationsfront", 44, 8, screen=-38, dist="2.600 Lj",
                 alt="Teil von NGC 7000", label=[200, 190]),
             obj("IC 5070", "20 50 46", "+44 22 00", "Emissionsnebel", 60, 50, mag=8.0, dist="1.800 Lj",
                 alt="Pelikannebel"),
         ]),
]


def main() -> None:
    render = {}
    for s in SAMPLES:
        wcs = s["wcs"]
        name = f'{s["slug"]}_{"master" if s["stack"] == "master" else s["nights"][-1][0]}_{s["filter"]}_siril'
        # Positionswinkel aus gewuenschtem Bildwinkel ableiten (nur fuer die Beispielbilder)
        for o in s["objects"]:
            if "screen" in o:
                target = o.pop("screen")
                o["pa"] = min(range(0, 360), key=lambda pa: abs(((screen_angle_of_pa(pa, wcs) - target + 90) % 180) - 90))
        meta = {
            "schema": 1, "sample": True, "id": name.lower(),
            "target": s["target"], "stack": s["stack"], "filter": s["filter"],
            "exposure_s": 10, "gain": 80,
            "nights": [{"date": d, "frames": f} for d, f in s["nights"]],
            "image": f"{name}.jpg", "width": W, "height": H,
            "wcs": wcs, "objects": s["objects"], "caption": s["caption"],
            "versions": [{"label": "Seestar-App-Stack", "where": "Handy → Immich"},
                         {"label": "Beschriftet · plot-constellations", "where": "Immich"}],
        }
        d = OUT / s["slug"]
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{name}.meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        pos = {}
        for o in s["objects"]:
            px = world_to_pixel(o["ra"], o["dec"], wcs)
            pos[o["name"]] = [round((px[0] - W / 2) / S, 2), round((px[1] - H / 2) / S, 2)]
        render[s["slug"]] = {"file": str((d / f"{name}.jpg").relative_to(HERE.parent)), "pos": pos}
        print(f'{s["slug"]:9s} ' + "  ".join(f"{k}={v}" for k, v in pos.items()))
    (HERE / "render.json").write_text(json.dumps(render, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
