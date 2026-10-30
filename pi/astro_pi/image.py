"""Vorschau aus FITS-Aufnahmen: Lesen, Debayern (Superpixel), Strecken, JPEG, HFR-Schaetzung."""
from __future__ import annotations

import io
import math

import numpy as np
from PIL import Image

BITPIX = {8: ">u1", 16: ">i2", 32: ">i4", -32: ">f4", -64: ">f8"}


def read_fits(data: bytes) -> tuple[dict, np.ndarray]:
    """Minimaler FITS-Leser fuer das primaere HDU (2D oder 3D mit 3 Ebenen)."""
    hdr, pos = {}, 0
    while True:
        block = data[pos:pos + 2880]
        if len(block) < 2880:
            raise ValueError("FITS-Header unvollstaendig")
        pos += 2880
        end = False
        for i in range(0, 2880, 80):
            card = block[i:i + 80].decode("ascii", "replace")
            key = card[:8].strip()
            if key == "END":
                end = True
                break
            if card[8:10] == "= ":
                v = card[10:].split("/")[0].strip()
                if v.startswith("'"):
                    v = v.strip("'").strip()
                else:
                    try:
                        v = float(v) if any(c in v for c in ".EeDd") else int(v)
                    except ValueError:
                        v = {"T": True, "F": False}.get(v, v)
                hdr[key] = v
        if end:
            break
    naxis = int(hdr.get("NAXIS", 0))
    shape = [int(hdr[f"NAXIS{i}"]) for i in range(naxis, 0, -1)]
    dt = np.dtype(BITPIX[int(hdr["BITPIX"])])
    n = int(np.prod(shape))
    arr = np.frombuffer(data, dtype=dt, count=n, offset=pos).reshape(shape).astype(np.float32)
    arr = arr * float(hdr.get("BSCALE", 1)) + float(hdr.get("BZERO", 0))
    return hdr, arr


