"""Astro-Station: Astronomie, Stellarium-Protokoll, MJPEG, Seestar-Adapter und Web-API gegen den Uebungs-Seestar."""
from __future__ import annotations

import asyncio
import random
import socket
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "station"))
sys.path.insert(0, str(ROOT / "station" / "tools"))

from astro_station import astro, config, stellarium  # noqa: E402
from astro_station.livestream import MjpegParser  # noqa: E402

try:
    import aiohttp  # noqa: F401
    from aiohttp import web
    HAVE_AIOHTTP = True
except ImportError:
    HAVE_AIOHTTP = False

LAT, LON = 50.0, 10.0
NOW = datetime(2026, 10, 29, 21, 15, tzinfo=timezone.utc)


def free_offset() -> int:
    for _ in range(50):
        off = random.randrange(20000, 50000, 10)
        ok = True
        for p in (5555, 5432, 7556):
            with socket.socket() as s:
                try:
                    s.bind(("127.0.0.1", p + off))
                except OSError:
                    ok = False
        if ok:
            return off
    raise RuntimeError("kein freier Port")


class AstroTest(unittest.TestCase):
    def test_night_times(self):
        n = astro.night(LAT, LON, NOW)
        dusk, dawn = n.astro
        self.assertAlmostEqual((dusk - datetime(2026, 10, 29, 17, 51, tzinfo=timezone.utc)).total_seconds(), 0, delta=240)
        self.assertAlmostEqual((dawn - datetime(2026, 10, 30, 4, 17, tzinfo=timezone.utc)).total_seconds(), 0, delta=240)
        self.assertLess(n.nautical[0], dusk)

    def test_precession_roundtrip(self):
        ra, dec = astro.j2000_to_date(0.71232, 41.2691, NOW)       # M 31
        self.assertAlmostEqual(ra, 0.7369, delta=0.0015)
        self.assertAlmostEqual(dec, 41.416, delta=0.01)
        ra2, dec2 = astro.date_to_j2000(ra, dec, NOW)
        self.assertAlmostEqual(ra2, 0.71232, places=5)
        self.assertAlmostEqual(dec2, 41.2691, places=4)

    def test_visibility_and_moon(self):
        n = astro.night(LAT, LON, NOW)
        v = astro.visibility(2.37595, 42.3491, LAT, LON, n, NOW)    # NGC 891
        self.assertGreater(v["hours"], 8)
        self.assertGreater(v["peak_alt"], 80)
        illum, _ = astro.moon_phase(NOW)
        self.assertTrue(0 <= illum <= 1)

    def test_tonight_list(self):
        from astro_station.tonight import Sky
        sky = Sky(["/nicht/da.csv"])
        out = sky.tonight(LAT, LON, {"Saturn": (23.42, -5.2)}, now=NOW)
        names = [t["short"] for t in out]
        self.assertIn("Mond", names)
        self.assertTrue(any(t["kind"] == "planet" for t in out))
        self.assertTrue(all("window" in t and "peak" in t for t in out))
        self.assertTrue(sky.search("saturn", LAT, LON, now=NOW))
        self.assertEqual(sky.search("NGC 891", LAT, LON, now=NOW)[0]["short"], "NGC 891")


