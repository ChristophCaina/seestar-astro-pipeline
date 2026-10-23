"""Tests fuer den Astro-Pi-Dienst (pi/astro_pi): Bildvorschau, INDI-Client, Steuerung, Web-API.

Der Integrationsteil laeuft gegen die INDI-Simulatoren (indiserver + indi_simulator_telescope/_ccd)
und wird uebersprungen, wenn INDI nicht installiert ist.
"""
import asyncio
import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pi"))

from astro_pi import image  # noqa: E402
from astro_pi.control import Controller, Settings, _sep_arcmin  # noqa: E402
from astro_pi.indi import IndiClient  # noqa: E402
from astro_pi.targets import Targets  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "ngc_subset.csv"
BIN = ROOT / "tests" / "bin"


def fits_bytes(arr: np.ndarray, cards: dict | None = None) -> bytes:
    """16-Bit-FITS fuer Tests."""
    cards = {"SIMPLE": True, "BITPIX": 16, "NAXIS": 2, "NAXIS1": arr.shape[1], "NAXIS2": arr.shape[0],
             "BZERO": 32768, "BSCALE": 1, **(cards or {})}
    lines = []
    for k, v in cards.items():
        val = "T" if v is True else "F" if v is False else (f"'{v}'" if isinstance(v, str) else str(v))
        lines.append(f"{k:<8}= {val:>20}".ljust(80))
    lines.append("END".ljust(80))
    hdr = "".join(lines).encode()
    hdr += b" " * (-len(hdr) % 2880)
    data = (arr.astype(np.int64) - 32768).astype(">i2").tobytes()
    return hdr + data + b"\0" * (-len(data) % 2880)


def star_field(h=400, w=600, sigma=1.6, n=30, seed=1):
    rng = np.random.default_rng(seed)
    img = rng.normal(1000, 20, (h, w))
    yy, xx = np.mgrid[0:h, 0:w]
    for _ in range(n):
        y, x = rng.uniform(20, h - 20), rng.uniform(20, w - 20)
        img += rng.uniform(3000, 20000) * np.exp(-((yy - y) ** 2 + (xx - x) ** 2) / (2 * sigma ** 2))
    return np.clip(img, 0, 65535)


class TestImage(unittest.TestCase):
    def test_hfr_tracks_focus(self):
        sharp, _ = image.hfr(star_field(sigma=1.2))
        soft, n = image.hfr(star_field(sigma=3.0))
        self.assertGreater(n, 10)
        self.assertLess(sharp, soft)
        self.assertAlmostEqual(sharp, 1.2 * 1.177, delta=0.6)      # HFR einer Gauss-PSF ~ 1.18 sigma

    def test_preview_bayer(self):
        arr = star_field(h=200, w=300)
        pv = image.preview(fits_bytes(arr, {"BAYERPAT": "RGGB"}))
        im = Image.open(io.BytesIO(pv["jpeg"]))
        self.assertEqual(im.size, (150, 100))                        # Superpixel = halbe Aufloesung
        self.assertEqual((pv["width"], pv["height"]), (300, 200))
        self.assertIsNotNone(pv["hfr"])

    def test_read_fits_scaling(self):
        hdr, a = image.read_fits(fits_bytes(np.full((4, 6), 40000.0)))
        self.assertEqual(a.shape, (4, 6))
        self.assertAlmostEqual(float(a.mean()), 40000.0)


class TestTargets(unittest.TestCase):
    def test_search(self):
        t = Targets([FIXTURE])
        hit = t.search("m31", 5)[0]
        self.assertEqual(hit["name"], "M 31")
        self.assertAlmostEqual(hit["ra"], 0.7123, places=3)          # Stunden fuer INDI
        names = [h["name"] for h in t.search("andromeda", 5)]
        self.assertIn("M 31", names)
        self.assertEqual(t.search("", 5), [])
        self.assertTrue(all("altitude" in h for h in t.search("ngc", 3, altitude=lambda r, d: 42.0)))


class FakeIndi:
    """Kleiner INDI-Server: schickt Definitionen in Haeppchen und protokolliert, was der Client sendet."""

    def __init__(self, script: list[bytes]):
        self.script, self.received = script, b""
        self.server = None

    async def start(self):
        self.server = await asyncio.start_server(self._client, "127.0.0.1", 0)
        return self.server.sockets[0].getsockname()[1]

    async def _client(self, r, w):
        await r.read(100)                                            # getProperties
        for chunk in self.script:
            w.write(chunk)
            await w.drain()
            await asyncio.sleep(0.01)
        while True:
            d = await r.read(4096)
            if not d:
                break
            self.received += d


