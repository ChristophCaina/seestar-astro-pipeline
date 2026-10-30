"""Steuerlogik ueber dem INDI-Client: Montierung, Kamera, Serien, Live-View, Plate-Solving."""
from __future__ import annotations

import asyncio
import logging
import math
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import image
from .indi import IF_CCD, IF_TELESCOPE, Blob, IndiClient

log = logging.getLogger("astro_pi.control")


def slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9 +._-]", "", s).strip() or "Unbekannt"


@dataclass
class SeriesState:
    running: bool = False
    target: str = ""
    exposure: float = 0
    count: int = 0
    done: int = 0
    delay: float = 0
    started: float = 0
    error: str = ""
    last_file: str = ""
    frame_type: str = "Light"                 # Light | Dark | Flat | Bias
    mode: str = "deep"                        # deep | wide


@dataclass
class Settings:
    capture_dir: Path = Path("/data/captures")
    device_id: str = "eos6d"
    filter_name: str = "DSLR"                 # landet im Dateinamen, siril-auto gruppiert danach
    wide_filter: str = "WIDE"                 # Weitwinkel-Lights (siril-auto: wide_stack.ssf)
    mount: str = ""                           # leer = automatisch (DRIVER_INTERFACE)
    camera: str = ""
    solve_field: str = "solve-field"
    scale_low: float = 0.5                    # "/px – Default fuer 6D an kleinem Refraktor, anpassen
    scale_high: float = 6.0
    lat: float | None = None
    lon: float | None = None


@dataclass
class Event:
    kind: str
    data: dict = field(default_factory=dict)


