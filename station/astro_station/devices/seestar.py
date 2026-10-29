"""Seestar (S50, S30, S30 Pro) ueber seestar_alp.

Alles geht ueber die Alpaca-Schnittstelle von seestar_alp (dieselben Befehle wie dessen Web-UI):
  get_event_state            nicht-blockierend, alle 2 s: Ansicht, Stack, Akku, Arm, Vorgaenge
  scope_get_equ_coord        alle 3 s: Position (JNow) fuer Anzeige und Stellarium
  get_device_state           alle 30 s: Arm, Speicher, Modell
Bilder (Live/Stack) liefert der Bildserver von seestar_alp direkt: <imaging>/<n>/vid (MJPEG).

Keine Automatik: Arm ausfahren, Stacking usw. passieren nur auf Knopfdruck.
GoTo und Bewegen werden abgelehnt, solange der Arm eingeklappt ist.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import datetime, timezone
from urllib.parse import quote

import aiohttp

from .. import astro
from .base import Action, Device, NeedsConfirm, Refused

log = logging.getLogger("astro_station.seestar")

TIMEOUT_SENTINEL = "Exceeded allotted wait time"
PLANETS = {"merkur": "mercury", "venus": "venus", "mars": "mars", "jupiter": "jupiter", "saturn": "saturn",
           "uranus": "uranus", "neptun": "neptune"}
PLANET_RE = re.compile(r"""(?P<rh>\d+)h\s*(?P<rm>\d+)m\s*(?P<rs>[\d.]+)s\s*,\s*"""
                       r"""(?P<sign>[+-]?)(?P<dd>\d+)deg\s*(?P<dm>\d+)'\s*(?P<ds>[\d.]+)\"""")
# Richtung -> Winkel fuer scope_speed_move (wie der Joystick in seestar_alp: 0 = rechts, 90 = oben)
ANGLE = {"N": 90, "S": 270, "W": 180, "E": 0}
SPEEDS = (80, 300, 800, 1440)
WITH_WIDE = ("S30",)          # Modelle mit Weitwinkelkamera (S30, S30 Pro)

OP_EVENTS = {"ScopeMoveToHorizon": "Arm ausfahren", "ScopeHome": "Parken", "AutoGoto": "GoTo",
             "AutoFocus": "Autofokus", "PlateSolve": "Plate-Solving", "DarkLibrary": "Dunkelbilder",
             "Initialise": "Start", "3PPA": "Einnorden", "Stack": "Stacking"}
STATE_DE = {"start": "startet", "working": "läuft", "complete": "fertig", "fail": "fehlgeschlagen",
            "cancel": "abgebrochen"}
VIEW_DE = {"star": "Deep Sky", "moon": "Mond", "sun": "Sonne", "planet": "Planet", "scenery": "Landschaft"}
STAGE_DE = {"AutoGoto": "GoTo", "ContinuousExposure": "Live-Ansicht", "Stack": "Stacking", "RTSP": "Live-Video",
            "ScanSun": "Suchlauf", "ScanMoon": "Suchlauf", "ScanPlanet": "Suchlauf"}


ERROR_DE = {
    "below horizon": "unter dem Horizont – liegt das Ziel tief? Sonst Kompass und Horizont in der Seestar-App kalibrieren",
    "mount goto failed": "Montierung hat das GoTo nicht ausgeführt",
    "solve failed": "Plate-Solving fehlgeschlagen (Wolken, zu hell, drinnen?)",
    "fail to operate": "Seestar beschäftigt oder Bedienung gesperrt",
}


def error_de(err: str) -> str:
    low = str(err).lower()
    return next((v for k, v in ERROR_DE.items() if k in low), str(err))


def local_tz_name() -> str:
    """IANA-Zeitzone des Laptops (fuer pi_set_time), z. B. Europe/Berlin."""
    import os
    try:
        return open("/etc/timezone").read().strip() or "UTC"
    except OSError:
        pass
    try:
        link = os.path.realpath("/etc/localtime")
        return link.split("zoneinfo/", 1)[1] if "zoneinfo/" in link else "UTC"
    except OSError:
        return "UTC"


