"""Schlanker asyncio-Client fuer das INDI-Protokoll (XML ueber TCP, Port 7624).

Haelt einen Spiegel aller Properties ({device: {name: Property}}), meldet Aenderungen
an Callbacks und kann Properties setzen. BLOBs (Bilder) werden dekodiert weitergereicht.
Nur Standardbibliothek.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Awaitable, Callable
from xml.sax.saxutils import escape, quoteattr

log = logging.getLogger("astro_pi.indi")

# DRIVER_INTERFACE-Bits (libindi basedevice.h)
IF_TELESCOPE = 1 << 0
IF_CCD = 1 << 1
IF_FOCUSER = 1 << 3


@dataclass
class Property:
    device: str
    name: str
    kind: str                      # Number | Switch | Text | Light | BLOB
    label: str = ""
    group: str = ""
    state: str = "Idle"            # Idle | Ok | Busy | Alert
    perm: str = "ro"
    rule: str = ""
    values: dict = field(default_factory=dict)
    meta: dict = field(default_factory=dict)      # Element -> {label, min, max, format}
    updated: float = 0.0

    def as_dict(self) -> dict:
        return {"kind": self.kind, "label": self.label, "group": self.group, "state": self.state,
                "perm": self.perm, "rule": self.rule, "values": self.values}

    def on(self) -> list[str]:
        """Eingeschaltete Elemente eines Switch-Vektors."""
        return [k for k, v in self.values.items() if v == "On"]


@dataclass
class Blob:
    device: str
    name: str
    element: str
    fmt: str                       # z. B. ".fits", ".cr2", ".stream_jpg"
    data: bytes


def _parse_value(kind: str, text: str):
    t = (text or "").strip()
    if kind == "Number":
        try:
            return float(t)
        except ValueError:
            return _sexagesimal(t)
    return t


def _sexagesimal(t: str) -> float:
    """'12:30:00' -> 12.5 (INDI erlaubt sexagesimale Zahlen)."""
    parts = [p for p in t.replace(" ", ":").split(":") if p]
    if not parts:
        return 0.0
    sign = -1 if parts[0].startswith("-") else 1
    v = 0.0
    for i, p in enumerate(parts[:3]):
        v += abs(float(p)) / (60 ** i)
    return sign * v


class IndiClient:
    def __init__(self, host: str = "127.0.0.1", port: int = 7624):
        self.host, self.port = host, port
        self.props: dict[str, dict[str, Property]] = {}
        self.messages: list[tuple[float, str, str]] = []
        self._writer: asyncio.StreamWriter | None = None
        self._prop_cbs: list[Callable[[Property], Awaitable[None] | None]] = []
        self._blob_cbs: list[Callable[[Blob], Awaitable[None] | None]] = []
        self._waiters: list[tuple[Callable[[Property], bool], asyncio.Future]] = []
        self.connected = asyncio.Event()

    # ------------------------------------------------------------ Callbacks
    def on_property(self, cb):
        self._prop_cbs.append(cb)
        return cb

    def on_blob(self, cb):
        self._blob_cbs.append(cb)
        return cb

    async def _emit(self, cbs, arg):
        for cb in cbs:
            try:
                r = cb(arg)
                if asyncio.iscoroutine(r):
                    await r
            except Exception:                         # Callback-Fehler nicht durchreichen
                log.exception("Callback-Fehler")

    # ------------------------------------------------------------ Verbindung
    async def run(self, retry: float = 5.0):
        """Verbindet und liest dauerhaft; bei Abbruch neuer Versuch nach `retry` s."""
        while True:
            try:
                reader, self._writer = await asyncio.open_connection(self.host, self.port, limit=2**26)
                log.info("INDI verbunden: %s:%s", self.host, self.port)
                await self._send("<getProperties version='1.7'/>")
                self.connected.set()
                await self._read_loop(reader)
            except (OSError, asyncio.IncompleteReadError) as e:
                log.warning("INDI nicht erreichbar (%s), neuer Versuch in %ss", e, retry)
            finally:
                self.connected.clear()
                if self._writer:
                    self._writer.close()
                self._writer = None
            await asyncio.sleep(retry)

    async def _read_loop(self, reader: asyncio.StreamReader):
        parser = ET.XMLPullParser(events=("start", "end"))
        parser.feed(b"<indi>")
        depth = 0
        while True:
            chunk = await reader.read(1 << 20)
            if not chunk:
                raise asyncio.IncompleteReadError(b"", None)
            parser.feed(chunk)
            for ev, el in parser.read_events():
                if ev == "start":
                    depth += 1
                    continue
                depth -= 1
                if depth == 1:                       # vollstaendige Top-Level-Nachricht (unter <indi>)
                    await self._handle(el)
                    el.clear()

    async def _send(self, xml: str):
        if not self._writer:
            raise ConnectionError("INDI nicht verbunden")
        self._writer.write(xml.encode())
        await self._writer.drain()

    # ------------------------------------------------------------ Eingang
    async def _handle(self, el: ET.Element):
        tag = el.tag
        dev, name = el.get("device", ""), el.get("name", "")
        if el.get("message"):
            self.messages.append((time.time(), dev, el.get("message")))
            del self.messages[:-50]
        if tag.startswith("def") and tag.endswith("Vector"):
            kind = tag[3:-6]
            p = Property(dev, name, kind, el.get("label", ""), el.get("group", ""), el.get("state", "Idle"),
                         el.get("perm", "ro"), el.get("rule", ""))
            for c in el:
                p.values[c.get("name")] = None if kind == "BLOB" else _parse_value(kind, c.text)
                p.meta[c.get("name")] = {k: c.get(k) for k in ("label", "min", "max", "format") if c.get(k)}
            p.updated = time.time()
            self.props.setdefault(dev, {})[name] = p
            await self._changed(p)
        elif tag.startswith("set") and tag.endswith("Vector"):
            kind = tag[3:-6]
            p = self.props.get(dev, {}).get(name)
            if p is None:
                return
            if el.get("state"):
                p.state = el.get("state")
            if kind == "BLOB":
                for c in el:
                    if c.text and c.text.strip():
                        data = base64.b64decode(c.text)
                        await self._emit(self._blob_cbs, Blob(dev, name, c.get("name"), c.get("format", ""), data))
            else:
                for c in el:
                    p.values[c.get("name")] = _parse_value(kind, c.text)
            p.updated = time.time()
            await self._changed(p)
        elif tag == "delProperty":
            if name:
                self.props.get(dev, {}).pop(name, None)
            else:
                self.props.pop(dev, None)

    async def _changed(self, p: Property):
        for pred, fut in list(self._waiters):
            if not fut.done() and pred(p):
                fut.set_result(p)
        await self._emit(self._prop_cbs, p)

    # ------------------------------------------------------------ Abfragen
    def get(self, device: str, name: str) -> Property | None:
        return self.props.get(device, {}).get(name)

    def value(self, device: str, name: str, element: str, default=None):
        p = self.get(device, name)
        return p.values.get(element, default) if p else default

    def devices_with(self, interface_bit: int) -> list[str]:
        out = []
        for dev, props in self.props.items():
            info = props.get("DRIVER_INFO")
            try:
                if info and int(float(info.values.get("DRIVER_INTERFACE") or 0)) & interface_bit:
                    out.append(dev)
            except ValueError:
                pass
        return out

    async def wait_for(self, pred: Callable[[Property], bool], timeout: float) -> Property:
        fut = asyncio.get_running_loop().create_future()
        entry = (pred, fut)
        self._waiters.append(entry)
        try:
            return await asyncio.wait_for(fut, timeout)
        finally:
            self._waiters.remove(entry)

    async def wait_property(self, device: str, name: str, timeout: float = 10) -> Property:
        p = self.get(device, name)
        if p:
            return p
        return await self.wait_for(lambda q: q.device == device and q.name == name, timeout)

    # ------------------------------------------------------------ Ausgang
    async def set_switch(self, device: str, name: str, on: list[str] | str, off: list[str] = ()):
        on = [on] if isinstance(on, str) else list(on)
        p = self.get(device, name)
        elems = {e: "On" for e in on}
        if p and p.rule == "OneOfMany":
            elems = {e: ("On" if e in on else "Off") for e in p.values}
        for e in off:
            elems[e] = "Off"
        body = "".join(f"<oneSwitch name={quoteattr(k)}>{v}</oneSwitch>" for k, v in elems.items())
        await self._send(f"<newSwitchVector device={quoteattr(device)} name={quoteattr(name)}>{body}</newSwitchVector>")

    async def set_number(self, device: str, name: str, values: dict[str, float]):
        body = "".join(f"<oneNumber name={quoteattr(k)}>{float(v):.8f}</oneNumber>" for k, v in values.items())
        await self._send(f"<newNumberVector device={quoteattr(device)} name={quoteattr(name)}>{body}</newNumberVector>")

    async def set_text(self, device: str, name: str, values: dict[str, str]):
        body = "".join(f"<oneText name={quoteattr(k)}>{escape(str(v))}</oneText>" for k, v in values.items())
        await self._send(f"<newTextVector device={quoteattr(device)} name={quoteattr(name)}>{body}</newTextVector>")

    async def enable_blob(self, device: str, mode: str = "Also", name: str | None = None):
        attr = f" name={quoteattr(name)}" if name else ""
        await self._send(f"<enableBLOB device={quoteattr(device)}{attr}>{mode}</enableBLOB>")

    async def connect_device(self, device: str, timeout: float = 15) -> bool:
        p = await self.wait_property(device, "CONNECTION", timeout)
        if "CONNECT" in p.on():
            return True
        await self.set_switch(device, "CONNECTION", "CONNECT")
        try:
            await self.wait_for(lambda q: q.device == device and q.name == "CONNECTION"
                                and "CONNECT" in q.on() and q.state == "Ok", timeout)
            return True
        except asyncio.TimeoutError:
            return False
