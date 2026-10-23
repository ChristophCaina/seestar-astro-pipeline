"""Astro-Pi: Web-App + REST/WebSocket ueber INDI.

    python3 -m astro_pi.server --config /etc/astro-pi.toml

Alle Aktionen (GoTo, Serie, Parken ...) kommen nur aus der Web-App – Home Assistant
bekommt per MQTT ausschliesslich Status und Vorschau.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
import tomllib
from pathlib import Path

from aiohttp import WSMsgType, web

from .control import Controller, Settings
from .indi import IndiClient
from .mqtt import Mqtt
from .targets import Targets
from . import wide as W

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "pi" / "web"
log = logging.getLogger("astro_pi")

CTL = web.AppKey("ctl", Controller)
INDI = web.AppKey("indi", IndiClient)
MQTT = web.AppKey("mqtt", Mqtt)
TASKS = web.AppKey("tasks", list)

DEFAULTS = {
    "indi": {"host": "127.0.0.1", "port": 7624, "mount": "", "camera": ""},
    "capture": {"dir": "/data/captures", "filter": "DSLR", "device_id": "eos6d"},
    "location": {"lat": None, "lon": None},
    "solve": {"solve_field": "solve-field", "scale_low": 0.5, "scale_high": 6.0},
    "catalog": {"paths": [str(ROOT / "siril" / "data" / "NGC.csv"), str(ROOT / "siril" / "data" / "addendum.csv")]},
    "web": {"host": "0.0.0.0", "port": 8080, "url": ""},
    "mqtt": {"host": "", "port": 1883, "user": "", "password": "", "topic": "astro_pi", "interval": 10},
    "camera": {"name": "Canon EOS 6D", "width_mm": 35.8, "height_mm": 23.9, "width_px": 5472, "height_px": 3648},
    "wide": {"filter": "WIDE", "lenses": []},          # lenses: [{name, focal, aperture}], leer = Standardliste
}


def load_config(path: str | None) -> dict:
    cfg = json.loads(json.dumps(DEFAULTS))
    if path and Path(path).exists():
        with open(path, "rb") as f:
            for sec, vals in tomllib.load(f).items():
                cfg.setdefault(sec, {}).update(vals)
    return cfg


def json_ok(data=None, **kw):
    return web.json_response({"ok": True, **(data or {}), **kw})


def make_app(cfg: dict, indi: IndiClient | None = None) -> web.Application:
    indi = indi or IndiClient(cfg["indi"]["host"], int(cfg["indi"]["port"]))
    s = Settings(capture_dir=Path(cfg["capture"]["dir"]), device_id=cfg["capture"]["device_id"],
                 filter_name=cfg["capture"]["filter"], mount=cfg["indi"]["mount"], camera=cfg["indi"]["camera"],
                 solve_field=cfg["solve"]["solve_field"], scale_low=float(cfg["solve"]["scale_low"]),
                 scale_high=float(cfg["solve"]["scale_high"]),
                 lat=cfg["location"]["lat"], lon=cfg["location"]["lon"], wide_filter=cfg["wide"]["filter"])
    ctl = Controller(indi, s)
    sensor = W.Sensor(**cfg["camera"])
    lenses = [W.Lens(**x) for x in cfg["wide"]["lenses"]] or list(W.DEFAULT_LENSES)
    ctl.wide = {"lens": None, "aperture": None, "tracked": True}
    targets = Targets([Path(p) for p in cfg["catalog"]["paths"]])
    mq = Mqtt(cfg["mqtt"]["host"], int(cfg["mqtt"]["port"]), cfg["mqtt"]["user"], cfg["mqtt"]["password"],
              cfg["mqtt"]["topic"], url=cfg["web"]["url"])

    app = web.Application(client_max_size=4 * 1024 * 1024)
    app[CTL], app[INDI], app[MQTT] = ctl, indi, mq

    @web.middleware
    async def errors(request, handler):
        try:
            return await handler(request)
        except web.HTTPException:
            raise
        except (RuntimeError, ValueError, KeyError, ConnectionError, asyncio.TimeoutError) as e:
            msg = str(e) or e.__class__.__name__
            return web.json_response({"ok": False, "error": msg}, status=409)
    app.middlewares.append(errors)

    async def body(request) -> dict:
        try:
            return await request.json()
        except json.JSONDecodeError:
            return {}

    # ------------------------------------------------------------ API
    async def state(_):
        return web.json_response(ctl.state())

    async def search(request):
        q = request.query.get("q", "")
        return web.json_response(targets.search(q, int(request.query.get("limit", 20)), ctl.altitude,
                                                wide=request.query.get("wide") in ("1", "true")))

    # ------------------------------------------------------------ Weitwinkel
    async def wide_info(_):
        return web.json_response({"sensor": sensor.__dict__ | {"pixel_um": round(sensor.pixel_um, 2)},
                                  "lenses": [x.__dict__ for x in lenses],
                                  "presets": [x.__dict__ for x in W.DEFAULT_PRESETS], "active": ctl.wide})

    async def wide_calc(request):
        q = request.query
        dec = float(q.get("dec") or (ctl.state()["mount"]["dec"] or 0))
        return web.json_response(W.calc(float(q["focal"]), float(q["aperture"]), dec, sensor).as_dict())

    async def wide_lens(request):
        b = await body(request)
        focal, ap = float(b["focal"]), float(b.get("aperture") or 2.8)
        c = W.calc(focal, ap, 0, sensor)
        ctl.wide = {"lens": b.get("name") or f"{focal:g} mm", "focal": focal, "aperture": ap,
                    "tracked": bool(b.get("tracked", True))}
        ctl.solve_scale = (round(c.pixscale * 0.8, 2), round(c.pixscale * 1.25, 2))
        ctl.publish("state")
        return json_ok({"calc": c.as_dict()})

    async def goto(request):
        b = await body(request)
        if "name" in b and "ra" not in b:
            hit = targets.search(b["name"], 1)
            if not hit:
                raise ValueError(f"Ziel '{b['name']}' nicht gefunden")
            b.update(ra=hit[0]["ra"], dec=hit[0]["dec"], name=hit[0]["name"])
        ra, dec = float(b["ra"]), float(b["dec"])
        if b.get("center"):
            asyncio.create_task(_bg(ctl.center(ra, dec, b.get("name", ""), float(b.get("exposure", 5)))))
        else:
            await ctl.goto(ra, dec, b.get("name", ""))
        return json_ok()

    async def _bg(coro):
        try:
            r = await coro
            ctl.publish("done", result=r)
        except Exception as e:
            ctl.publish("error", message=str(e))

    async def park(request):
        await ctl.park((await body(request)).get("park", True))
        return json_ok()

    async def track(request):
        await ctl.track(bool((await body(request)).get("on", True)))
        return json_ok()

    async def move(request):
        b = await body(request)
        if "rate" in b:
            await ctl.slew_rate(int(b["rate"]))
        if "dir" in b:
            await ctl.move(b["dir"], bool(b.get("on", True)))
        return json_ok()

    async def abort(_):
        await ctl.abort()
        return json_ok()

    async def capture(request):
        b = await body(request)
        if b.get("iso"):
            await ctl.set_iso(str(b["iso"]))
        r = await ctl.capture(float(b.get("exposure", 2)), b.get("target", ""), save=bool(b.get("save", False)))
        return json_ok(r)

    async def series(request):
        b = await body(request)
        if b.get("stop"):
            await ctl.stop_series()
        else:
            await ctl.start_series(float(b["exposure"]), int(b.get("count", 0)), float(b.get("delay", 2)),
                                   b.get("target", ""), str(b.get("iso", "")), b.get("type", "Light"),
                                   b.get("mode", "deep"))
        return json_ok()

    async def live(request):
        await ctl.live(bool((await body(request)).get("on", True)))
        return json_ok()

    async def solve(request):
        b = await body(request)
        r = await (ctl.solve_and_sync() if b.get("sync") else ctl.solve_last())
        return json_ok(r)

    async def preview_jpg(_):
        if not ctl.preview:
            raise web.HTTPNotFound()
        return web.Response(body=ctl.preview["jpeg"], content_type="image/jpeg",
                            headers={"Cache-Control": "no-store"})

    async def live_jpg(_):
        if not ctl.live_frame:
            raise web.HTTPNotFound()
        return web.Response(body=ctl.live_frame, content_type="image/jpeg", headers={"Cache-Control": "no-store"})

    async def ws(request):
        sock = web.WebSocketResponse(heartbeat=20)
        await sock.prepare(request)
        q = ctl.subscribe()
        await sock.send_json({"type": "state", "state": ctl.state()})
        last_state = 0.0

        async def reader():
            async for msg in sock:
                if msg.type == WSMsgType.ERROR:
                    break
        rtask = asyncio.create_task(reader())
        try:
            while not sock.closed:
                try:
                    ev = await asyncio.wait_for(q.get(), 5)
                except asyncio.TimeoutError:
                    ev = None
                now = time.monotonic()
                if ev is None or ev.kind == "state":
                    if ev is None or now - last_state > 0.25:      # Zustand gedrosselt
                        await sock.send_json({"type": "state", "state": ctl.state()})
                        last_state = now
                elif ev.kind == "live":
                    await sock.send_json({"type": "live", "t": now})
                else:
                    await sock.send_json({"type": ev.kind, **ev.data})
        except (ConnectionResetError, RuntimeError):
            pass
        finally:
            ctl.unsubscribe(q)
            rtask.cancel()
        return sock

    async def index(_):
        return web.FileResponse(WEB / "index.html", headers={"Cache-Control": "no-cache"})

    r = app.router
    r.add_get("/", index)
    r.add_get("/api/state", state)
    r.add_get("/api/targets", search)
    r.add_post("/api/mount/goto", goto)
    r.add_post("/api/mount/park", park)
    r.add_post("/api/mount/track", track)
    r.add_post("/api/mount/move", move)
    r.add_post("/api/abort", abort)
    r.add_get("/api/wide", wide_info)
    r.add_get("/api/wide/calc", wide_calc)
    r.add_post("/api/wide/lens", wide_lens)
    r.add_post("/api/camera/capture", capture)
    r.add_post("/api/camera/series", series)
    r.add_post("/api/camera/live", live)
    r.add_post("/api/solve", solve)
    r.add_get("/api/preview.jpg", preview_jpg)
    r.add_get("/api/live.jpg", live_jpg)
    r.add_get("/ws", ws)
    r.add_static("/", WEB, show_index=False)

    # ------------------------------------------------------------ Hintergrund
    async def mqtt_loop():
        await mq.discovery()
        q = ctl.subscribe()
        last = 0.0
        sent_preview = None
        while True:
            try:
                await asyncio.wait_for(q.get(), float(cfg["mqtt"]["interval"]))
            except asyncio.TimeoutError:
                pass
            if time.monotonic() - last >= 2:
                await mq.pub("state", json.dumps(ctl.state(), default=str))
                last = time.monotonic()
            if ctl.preview and ctl.preview.get("time") != sent_preview:
                sent_preview = ctl.preview["time"]
                await mq.pub("preview", ctl.preview["jpeg"])

    async def start_bg(app_):
        app_[TASKS] = [asyncio.create_task(indi.run()), asyncio.create_task(ctl.setup_devices())]
        if mq.enabled:
            app_[TASKS].append(asyncio.create_task(mqtt_loop()))

    async def stop_bg(app_):
        await ctl.stop_series()
        if mq.enabled:
            await mq.pub("online", "offline")
        for t in app_[TASKS]:
            t.cancel()

    app.on_startup.append(start_bg)
    app.on_cleanup.append(stop_bg)
    return app


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="/etc/astro-pi.toml")
    ap.add_argument("--port", type=int)
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO, format="%(asctime)s %(name)s %(message)s")
    cfg = load_config(a.config)
    if a.port:
        cfg["web"]["port"] = a.port
    web.run_app(make_app(cfg), host=cfg["web"]["host"], port=int(cfg["web"]["port"]), print=None)


if __name__ == "__main__":
    main()
