"""Geraete-Typen der Astro-Station. Neuer Typ: Klasse schreiben (siehe base.Device) und hier eintragen."""
from .base import Action, Device, NeedsConfirm, Refused
from .indi_mount import IndiMount
from .seestar import Seestar

TYPES = {"seestar": Seestar, "indi-mount": IndiMount}

__all__ = ["Action", "Device", "NeedsConfirm", "Refused", "TYPES", "make"]


def make(spec: dict) -> Device:
    spec = dict(spec)
    kind = spec.pop("type", "")
    if kind not in TYPES:
        raise ValueError(f"Gerät {spec.get('id')}: unbekannter Typ {kind!r} (bekannt: {', '.join(TYPES)})")
    return TYPES[kind](**spec)