DEF = (b'<defSwitchVector device="Mount" name="CONNECTION" state="Idle" perm="rw" rule="OneOfMany">'
       b'<defSwitch name="CONNECT">Off</defSwitch><defSwitch name="DISCONNECT">On</defSwitch></defSwitchVector>'
       b'<defTextVector device="Mount" name="DRIVER_INFO" perm="ro"><defText name="DRIVER_INTERFACE">5</defText></defTextVector>'
       b'<defNumberVector device="Mount" name="EQUATORIAL_EOD_COORD" state="Idle" perm="rw">'
       b'<defNumber name="RA">12:30:00</defNumber><defNumber name="DEC">-5.5</defNumber></defNumberVector>'
       b'<defBLOBVector device="Cam" name="CCD1" perm="ro"><defBLOB name="CCD1"/></defBLOBVector>')


class TestIndiClient(unittest.IsolatedAsyncioTestCase):
    async def test_parse_set_and_blob(self):
        blob = b"SIMPLE  =                    T"
        import base64
        setb = (b'<setBLOBVector device="Cam" name="CCD1" state="Ok"><oneBLOB name="CCD1" size="30" format=".fits">'
                + base64.b64encode(blob) + b'</oneBLOB></setBLOBVector>')
        setn = b'<setNumberVector device="Mount" name="EQUATORIAL_EOD_COORD" state="Busy" message="schwenke">' \
               b'<oneNumber name="RA">1.5</oneNumber><oneNumber name="DEC">41</oneNumber></setNumberVector>'
        whole = DEF + setn + setb
        fake = FakeIndi([whole[i:i + 37] for i in range(0, len(whole), 37)])   # absichtlich zerstueckelt
        port = await fake.start()
        c = IndiClient("127.0.0.1", port)
        blobs = []
        c.on_blob(blobs.append)
        task = asyncio.create_task(c.run(retry=0.1))
        try:
            p = await c.wait_for(lambda q: q.name == "EQUATORIAL_EOD_COORD" and q.state == "Busy", 5)
            self.assertEqual(p.values, {"RA": 1.5, "DEC": 41.0})
            await asyncio.sleep(0.2)
            self.assertEqual(c.devices_with(1), ["Mount"])
            self.assertEqual(blobs[0].data, blob)
            self.assertEqual(blobs[0].fmt, ".fits")
            self.assertEqual(c.messages[-1][2], "schwenke")
            await c.set_switch("Mount", "CONNECTION", "CONNECT")
            await c.set_number("Mount", "EQUATORIAL_EOD_COORD", {"RA": 2, "DEC": 3})
            await asyncio.sleep(0.2)
            sent = fake.received.decode()
            self.assertIn("<oneSwitch name=\"CONNECT\">On</oneSwitch><oneSwitch name=\"DISCONNECT\">Off</oneSwitch>", sent)
            self.assertIn("<oneNumber name=\"RA\">2.00000000</oneNumber>", sent)
        finally:
            task.cancel()
            fake.server.close()

    def test_sexagesimal(self):
        from astro_pi.indi import _parse_value
        self.assertAlmostEqual(_parse_value("Number", "12:30:00"), 12.5)
        self.assertAlmostEqual(_parse_value("Number", "-05:30"), -5.5)