def parse_planet(text: str) -> tuple[float, float] | None:
    m = PLANET_RE.search(text or "")
    if not m:
        return None
    ra = int(m["rh"]) + int(m["rm"]) / 60 + float(m["rs"]) / 3600
    dec = int(m["dd"]) + int(m["dm"]) / 60 + float(m["ds"]) / 3600
    return ra, -dec if m["sign"] == "-" else dec


def _nav(obj, *path):
    for k in path:
        if isinstance(obj, dict):
            obj = obj.get(k)
        elif isinstance(obj, list) and isinstance(k, int) and -len(obj) <= k < len(obj):
            obj = obj[k]
        else:
            return None
    return obj


def _stamp(ev) -> float:
    try:
        return float(ev.get("Timestamp"))
    except (TypeError, ValueError, AttributeError):
        return float("-inf")


def operations(events: dict) -> tuple[str, str, bool]:
    """(aktueller Vorgang, letztes Ergebnis, Ergebnis ok) aus get_event_state."""
    ops = []
    for name, ev in (events or {}).items():
        if isinstance(ev, dict) and isinstance(name, str):
            label = OP_EVENTS.get(name) or ("Mond/Sonne-Suche" if name.startswith("Scan") else None)
            if label:
                ops.append((_stamp(ev), label, ev))
    ops.sort(key=lambda o: o[0], reverse=True)
    view = events.get("View") if isinstance(events.get("View"), dict) else {}
    target = view.get("target_name")
    running = [o for o in ops if o[2].get("state") in ("start", "working")]
    short = [o for o in running if o[1] != "Stacking"]
    if short:
        current = short[0][1] + (f" – {target}" if target and short[0][1] == "GoTo" else "")
    elif running:
        current = "Stacking" + (f" – {target}" if target else "")
    elif view.get("state") in ("start", "working"):
        current = f"{STAGE_DE.get(view.get('stage'), 'Ansicht')} ({VIEW_DE.get(view.get('mode'), view.get('mode') or '?')})" \
                  + (f" – {target}" if target else "")
    else:
        current = "Bereit"
    result, ok = "", True
    done = [o for o in ops if o[2].get("state") in ("complete", "fail", "cancel")]
    if done:
        _, label, ev = done[0]
        result = f"{label}: {STATE_DE.get(ev['state'], ev['state'])}"
        if ev.get("error"):
            result += f" – {error_de(ev['error'])}"
        elif ev["state"] == "complete" and isinstance(ev.get("lapse_ms"), (int, float)):
            result += f" ({round(ev['lapse_ms'] / 1000)} s)"
        ok = ev["state"] == "complete"
    return current, result, ok