def debayer_superpixel(a: np.ndarray, pattern: str) -> np.ndarray:
    """2x2-Superpixel -> RGB in halber Aufloesung (fuer die Vorschau genau richtig)."""
    h, w = (a.shape[0] // 2) * 2, (a.shape[1] // 2) * 2
    a = a[:h, :w]
    q = {pattern[0]: a[0::2, 0::2], pattern[1]: a[0::2, 1::2], pattern[2]: a[1::2, 0::2], pattern[3]: a[1::2, 1::2]}
    if pattern.count("G") == 2:
        g = (a[0::2, 1::2] + a[1::2, 0::2]) / 2 if pattern in ("RGGB", "BGGR") else (a[0::2, 0::2] + a[1::2, 1::2]) / 2
    else:
        g = q["G"]
    return np.dstack([q["R"], g, q["B"]])


def to_rgb(hdr: dict, arr: np.ndarray) -> np.ndarray:
    if arr.ndim == 3 and arr.shape[0] == 3:
        return np.moveaxis(arr, 0, -1)
    pattern = str(hdr.get("BAYERPAT", "")).upper().strip()
    if arr.ndim == 2 and pattern in ("RGGB", "BGGR", "GRBG", "GBRG"):
        return debayer_superpixel(arr, pattern)
    return np.dstack([arr] * 3) if arr.ndim == 2 else arr


def stretch(rgb: np.ndarray) -> np.ndarray:
    """Automatische Streckung (Median/MAD, wie STF in PixInsight/Siril vereinfacht)."""
    out = np.empty_like(rgb, dtype=np.float32)
    lum = rgb.mean(axis=2)
    med = float(np.median(lum))
    mad = float(np.median(np.abs(lum - med))) or 1.0
    lo = med - 2.8 * mad * 1.4826
    hi = float(np.percentile(lum, 99.95))
    if hi <= lo:
        hi = lo + 1
    for c in range(3):
        x = np.clip((rgb[..., c] - lo) / (hi - lo), 0, 1)
        m = 0.25                                       # Ziel-Hintergrund
        b = np.clip((med - lo) / (hi - lo), 1e-4, 0.999)
        k = (m - 1) * b / ((2 * m - 1) * b - m)        # Midtones-Transfer, bildet b auf m ab
        out[..., c] = ((k - 1) * x) / ((2 * k - 1) * x - k)
    return np.clip(out * 255, 0, 255).astype(np.uint8)


def jpeg(rgb8: np.ndarray, max_w: int = 1600, quality: int = 85) -> bytes:
    im = Image.fromarray(rgb8)
    if im.width > max_w:
        im = im.resize((max_w, round(im.height * max_w / im.width)), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def hfr(lum: np.ndarray, max_stars: int = 40) -> tuple[float | None, int]:
    """Half-Flux-Radius-Mittel ueber helle, freistehende Sterne. Grob, aber reicht zum Fokussieren."""
    a = lum.astype(np.float32)
    if a.shape[0] > 1200:                               # fuer Geschwindigkeit verkleinern
        f = math.ceil(a.shape[0] / 1200)
        a = a[: a.shape[0] // f * f, : a.shape[1] // f * f].reshape(a.shape[0] // f, f, a.shape[1] // f, f).mean((1, 3))
    else:
        f = 1
    med = float(np.median(a))
    sigma = float(np.median(np.abs(a - med))) * 1.4826 or 1.0
    thr = med + 8 * sigma
    r = 7
    pad = np.pad(a, r, mode="edge")
    # lokale Maxima ueber Schwelle
    cand = np.argwhere(a > thr)
    if len(cand) == 0:
        return None, 0
    vals = a[cand[:, 0], cand[:, 1]]
    order = np.argsort(-vals)
    taken, radii = [], []
    for idx in order:
        y, x = cand[idx]
        if any(abs(y - ty) <= 2 * r and abs(x - tx) <= 2 * r for ty, tx in taken):
            continue
        win = pad[y:y + 2 * r + 1, x:x + 2 * r + 1] - med
        if win[r, r] < win.max():                       # kein Maximum
            continue
        win = np.clip(win, 0, None)
        total = win.sum()
        if total <= 0:
            continue
        yy, xx = np.mgrid[-r:r + 1, -r:r + 1]
        dist = np.hypot(yy, xx)
        o = np.argsort(dist, axis=None)
        csum = np.cumsum(win.ravel()[o])
        half = dist.ravel()[o][np.searchsorted(csum, total / 2)]
        taken.append((y, x))
        radii.append(float(half) * f)
        if len(taken) >= max_stars:
            break
    if not radii:
        return None, 0
    return float(np.median(radii)), len(radii)


def preview(fits_bytes: bytes, max_w: int = 1600) -> dict:
    hdr, arr = read_fits(fits_bytes)
    rgb = to_rgb(hdr, arr)
    h, n = hfr(rgb.mean(axis=2))
    return {"jpeg": jpeg(stretch(rgb), max_w), "hfr": h, "stars": n,
            "width": int(arr.shape[-1]), "height": int(arr.shape[-2]), "header": hdr}


def _card(key: str, value) -> bytes:
    if isinstance(value, bool):
        v = f"{'T' if value else 'F':>20}"
    elif isinstance(value, (int, float)):
        v = f"{value:>20}" if isinstance(value, int) else f"{value:>20.6f}"
    else:
        s = str(value).replace("'", "''")[:68]
        v = f"'{s:<8}'"
    return f"{key[:8]:<8}= {v}".ljust(80)[:80].encode("ascii", "replace")


def add_cards(data: bytes, cards: dict) -> bytes:
    """Schluesselwoerter in den primaeren FITS-Header schreiben (ersetzen oder vor END anfuegen).
    Daten bleiben unveraendert; der Header wird auf volle 2880-Byte-Bloecke aufgefuellt."""
    if not cards or not data.startswith(b"SIMPLE"):
        return data
    out, pos = [], 0
    while pos + 80 <= len(data):
        card = data[pos:pos + 80]
        pos += 80
        if card[:8].rstrip() == b"END":
            break
        out.append(card)
    else:
        return data
    data_start = ((pos + 2879) // 2880) * 2880
    keys = {k.upper()[:8]: v for k, v in cards.items() if v is not None and v != ""}
    for i, card in enumerate(out):
        k = card[:8].decode("ascii", "replace").rstrip()
        if k in keys:
            out[i] = _card(k, keys.pop(k))
    out += [_card(k, v) for k, v in keys.items()]
    head = b"".join(out) + b"END".ljust(80)
    head += b" " * (-len(head) % 2880)
    return head + data[data_start:]
