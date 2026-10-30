"""Gemeinsame Schnittstelle aller Geraete der Astro-Station.

Ein neues Geraet = eine neue Klasse mit diesen Methoden + ein Eintrag in devices.TYPES.
Die Web-App zeigt nur, was snapshot() liefert: Fakten, Aktionen, Faehigkeiten.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field


class Refused(Exception):
    """Befehl abgelehnt – Text geht als Meldung an die Web-App (kein Fehler im Log)."""


class NeedsConfirm(Refused):
    """Befehl braucht eine Bestaetigung in der Station (z. B. laufendes Stacking beenden).
    Die Web-App fragt mit `question` nach und schickt den Befehl mit confirm=true erneut;
    aus Stellarium (ohne Rueckfrage-Moeglichkeit) bleibt es abgelehnt."""

    def __init__(self, message: str, question: str):
        super().__init__(message)
        self.question = question


@dataclass
class Action:
    id: str
    label: str
    hint: str = ""
    confirm: str = ""            # nicht leer = Rueckfrage in der Web-App mit diesem Text
    danger: bool = False
    primary: bool = False
    disabled: str = ""           # nicht leer = ausgegraut, Text erklaert warum

    def as_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class Device:
    id: str
    name: str
    kind: str = "device"
    stellarium_port: int = 0
    enabled: bool = True
    online: bool = False
    last_result: str = ""
    last_result_ok: bool = True
    position: tuple[float, float] | None = None      # RA (h), Dec (°) JNow – fuer Stellarium
    location: dict = field(default_factory=dict)     # {"lat", "lon"} von der Station gesetzt

    # --- Lebenszyklus
    async def start(self):
        pass

    async def close(self):
        pass

    # --- Anzeige
    def snapshot(self) -> dict:
        return {"id": self.id, "name": self.name, "kind": self.kind, "online": self.online,
                "enabled": self.enabled, "stellarium_port": self.stellarium_port}

    def note(self, text: str, ok: bool = True):
        self.last_result, self.last_result_ok = f"{time.strftime('%H:%M')} {text}", ok

    # --- Befehle (Standard: nicht unterstuetzt)
    async def goto(self, target: dict) -> str:
        raise Refused(f"{self.name}: kein GoTo")

    async def goto_jnow(self, ra_h: float, dec: float, name: str = "Stellarium") -> str:
        return await self.goto({"name": name, "ra_jnow": ra_h, "dec_jnow": dec, "kind": "coords"})

    async def move(self, direction: str, speed: int, on: bool) -> None:
        raise Refused(f"{self.name}: lässt sich nicht von Hand bewegen")

    async def stop(self) -> str:
        return f"{self.name}: nichts zu stoppen"

    async def action(self, action_id: str, params: dict) -> str:
        raise Refused(f"{self.name}: unbekannte Aktion {action_id}")
