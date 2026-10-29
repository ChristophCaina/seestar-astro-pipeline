"""Astro-Station: eine Oberflaeche fuer alle Geraete einer Session (Seestar, INDI-Montierung mit Kamera ...).

    python3 -m astro_station.server [--config ~/.config/astro-station.toml]
    -> http://localhost:8070

Nichts startet von selbst: GoTo, Arm ausfahren, Stacking, Serien nur auf Knopfdruck (oder Strg+1 in Stellarium).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

from aiohttp import web

from . import config as C
from .devices import Device, Refused, make
from .devices.seestar import Seestar
from .stellarium import Bridge
from .tonight import PLANETS_DE, Sky

log = logging.getLogger("astro_station")
WEB = Path(__file__).with_name("web")
STATE_DIR = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "astro-station"


def desktop_notify(text: str):
    """Meldung auch als Desktop-Benachrichtigung – wichtig, wenn man gerade in Stellarium ist."""
    if shutil.which("notify-send") and any(os.environ.get(k) for k in ("DISPLAY", "WAYLAND_DISPLAY", "DBUS_SESSION_BUS_ADDRESS")):
        try:
            subprocess.Popen(["notify-send", "-a", "Astro-Station", "-i", "applications-science", "Astro-Station", text],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            pass


class Station:
    def __init__(self, cfg: dict, notify=desktop_notify):
        self.cfg = cfg
        self.devices: list[Device] = [make(d) for d in cfg["device"]]
        self.by_id = {d.id: d for d in self.devices}
        self.sky = Sky(cfg.get("catalog", {}).get("paths") or None)
        self.location = C.location(cfg)
        self.events: list[dict] = []
        self.planets: dict = {}
        self._planets_at = 0.0
        self.bridges: dict[str, Bridge] = {}
        self.notify = notify
        self._tasks: list[asyncio.Task] = []
        self._session_file = STATE_DIR / "session.json"
        try:
            for k, v in json.loads(self._session_file.read_text()).items():
                if k in self.by_id:
                    self.by_id[k].enabled = bool(v)
        except (OSError, ValueError):
            pass
        for d in self.devices:
            d.location = self.location

    # ------------------------------------------------------------ Meldungen
    def say(self, text: str, kind: str = "info", desktop: bool = False):
        self.events.append({"id": (self.events[-1]["id"] + 1) if self.events else 1, "t": time.time(),
                            "text": text, "kind": kind})
        del self.events[:-50]
        if desktop:
            self.notify(text)

    # ------------------------------------------------------------ Lebenszyklus
    async def start(self):
        for d in self.devices:
            if d.enabled:
                await d.start()
            if d.stellarium_port:
                b = Bridge(d, d.stellarium_port, self.stellarium_goto)
                try:
                    await b.start()
                    self.bridges[d.id] = b
                except OSError as e:
                    log.warning("Stellarium-Port %s: %s", d.stellarium_port, e)
        self._tasks = [asyncio.create_task(self._background())]

    async def close(self):
        for t in self._tasks:
            t.cancel()
        for b in self.bridges.values():
            await b.close()
        for d in self.devices:
            await d.close()

    async def _background(self):
        while True:
            try:
                loc = C.location(self.cfg)
                if loc != self.location:
                    self.location = loc
                    for d in self.devices:
                        d.location = loc
                await self._refresh_planets()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.debug("Hintergrund: %s", e)
            await asyncio.sleep(60)

    async def _refresh_planets(self, force: bool = False):
        if not force and time.time() - self._planets_at < 1800:
            return
        scope = next((d for d in self.devices if isinstance(d, Seestar) and d.enabled and d.online), None)
        if not scope:
            return
        found = {}
        for p in PLANETS_DE:
            try:
                found[p] = await scope.planet_position(p)
            except Refused:
                pass
        if found:
            self.planets, self._planets_at = found, time.time()

    async def set_enabled(self, dev_id: str, on: bool) -> str:
        d = self.dev(dev_id)
        if d.enabled == on:
            return ""
        d.enabled = on
        if on:
            await d.start()
        else:
            await d.close()
            d.online = False
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        self._session_file.write_text(json.dumps({x.id: x.enabled for x in self.devices}))
        return f"{d.name}: {'eingeschaltet' if on else 'ausgeschaltet'}"

    def dev(self, dev_id: str) -> Device:
        d = self.by_id.get(dev_id)
        if not d:
            raise web.HTTPNotFound(text=f"Gerät {dev_id} unbekannt")
        return d

    async def stellarium_goto(self, d: Device, ra: float, dec: float):
        try:
            msg = await d.goto_jnow(ra, dec, "Stellarium-Ziel")
            self.say(msg + " (aus Stellarium)", desktop=True)
        except Refused as e:
            self.say(str(e), "warn", desktop=True)
        except Exception as e:
            self.say(f"{d.name}: GoTo aus Stellarium fehlgeschlagen – {e}", "warn", desktop=True)

    def state(self) -> dict:
        loc = self.location
        sky = self.sky.summary(loc["lat"], loc["lon"]) if loc["lat"] is not None else None
        return {"now": datetime.now().astimezone().strftime("%H:%M"), "location": loc, "sky": sky,
                "devices": [d.snapshot() | {"stellarium_clients": self.bridges[d.id].clients if d.id in self.bridges else 0}
                            for d in self.devices],
                "events": self.events[-10:]}


STATION = web.AppKey("station", Station)


def make_app(cfg: dict, station: Station | None = None) -> web.Application:
    st = station or Station(cfg)
    app = web.Application()
    app[STATION] = st

    @web.middleware
    async def errors(request, handler):
        try:
            return await handler(request)
        except web.HTTPException:
            raise
        except Refused as e:
            st.say(str(e), "warn")
            return web.json_response({"ok": False, "error": str(e)}, status=409)
        except (RuntimeError, ValueError, KeyError, TimeoutError, ConnectionError, OSError, asyncio.TimeoutError) as e:
            msg = str(e) or e.__class__.__name__
            log.warning("%s: %s", request.path, msg)
            st.say(msg, "warn")
            return web.json_response({"ok": False, "error": msg}, status=409)
    app.middlewares.append(errors)

    async def body(request) -> dict:
        try:
            return await request.json()
        except (json.JSONDecodeError, ValueError):
            return {}

    def ok(msg: str = ""):
        if msg:
            st.say(msg)
        return web.json_response({"ok": True, "message": msg})

    async def index(_):
        return web.FileResponse(WEB / "index.html", headers={"Cache-Control": "no-cache"})

    async def state(_):
        return web.json_response(st.state())

    async def tonight(request):
        loc = st.location
        if loc["lat"] is None:
            return web.json_response([])
        await st._refresh_planets()
        return web.json_response(st.sky.tonight(loc["lat"], loc["lon"], st.planets))

    async def search(request):
        loc = st.location
        return web.json_response(st.sky.search(request.query.get("q", ""), loc["lat"], loc["lon"]))

    async def session(request):
        b = await body(request)
        return ok(await st.set_enabled(b["id"], bool(b.get("enabled", True))))

    async def goto(request):
        d = st.dev(request.match_info["id"])
        b = await body(request)
        t = b.get("target") or b
        if b.get("center"):
            t = {**t, "center": True}
        return ok(await d.goto(t))

    async def move(request):
        d = st.dev(request.match_info["id"])
        b = await body(request)
        await d.move(str(b.get("dir", "N")).upper()[:1], int(b.get("speed", 1)), bool(b.get("on", True)))
        return ok()

    async def action(request):
        d = st.dev(request.match_info["id"])
        b = await body(request)
        return ok(await d.action(b["action"], b.get("params") or {}))

    async def stop_all(_):
        msgs = []
        for d in st.devices:
            if d.enabled:
                try:
                    msgs.append(await d.stop())
                except Exception as e:
                    msgs.append(f"{d.name}: Stopp fehlgeschlagen – {e}")
        return ok(" · ".join(msgs) or "Kein Gerät aktiv")

    async def preview(request):
        d = st.dev(request.match_info["id"])
        data = d.preview_jpeg() if hasattr(d, "preview_jpeg") else None
        if not data:
            raise web.HTTPNotFound()
        return web.Response(body=data, content_type="image/jpeg", headers={"Cache-Control": "no-store"})

    async def live(_):
        """Fuer das Live-Fenster: alle eingeschalteten Seestars mit Bildserver-Adresse und Stack-Zahlen."""
        return web.json_response([{"id": d.id, "name": d.name, "online": d.online, **d.live()}
                                  for d in st.devices if isinstance(d, Seestar) and d.enabled])

    r = app.router
    r.add_get("/", index)
    r.add_get("/api/state", state)
    r.add_get("/api/tonight", tonight)
    r.add_get("/api/search", search)
    r.add_get("/api/live", live)
    r.add_post("/api/session", session)
    r.add_post("/api/stop_all", stop_all)
    r.add_post("/api/dev/{id}/goto", goto)
    r.add_post("/api/dev/{id}/move", move)
    r.add_post("/api/dev/{id}/action", action)
    r.add_get("/api/dev/{id}/preview.jpg", preview)
    r.add_static("/static", WEB, show_index=False)

    async def on_start(_):
        await st.start()

    async def on_stop(_):
        await st.close()
    app.on_startup.append(on_start)
    app.on_cleanup.append(on_stop)
    return app


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(C.DEFAULT_PATH))
    ap.add_argument("--port", type=int)
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO, format="%(asctime)s %(name)s %(message)s")
    cfg = C.load(a.config)
    if a.port:
        cfg["web"]["port"] = a.port
    print(f"Astro-Station: http://{cfg['web']['host']}:{cfg['web']['port']}", flush=True)
    web.run_app(make_app(cfg), host=cfg["web"]["host"], port=int(cfg["web"]["port"]), print=None)


if __name__ == "__main__":
    main()
