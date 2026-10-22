"""FITS-Header lesen und WCS in die JPG-Konvention der Galerie umrechnen.

Ohne externe Abhaengigkeiten: es wird nur der Primaer-Header gelesen
(2880-Byte-Bloecke aus 80-Zeichen-Karten bis END).
"""
from __future__ import annotations

import math
from pathlib import Path


def read_header(path: str | Path) -> dict:
    hdr: dict = {}
    with open(path, "rb") as f:
        while True:
            block = f.read(2880)
            if len(block) < 2880:
                raise ValueError(f"{path}: kein gueltiger FITS-Header")
            for i in range(0, 2880, 80):
                card = block[i:i + 80].decode("ascii", "replace")
                key = card[:8].strip()
                if key == "END":
                    return hdr
                if card[8:10] != "= " or not key:
                    continue
                hdr[key] = _value(card[10:])


def _value(raw: str):
    raw = raw.strip()
    if raw.startswith("'"):
        # String bis zum schliessenden Hochkomma ('' = escaptes Hochkomma)
        out, i = [], 1
        while i < len(raw):
            if raw[i] == "'":
                if i + 1 < len(raw) and raw[i + 1] == "'":
                    out.append("'"); i += 2; continue
                break
            out.append(raw[i]); i += 1
        return "".join(out).rstrip()
    val = raw.split("/", 1)[0].strip()
    if val in ("T", "F"):
        return val == "T"
    try:
        return int(val)
    except ValueError:
        pass
    try:
        return float(val.replace("D", "E"))
    except ValueError:
        return val


def wcs_from_header(h: dict, flip_y: bool = True) -> dict | None:
    """TAN-WCS aus dem Header in JPG-Konvention (0-basiert, y nach unten).

    flip_y=True: das FITS ist bottom-up gespeichert (FITS-Standard, Siril-Default)
    und das exportierte JPG zeigt die oberste Bildzeile zuerst.
    """
    if "CRVAL1" not in h or "CRPIX1" not in h:
        return None
    ctype = str(h.get("CTYPE1", "RA---TAN"))
    if "TAN" not in ctype:
        return None
    if "CD1_1" in h:
        cd = [[h.get("CD1_1", 0.0), h.get("CD1_2", 0.0)], [h.get("CD2_1", 0.0), h.get("CD2_2", 0.0)]]
    elif "PC1_1" in h:
        c1, c2 = h.get("CDELT1", 1.0), h.get("CDELT2", 1.0)
        cd = [[c1 * h.get("PC1_1", 1.0), c1 * h.get("PC1_2", 0.0)],
              [c2 * h.get("PC2_1", 0.0), c2 * h.get("PC2_2", 1.0)]]
    elif "CDELT1" in h:
        c1, c2 = h["CDELT1"], h.get("CDELT2", h["CDELT1"])
        rot = math.radians(h.get("CROTA2", 0.0))
        cd = [[c1 * math.cos(rot), -c2 * math.sin(rot)], [c1 * math.sin(rot), c2 * math.cos(rot)]]
    else:
        return None
    x0 = h["CRPIX1"] - 1.0                       # 1-basiert -> 0-basiert
    y0 = h["CRPIX2"] - 1.0
    if flip_y:
        ny = h.get("NAXIS2")
        if not ny:
            raise ValueError("NAXIS2 fehlt fuer flip_y")
        y0 = (ny - 1) - y0
        cd = [[cd[0][0], -cd[0][1]], [cd[1][0], -cd[1][1]]]   # dy -> -dy
    return {"crval": [float(h["CRVAL1"]), float(h["CRVAL2"])], "crpix": [x0, y0], "cd": cd}


def write_minimal_fits(path: str | Path, cards: dict, nx: int = 8, ny: int = 8) -> None:
    """Nur fuer Tests: minimale FITS-Datei mit leerem 8-Bit-Bild."""
    lines = [("SIMPLE", True), ("BITPIX", 8), ("NAXIS", 2), ("NAXIS1", nx), ("NAXIS2", ny)] + list(cards.items())
    out = []
    for k, v in lines:
        if isinstance(v, bool):
            s = "T" if v else "F"; card = f"{k:<8}= {s:>20}"
        elif isinstance(v, (int, float)):
            card = f"{k:<8}= {v!r:>20}"
        else:
            card = f"{k:<8}= '{v}'"
        out.append(card.ljust(80)[:80])
    out.append("END".ljust(80))
    data = "".join(out).encode("ascii")
    data += b" " * (-len(data) % 2880)
    body = bytes(nx * ny)
    body += b"\0" * (-len(body) % 2880)
    Path(path).write_bytes(data + body)
