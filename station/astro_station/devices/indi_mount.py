"""Montierung ueber INDI (z. B. iEXOS-100 mit indi_pmc8_telescope) plus die Kamera, die gerade daran haengt.

Baut auf dem Controller der Astro-Pi-App auf (pi/astro_pi): GoTo, Zentrieren per Plate-Solving,
Serien mit siril-auto-Dateinamen, Live View. Die Kamera ist Teil des "Aufbaus" der Montierung:
welche Kamera und welche Optik dranhaengen, geht als FITS-Header in jede Aufnahme.

Treiber koennen beim Einschalten ueber die FIFO des laufenden indiserver nachgestartet werden
(dieselbe FIFO, ueber die field/systemd/seestar-indi.service den Seestar-Treiber startet).
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from .. import astro
from .base import Action, Device, Refused
from .seestar import fmt_radec

log = logging.getLogger("astro_station.indi")
ROOT = Path(__file__).resolve().parents[3]
STATE_DIR = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "astro-station"


class IndiMount(Device):
    def __init__(self, id: str, name: str, indi: str = "localhost:7624", mount: str = "", fifo: str = "",
                 driver: str = "", cameras: list | None = None, optics: list | None = None,
                 capture_dir: str = "~/Astro/captures", solve_field: str = "solve-field",
                 stellarium_port: int = 0, enabled: bool = True, **_):
        super().__init__(id=id, name=name, kind="mount", stellarium_port=stellarium_port, enabled=enabled)
        host, _, port = indi.partition(":")
        self.host, self.port = host or "localhost", int(port or 7624)
        self.mount_name, self.fifo, self.driver = mount, fifo, driver
        self.cameras = [{"id": "none", "name": "Keine Kamera", "sub": "nur visuell"}] + list(cameras or [])
        self.optics = list(optics or [])
        self.capture_dir = Path(capture_dir).expanduser()
        self.solve_field = solve_field
        self.rig = {"camera": self.cameras[1]["id"] if len(self.cameras) > 1 else "none",
                    "optic": self.optics[0]["name"] if self.optics else "",
                    "focal": self.optics[0].get("focal") if self.optics else None,
                    "aperture": self.optics[0].get("aperture") if self.optics else None}
        self._load_rig()
        self.ctl = None
        self.indi = None
        self._tasks: list[asyncio.Task] = []
        self._started_drivers: set[str] = set()
        self.busy = ""                              # laufender Hintergrund-Vorgang (Zentrieren ...)

    # ------------------------------------------------------------ Aufbau
    def _rig_file(self) -> Path:
        return STATE_DIR / f"rig-{self.id}.json"

    def _load_rig(self):
        try:
            self.rig.update(json.loads(self._rig_file().read_text()))
        except (OSError, ValueError):
            pass

    def camera(self) -> dict:
        return next((c for c in self.cameras if c["id"] == self.rig.get("camera")), self.cameras[0])

    def rig_info(self) -> dict:
        cam, f = self.camera(), self.rig.get("focal")
        w, h, px = cam.get("width_mm"), cam.get("height_mm"), cam.get("pixel_um")
        fov = scale = None
        if f and w and h:
            fov = [round(2 * math.degrees(math.atan(x / (2 * f))), 2) for x in (w, h)]
        if f and px:
            scale = round(206.265 * px / f, 2)
        return {**self.rig, "camera_name": cam["name"], "fov": fov, "scale": scale,
                "device_id": cam.get("device_id", ""), "filter": cam.get("filter", ""),
                "cameras": [{"id": c["id"], "name": c["name"], "sub": c.get("sub", "")} for c in self.cameras],
                "optics": self.optics}

    async def set_rig(self, rig: dict) -> str:
        for k in ("camera", "optic", "focal", "aperture"):
            if k in rig:
                self.rig[k] = rig[k]
        for k in ("focal", "aperture"):
            if self.rig.get(k) not in (None, ""):
                self.rig[k] = float(self.rig[k])
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        self._rig_file().write_text(json.dumps(self.rig))
        self._apply_rig()
        cam = self.camera()
        if cam.get("driver"):
            self._start_driver(cam["driver"])
        return f"{self.name}: Aufbau – {cam['name']}" + (f" an {self.rig['optic']}" if self.rig.get("optic") else "")

    def _apply_rig(self):
        if not self.ctl:
            return
        cam, info = self.camera(), self.rig_info()
        s = self.ctl.s
        s.camera = cam.get("indi", "") if cam["id"] != "none" else "__none__"
        s.device_id = cam.get("device_id", s.device_id)
        s.filter_name = cam.get("filter", s.filter_name)
        if info["scale"]:
            self.ctl.solve_scale = (round(info["scale"] * 0.8, 2), round(info["scale"] * 1.25, 2))
        loc = self.location
        self.ctl.extra_cards = {
            "TELESCOP": self.name, "INSTRUME": cam["name"] if cam["id"] != "none" else "",
            "FOCALLEN": self.rig.get("focal"), "APTDIA": self.rig.get("aperture"), "OPTIC": self.rig.get("optic") or "",
            "SITELAT": loc.get("lat"), "SITELONG": loc.get("lon"), "CREATOR": "Astro-Station",
        }
        s.lat, s.lon = loc.get("lat"), loc.get("lon")

    # ------------------------------------------------------------ Lebenszyklus
    def _start_driver(self, drv: str):
        if not self.fifo or drv in self._started_drivers:
            return
        try:
            fd = os.open(self.fifo, os.O_WRONLY | os.O_NONBLOCK)
            os.write(fd, f"start {drv}\n".encode())
            os.close(fd)
            self._started_drivers.add(drv)
            log.info("%s: Treiber %s gestartet", self.id, drv)
        except OSError as e:
            log.warning("%s: Treiber %s nicht startbar (%s)", self.id, drv, e)

    async def start(self):
        sys.path.insert(0, str(ROOT / "pi"))
        try:
            from astro_pi.control import Controller, Settings
            from astro_pi.indi import IndiClient
        except ImportError as e:                    # numpy/PIL fehlen
            self.last_result, self.last_result_ok = f"astro_pi nicht ladbar: {e}", False
            log.warning("%s: %s", self.id, e)
            return
        self.indi = IndiClient(self.host, self.port)
        self.ctl = Controller(self.indi, Settings(capture_dir=self.capture_dir, mount=self.mount_name or "__none__",
                                                  solve_field=self.solve_field))
        self._apply_rig()
        if self.driver:
            self._start_driver(self.driver)
        cam = self.camera()
        if cam.get("driver"):
            self._start_driver(cam["driver"])
        self._tasks = [asyncio.create_task(self.indi.run()), asyncio.create_task(self._setup())]

    def _resolve_mount(self) -> str:
        """Konfigurierter Name, sonst die erste Montierung, die kein Seestar ist (der laeuft im selben indiserver)."""
        if self.mount_name:
            return self.mount_name
        from astro_pi.indi import IF_TELESCOPE
        return next((d for d in self.indi.devices_with(IF_TELESCOPE) if not d.lower().startswith("seestar")), "")

    async def _setup(self):
        """Verbinden, sobald der Treiber da ist; danach alle 10 s pruefen (Treiber spaeter gestartet, USB neu)."""
        await self.indi.connected.wait()
        await asyncio.sleep(1.5)
        while True:
            try:
                mount = self._resolve_mount()
                if mount and (mount != self.ctl.s.mount or not self.online):
                    self.ctl.s.mount = mount
                    await self.ctl.setup_devices()
                    self.online = bool(self._st().get("mount", {}).get("connected"))
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("%s: Einrichten: %s", self.id, e)
            await asyncio.sleep(10)

    async def close(self):
        if self.ctl:
            try:
                await self.ctl.stop_series()
            except Exception:
                pass
        for t in self._tasks:
            t.cancel()

    # ------------------------------------------------------------ Anzeige
    def _st(self) -> dict:
        try:
            return self.ctl.state() if self.ctl else {}
        except Exception:
            return {}

    def snapshot(self) -> dict:
        d = super().snapshot()
        st = self._st()
        m, c = st.get("mount", {}), st.get("camera", {})
        self.online = bool(m.get("connected"))
        if m.get("ra") is not None:
            self.position = (m["ra"], m["dec"])
        aa = None
        if self.position and self.location.get("lat") is not None:
            aa = astro.altaz(*self.position, self.location["lat"], self.location["lon"], datetime.now(timezone.utc))
        cam = self.camera()
        has_cam = cam["id"] != "none"
        ser = st.get("series") or {}
        op = self.busy or ("Serie {done}/{count} – {target}".format(**ser) if ser.get("running") else
                           "fährt …" if m.get("slewing") else "Bereit" + (" (geparkt)" if m.get("parked") else ""))
        d.update({
            "sub": f"INDI {self.host}:{self.port} · mit {cam['name']}",
            "can": {"goto": True, "move": True, "center": has_cam, "series": has_cam, "rig": True},
            "facts": [
                {"label": "Zustand", "value": "geparkt" if m.get("parked") else ("fährt" if m.get("slewing") else "bereit") if self.online else "nicht verbunden"},
                {"label": "Nachführung", "value": "siderisch" if m.get("tracking") else "aus"},
                {"label": "RA / Dec (JNow)", "value": fmt_radec(self.position)},
                {"label": "Höhe / Azimut", "value": f"{aa[0]:.1f}° · {aa[1]:.1f}°".replace(".", ",") if aa else "–"},
                {"label": "Kamera", "value": (cam["name"] + (" · verbunden" if c.get("connected") else " · nicht verbunden")) if has_cam else "–"},
                {"label": "Freier Speicher", "value": f"{st.get('disk_free_gb')} GB".replace(".", ",") if st.get("disk_free_gb") is not None else "–"},
            ],
            "op": op if self.ctl else "astro_pi fehlt",
            "result": self.last_result or ((st.get("messages") or [{}])[-1].get("text") or "–"),
            "result_ok": self.last_result_ok,
            "rig": self.rig_info(),
            "series": ser,
            "iso_options": c.get("iso_options") or [],
            "image": {"title": "Letzte Aufnahme", "hint": (f"HFR {st['preview']['hfr']}" if (st.get("preview") or {}).get("hfr") else "Testbild oder Serie"),
                      "jpeg": f"/api/dev/{self.id}/preview.jpg" if (st.get("preview") or {}).get("time") else ""},
            "actions": [a.as_dict() for a in self.actions(m, has_cam)],
        })
        return d

    def actions(self, m: dict, has_cam: bool) -> list[Action]:
        off = "" if self.online else "nicht verbunden"
        return [
            Action("unpark" if m.get("parked") else "park", "Entparken" if m.get("parked") else "Parken",
                   "Parkposition", confirm="" if m.get("parked") else f"{self.name} parken?", primary=True, disabled=off),
            Action("track_off" if m.get("tracking") else "track_on", "Nachführung aus" if m.get("tracking") else "Nachführung an",
                   "siderisch", primary=True, disabled=off),
            Action("solve", "Plate-Solve + Sync", "Position abgleichen", disabled=off or ("braucht eine Kamera" if not has_cam else "")),
            Action("stop", "Stopp", "Bewegung und Serie", danger=True),
        ]

    # ------------------------------------------------------------ Befehle
    def _need(self):
        if not self.ctl:
            raise Refused(f"{self.name}: astro_pi nicht geladen (python3-numpy, python3-pil installiert?)")
        if not self.online:
            raise Refused(f"{self.name}: nicht verbunden – Treiber im INDI-Server gestartet?")

    def _jnow(self, target: dict) -> tuple[float, float]:
        if "ra_jnow" in target:
            return float(target["ra_jnow"]), float(target["dec_jnow"])
        if target.get("kind") in ("planet", "moon"):
            raise Refused(f"{self.name}: Planeten und Mond bitte über Stellarium anfahren")
        return astro.j2000_to_date(float(target["ra"]), float(target["dec"]), datetime.now(timezone.utc))

    async def goto(self, target: dict) -> str:
        self._need()
        ra, dec = self._jnow(target)
        name = target.get("name", "")
        if target.get("center"):
            if self.camera()["id"] == "none":
                raise Refused(f"{self.name}: Zentrieren braucht eine Kamera")
            asyncio.create_task(self._center(ra, dec, name))
            return f"{self.name}: GoTo {name}, danach Plate-Solving bis < 2′"
        await self.ctl.goto(ra, dec, name)
        self.note(f"GoTo {name}")
        return f"{self.name}: GoTo {name}"

    async def _center(self, ra, dec, name):
        self.busy = f"Zentrieren – {name}"
        try:
            r = await self.ctl.center(ra, dec, name)
            self.note(f"Zentriert: {r.get('error_arcmin')}′ nach {r.get('attempts')} Versuch(en)", r.get("ok", False))
        except Exception as e:
            self.note(f"Zentrieren fehlgeschlagen: {e}", False)
        finally:
            self.busy = ""

    async def move(self, direction: str, speed: int, on: bool) -> None:
        self._need()
        if on:
            p = self.indi.get(self.ctl.mount, "TELESCOPE_SLEW_RATE")
            if p:
                n = len(p.values)
                await self.ctl.slew_rate(round(speed * (n - 1) / 3))
        await self.ctl.move(direction, on)

    async def stop(self) -> str:
        if not self.ctl:
            return f"{self.name}: nichts zu stoppen"
        await self.ctl.abort()
        self.note("gestoppt")
        return f"{self.name}: gestoppt"

    async def action(self, action_id: str, params: dict) -> str:
        if action_id == "stop":
            return await self.stop()
        if action_id == "rig":
            return await self.set_rig(params)
        self._need()
        c = self.ctl
        if action_id in ("park", "unpark"):
            await c.park(action_id == "park")
            text = "parkt …" if action_id == "park" else "entparkt"
        elif action_id in ("track_on", "track_off"):
            await c.track(action_id == "track_on")
            text = "Nachführung " + ("an" if action_id == "track_on" else "aus")
        elif action_id == "solve":
            r = await c.solve_and_sync()
            text = f"Plate-Solving: RA {r['ra']:.3f} h, Dec {r['dec']:+.2f}° – synchronisiert"
        elif action_id == "test":
            r = await c.capture(float(params.get("exposure") or 5), params.get("target", ""), save=False)
            text = "Testbild" + (f" – HFR {r['hfr']}" if r.get("hfr") else "")
        elif action_id == "series":
            await c.start_series(float(params["exposure"]), int(params.get("count") or 0), float(params.get("delay") or 2),
                                 params.get("target", ""), str(params.get("iso") or ""), params.get("type") or "Light")
            text = f"Serie gestartet – {params.get('count') or '∞'} × {params['exposure']} s"
        elif action_id == "series_stop":
            await c.stop_series()
            text = "Serie gestoppt"
        elif action_id in ("live_on", "live_off"):
            await c.live(action_id == "live_on")
            text = "Live View " + ("an" if action_id == "live_on" else "aus")
        else:
            raise Refused(f"{self.name}: unbekannte Aktion {action_id}")
        self.note(text)
        return f"{self.name}: {text}"

    def preview_jpeg(self) -> bytes | None:
        if self.ctl and self.ctl.live_on and self.ctl.live_frame:
            return self.ctl.live_frame
        return (self.ctl.preview or {}).get("jpeg") if self.ctl else None