class ProtocolTest(unittest.TestCase):
    def test_stellarium_roundtrip(self):
        msg = stellarium.encode_position(5.5, -12.25)
        self.assertEqual(len(msg), 24)
        goto = stellarium.GOTO.pack(20, 0, 0, int(5.5 / 24 * 2 ** 32), int(-12.25 / 90 * 2 ** 30))
        ra, dec = stellarium.decode_goto(goto)
        self.assertAlmostEqual(ra, 5.5, places=6)
        self.assertAlmostEqual(dec, -12.25, places=6)

    def test_mjpeg_parser(self):
        b = b"\r\n--frame\r\n"
        data = (b + b"Content-Type: image/gif\r\n\r\nGIF" + b + b"Content-Type: image/jpeg\r\n\r\n\xff\xd8A\xff\xd9" + b)
        p = MjpegParser()
        parts = p.feed(data[:17]) + p.feed(data[17:])
        self.assertEqual([c for c, _ in parts], ["image/gif", "image/jpeg"])
        self.assertEqual(parts[1][1], b"\xff\xd8A\xff\xd9")

    def test_config_location(self):
        cfg = config.load(ROOT / "station" / "astro-station.toml.example")
        self.assertEqual([d["id"] for d in cfg["device"]], ["s50", "iexos"])
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as f:
            f.write("[seestar_initialization]\nlat = 47.5\nlong = 7.25\n")
        cfg["location"] = {"alp_config": f.name}
        self.assertEqual(config.location(cfg), {"lat": 47.5, "lon": 7.25, "source": "seestar_alp"})


class FitsCardsTest(unittest.TestCase):
    def test_add_cards(self):
        try:
            sys.path.insert(0, str(ROOT / "pi"))
            from astro_pi import image
        except ImportError:
            self.skipTest("numpy/PIL fehlen")
        hdr = "".join(c.ljust(80) for c in ["SIMPLE  =                    T", "BITPIX  =                    8",
                                             "NAXIS   =                    2", "NAXIS1  =                    4",
                                             "NAXIS2  =                    2", "OBJECT  = 'alt'", "END"])
        data = hdr.encode().ljust(2880) + bytes(range(8)).ljust(2880, b"\0")
        out = image.add_cards(data, {"OBJECT": "M 31", "FOCALLEN": 400.0, "TELESCOP": "iEXOS-100"})
        h, arr = image.read_fits(out)
        self.assertEqual((h["OBJECT"], h["FOCALLEN"], h["TELESCOP"]), ("M 31", 400.0, "iEXOS-100"))
        self.assertEqual(arr.shape, (2, 4))
        self.assertEqual(len(out) % 2880, 0)


