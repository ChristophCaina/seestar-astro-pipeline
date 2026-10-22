"""Minimale TAN-(gnomonische) WCS ohne externe Abhaengigkeiten.

Konvention in diesem Projekt (siehe gallery/SCHEMA.md):
  * Pixelkoordinaten beziehen sich auf das exportierte JPG:
    Ursprung oben links, x nach rechts, y nach unten, 0-basiert.
  * wcs = {"crval": [ra_deg, dec_deg],
           "crpix": [x, y],                  # Referenzpixel (JPG-Konvention)
           "cd":    [[cd11, cd12], [cd21, cd22]]}   # Grad pro Pixel
    (xi, eta) = CD @ (x - crpix_x, y - crpix_y), xi = Richtung Osten, eta = Norden.

Siril/FITS speichern CRPIX 1-basiert und meist "bottom-up". Die Umrechnung macht
siril-auto beim Schreiben der meta.json (y_jpg = NAXIS2 - y_fits, CD-Zeile 2 negieren).
"""
from __future__ import annotations

import math

D2R = math.pi / 180.0
R2D = 180.0 / math.pi


def _inv2(m):
    (a, b), (c, d) = m
    det = a * d - b * c
    if det == 0:
        raise ValueError("CD-Matrix ist singulaer")
    return [[d / det, -b / det], [-c / det, a / det]]


def world_to_pixel(ra: float, dec: float, wcs: dict) -> tuple[float, float] | None:
    """RA/Dec (Grad) -> Pixel. None, wenn der Punkt hinter der Tangentialebene liegt."""
    ra0, dec0 = (v * D2R for v in wcs["crval"])
    ra_r, dec_r = ra * D2R, dec * D2R
    dra = ra_r - ra0
    cosc = math.sin(dec0) * math.sin(dec_r) + math.cos(dec0) * math.cos(dec_r) * math.cos(dra)
    if cosc <= 0:
        return None
    xi = math.cos(dec_r) * math.sin(dra) / cosc * R2D
    eta = (math.cos(dec0) * math.sin(dec_r) - math.sin(dec0) * math.cos(dec_r) * math.cos(dra)) / cosc * R2D
    inv = _inv2(wcs["cd"])
    dx = inv[0][0] * xi + inv[0][1] * eta
    dy = inv[1][0] * xi + inv[1][1] * eta
    return wcs["crpix"][0] + dx, wcs["crpix"][1] + dy


def pixel_to_world(x: float, y: float, wcs: dict) -> tuple[float, float]:
    """Pixel -> RA/Dec (Grad)."""
    cd = wcs["cd"]
    dx, dy = x - wcs["crpix"][0], y - wcs["crpix"][1]
    xi = (cd[0][0] * dx + cd[0][1] * dy) * D2R
    eta = (cd[1][0] * dx + cd[1][1] * dy) * D2R
    ra0, dec0 = (v * D2R for v in wcs["crval"])
    rho = math.hypot(xi, eta)
    if rho == 0:
        return wcs["crval"][0], wcs["crval"][1]
    c = math.atan(rho)
    dec = math.asin(math.cos(c) * math.sin(dec0) + eta * math.sin(c) * math.cos(dec0) / rho)
    ra = ra0 + math.atan2(xi * math.sin(c), rho * math.cos(dec0) * math.cos(c) - eta * math.sin(dec0) * math.sin(c))
    return (ra * R2D) % 360.0, dec * R2D


def pixscale_arcsec(wcs: dict) -> float:
    (a, b), (c, d) = wcs["cd"]
    return math.sqrt(abs(a * d - b * c)) * 3600.0


def screen_angle_of_pa(pa_deg: float, wcs: dict) -> float:
    """Winkel (Grad, SVG-Konvention: im Uhrzeigersinn ab +x) einer Richtung mit
    Positionswinkel pa (Nord ueber Ost) im Bild."""
    xi, eta = math.sin(pa_deg * D2R), math.cos(pa_deg * D2R)
    inv = _inv2(wcs["cd"])
    dx = inv[0][0] * xi + inv[0][1] * eta
    dy = inv[1][0] * xi + inv[1][1] * eta
    return math.degrees(math.atan2(dy, dx))


def parse_ra(s: str) -> float:
    """'00h42m44.3s' / '00 42 44.3' -> Grad"""
    p = [float(v) for v in s.replace("h", " ").replace("m", " ").replace("s", " ").split()]
    return (p[0] + p[1] / 60 + (p[2] if len(p) > 2 else 0) / 3600) * 15


def parse_dec(s: str) -> float:
    """'+41°16′09″' / '+41 16 09' -> Grad"""
    t = s.replace("°", " ").replace("′", " ").replace("″", " ").replace("'", " ").replace('"', " ")
    t = t.replace("−", "-").strip()
    sign = -1 if t.startswith("-") else 1
    p = [abs(float(v)) for v in t.lstrip("+-").split()]
    return sign * (p[0] + p[1] / 60 + (p[2] if len(p) > 2 else 0) / 3600)


def fmt_ra(ra: float) -> str:
    h = ra / 15
    hh = int(h); mm = int((h - hh) * 60); ss = round(((h - hh) * 60 - mm) * 60)
    if ss == 60: ss = 0; mm += 1
    if mm == 60: mm = 0; hh = (hh + 1) % 24
    return f"{hh:02d}h {mm:02d}m {ss:02d}s"


def fmt_dec(dec: float) -> str:
    sign = "+" if dec >= 0 else "−"
    a = abs(dec); d = int(a); m = int((a - d) * 60); s = round(((a - d) * 60 - m) * 60)
    if s == 60: s = 0; m += 1
    if m == 60: m = 0; d += 1
    return f"{sign}{d:02d}° {m:02d}′ {s:02d}″"
