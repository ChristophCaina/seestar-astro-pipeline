"""Uebungs-Seestar: tut so, als waeren seestar_alp und ein Seestar da – fuer Tests und Trockenlaeufe.

    python3 station/tools/fake_seestar.py            # Alpaca :5555, Web-UI :5432, Bildserver :7556
    python3 station/tools/fake_seestar.py --offset 10000   # Ports +10000 (laeuft neben einem echten seestar_alp)

Arm, GoTo, Live-Ansicht, Stacking und Stopp verhalten sich grob wie am Geraet; der Bildserver
liefert waehrend einer Ansicht synthetische Sternfeld-Bilder (MJPEG), danach endet der Strom.
Nur fuer Tests – steuert nichts.
"""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import random
import time

from aiohttp import web

try:
    from PIL import Image, ImageDraw
except ImportError:                                    # ohne Pillow: graue Bilder
    Image = None


class FakeScope:
    def __init__(self, model="Seestar S50"):
        self.model = model
        self.arm_closed = True
        self.ra, self.dec = 21.65, -48.6
        self.view = None                               # {"mode", "stage", "target_name", "since"}
        self.stack_started = None
        self.events: dict = {}
        self.calls: list[tuple] = []
        self.frame_no = 0

    def _ev(self, name, **kw):
        self.events[name] = {"Event": name, "Timestamp": f"{time.time():.3f}", **kw}

    def stacked(self) -> int:
        return int((time.time() - self.stack_started) / 10) if self.stack_started else 0

    def event_state(self) -> dict:
        ev = dict(self.events)
        if self.view:
            ev["View"] = {"Event": "View", "Timestamp": f"{self.view['since']:.3f}", "state": "working",
                          "stage": "Stack" if self.stack_started else "ContinuousExposure",
                          "mode": self.view["mode"], "target_name": self.view["target_name"]}
        elif "View" in self.events:
            ev["View"] = self.events["View"]
        if self.stack_started:
            ev["Stack"] = {"Event": "Stack", "Timestamp": f"{time.time():.3f}", "state": "working",
                           "stacked_frame": self.stacked(), "dropped_frame": self.stacked() // 12}
            ev["Exposure"] = {"Event": "Exposure", "exp_ms": 10000}
        ev["PiStatus"] = {"Event": "PiStatus", "battery_capacity": 87, "charger_status": "Discharging", "temp": 31.5}
        return ev

    def method(self, m: str, params):
        self.calls.append((m, params))
        if m == "get_device_state":
            return {"device": {"product_model": self.model, "firmware_ver_string": "9.75"},
                    "mount": {"close": self.arm_closed, "equ_mode": False},
                    "pi_status": {"battery_capacity": 87, "charger_status": "Discharging"},
                    "storage": {"storage_volume": [{"state": "mounted", "totalMB": 51000, "freeMB": 15000}]}}
        if m == "scope_get_equ_coord":
            return {"ra": self.ra, "dec": self.dec}
        if m == "scope_move_to_horizon":
            self.arm_closed = False
            self.ra, self.dec = 2.0, 30.0
            self._ev("ScopeMoveToHorizon", state="complete", close=False, lapse_ms=17000)
            return 0
        if m == "scope_park":
            self.arm_closed, self.view, self.stack_started = True, None, None
            self._ev("ScopeHome", state="complete", close=True, lapse_ms=12000)
            return 0
        if m == "iscope_start_view":
            p = params or {}
            if p.get("target_ra_dec"):
                self.ra, self.dec = p["target_ra_dec"]
                self._ev("AutoGoto", state="complete", lapse_ms=25000)
            self.view = {"mode": p.get("mode", "star"), "target_name": p.get("target_name", ""), "since": time.time()}
            self.stack_started = None
            return 0
        if m == "iscope_stop_view":
            if self.view:
                self.events["View"] = {"Event": "View", "Timestamp": f"{time.time():.3f}", "state": "cancel",
                                       "mode": self.view["mode"], "target_name": self.view["target_name"]}
            if self.stack_started:
                self._ev("Stack", state="cancel", stacked_frame=self.stacked())
            self.view, self.stack_started = None, None
            return 0
        if m in ("scope_speed_move", "start_scan_planet"):
            return 0
        return 0

    def action(self, name: str, params: dict):
        if name == "get_event_state":
            return self.event_state()
        if name == "method_sync":
            res = self.method(params.get("method"), params.get("params"))
            return {"method": params.get("method"), "result": res, "code": 0}
        if name == "start_stack":
            self.calls.append(("start_stack", params))
            if not self.view:
                return {"code": -1, "result": "no view"}
            self.stack_started = time.time()
            return {"code": 0, "result": 0}
        if name == "stop_scheduler":
            return {"code": 0}
        return {"code": 0}

    def jpeg(self) -> bytes:
        self.frame_no += 1
        if Image is None:
            return b""
        rnd = random.Random(42)
        im = Image.new("RGB", (540, 960), (6, 8, 14))
        dr = ImageDraw.Draw(im)
        n = min(400, 60 + self.stacked() * 20) if self.stack_started else 120
        for _ in range(n):
            x, y, r = rnd.randrange(540), rnd.randrange(960), rnd.choice((1, 1, 1, 2, 3))
            g = rnd.randrange(120, 255)
            dr.ellipse((x - r, y - r, x + r, y + r), fill=(g, g, min(255, g + 20)))
        cx, cy = 270, 480
        for i in range(30, 0, -1):                   # "Galaxie"
            a = int(8 * i)
            dr.ellipse((cx - 6 * i, cy - 2 * i, cx + 6 * i, cy + 2 * i), fill=(a // 3 + 20, a // 3 + 18, a // 4 + 15))
        dr.text((12, 930), f"Uebungs-Seestar · Bild {self.frame_no}", fill=(160, 160, 160))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=80)
        return buf.getvalue()


def make_apps(scope: FakeScope):
    alpaca = web.Application()

    async def action(request):
        b = await request.json()
        params = json.loads(b.get("Parameters") or "{}")
        return web.json_response({"Value": scope.action(b["Action"], params), "ErrorNumber": 0, "ErrorMessage": ""})

    async def connected(_):
        return web.json_response({"Value": True, "ErrorNumber": 0})
    alpaca.router.add_put("/api/v1/telescope/1/action", action)
    alpaca.router.add_get("/api/v1/telescope/1/connected", connected)

    webui = web.Application()

    async def planet(request):
        name = request.query.get("planetname", "")
        pos = {"saturn barycenter": "23h 25m 10.00s, -05deg 12' 30.0\"",
               "jupiter barycenter": "07h 48m 00.00s, +21deg 03' 00.0\""}.get(name.lower())
        return web.Response(text=pos or "Not found")

    async def root(_):
        return web.Response(text="<html>Uebungs-seestar_alp</html>", content_type="text/html")
    webui.router.add_get("/getplanetcoordinates", planet)
    webui.router.add_get("/", root)

    imaging = web.Application()

    async def vid(request):
        resp = web.StreamResponse(headers={"Content-Type": "multipart/x-mixed-replace; boundary=frame"})
        await resp.prepare(request)

        async def part(data, ctype="image/jpeg"):
            await resp.write(b"\r\n--frame\r\nContent-Type: " + ctype.encode() + b"\r\n\r\n" + data)
        try:
            img = scope.jpeg()
            await part(img)
            await part(img)                          # seestar_alp schickt jedes Bild doppelt
            while scope.view:
                await asyncio.sleep(1.0)
                img = scope.jpeg()
                await part(img)
                await part(img)
            await part(scope.jpeg())                 # "Idle"-Bild, dann Ende wie bei seestar_alp
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        return resp
    imaging.router.add_get("/1/vid", vid)
    return alpaca, webui, imaging


async def serve(scope: FakeScope, offset: int = 0, host: str = "127.0.0.1"):
    runners = []
    for app, port in zip(make_apps(scope), (5555, 5432, 7556)):
        r = web.AppRunner(app)
        await r.setup()
        await web.TCPSite(r, host, port + offset).start()
        runners.append(r)
    return runners


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--model", default="Seestar S50")
    a = ap.parse_args()

    async def run():
        await serve(FakeScope(a.model), a.offset)
        print(f"Übungs-Seestar: Alpaca :{5555 + a.offset}, Web-UI :{5432 + a.offset}, Bilder :{7556 + a.offset}", flush=True)
        while True:
            await asyncio.sleep(3600)
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