class TestControlOffline(unittest.TestCase):
    def test_frame_name_matches_siril_auto(self):
        ctl = Controller(IndiClient(), Settings(capture_dir=Path("/x")))
        from datetime import datetime
        p = ctl.frame_path("M 31", 120, ".fits", datetime(2026, 10, 18, 23, 5, 9))
        self.assertEqual(str(p), "/x/M 31_sub/Light_M 31_120.0s_DSLR_20261018-230509.fits")
        # gleicher Ausdruck wie frame_info() in siril/siril-auto.sh
        self.assertRegex(p.name, r"_([0-9]+(\.[0-9]+)?)s_([A-Za-z0-9]+)_(20[0-9]{6})-([0-9]{6})")

    def test_altitude_and_separation(self):
        ctl = Controller(IndiClient(), Settings(lat=48.7, lon=8.9))
        from datetime import datetime, timezone
        # Polarstern steht immer etwa auf Breitengrad-Hoehe
        alt = ctl.altitude(2.53, 89.26, datetime(2026, 10, 18, 22, tzinfo=timezone.utc))
        self.assertAlmostEqual(alt, 48.7, delta=1.0)
        self.assertAlmostEqual(_sep_arcmin(1, 10, 1, 10.5), 30, places=3)

    def test_solve_with_fake_solve_field(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "x.fits"
            f.write_bytes(fits_bytes(np.zeros((8, 8))))
            ctl = Controller(IndiClient(), Settings(solve_field=str(BIN / "solve-field")))
            ctl.preview = {"file": str(f), "width": 1920, "height": 1080}
            r = ctl._solve_file(f)
            # Fake liefert die WCS des M31-Beispiels; Bildmitte liegt nahe M 31
            self.assertLess(_sep_arcmin(r["ra"], r["dec"], 0.7123, 41.27), 40)
            self.assertAlmostEqual(r["scale"], 2.39, places=1)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@unittest.skipUnless(shutil.which("indiserver") and shutil.which("indi_simulator_telescope"), "INDI nicht installiert")
class TestWithSimulators(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = free_port()
        cls.proc = subprocess.Popen(["indiserver", "-p", str(cls.port), "-u", f"/tmp/indiserver-test-{cls.port}", "indi_simulator_telescope", "indi_simulator_ccd"],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            try:
                socket.create_connection(("127.0.0.1", cls.port), 0.2).close()
                break
            except OSError:
                time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait(5)

    async def asyncSetUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        from astro_pi.server import CTL, load_config, make_app
        self.CTL = CTL
        from aiohttp.test_utils import TestClient, TestServer
        cfg = load_config(None)
        cfg["indi"]["port"] = self.port
        cfg["capture"]["dir"] = str(self.tmp / "captures")
        cfg["catalog"]["paths"] = [str(FIXTURE)]
        cfg["location"].update(lat=48.7, lon=8.9)
        cfg["mqtt"].update(host="localhost", interval=1)
        self.mqtt_log = self.tmp / "mqtt.log"
        os.environ["FAKE_MQTT_LOG"] = str(self.mqtt_log)
        self._path = os.environ["PATH"]
        os.environ["PATH"] = f"{BIN}:{self._path}"
        self.app = make_app(cfg)
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()
        ctl = self.app[self.CTL]
        for _ in range(100):                                          # Geraete verbunden?
            s = ctl.state()
            if s["mount"]["connected"] and s["camera"]["connected"]:
                break
            await asyncio.sleep(0.1)

    async def asyncTearDown(self):
        await self.client.close()
        os.environ["PATH"] = self._path
        shutil.rmtree(self.tmp, ignore_errors=True)

    async def post(self, path, body):
        r = await self.client.post(path, json=body)
        return r.status, await r.json()

    async def test_goto_series_and_mqtt(self):
        st = await (await self.client.get("/api/state")).json()
        self.assertEqual(st["mount"]["device"], "Telescope Simulator")
        self.assertEqual(st["camera"]["device"], "CCD Simulator")

        code, j = await self.post("/api/mount/goto", {"name": "M 31"})
        self.assertEqual(code, 200, j)
        ctl = self.app[self.CTL]
        await ctl.wait_slew(60)
        st = ctl.state()["mount"]
        self.assertEqual(st["target"], "M 31")
        self.assertLess(_sep_arcmin(st["ra"], st["dec"], 0.7123, 41.27), 10)

        code, j = await self.post("/api/camera/series", {"exposure": 0.2, "count": 2, "delay": 0})
        self.assertEqual(code, 200, j)
        code, j = await self.post("/api/camera/series", {"exposure": 0.2, "count": 2})
        self.assertEqual(code, 409)                                     # laeuft schon
        for _ in range(200):
            if not ctl.series.running:
                break
            await asyncio.sleep(0.1)
        self.assertEqual(ctl.series.done, 2, ctl.series)
        files = sorted((self.tmp / "captures" / "M 31_sub").glob("*.fits"))
        self.assertEqual(len(files), 2)
        self.assertTrue(re.match(r"Light_M 31_0\.2s_DSLR_20\d{6}-\d{6}\.fits", files[0].name))
        r = await self.client.get("/api/preview.jpg")
        self.assertEqual(r.headers["Content-Type"], "image/jpeg")

        await asyncio.sleep(2.5)                                       # MQTT-Schleife
        msgs = [json.loads(x) for x in self.mqtt_log.read_text().splitlines()]
        topics = {m["topic"] for m in msgs}
        self.assertIn("homeassistant/image/astro_pi_preview/config", topics)
        self.assertIn("astro_pi/preview", topics)
        state = [json.loads(m["payload"]) for m in msgs if m["topic"] == "astro_pi/state"][-1]
        self.assertEqual(state["mount"]["target"], "M 31")

    async def test_park_track_errors(self):
        code, _ = await self.post("/api/mount/park", {"park": True})
        self.assertEqual(code, 200)
        ctl = self.app[self.CTL]
        for _ in range(100):
            if ctl.state()["mount"]["parked"]:
                break
            await asyncio.sleep(0.1)
        self.assertTrue(ctl.state()["mount"]["parked"])
        await self.post("/api/mount/park", {"park": False})
        code, j = await self.post("/api/mount/goto", {"name": "gibtsnicht"})
        self.assertEqual(code, 409)
        self.assertIn("nicht gefunden", j["error"])
        code, j = await self.post("/api/solve", {})
        self.assertEqual(code, 409)
        r = await self.client.get("/")
        self.assertIn("Astro-Pi", await r.text())


if __name__ == "__main__":
    unittest.main()