@unittest.skipUnless(HAVE_AIOHTTP, "aiohttp fehlt")
class SeestarTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import fake_seestar
        from astro_station.devices.seestar import Seestar
        self.off = free_offset()
        self.scope = fake_seestar.FakeScope()
        self.runners = await fake_seestar.serve(self.scope, self.off)
        o = self.off
        self.dev = Seestar("s50", "Seestar S50", alp=f"http://127.0.0.1:{5555 + o}", imaging=f"http://127.0.0.1:{7556 + o}",
                           webui=f"http://127.0.0.1:{5432 + o}")
        self.dev.location = {"lat": LAT, "lon": LON}

    async def asyncTearDown(self):
        await self.dev.close()
        for r in self.runners:
            await r.cleanup()

    async def test_arm_gating_goto_stack_stop(self):
        from astro_station.devices import Refused
        await self.dev.poll_once(0)
        self.assertTrue(self.dev.online)
        self.assertTrue(self.dev.arm_closed)
        with self.assertRaises(Refused):
            await self.dev.goto({"name": "NGC 891", "ra": 2.37595, "dec": 42.3491})
        await self.dev.action("unfold", {})
        await self.dev.poll_once(1)
        await self.dev.goto({"name": "NGC 7000", "ra": 20.98, "dec": 44.33, "lp": True})
        m, p = self.scope.calls[-1]
        self.assertEqual(m, "iscope_start_view")
        self.assertEqual(p["mode"], "star")
        self.assertTrue(p["lp_filter"])
        self.assertGreater(p["target_ra_dec"][0], 20.98)             # praezediert (JNow)
        await self.dev.poll_once(1)
        n = len(self.scope.calls)
        await self.dev.goto({"name": "M 31", "ra": 0.71232, "dec": 41.2691})           # neues Ziel: alte Ansicht zuerst beenden
        new = [m for m, _ in self.scope.calls[n:]]
        self.assertEqual(new[:2], ["iscope_stop_view", "iscope_start_view"])
        await self.dev.action("stack", {})
        await self.dev.poll_once(1)
        from astro_station.devices import NeedsConfirm
        with self.assertRaises(NeedsConfirm):                       # Stacking nur mit Bestaetigung beenden
            await self.dev.goto({"name": "M 33", "ra": 1.56, "dec": 30.66})
        with self.assertRaises(NeedsConfirm):                       # Stellarium kann nicht bestaetigen
            await self.dev.goto_jnow(1.6, 30.8)
        snap = self.dev.snapshot()
        self.assertTrue(snap["live"]["active"])
        self.assertTrue(snap["live"]["stacking"])
        self.assertIn("Stacking", snap["op"])
        await self.dev.action("park", {})
        calls = [m for m, _ in self.scope.calls]
        self.assertLess(calls.index("iscope_stop_view"), calls.index("scope_park"))   # erst Ansicht beenden
        await self.dev.poll_once(2)
        self.assertFalse(self.dev.view_active())

    async def test_clock_on_connect_and_firmware_alert(self):
        await self.dev.poll_once(0)
        sent = [m for m, _ in self.scope.calls]
        self.assertIn("pi_set_time", sent)
        self.assertIn("set_user_location", sent)
        n = len(self.scope.calls)
        await self.dev.poll_once(1)                                   # nur einmal pro Standort
        self.assertNotIn("pi_set_time", [m for m, _ in self.scope.calls[n:]])
        self.scope._ev("AutoGoto", state="fail", error="below horizon", code=270)
        await self.dev.poll_once(2)
        self.assertTrue(self.dev.alerts and "Horizont" in self.dev.alerts[0])
        await self.dev.action("unfold", {})
        self.assertTrue(self.dev.arm_closed is None or self.dev.arm_closed is True)   # erst die Meldung zaehlt
        await self.dev.poll_once(3)
        self.assertFalse(self.dev.arm_closed)

    async def test_shutdown_ends_view_first(self):
        await self.dev.poll_once(0)
        await self.dev.action("unfold", {})
        await self.dev.poll_once(1)
        await self.dev.action("live", {})
        await self.dev.poll_once(2)
        await self.dev.action("shutdown", {})
        calls = [m for m, _ in self.scope.calls]
        self.assertLess(calls.index("iscope_stop_view"), calls.index("pi_shutdown"))
        self.assertIn("shutdown", [a["id"] for a in self.dev.snapshot()["actions"]])

    async def test_planet_and_move(self):
        await self.dev.poll_once(0)
        await self.dev.action("unfold", {})
        await self.dev.poll_once(1)
        ra, dec = await self.dev.planet_position("Saturn")
        self.assertAlmostEqual(ra, 23 + 25 / 60 + 10 / 3600, places=4)
        self.assertLess(dec, 0)
        await self.dev.goto({"name": "Saturn", "kind": "planet", "planet": "Saturn"})
        self.assertEqual(self.scope.calls[-1][1]["mode"], "planet")
        await self.dev.move("N", 2, True)
        await asyncio.sleep(0.2)
        await self.dev.move("N", 2, False)
        moves = [p for m, p in self.scope.calls if m == "scope_speed_move"]
        self.assertEqual((moves[0]["angle"], moves[0]["speed"]), (90, 800))
        self.assertEqual(moves[-1]["speed"], 0)

    async def test_snapshot_instead_of_stream(self):
        await self.dev.poll_once(0)
        await self.dev.action("unfold", {})
        await self.dev.poll_once(1)
        await self.dev.action("live", {})
        await self.dev.poll_once(2)
        img = await self.dev.fetch_image()
        self.assertTrue(img and img.startswith(b"\xff\xd8"))
        self.dev.wanted = time.time()
        await self.dev.start()
        for _ in range(40):
            if self.dev.jpeg:
                break
            await asyncio.sleep(0.1)
        self.assertTrue(self.dev.live()["jpeg"].endswith("/live.jpg"))
        self.assertGreaterEqual(self.dev.live()["frame"], 1)