class Controller:
    def __init__(self, indi: IndiClient, settings: Settings):
        self.indi, self.s = indi, settings
        self.series = SeriesState()
        self.preview: dict | None = None           # {"jpeg", "hfr", "stars", "time", "file"}
        self.live_frame: bytes | None = None
        self.live_on = False
        self.subscribers: set[asyncio.Queue] = set()
        self._exposure_waiter: asyncio.Future | None = None
        self._series_task: asyncio.Task | None = None
        self._capture_lock = asyncio.Lock()
        self.target_name = ""
        self.extra_cards: dict = {}                  # zusaetzliche FITS-Schluessel fuer jede Aufnahme (Aufbau, Standort)
        self.ready = asyncio.Event()                 # Geraete eingerichtet (BLOBs aktiv, Upload an Client)
        indi.on_blob(self._blob)
        indi.on_property(self._prop)

    # ------------------------------------------------------------ Geraete
    @property
    def mount(self) -> str:
        return self.s.mount or next(iter(self.indi.devices_with(IF_TELESCOPE)), "")

    @property
    def camera(self) -> str:
        return self.s.camera or next(iter(self.indi.devices_with(IF_CCD)), "")

    async def setup_devices(self):
        """Verbinden, BLOBs aktivieren, Upload an den Client, FITS als Format."""
        await self.indi.connected.wait()
        await asyncio.sleep(1.0)                     # defProperties abwarten
        for dev in filter(None, [self.mount, self.camera]):
            ok = await self.indi.connect_device(dev)
            log.info("%s: %s", dev, "verbunden" if ok else "Verbindung fehlgeschlagen")
        cam = self.camera
        if cam:
            await self.indi.enable_blob(cam, "Also")
            if self.indi.get(cam, "UPLOAD_MODE"):
                await self.indi.set_switch(cam, "UPLOAD_MODE", "UPLOAD_CLIENT")
            if self.indi.get(cam, "CCD_TRANSFER_FORMAT"):
                await self.indi.set_switch(cam, "CCD_TRANSFER_FORMAT", "FORMAT_FITS")
        if self.mount and self.s.lat is not None and self.indi.get(self.mount, "GEOGRAPHIC_COORD"):
            await self.indi.set_number(self.mount, "GEOGRAPHIC_COORD",
                                       {"LAT": self.s.lat, "LONG": self.s.lon % 360, "ELEV": 0})
        self.ready.set()

    # ------------------------------------------------------------ Ereignisse
    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=50)
        self.subscribers.add(q)
        return q

    def unsubscribe(self, q):
        self.subscribers.discard(q)

    def publish(self, kind: str, **data):
        ev = Event(kind, data)
        for q in list(self.subscribers):
            if q.full():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            q.put_nowait(ev)

    def _prop(self, p):
        if p.device in (self.mount, self.camera) and p.name in (
                "EQUATORIAL_EOD_COORD", "TELESCOPE_PARK", "TELESCOPE_TRACK_STATE", "CCD_EXPOSURE",
                "CONNECTION", "CCD_VIDEO_STREAM", "ON_COORD_SET"):
            self.publish("state")

    async def _blob(self, b: Blob):
        if b.device != self.camera:
            return
        fmt = b.fmt.lower()
        waiting = self._exposure_waiter is not None and not self._exposure_waiter.done()
        if waiting and "stream" not in fmt:
            self._exposure_waiter.set_result(b)
        elif self.live_on:
            frame = b.data if fmt.endswith(("jpg", "jpeg")) else await self._to_jpeg(b.data, 960)
            if frame:
                self.live_frame = frame
                self.publish("live")

    async def _to_jpeg(self, data: bytes, max_w: int) -> bytes | None:
        try:
            return await asyncio.to_thread(lambda: image.preview(data, max_w)["jpeg"])
        except Exception:
            return None

    # ------------------------------------------------------------ Status
    def state(self) -> dict:
        m, c, i = self.mount, self.camera, self.indi
        ra = i.value(m, "EQUATORIAL_EOD_COORD", "RA")
        dec = i.value(m, "EQUATORIAL_EOD_COORD", "DEC")
        park = i.get(m, "TELESCOPE_PARK")
        track = i.get(m, "TELESCOPE_TRACK_STATE")
        coord = i.get(m, "EQUATORIAL_EOD_COORD")
        expo = i.get(c, "CCD_EXPOSURE")
        iso = i.get(c, "CCD_ISO")
        disk = shutil.disk_usage(self.s.capture_dir) if self.s.capture_dir.exists() else None
        return {
            "indi": i.connected.is_set(),
            "mount": {
                "device": m, "connected": bool(m and "CONNECT" in (i.get(m, "CONNECTION").on() if i.get(m, "CONNECTION") else [])),
                "ra": ra, "dec": dec, "alt": self.altitude(ra, dec) if ra is not None else None,
                "slewing": bool(coord and coord.state == "Busy"),
                "parked": bool(park and "PARK" in park.on()),
                "tracking": bool(track and "TRACK_ON" in track.on()),
                "target": self.target_name,
            },
            "camera": {
                "device": c, "connected": bool(c and "CONNECT" in (i.get(c, "CONNECTION").on() if i.get(c, "CONNECTION") else [])),
                "exposing": bool(expo and expo.state == "Busy"),
                "remaining": i.value(c, "CCD_EXPOSURE", "CCD_EXPOSURE_VALUE"),
                "iso": iso.on()[0] if iso and iso.on() else None,
                "iso_options": list(iso.values) if iso else [],
                "live": self.live_on,
            },
            "series": self.series.__dict__,
            "wide": getattr(self, "wide", None),
            "preview": {k: v for k, v in (self.preview or {}).items() if k != "jpeg"},
            "disk_free_gb": round(disk.free / 1e9, 1) if disk else None,
            "messages": [{"t": t, "device": d, "text": x} for t, d, x in self.indi.messages[-5:]],
            "location": {"lat": self.s.lat, "lon": self.s.lon},
        }

    def altitude(self, ra_h: float, dec: float, when: datetime | None = None) -> float | None:
        if self.s.lat is None or self.s.lon is None or ra_h is None or dec is None:
            return None
        when = when or datetime.now(timezone.utc)
        jd = when.timestamp() / 86400 + 2440587.5
        gmst = (280.46061837 + 360.98564736629 * (jd - 2451545.0)) % 360
        ha = math.radians((gmst + self.s.lon - ra_h * 15) % 360)
        lat, d = math.radians(self.s.lat), math.radians(dec)
        alt = math.asin(math.sin(lat) * math.sin(d) + math.cos(lat) * math.cos(d) * math.cos(ha))
        return round(math.degrees(alt), 1)

    # ------------------------------------------------------------ Montierung
    async def goto(self, ra_h: float, dec: float, name: str = "", sync: bool = False):
        m = self._need(self.mount, "Montierung")
        park = self.indi.get(m, "TELESCOPE_PARK")
        if park and "PARK" in park.on() and not sync:
            await self.indi.set_switch(m, "TELESCOPE_PARK", "UNPARK")
            await asyncio.sleep(1)
        await self.indi.set_switch(m, "ON_COORD_SET", "SYNC" if sync else "TRACK")
        await self.indi.set_number(m, "EQUATORIAL_EOD_COORD", {"RA": ra_h, "DEC": dec})
        if name and not sync:
            self.target_name = name
        self.publish("state")

    async def wait_slew(self, timeout: float = 300):
        m = self.mount
        await asyncio.sleep(1.0)
        p = self.indi.get(m, "EQUATORIAL_EOD_COORD")
        if p and p.state != "Busy":
            return
        await self.indi.wait_for(lambda q: q.device == m and q.name == "EQUATORIAL_EOD_COORD" and q.state != "Busy",
                                 timeout)

    async def park(self, on: bool = True):
        m = self._need(self.mount, "Montierung")
        await self.indi.set_switch(m, "TELESCOPE_PARK", "PARK" if on else "UNPARK")

    async def track(self, on: bool):
        m = self._need(self.mount, "Montierung")
        await self.indi.set_switch(m, "TELESCOPE_TRACK_STATE", "TRACK_ON" if on else "TRACK_OFF")

    async def move(self, direction: str, on: bool):
        m = self._need(self.mount, "Montierung")
        axis = {"N": ("TELESCOPE_MOTION_NS", "MOTION_NORTH"), "S": ("TELESCOPE_MOTION_NS", "MOTION_SOUTH"),
                "W": ("TELESCOPE_MOTION_WE", "MOTION_WEST"), "E": ("TELESCOPE_MOTION_WE", "MOTION_EAST")}[direction]
        if on:
            await self.indi.set_switch(m, axis[0], axis[1])
        else:
            p = self.indi.get(m, axis[0])
            await self.indi.set_switch(m, axis[0], [], off=list(p.values) if p else [axis[1]])

    async def slew_rate(self, index: int):
        m = self._need(self.mount, "Montierung")
        p = self.indi.get(m, "TELESCOPE_SLEW_RATE")
        if p:
            names = list(p.values)
            await self.indi.set_switch(m, "TELESCOPE_SLEW_RATE", names[max(0, min(index, len(names) - 1))])

    async def abort(self):
        if self.mount:
            await self.indi.set_switch(self.mount, "TELESCOPE_ABORT_MOTION", "ABORT")
        await self.stop_series()

    # ------------------------------------------------------------ Kamera
    async def set_iso(self, iso: str):
        c = self._need(self.camera, "Kamera")
        if self.indi.get(c, "CCD_ISO"):
            await self.indi.set_switch(c, "CCD_ISO", iso)

    async def expose(self, seconds: float, timeout_extra: float = 60) -> Blob:
        c = self._need(self.camera, "Kamera")
        if not self.ready.is_set():
            try:
                await asyncio.wait_for(self.ready.wait(), 20)
            except asyncio.TimeoutError:
                raise RuntimeError("Kamera noch nicht bereit") from None
        async with self._capture_lock:
            # Treiber noch mit dem letzten Bild beschaeftigt? Kurz warten, sonst ignoriert er den Befehl.
            p = self.indi.get(c, "CCD_EXPOSURE")
            if p and p.state == "Busy":
                try:
                    await self.indi.wait_for(lambda q: q.device == c and q.name == "CCD_EXPOSURE" and q.state != "Busy", 10)
                except asyncio.TimeoutError:
                    pass
            fut = asyncio.get_running_loop().create_future()
            self._exposure_waiter = fut
            try:
                for attempt in range(2):
                    await self.indi.set_number(c, "CCD_EXPOSURE", {"CCD_EXPOSURE_VALUE": seconds})
                    try:
                        # Bild schon da (sehr kurze Belichtung) oder Treiber meldet "Busy" -> laeuft
                        await asyncio.wait_for(asyncio.shield(self._started(c, fut)), 5)
                        break
                    except asyncio.TimeoutError:
                        log.warning("Belichtung nicht gestartet – neuer Versuch (%d)", attempt + 1)
                return await asyncio.wait_for(fut, seconds + timeout_extra)
            finally:
                self._exposure_waiter = None

    async def _started(self, cam: str, fut: asyncio.Future):
        if fut.done():
            return
        busy = asyncio.ensure_future(self.indi.wait_for(
            lambda q: q.device == cam and q.name == "CCD_EXPOSURE" and q.state == "Busy", 6))
        done, pending = await asyncio.wait({busy, fut}, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            if t is busy:
                t.cancel()
        if busy in done and busy.exception():
            raise asyncio.TimeoutError()

    def frame_path(self, target: str, seconds: float, ext: str, when: datetime | None = None,
                   frame_type: str = "Light", mode: str = "deep") -> Path:
        """Gleiche Namenskonvention wie das Seestar -> siril-auto erkennt Ziel, Belichtung, Nacht.
        Kalibrierbilder (Dark/Flat/Bias) landen in _calib/ und werden dort zu Masters gestackt."""
        when = when or datetime.now()
        exp = f"{seconds:.6f}".rstrip("0") if seconds < 1 else f"{seconds:.1f}"   # 0.00025 / 0.5 / 60.0
        filt = self.s.wide_filter if mode == "wide" and frame_type == "Light" else self.s.filter_name
        stamp = f"{exp}s_{filt}_{when:%Y%m%d-%H%M%S}{ext}"
        if frame_type != "Light":
            return self.s.capture_dir / "_calib" / f"{frame_type}_{stamp}"
        t = slug(target or self.target_name or "Unbekannt")
        return self.s.capture_dir / f"{t}_sub" / f"Light_{t}_{stamp}"

    async def set_frame_type(self, frame_type: str):
        c = self._need(self.camera, "Kamera")
        if frame_type not in ("Light", "Dark", "Flat", "Bias"):
            raise ValueError("Bildtyp: Light, Dark, Flat oder Bias")
        if self.indi.get(c, "CCD_FRAME_TYPE"):
            await self.indi.set_switch(c, "CCD_FRAME_TYPE", f"FRAME_{frame_type.upper()}")

    async def capture(self, seconds: float, target: str = "", save: bool = True, frame_type: str = "Light",
                      mode: str = "deep") -> dict:
        b = await self.expose(seconds)
        ext = b.fmt if b.fmt.startswith(".") else f".{b.fmt or 'fits'}"
        path = None
        if save:
            path = self.frame_path(target, seconds, ".fits" if ext.lower() in (".fits", ".fit") else ext,
                                   frame_type=frame_type, mode=mode)
            base, n = path, 1
            while path.exists():                     # mehrere Bilder in derselben Sekunde (Bias, kurze Serien)
                n += 1
                path = base.with_name(f"{base.stem}_{n}{base.suffix}")
            path.parent.mkdir(parents=True, exist_ok=True)
            data = b.data
            if self.extra_cards and path.suffix.lower() in (".fits", ".fit"):
                data = image.add_cards(data, {"OBJECT": target or self.target_name, **self.extra_cards})
            path.write_bytes(data)
        if ext.lower() in (".fits", ".fit"):
            pv = await asyncio.to_thread(image.preview, b.data)
            self.preview = {"jpeg": pv["jpeg"], "hfr": pv["hfr"], "stars": pv["stars"], "time": time.time(),
                            "file": str(path) if path else "", "width": pv["width"], "height": pv["height"],
                            "exposure": seconds}
            self.publish("preview")
        return {"file": str(path) if path else "", "format": ext, "bytes": len(b.data),
                "hfr": (self.preview or {}).get("hfr")}

    # ------------------------------------------------------------ Serien
    async def start_series(self, exposure: float, count: int, delay: float = 2.0, target: str = "", iso: str = "",
                           frame_type: str = "Light", mode: str = "deep"):
        if mode not in ("deep", "wide"):
            raise ValueError("Modus: deep oder wide")
        if self.series.running:
            raise RuntimeError("Es läuft bereits eine Serie")
        self._need(self.camera, "Kamera")
        if iso:
            await self.set_iso(iso)
        await self.set_frame_type(frame_type)
        self.series = SeriesState(True, target or self.target_name, exposure, count, 0, delay, time.time(),
                                  frame_type=frame_type, mode=mode)
        self._series_task = asyncio.create_task(self._run_series())
        self.publish("state")

    async def _run_series(self):
        s = self.series
        try:
            while s.running and (s.count <= 0 or s.done < s.count):
                r = await self.capture(s.exposure, s.target, frame_type=s.frame_type, mode=s.mode)
                s.done += 1
                s.last_file = r["file"]
                self.publish("state")
                if s.delay and s.running:
                    await asyncio.sleep(s.delay)
        except asyncio.CancelledError:
            pass
        except Exception as e:                       # Kamera weg, Timeout ...
            log.exception("Serie abgebrochen")
            s.error = str(e)
        finally:
            s.running = False
            if s.frame_type != "Light":
                try:
                    await self.set_frame_type("Light")
                except Exception:
                    pass
            self.publish("state")

    async def stop_series(self):
        if self._series_task and not self._series_task.done():
            self.series.running = False
            c = self.camera
            if c and self.indi.get(c, "CCD_ABORT_EXPOSURE"):
                await self.indi.set_switch(c, "CCD_ABORT_EXPOSURE", "ABORT")
            self._series_task.cancel()
            try:
                await self._series_task
            except asyncio.CancelledError:
                pass

    # ------------------------------------------------------------ Live-View
    async def live(self, on: bool):
        c = self._need(self.camera, "Kamera")
        if not self.indi.get(c, "CCD_VIDEO_STREAM"):
            raise RuntimeError("Kamera-Treiber bietet keinen Live-View")
        self.live_on = on
        await self.indi.set_switch(c, "CCD_VIDEO_STREAM", "STREAM_ON" if on else "STREAM_OFF")
        self.publish("state")

    # ------------------------------------------------------------ Plate-Solving
    solve_scale: tuple[float, float] | None = None     # Weitwinkel: ("/px min, max) aus dem Objektiv

    async def solve_last(self) -> dict:
        """Letzte Aufnahme mit astrometry.net loesen. Liefert RA (h) / Dec (Grad) der Bildmitte."""
        f = (self.preview or {}).get("file")
        if not f or not Path(f).exists():
            raise RuntimeError("Keine gespeicherte Aufnahme zum Lösen")
        return await asyncio.to_thread(self._solve_file, Path(f))

    def _solve_file(self, path: Path) -> dict:
        with tempfile.TemporaryDirectory() as tmp:
            cmd = [self.s.solve_field, "--overwrite", "--no-plots", "--downsample", "2", "--cpulimit", "60",
                   "--scale-units", "arcsecperpix",
                   "--scale-low", str((self.solve_scale or (self.s.scale_low, 0))[0]),
                   "--scale-high", str((self.solve_scale or (0, self.s.scale_high))[1]), "--dir", tmp, "--new-fits", "none", str(path)]
            r = subprocess.run(cmd, capture_output=True, text=True)
            wcs = Path(tmp) / (path.stem + ".wcs")
            if r.returncode != 0 or not wcs.exists():
                raise RuntimeError("Plate-Solving fehlgeschlagen")
            hdr = self._hdr(wcs)
        w = int(hdr.get("IMAGEW") or (self.preview or {}).get("width") or 0)
        h = int(hdr.get("IMAGEH") or (self.preview or {}).get("height") or 0)
        ra, dec = _pix2world(hdr, w / 2, h / 2)
        scale = math.hypot(hdr.get("CD1_1", 0), hdr.get("CD2_1", 0)) * 3600
        return {"ra": ra / 15, "dec": dec, "scale": round(scale, 3)}

    @staticmethod
    def _hdr(path: Path) -> dict:
        hdr = {}
        raw = path.read_bytes()
        for i in range(0, len(raw), 80):
            card = raw[i:i + 80].decode("ascii", "replace")
            if card.startswith("END"):
                break
            if card[8:10] == "= ":
                v = card[10:].split("/")[0].strip().strip("'").strip()
                try:
                    hdr[card[:8].strip()] = float(v)
                except ValueError:
                    hdr[card[:8].strip()] = v
        return hdr

    async def solve_and_sync(self) -> dict:
        r = await self.solve_last()
        await self.goto(r["ra"], r["dec"], sync=True)
        return r

    async def center(self, ra_h: float, dec: float, name: str = "", exposure: float = 5.0,
                     tolerance_arcmin: float = 2.0, attempts: int = 3) -> dict:
        """GoTo, Aufnahme, Loesen, Sync, erneut GoTo – bis das Ziel in der Bildmitte liegt."""
        await self.goto(ra_h, dec, name)
        await self.wait_slew()
        err = None
        for n in range(attempts):
            await self.capture(exposure, name or self.target_name, save=True)
            r = await self.solve_last()
            err = _sep_arcmin(r["ra"], r["dec"], ra_h, dec)
            self.publish("center", attempt=n + 1, error_arcmin=round(err, 2))
            if err <= tolerance_arcmin:
                return {"ok": True, "attempts": n + 1, "error_arcmin": round(err, 2)}
            await self.goto(r["ra"], r["dec"], sync=True)
            await self.goto(ra_h, dec, name)
            await self.wait_slew()
        return {"ok": False, "attempts": attempts, "error_arcmin": round(err, 2) if err is not None else None}

    @staticmethod
    def _need(dev: str, what: str) -> str:
        if not dev:
            raise RuntimeError(f"{what} nicht gefunden – INDI-Treiber gestartet?")
        return dev


def _pix2world(h: dict, x: float, y: float) -> tuple[float, float]:
    """TAN-Projektion, Pixel 0-basiert (FITS 1-basiert)."""
    dx, dy = x + 1 - h["CRPIX1"], y + 1 - h["CRPIX2"]
    xi = math.radians(h["CD1_1"] * dx + h.get("CD1_2", 0) * dy)
    eta = math.radians(h.get("CD2_1", 0) * dx + h["CD2_2"] * dy)
    ra0, dec0 = math.radians(h["CRVAL1"]), math.radians(h["CRVAL2"])
    den = math.cos(dec0) - eta * math.sin(dec0)
    ra = ra0 + math.atan2(xi, den)
    dec = math.atan2(math.sin(dec0) + eta * math.cos(dec0), math.hypot(xi, den))
    return math.degrees(ra) % 360, math.degrees(dec)


def _sep_arcmin(ra1_h, dec1, ra2_h, dec2) -> float:
    a1, d1, a2, d2 = map(math.radians, (ra1_h * 15, dec1, ra2_h * 15, dec2))
    c = math.sin(d1) * math.sin(d2) + math.cos(d1) * math.cos(d2) * math.cos(a1 - a2)
    return math.degrees(math.acos(max(-1.0, min(1.0, c)))) * 60
