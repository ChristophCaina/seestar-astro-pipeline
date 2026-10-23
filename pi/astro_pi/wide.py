"""Weitwinkel-Rechner: maximale Belichtung ohne Nachführung (NPF/500er-Regel), Bildfeld, Bildmaßstab, Vorlagen."""
from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class Sensor:
    name: str = "Canon EOS 6D"
    width_mm: float = 35.8
    height_mm: float = 23.9
    width_px: int = 5472
    height_px: int = 3648

    @property
    def pixel_um(self) -> float:
        return self.width_mm / self.width_px * 1000


@dataclass
class Lens:
    name: str
    focal: float                 # mm
    aperture: float              # kleinste Blendenzahl (Offenblende)


DEFAULT_LENSES = [Lens("14 mm f/2.8", 14, 2.8), Lens("24 mm f/1.4", 24, 1.4), Lens("35 mm f/2", 35, 2.0),
                  Lens("50 mm f/1.8", 50, 1.8), Lens("135 mm f/2", 135, 2.0)]


@dataclass
class Preset:
    name: str
    exposure: float | None       # None = aus NPF-Regel (ohne Nachführung)
    count: int
    iso: str
    tracked: bool
    note: str = ""


DEFAULT_PRESETS = [
    Preset("Milchstraße vom Stativ", None, 40, "3200", False, "Belichtung nach NPF-Regel, Vordergrund-Stack nutzen"),
    Preset("Milchstraße nachgeführt", 120, 30, "1600", True, "iEXOS-100 als Nachführung, Vordergrund separat"),
    Preset("Sternbild / Sternfeld", 60, 40, "1600", True, ""),
    Preset("Nordamerika-/Cirrusregion (H-alpha)", 180, 30, "1600", True, "astromodifizierte 6D: rot-empfindlich"),
]


@dataclass
class Calc:
    focal: float
    aperture: float
    dec: float
    pixel_um: float
    npf_s: float                 # streng (Michaud), deklinationsabhängig
    npf_simple_s: float          # (35·N + 30·p)/f
    rule500_s: float
    pixscale: float              # "/px
    fov: tuple[float, float]     # Grad
    diag: float
    tips: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {k: (round(v, 2) if isinstance(v, float) else v) for k, v in self.__dict__.items()} | {
            "fov": [round(self.fov[0], 1), round(self.fov[1], 1)]}


def calc(focal: float, aperture: float, dec: float = 0.0, sensor: Sensor | None = None, crop: float = 1.0) -> Calc:
    """NPF nach F. Michaud: t = (16.856·N + 0.0997·f + 13.713·p) / (f · cos δ)."""
    if focal <= 0 or aperture <= 0:
        raise ValueError("Brennweite und Blende müssen > 0 sein")
    s = sensor or Sensor()
    p = s.pixel_um
    cosd = max(math.cos(math.radians(min(abs(dec), 89.0))), 0.02)
    npf = (16.856 * aperture + 0.0997 * focal + 13.713 * p) / (focal * cosd)
    simple = (35 * aperture + 30 * p) / focal
    r500 = 500 / (focal * crop)
    scale = 206.265 * p / focal
    fw = math.degrees(2 * math.atan(s.width_mm / (2 * focal)))
    fh = math.degrees(2 * math.atan(s.height_mm / (2 * focal)))
    diag = math.degrees(2 * math.atan(math.hypot(s.width_mm, s.height_mm) / (2 * focal)))
    tips = []
    if npf < 5:
        tips.append("Sehr kurze Belichtung – lieber nachführen oder mehr Frames.")
    if abs(dec) > 70:
        tips.append("Nahe am Himmelspol: deutlich längere Belichtung möglich.")
    if fw > 60:
        tips.append("Sehr großes Feld: Indexdateien 4116–4119 für das Plate-Solving nötig.")
    return Calc(focal, aperture, dec, p, npf, simple, r500, scale, (fw, fh), diag, tips)