@unittest.skipUnless(HAVE_AIOHTTP, "aiohttp fehlt")
class StationApiTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import os

        import fake_seestar
        from astro_station import server
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["XDG_STATE_HOME"] = self.tmp.name
        server.STATE_DIR = Path(self.tmp.name) / "astro-station"
        self.off = free_offset()
        self.scope = fake_seestar.FakeScope()
        self.runners = await fake_seestar.serve(self.scope, self.off)
        o = self.off
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.stel_port = s.getsockname()[1]
        cfg = config.load(None) | {"location": {"lat": LAT, "lon": LON}}
        cfg["device"] = [{"id": "s50", "type": "seestar", "name": "Seestar S50", "alp": f"http://127.0.0.1:{5555 + o}",
                          "imaging": f"http://127.0.0.1:{7556 + o}", "webui": f"http://127.0.0.1:{5432 + o}",
                          "stellarium_port": self.stel_port}]
        self.notes = []
        self.station = server.Station(cfg, notify=self.notes.append)
        self.app = server.make_app(cfg, self.station)
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.base = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
        self.http = aiohttp.ClientSession()
        await self.station.devices[0].poll_once(0)

    async def asyncTearDown(self):
        await self.http.close()
        await self.runner.cleanup()
        for r in self.runners:
            await r.cleanup()
        self.tmp.cleanup()

    async def post(self, path, body):
        async with self.http.post(self.base + path, json=body) as r:
            return r.status, await r.json()

    async def test_state_tonight_and_commands(self):
        async with self.http.get(self.base + "/api/state") as r:
            st = await r.json()
        self.assertEqual(st["devices"][0]["id"], "s50")
        self.assertIsNotNone(st["sky"])
        async with self.http.get(self.base + "/api/tonight") as r:
            self.assertTrue(await r.json())
        status, res = await self.post("/api/dev/s50/goto", {"target": {"name": "M 31", "ra": 0.71232, "dec": 41.2691}})
        self.assertEqual(status, 409)
        self.assertIn("Arm eingeklappt", res["error"])
        self.assertEqual((await self.post("/api/dev/s50/action", {"action": "unfold"}))[0], 200)
        await self.station.devices[0].poll_once(1)
        self.assertEqual((await self.post("/api/dev/s50/goto", {"target": {"name": "M 31", "ra": 0.71232, "dec": 41.2691}}))[0], 200)
        self.assertEqual((await self.post("/api/dev/s50/action", {"action": "stack"}))[0], 200)
        await self.station.devices[0].poll_once(2)
        status, res = await self.post("/api/dev/s50/goto", {"target": {"name": "M 33", "ra": 1.56, "dec": 30.66}})
        self.assertEqual(status, 409)
        self.assertIn("beenden", res["confirm"])
        status, _ = await self.post("/api/dev/s50/goto", {"target": {"name": "M 33", "ra": 1.56, "dec": 30.66}, "confirm": True})
        self.assertEqual(status, 200)
        status, res = await self.post("/api/stop_all", {})
        self.assertEqual(status, 200)
        self.assertIn("gestoppt", res["message"])
        status, _ = await self.post("/api/session", {"id": "s50", "enabled": False})
        async with self.http.get(self.base + "/api/live") as r:
            self.assertEqual(await r.json(), [])

    async def test_stellarium_goto_refused_when_arm_closed(self):
        reader, writer = await asyncio.open_connection("127.0.0.1", self.stel_port)
        writer.write(stellarium.GOTO.pack(20, 0, int(time.time() * 1e6), int(0.737 / 24 * 2 ** 32), int(41.4 / 90 * 2 ** 30)))
        await writer.drain()
        for _ in range(30):
            if self.notes:
                break
            await asyncio.sleep(0.1)
        writer.close()
        self.assertTrue(self.notes and "Arm eingeklappt" in self.notes[0])
        self.assertFalse(any(m == "iscope_start_view" for m, _ in self.scope.calls))


if __name__ == "__main__":
    unittest.main()