class Seestar(Device):
    def __init__(self, id: str, name: str, alp: str = "http://localhost:5555", device_num: int = 1,
                 imaging: str = "http://localhost:7556", webui: str = "http://localhost:5432",
                 stellarium_port: int = 0, enabled: bool = True, **_):
        super().__init__(id=id, name=name, kind="seestar", stellarium_port=stellarium_port, enabled=enabled)
        self.alp, self.n = alp.rstrip("/"), int(device_num)
        self.imaging, self.webui = imaging.rstrip("/"), webui.rstrip("/")
        self.events: dict = {}
        self.device_state: dict = {}
        self.arm_closed: bool | None = None
        self._arm_seen: tuple | None = None
        self.model = ""
        self.view_since: float | None = None          # seit wann eine Ansicht laeuft (Station-Zeit)
        self.jpeg: bytes | None = None                 # letztes Standbild vom Bildserver
        self.frame = 0                                 # zaehlt neue Standbilder (fuer Browser/Live-Fenster)
        self.wanted = 0.0                              # wann zuletzt jemand Bilder wollte
        self.alerts: list[str] = []                    # neue Fehlermeldungen der Firmware (Station meldet sie)
        self._fail_seen: set = set()
        self._clock_for: tuple | None = None           # Standort, fuer den Uhr/Standort zuletzt gesetzt wurden
        self._txn = 0
        self._http: aiohttp.ClientSession | None = None
        self._tasks: list[asyncio.Task] = []
        self._move_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------ Transport
    async def _session(self) -> aiohttp.ClientSession:
        if self._http is None or self._http.closed:
            self._http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
        return self._http

    async def call(self, action: str, params: dict | None = None):
        """PUT /api/v1/telescope/<n>/action – wie die Web-UI von seestar_alp."""
        async with self._lock:
            self._txn += 1
            body = {"Action": action, "Parameters": json.dumps(params or {}), "ClientID": 1,
                    "ClientTransactionID": self._txn}
            s = await self._session()
            async with s.put(f"{self.alp}/api/v1/telescope/{self.n}/action", json=body) as r:
                data = await r.json(content_type=None)
        if data.get("ErrorNumber"):
            raise RuntimeError(f"{action}: {data.get('ErrorMessage') or data['ErrorNumber']}")
        val = data.get("Value")
        if isinstance(val, dict) and isinstance(val.get("code"), int) and val["code"] != 0:
            raise Refused(f"{self.name}: abgelehnt – {val.get('result') or val.get('error') or val['code']}")
        if isinstance(val, dict) and "result" in val and isinstance(val["result"], (dict, list)):
            val = val["result"]
        sentinel = val.get("result") if isinstance(val, dict) else val
        if isinstance(sentinel, str) and TIMEOUT_SENTINEL in sentinel:
            raise TimeoutError(f"{action}: Seestar beschäftigt")
        if isinstance(val, dict) and val.get("error"):
            raise Refused(f"{self.name}: {val['error']}")
        return val

    async def method(self, method: str, params=None):
        p = {"method": method}
        if params is not None:
            p["params"] = params
        quiet = method in ("get_device_state", "scope_get_equ_coord", "scope_speed_move")
        if not quiet:
            log.info("%s → %s %s", self.id, method, json.dumps(params) if params is not None else "")
        try:
            res = await self.call("method_sync", p)
        except Exception as e:
            if not quiet:
                log.warning("%s ← %s: %s", self.id, method, e)
            raise
        if not quiet:
            log.info("%s ← %s: %s", self.id, method, str(res)[:300])
        return res

    async def connected(self) -> bool:
        try:
            s = await self._session()
            async with s.get(f"{self.alp}/api/v1/telescope/{self.n}/connected",
                             params={"ClientID": 1, "ClientTransactionID": 0},
                             timeout=aiohttp.ClientTimeout(total=4)) as r:
                return bool((await r.json(content_type=None)).get("Value"))
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, OSError):
            return False

    # ------------------------------------------------------------ Abfragen
    async def start(self):
        self._tasks = [asyncio.create_task(self._poll()), asyncio.create_task(self._snapshots())]

    # ------------------------------------------------------------ Bilder
    async def fetch_image(self) -> bytes | None:
        """Ein Bild vom Bildserver: verbinden, erstes JPEG lesen, trennen.

        Ein dauerhaft offener MJPEG-Strom kostet seestar_alp viel Rechenzeit (pro Zuschauer eine
        Analyse-Schleife ~10x/s). Beim Verbinden schickt es sofort das aktuelle Bild – das reicht."""
        from ..livestream import MjpegParser
        parser = MjpegParser()
        s = await self._session()
        async with s.get(f"{self.imaging}/{self.n}/vid", timeout=aiohttp.ClientTimeout(total=12)) as r:
            async for chunk in r.content.iter_chunked(65536):
                for ctype, body in parser.feed(chunk):
                    if "jpeg" in ctype:
                        return body
                    # "Loading"-GIF: noch kein Bild – weiterlesen
        return None

    async def _snapshots(self):
        """Solange eine Ansicht laeuft und jemand zuschaut: alle 3 s (Stacking 6 s) ein Standbild holen."""
        while True:
            try:
                if self.online and self.view_active() and time.time() - self.wanted < 30:
                    img = await self.fetch_image()
                    if img and img != self.jpeg:
                        self.jpeg, self.frame = img, self.frame + 1
                    await asyncio.sleep(6 if self.live()["stacking"] else 3)
                    continue
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.debug("%s: Standbild: %s", self.id, e)
            await asyncio.sleep(2)

    async def close(self):
        for t in self._tasks + ([self._move_task] if self._move_task else []):
            t.cancel()
        if self._http:
            await self._http.close()

    async def _poll(self):
        n = 0
        while True:
            try:
                await self.poll_once(n)
            except asyncio.CancelledError:
                raise
            except Exception as e:                  # seestar_alp weg, Neustart ...
                log.debug("%s: %s", self.id, e)
                self.online = False
            n += 1
            await asyncio.sleep(2)

    async def poll_once(self, n: int = 0):
        self.online = await self.connected()
        if not self.online:
            self._clock_for = None
            return
        await self.init_clock()
        ev = await self.call("get_event_state", {})
        if isinstance(ev, dict):
            self.update_events(ev)
        if n % 15 == 0 or not self.device_state:
            try:
                ds = await self.method("get_device_state")
                if isinstance(ds, dict):
                    self.update_device_state(ds)
            except (TimeoutError, Refused):
                pass
        if n % 2 == 0:
            try:
                pos = await self.method("scope_get_equ_coord")
                if isinstance(pos, dict) and pos.get("ra") is not None:
                    self.position = (float(pos["ra"]), float(pos["dec"]))
            except (TimeoutError, Refused, ValueError, TypeError):
                pass

    async def init_clock(self, force: bool = False):
        """Uhrzeit, Zeitzone und Standort im Seestar setzen – beim Verbinden und wenn sich der Standort aendert.

        Dasselbe macht die Startsequenz von seestar_alp bzw. die Seestar-App beim Verbinden. Ohne stimmt die
        Lageberechnung nicht und die Firmware lehnt GoTos ab. Bewegt nichts."""
        lat, lon = self.location.get("lat"), self.location.get("lon")
        if lat is None or lon is None or (not force and self._clock_for == (lat, lon)):
            return
        from datetime import datetime as _dt
        now = _dt.now().astimezone()
        try:
            await self.method("pi_set_time", {"year": now.year, "mon": now.month, "day": now.day, "hour": now.hour,
                                              "min": now.minute, "sec": now.second, "time_zone": local_tz_name()})
            await self.method("set_user_location", {"lat": lat, "lon": lon, "force": True})
            self._clock_for = (lat, lon)
            self.note(f"Uhr und Standort gesetzt ({lat:.2f}, {lon:.2f})".replace(".", ","))
        except (TimeoutError, Refused, RuntimeError) as e:
            log.warning("%s: Uhr/Standort setzen: %s", self.id, e)

    def update_events(self, ev: dict):
        self.events = ev
        seeding = not getattr(self, "_events_seen", False)   # alte Fehler beim ersten Abruf nicht melden
        for name, e in ev.items():                   # neue Fehlschlaege der Firmware melden
            if isinstance(e, dict) and e.get("state") == "fail" and (name in OP_EVENTS or name.startswith("Scan")):
                key = (name, e.get("Timestamp"))
                if key not in self._fail_seen:
                    self._fail_seen.add(key)
                    if not seeding:
                        label = OP_EVENTS.get(name) or "Mond/Sonne-Suche"
                        self.alerts.append(f"{self.name}: {label} abgelehnt – {error_de(e.get('error') or 'fehlgeschlagen')}")
        self._events_seen = True
        best = None
        for name in ("ScopeHome", "ScopeMoveToHorizon"):
            e = ev.get(name)
            if isinstance(e, dict) and isinstance(e.get("close"), bool) and (best is None or _stamp(e) > _stamp(best)):
                best = e
        if best is not None:
            key = (best.get("Timestamp"), best.get("state"), best["close"])
            if key != self._arm_seen:               # nur neue Ereignisse ueberschreiben get_device_state
                self._arm_seen = key
                self.arm_closed = best["close"]
        active = self.view_active()
        if active and self.view_since is None:
            self.view_since = time.time()
        elif not active:
            self.view_since = None

    def update_device_state(self, ds: dict):
        self.device_state = ds
        if isinstance(_nav(ds, "mount", "close"), bool):
            self.arm_closed = ds["mount"]["close"]
        self.model = _nav(ds, "device", "product_model") or self.model

    def view_active(self) -> bool:
        v = self.events.get("View") if isinstance(self.events.get("View"), dict) else {}
        return v.get("state") in ("start", "working")

    def live(self) -> dict:
        """Daten fuer das Live-Fenster."""
        e = self.events
        v = e.get("View") if isinstance(e.get("View"), dict) else {}
        st = e.get("Stack") if isinstance(e.get("Stack"), dict) else {}
        exp_ms, exp_us = _nav(e, "Exposure", "exp_ms"), _nav(e, "Exposure", "exp_us")
        exp_s = exp_ms / 1000 if isinstance(exp_ms, (int, float)) else (exp_us / 1e6 if isinstance(exp_us, (int, float)) else None)
        stacking = v.get("stage") == "Stack" or st.get("state") in ("start", "working")
        frames = st.get("stacked_frame") if stacking else None
        return {"active": self.view_active(), "since": self.view_since, "stacking": bool(stacking),
                "mode": VIEW_DE.get(v.get("mode"), v.get("mode") or ""), "stage": STAGE_DE.get(v.get("stage"), v.get("stage") or ""),
                "target": v.get("target_name") or "", "frames": frames, "dropped": st.get("dropped_frame") if stacking else None,
                "exposure_s": exp_s, "total_min": round(frames * exp_s / 60, 1) if frames and exp_s else None,
                "frame": self.frame, "jpeg": f"/api/dev/{self.id}/live.jpg" if self.jpeg else ""}

    # ------------------------------------------------------------ Anzeige
    def _alt_az(self):
        if not self.position or self.location.get("lat") is None:
            return None
        return astro.altaz(*self.position, self.location["lat"], self.location["lon"], datetime.now(timezone.utc))

    def snapshot(self) -> dict:
        d = super().snapshot()
        ds, e = self.device_state, self.events
        battery = _nav(ds, "pi_status", "battery_capacity") or _nav(e, "PiStatus", "battery_capacity")
        charging = (_nav(ds, "pi_status", "charger_status") or _nav(e, "PiStatus", "charger_status") or "")
        vol = _nav(ds, "storage", "storage_volume", 0)
        disk = None
        if isinstance(vol, dict) and vol.get("state") == "mounted":
            disk = vol.get("used_percent")
            if disk is None and vol.get("totalMB") and vol.get("freeMB") is not None:
                disk = round(100 * (1 - vol["freeMB"] / vol["totalMB"]))
        aa = self._alt_az()
        op, res, ok = operations(e)
        arm = {True: "eingeklappt", False: "ausgefahren", None: "?"}[self.arm_closed]
        d.update({
            "sub": f"{self.model or 'Seestar'} · seestar_alp {self.alp.split('//')[-1]}",
            "model": self.model, "arm_closed": self.arm_closed,
            "can": {"goto": True, "move": True, "center": False, "series": False, "rig": False},
            "facts": [
                {"label": "Arm", "value": arm},
                {"label": "Ansicht", "value": op if self.view_active() else "keine"},
                {"label": "RA / Dec (JNow)", "value": fmt_radec(self.position)},
                {"label": "Höhe / Azimut", "value": f"{aa[0]:.1f}° · {aa[1]:.1f}°".replace(".", ",") if aa else "–"},
                {"label": "Akku", "value": (f"{battery} %" + (" · lädt" if str(charging).lower().startswith("charg") else "")) if battery is not None else "–"},
                {"label": "Speicher belegt", "value": f"{disk} %" if disk is not None else "–"},
            ],
            "op": op if self.online else "–",
            "result": self.last_result or res or "–", "result_ok": self.last_result_ok if self.last_result else ok,
            "image": {"title": "Live-Ansicht", "hint": "Standbild alle paar Sekunden",
                      "jpeg": f"/api/dev/{self.id}/live.jpg?f={self.frame}" if self.jpeg and self.view_active() else ""},
            "live": self.live(),
            "actions": [a.as_dict() for a in self.actions()],
        })
        return d

    def actions(self) -> list[Action]:
        closed = self.arm_closed
        off = "" if self.online else "nicht verbunden"
        acts = [
            Action("unfold", "Arm ausfahren", "zum Horizont", confirm=f"{self.name}: Arm ausfahren?", primary=True, disabled=off)
            if closed is not False else
            Action("park", "Parken (Arm einklappen)", "beendet alles", confirm=f"{self.name}: Arm einklappen und parken?",
                   primary=True, disabled=off),
            Action("live", "Live-Ansicht starten", "an der aktuellen Position", disabled=off or ("Arm eingeklappt" if closed else "")),
            Action("stack", "Stacking starten", "nach dem GoTo", primary=True,
                   disabled=off or ("Arm eingeklappt" if closed else "") or ("erst GoTo oder Live-Ansicht" if not self.view_active() else "")),
            Action("moon", "Mond suchen", "Suchlauf am Gerät", disabled=off or ("Arm eingeklappt" if closed else "")),
            Action("sun", "Sonne suchen", "nur mit Sonnenfilter!", confirm="Sonnenfilter aufgesetzt? Ohne Filter nimmt der Sensor Schaden.",
                   disabled=off or ("Arm eingeklappt" if closed else "")),
            Action("stop", "Stopp", "Ansicht, Stack, GoTo", danger=True, disabled=off),
        ]
        return acts

    def preview_jpeg(self) -> bytes | None:
        self.wanted = time.time()
        return self.jpeg

    # ------------------------------------------------------------ Befehle
    def _busy(self) -> bool:
        """Laeuft noch etwas, das ein neues Ziel blockieren wuerde (Ansicht, GoTo mit Wiederholungen, Plan)?"""
        e = self.events
        return (self.view_active() or _nav(e, "ViewPlan", "state") == "working"
                or any(_nav(e, k, "state") in ("start", "working") for k in ("AutoGoto", "PlateSolve", "AutoFocus")))

    def _guard_stack(self, new: str, confirmed: bool):
        """Laufendes Stacking nur mit Bestaetigung in der Station beenden."""
        live = self.live()
        if live["stacking"] and not confirmed:
            frames = f" ({live['frames']} Frames)" if live.get("frames") else ""
            target = live.get("target") or "dem aktuellen Ziel"
            raise NeedsConfirm(f"{self.name}: Stacking läuft{frames} – erst in der Station bestätigen",
                               f"Stacking an {target}{frames} beenden und {new}?")

    async def _end_view(self):
        """Laufende Ansicht/Plan beenden, damit die Firmware nicht am alten Ziel festhaelt."""
        if not self._busy():
            return
        if _nav(self.events, "ViewPlan", "state") == "working":
            await self.call("stop_scheduler", {})
        await self.method("iscope_stop_view")
        # warten, bis die Firmware wirklich frei meldet (Plate-Solving/Autofokus raeumen erst auf)
        for _ in range(30):
            await asyncio.sleep(0.5)
            try:
                ev = await self.call("get_event_state", {})
                if isinstance(ev, dict):
                    self.update_events(ev)
            except (TimeoutError, Refused, RuntimeError, aiohttp.ClientError, asyncio.TimeoutError):
                continue
            if not self._busy():
                break
        else:
            log.warning("%s: Seestar meldet nach 15 s noch nicht frei – neues Ziel trotzdem", self.id)
        await asyncio.sleep(0.5)

    def _need_arm(self):
        if not self.online:
            raise Refused(f"{self.name}: nicht verbunden – läuft seestar_alp?")
        if self.arm_closed:
            raise Refused(f"{self.name}: Arm eingeklappt – erst „Arm ausfahren“.")

    async def goto(self, target: dict) -> str:
        self._need_arm()
        name = target.get("name") or "Ziel"
        kind = target.get("kind")
        self._guard_stack(f"zu {name} wechseln", bool(target.get("confirm")))
        await self._end_view()                       # altes Ziel samt Wiederholversuchen beenden
        if kind == "moon":
            await self.method("iscope_start_view", {"mode": "moon"})
            await self.method("start_scan_planet")
            msg = f"{self.name}: Mond-Suchlauf gestartet"
        elif kind == "planet":
            ra, dec = await self.planet_position(target.get("planet") or name)
            await self.method("iscope_start_view", {"mode": "planet", "target_name": name,
                                                    "target_ra_dec": [ra, dec], "lp_filter": False})
            msg = f"{self.name}: GoTo {name} (Planetenmodus)"
        else:
            if "ra_jnow" in target:
                ra, dec = float(target["ra_jnow"]), float(target["dec_jnow"])
            else:
                ra, dec = astro.j2000_to_date(float(target["ra"]), float(target["dec"]), datetime.now(timezone.utc))
            lp = bool(target.get("lp"))
            await self.method("iscope_start_view", {"mode": "star", "target_ra_dec": [round(ra, 6), round(dec, 5)],
                                                    "target_name": name, "lp_filter": lp})
            msg = f"{self.name}: GoTo {name}" + (" mit LP-Filter" if lp else "")
        self.note(msg.split(": ", 1)[1])
        log.info("%s", msg)
        return msg

    async def planet_position(self, planet: str) -> tuple[float, float]:
        key = PLANETS.get(planet.lower(), planet.lower())
        s = await self._session()
        try:
            async with s.get(f"{self.webui}/getplanetcoordinates?planetname={quote(key + ' barycenter')}") as r:
                pos = parse_planet(await r.text())
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            raise Refused(f"Planetenposition nicht abrufbar ({e.__class__.__name__}) – Web-UI {self.webui} erreichbar?") from None
        if not pos:
            raise Refused(f"Planetenposition für {planet} nicht gefunden")
        return pos

    async def move(self, direction: str, speed: int, on: bool) -> None:
        if self._move_task:
            self._move_task.cancel()
            self._move_task = None
        if not on:
            await self.method("scope_speed_move", {"speed": 0, "angle": 0, "dur_sec": 1})
            return
        self._need_arm()
        angle, spd = ANGLE[direction], SPEEDS[max(0, min(speed, len(SPEEDS) - 1))]

        async def hold():                          # solange gedrueckt: alle 1 s verlaengern, max. 20 s
            try:
                for _ in range(20):
                    await self.method("scope_speed_move", {"speed": spd, "angle": angle, "dur_sec": 2})
                    await asyncio.sleep(1)
                await self.method("scope_speed_move", {"speed": 0, "angle": 0, "dur_sec": 1})
            except asyncio.CancelledError:
                pass
            except Exception as e:
                log.warning("%s: Bewegen: %s", self.id, e)
        self._move_task = asyncio.create_task(hold())

    async def stop(self) -> str:
        if self._move_task:
            self._move_task.cancel()
        if not self.online:
            return f"{self.name}: nicht verbunden"
        if _nav(self.events, "ViewPlan", "state") == "working":
            await self.call("stop_scheduler", {})
        await self.method("iscope_stop_view")
        self.note("gestoppt")
        return f"{self.name}: gestoppt"

    async def action(self, action_id: str, params: dict) -> str:
        if action_id == "stop":
            return await self.stop()
        if not self.online:
            raise Refused(f"{self.name}: nicht verbunden – läuft seestar_alp?")
        if action_id == "unfold":
            await self.init_clock(force=True)
            await self.method("scope_move_to_horizon")
            self.device_state = {}                   # Arm-Stand beim naechsten Abfragen vom Geraet holen
            text = "Arm fährt aus …"
        elif action_id == "park":
            # erst Plan und Ansicht beenden – sonst faehrt die Firmware nach dem Parken ihr letztes Ziel wieder an
            await self._end_view()
            await self.method("scope_park")
            self.device_state = {}
            text = "Ansicht beendet, parkt …"
        elif action_id == "live":
            self._need_arm()
            self._guard_stack("die Live-Ansicht starten", bool(params.get("confirm")))
            await self._end_view()
            await self.method("iscope_start_view", {"mode": "star"})
            text = "Live-Ansicht gestartet"
        elif action_id == "stack":
            self._need_arm()
            await self.call("start_stack", {"restart": True})
            text = "Stacking gestartet"
        elif action_id in ("moon", "sun"):
            self._need_arm()
            self._guard_stack("den Suchlauf starten", bool(params.get("confirm")))
            await self._end_view()
            await self.method("iscope_start_view", {"mode": action_id})
            await self.method("start_scan_planet")
            text = ("Mond" if action_id == "moon" else "Sonnen") + "-Suchlauf gestartet"
        else:
            raise Refused(f"{self.name}: unbekannte Aktion {action_id}")
        self.note(text)
        return f"{self.name}: {text}"


def fmt_radec(pos) -> str:
    if not pos:
        return "–"
    ra, dec = pos
    h = int(ra)
    m = int(round((ra - h) * 60))
    if m == 60:
        h, m = (h + 1) % 24, 0
    return f"{h:02d}h {m:02d}m · {dec:+.1f}°".replace(".", ",")
