"""Kleine Astronomie ohne Zusatzpakete: Sonne, Mond, Praezession, Hoehe/Azimut, Nacht, Sichtbarkeit.

Genauigkeit reicht fuer Planung und Anzeige (Sonne ~0,01°, Mond ~0,3°, Zeiten auf wenige Minuten).
Formeln: J. Meeus, "Astronomical Algorithms" (Praezession Kap. 21) und die Kurzformeln des
Astronomical Almanac fuer Sonne und Mond. Rektaszension immer in Stunden, Deklination in Grad.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

D2R = math.pi / 180


def jd(t: datetime) -> float:
    return t.timestamp() / 86400 + 2440587.5


def gmst_deg(t: datetime) -> float:
    return (280.46061837 + 360.98564736629 * (jd(t) - 2451545.0)) % 360


def altaz(ra_h: float, dec: float, lat: float, lon: float, t: datetime) -> tuple[float, float]:
    """Hoehe und Azimut (Nord = 0°, Ost = 90°) in Grad."""
    ha = (gmst_deg(t) + lon - ra_h * 15) * D2R
    la, d = lat * D2R, dec * D2R
    alt = math.asin(max(-1.0, min(1.0, math.sin(la) * math.sin(d) + math.cos(la) * math.cos(d) * math.cos(ha))))
    az = math.atan2(-math.sin(ha) * math.cos(d), math.sin(d) * math.cos(la) - math.cos(d) * math.sin(la) * math.cos(ha))
    return alt / D2R, (az / D2R) % 360


def _ecl_to_eq(lam: float, beta: float, t: datetime) -> tuple[float, float]:
    eps = (23.439291 - 0.0130042 * (jd(t) - 2451545.0) / 36525) * D2R
    lam, beta = lam * D2R, beta * D2R
    ra = math.atan2(math.sin(lam) * math.cos(eps) - math.tan(beta) * math.sin(eps), math.cos(lam))
    dec = math.asin(math.sin(beta) * math.cos(eps) + math.cos(beta) * math.sin(eps) * math.sin(lam))
    return (ra / D2R / 15) % 24, dec / D2R


def sun_lon(t: datetime) -> float:
    n = jd(t) - 2451545.0
    g = (357.528 + 0.9856003 * n) * D2R
    return (280.460 + 0.9856474 * n + 1.915 * math.sin(g) + 0.020 * math.sin(2 * g)) % 360


def sun_radec(t: datetime) -> tuple[float, float]:
    return _ecl_to_eq(sun_lon(t), 0.0, t)


def _moon_ecl(t: datetime) -> tuple[float, float, float]:
    T = (jd(t) - 2451545.0) / 36525

    def s(a, b):
        return math.sin((a + b * T) * D2R)

    def c(a, b):
        return math.cos((a + b * T) * D2R)
    lam = (218.32 + 481267.881 * T + 6.29 * s(135.0, 477198.87) - 1.27 * s(259.3, -413335.36)
           + 0.66 * s(235.7, 890534.22) + 0.21 * s(269.9, 954397.74) - 0.19 * s(357.5, 35999.05)
           - 0.11 * s(186.5, 966404.03)) % 360
    beta = (5.13 * s(93.3, 483202.02) + 0.28 * s(228.2, 960400.89) - 0.28 * s(318.3, 6003.15)
            - 0.17 * s(217.6, -407332.21))
    par = (0.9508 + 0.0518 * c(135.0, 477198.87) + 0.0095 * c(259.3, -413335.36)
           + 0.0078 * c(235.7, 890534.22) + 0.0028 * c(269.9, 954397.74))
    return lam, beta, par


def moon_radec(t: datetime) -> tuple[float, float]:
    lam, beta, _ = _moon_ecl(t)
    return _ecl_to_eq(lam, beta, t)


def moon_alt(lat: float, lon: float, t: datetime) -> float:
    """Topozentrische Hoehe des Mondmittelpunkts (Parallaxe ~1° beruecksichtigt)."""
    lam, beta, par = _moon_ecl(t)
    ra, dec = _ecl_to_eq(lam, beta, t)
    alt, _ = altaz(ra, dec, lat, lon, t)
    return alt - par * math.cos(alt * D2R)


def moon_phase(t: datetime) -> tuple[float, bool]:
    """(beleuchteter Anteil 0..1, zunehmend)"""
    lam, beta, _ = _moon_ecl(t)
    elong = (lam - sun_lon(t)) % 360
    psi = math.acos(math.cos(beta * D2R) * math.cos(elong * D2R))
    return (1 - math.cos(psi)) / 2, elong < 180


def _rz(a):
    c, s = math.cos(a), math.sin(a)
    return [[c, -s, 0], [s, c, 0], [0, 0, 1]]


def _ry(a):
    c, s = math.cos(a), math.sin(a)
    return [[c, 0, -s], [0, 1, 0], [s, 0, c]]


def _mm(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def _prec_matrix(t: datetime):
    T = (jd(t) - 2451545.0) / 36525
    asec = D2R / 3600
    zeta = (2306.2181 * T + 0.30188 * T * T + 0.017998 * T ** 3) * asec
    z = (2306.2181 * T + 1.09468 * T * T + 0.018203 * T ** 3) * asec
    theta = (2004.3109 * T - 0.42665 * T * T - 0.041833 * T ** 3) * asec
    return _mm(_rz(z), _mm(_ry(theta), _rz(zeta)))


def _apply(m, ra_h, dec):
    a, d = ra_h * 15 * D2R, dec * D2R
    v = [math.cos(d) * math.cos(a), math.cos(d) * math.sin(a), math.sin(d)]
    w = [sum(m[i][k] * v[k] for k in range(3)) for i in range(3)]
    return (math.atan2(w[1], w[0]) / D2R / 15) % 24, math.asin(max(-1.0, min(1.0, w[2]))) / D2R


def j2000_to_date(ra_h: float, dec: float, t: datetime) -> tuple[float, float]:
    """J2000 (Katalog) -> Aequinoktium des Datums (JNow, Montierung/Stellarium)."""
    return _apply(_prec_matrix(t), ra_h, dec)


def date_to_j2000(ra_h: float, dec: float, t: datetime) -> tuple[float, float]:
    m = _prec_matrix(t)
    return _apply([[m[j][i] for j in range(3)] for i in range(3)], ra_h, dec)


# ------------------------------------------------------------------ Nacht und Sichtbarkeit
STEP = timedelta(minutes=10)


def night_start(now: datetime) -> datetime:
    """Beginn der 'heutigen' Nacht: lokaler Mittag vor jetzt."""
    loc = now.astimezone()
    noon = loc.replace(hour=12, minute=0, second=0, microsecond=0)
    return noon if loc >= noon else noon - timedelta(days=1)


def _crossings(f, start: datetime, end: datetime, level: float) -> list[tuple[datetime, bool]]:
    """Zeitpunkte, an denen f(t) den Wert level kreuzt: (Zeit, steigend)."""
    out = []
    t, v = start, f(start) - level
    while t < end:
        t2 = min(t + STEP, end)
        v2 = f(t2) - level
        if (v < 0) != (v2 < 0):
            a, b, va = t, t2, v
            for _ in range(12):                      # Bisektion auf < 15 s
                m = a + (b - a) / 2
                vm = f(m) - level
                if (va < 0) != (vm < 0):
                    b = m
                else:
                    a, va = m, vm
            out.append((a + (b - a) / 2, v2 >= 0))
        t, v = t2, v2
    return out


@dataclass
class Night:
    start: datetime                    # lokaler Mittag
    end: datetime                      # naechster Mittag
    astro: tuple[datetime | None, datetime | None]       # Sonne unter -18°
    nautical: tuple[datetime | None, datetime | None]    # Sonne unter -12° (Fenster fuer Ziele)
    moon_rise: datetime | None
    moon_set: datetime | None
    moon_illum: float
    moon_waxing: bool


def _dark_span(cross, start, end, sun_below_at_start):
    dusk = next((t for t, up in cross if not up), None)
    dawn = next((t for t, up in cross if up and (dusk is None or t > dusk)), None)
    if dusk is None and sun_below_at_start:
        dusk = start
    if dusk is not None and dawn is None:
        dawn = end
    return dusk, dawn


def night(lat: float, lon: float, now: datetime | None = None) -> Night:
    now = now or datetime.now(timezone.utc)
    start = night_start(now)
    end = start + timedelta(days=1)

    def sun(t):
        return altaz(*sun_radec(t), lat, lon, t)[0]
    a = _dark_span(_crossings(sun, start, end, -18), start, end, sun(start) < -18)
    n = _dark_span(_crossings(sun, start, end, -12), start, end, sun(start) < -12)
    mc = _crossings(lambda t: moon_alt(lat, lon, t), start, end, -0.6)
    rise = next((t for t, up in mc if up), None)
    mset = next((t for t, up in mc if not up), None)
    mid = (n[0] + (n[1] - n[0]) / 2) if n[0] and n[1] else start + timedelta(hours=12)
    illum, waxing = moon_phase(mid)
    return Night(start, end, a, n, rise, mset, illum, waxing)


def visibility(ra_h: float, dec: float, lat: float, lon: float, nt: Night, now: datetime,
               min_alt: float = 30.0) -> dict | None:
    """Laengstes Fenster ueber min_alt in der (nautischen) Nacht, Kulmination, Hoehe jetzt."""
    lo, hi = nt.nautical
    if lo is None or hi is None:
        return None
    best, cur, peak = None, None, (-90.0, lo)
    t = lo
    while t <= hi:
        alt = altaz(ra_h, dec, lat, lon, t)[0]
        if alt > peak[0]:
            peak = (alt, t)
        if alt >= min_alt:
            cur = (cur[0], t) if cur else (t, t)
        elif cur:
            best = cur if not best or cur[1] - cur[0] > best[1] - best[0] else best
            cur = None
        t += STEP
    if cur:
        best = cur if not best or cur[1] - cur[0] > best[1] - best[0] else best
    alt_now, az_now = altaz(ra_h, dec, lat, lon, now)
    return {"from": best[0] if best else None, "to": best[1] if best else None,
            "hours": round((best[1] - best[0]).total_seconds() / 3600, 1) if best else 0.0,
            "peak_alt": round(peak[0], 1), "peak_time": peak[1],
            "alt": round(alt_now, 1), "az": round(az_now, 1)}
