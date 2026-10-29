"""Stellarium-Bruecke: ein TCP-Port pro Geraet im Telescope-Control-Protokoll von Stellarium.

In Stellarium: Teleskopsteuerung → Teleskop hinzufügen → "Externe Software oder ein entfernter Computer",
Host localhost, Port aus der Station (Seestar 10001, iEXOS 10002), Koordinatensystem JNow.
Stellarium zeigt dann das Fadenkreuz an der echten Position und Strg+1 faehrt das ausgewaehlte Objekt an.

Protokoll (little endian):
  Stellarium -> Station  20 Byte: Laenge u16, Typ u16 = 0, Zeit i64 (µs), RA u32 (2^32 = 24 h), Dec i32 (2^30 = 90°)
  Station -> Stellarium  24 Byte: wie oben + Status i32 (0 = ok)
"""
from __future__ import annotations

import asyncio
import logging
import struct
import time

log = logging.getLogger("astro_station.stellarium")

GOTO = struct.Struct("<HHqIi")
POS = struct.Struct("<HHqIiI")


def encode_position(ra_h: float, dec: float) -> bytes:
    ra = int(round((ra_h % 24) / 24 * 2 ** 32)) % 2 ** 32
    de = int(round(max(-90.0, min(90.0, dec)) / 90 * 2 ** 30))
    return POS.pack(POS.size, 0, int(time.time() * 1e6), ra, de, 0)


def decode_goto(msg: bytes) -> tuple[float, float]:
    _, typ, _, ra, de = GOTO.unpack(msg[:GOTO.size])
    if typ != 0:
        raise ValueError(f"unbekannter Nachrichtentyp {typ}")
    return ra / 2 ** 32 * 24, de / 2 ** 30 * 90


class Bridge:
    """Ein Port fuer ein Geraet. on_goto(ra_h, dec) bekommt JNow-Koordinaten."""

    def __init__(self, device, port: int, on_goto, host: str = "127.0.0.1"):
        self.device, self.port, self.host, self.on_goto = device, port, host, on_goto
        self.server: asyncio.base_events.Server | None = None
        self.clients = 0

    async def start(self):
        self.server = await asyncio.start_server(self._client, self.host, self.port)
        log.info("Stellarium-Port %s für %s", self.port, self.device.name)

    async def close(self):
        if self.server:
            self.server.close()

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.clients += 1
        sender = asyncio.create_task(self._send_loop(writer))
        try:
            while True:
                head = await reader.readexactly(2)
                (length,) = struct.unpack("<H", head)
                body = await reader.readexactly(max(0, length - 2))
                if length == GOTO.size:
                    try:
                        ra, dec = decode_goto(head + body)
                    except ValueError as e:
                        log.debug("Stellarium: %s", e)
                        continue
                    await self.on_goto(self.device, ra, dec)
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            self.clients -= 1
            sender.cancel()
            writer.close()

    async def _send_loop(self, writer: asyncio.StreamWriter):
        try:
            while True:
                pos = self.device.position
                if pos and self.device.enabled:
                    writer.write(encode_position(*pos))
                    await writer.drain()
                await asyncio.sleep(1)
        except (ConnectionError, asyncio.CancelledError):
            pass
