"""Tests fuer WCS, FITS-Header, Katalog, meta.json-Writer und Galerie-Builder."""
import json
import math
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gallery" / "build"))
sys.path.insert(0, str(ROOT / "siril" / "lib"))

import wcs as W  # noqa: E402
from catalog import Catalog, display, norm  # noqa: E402
from fitsheader import read_header, wcs_from_header, write_minimal_fits  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "ngc_subset.csv"
SAMPLE = ROOT / "gallery" / "sample-data" / "stacks"
M31_META = SAMPLE / "M_31" / "M_31_master_IRCUT_siril.meta.json"


class TestWcs(unittest.TestCase):
    def setUp(self):
        self.wcs = json.loads(M31_META.read_text())["wcs"]

    def test_roundtrip(self):
        for x, y in [(0, 0), (960, 540), (1919, 1079), (123.4, 987.6)]:
            ra, dec = W.pixel_to_world(x, y, self.wcs)
            x2, y2 = W.world_to_pixel(ra, dec, self.wcs)
            self.assertAlmostEqual(x, x2, places=6)
            self.assertAlmostEqual(y, y2, places=6)

    def test_reference_pixel(self):
        x, y = W.world_to_pixel(*self.wcs["crval"], self.wcs)
        self.assertAlmostEqual(x, self.wcs["crpix"][0], places=6)
        self.assertAlmostEqual(y, self.wcs["crpix"][1], places=6)

    def test_pixscale_and_parse(self):
        self.assertAlmostEqual(W.pixscale_arcsec(self.wcs), 2.39, places=4)
        self.assertAlmostEqual(W.parse_ra("00 42 44.3"), 10.684583, places=5)
        self.assertAlmostEqual(W.parse_dec("−05 23 28"), -5.391111, places=5)
        self.assertEqual(W.fmt_ra(10.684583), "00h 42m 44s")
        self.assertEqual(W.fmt_dec(-5.391111), "−05° 23′ 28″")

    def test_behind_tangent_plane(self):
        ra0, dec0 = self.wcs["crval"]
        self.assertIsNone(W.world_to_pixel((ra0 + 180) % 360, -dec0, self.wcs))


class TestFitsHeader(unittest.TestCase):
    def test_flip_matches_sample_wcs(self):
        w = json.loads(M31_META.read_text())["wcs"]
        ny = 1080
        cards = {"CTYPE1": "RA---TAN", "CTYPE2": "DEC--TAN", "CRVAL1": w["crval"][0], "CRVAL2": w["crval"][1],
                 "CRPIX1": w["crpix"][0] + 1, "CRPIX2": (ny - 1 - w["crpix"][1]) + 1,
                 "CD1_1": w["cd"][0][0], "CD1_2": -w["cd"][0][1], "CD2_1": w["cd"][1][0], "CD2_2": -w["cd"][1][1],
                 "OBJECT": "M 31", "EXPTIME": 10.0}
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "t.fit"
            write_minimal_fits(p, cards, nx=1920, ny=ny)
            h = read_header(p)
            self.assertEqual(h["OBJECT"], "M 31")
            self.assertEqual(h["NAXIS1"], 1920)
            got = wcs_from_header(h)
        for a, b in zip(got["crpix"], w["crpix"]):
            self.assertAlmostEqual(a, b, places=6)
        for ra, rb in zip(got["cd"], w["cd"]):
            for a, b in zip(ra, rb):
                self.assertAlmostEqual(a, b, places=12)

    def test_cdelt_crota(self):
        h = {"CTYPE1": "RA---TAN", "CRVAL1": 10.0, "CRVAL2": 40.0, "CRPIX1": 1.0, "CRPIX2": 1.0,
             "CDELT1": -0.001, "CDELT2": 0.001, "CROTA2": 0.0, "NAXIS2": 100}
        w = wcs_from_header(h, flip_y=False)
        self.assertEqual(w["crpix"], [0.0, 0.0])
        self.assertAlmostEqual(w["cd"][0][0], -0.001)

    def test_no_wcs(self):
        self.assertIsNone(wcs_from_header({"NAXIS2": 10}))


class TestCatalog(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cat = Catalog([FIXTURE], ROOT / "siril" / "lib" / "objects_de.json")

    def test_names(self):
        self.assertEqual(norm("M 031"), "M31")
        self.assertEqual(norm("ngc 0224"), "NGC224")
        self.assertEqual(display("NGC0224"), "NGC 224")

    def test_find(self):
        for q in ("M 31", "M31", "NGC 224", "Andromeda Galaxy", "andromeda galaxy"):
            self.assertEqual(self.cat.find(q)["name"], "M 31", q)
        o = self.cat.find("M 31")
        self.assertEqual(o["common"], "Andromeda-Galaxie")
        self.assertEqual(o["kind"], "galaxy")
        self.assertTrue(o["type"].startswith("Spiralgalaxie"))
        self.assertEqual(self.cat.find("NGC 7000")["common"], "Nordamerikanebel")

    def test_in_field(self):
        wcs = json.loads(M31_META.read_text())["wcs"]
        names = [o["name"] for o in self.cat.in_field(wcs, 1920, 1080, W.world_to_pixel)]
        self.assertIn("M 31", names)
        self.assertIn("M 32", names)
        self.assertIn("M 110", names)          # knapp ausserhalb, aber Messier
        self.assertNotIn("M 42", names)


class TestWriteMetaAndBuild(unittest.TestCase):
    def test_write_meta_then_build(self):
        w = json.loads(M31_META.read_text())["wcs"]
        ny = 1080
        cards = {"CTYPE1": "RA---TAN", "CTYPE2": "DEC--TAN", "CRVAL1": w["crval"][0], "CRVAL2": w["crval"][1],
                 "CRPIX1": w["crpix"][0] + 1, "CRPIX2": (ny - 1 - w["crpix"][1]) + 1,
                 "CD1_1": w["cd"][0][0], "CD1_2": -w["cd"][0][1], "CD2_1": w["cd"][1][0], "CD2_2": -w["cd"][1][1],
                 "EXPTIME": 10.0, "GAIN": 80}
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "export" / "M_31").mkdir(parents=True)
            fit = d / "r.fit"; write_minimal_fits(fit, cards, nx=1920, ny=ny)
            jpg = d / "export" / "M_31" / "M_31_master_IRCUT_siril.jpg"
            jpg.write_bytes((SAMPLE / "M_31" / "M_31_master_IRCUT_siril.jpg").read_bytes())
            meta = d / "stacks" / "M_31" / "M_31_master_IRCUT_siril.meta.json"
            subprocess.run([sys.executable, str(ROOT / "siril" / "lib" / "write_meta.py"), "--fits", str(fit),
                            "--jpg", str(jpg), "--out", str(meta), "--target", "Andromeda Galaxy", "--filter", "IRCUT",
                            "--exposure", "10", "--stack", "master", "--nights", "2026-10-18:212,2026-10-19:247",
                            "--catalog", str(FIXTURE)], check=True, capture_output=True)
            m = json.loads(meta.read_text())
            self.assertEqual(m["target"]["name"], "M 31")
            self.assertEqual(m["target"]["common"], "Andromeda-Galaxie")
            self.assertEqual(m["objects"][0]["name"], "M 31")
            self.assertTrue(m["objects"][0]["main"])
            self.assertEqual(m["image"], "../../export/M_31/M_31_master_IRCUT_siril.jpg")

            out = d / "web"
            subprocess.run([sys.executable, str(ROOT / "gallery" / "build" / "gallery_build.py"),
                            "--stacks", str(d / "stacks"), "--out", str(out)], check=True, capture_output=True)
            cat = json.loads((out / "catalog.json").read_text())
            e = cat["entries"][0]
            a = {x["name"]: x for x in e["annotations"]}
            self.assertAlmostEqual(a["M 31"]["x"], 1160.8, delta=1)
            self.assertAlmostEqual(a["M 31"]["y"], 540.0, delta=1)
            self.assertEqual([o["name"] for o in e["offscreen"]], ["M 110"])
            self.assertTrue((out / "index.html").exists() and (out / "img" / f'{e["id"]}_t.jpg').exists())

    def test_build_without_wcs(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            src = SAMPLE / "M_27"
            (d / "s").mkdir()
            for f in src.iterdir():
                (d / "s" / f.name).write_bytes(f.read_bytes())
            mp = next((d / "s").glob("*.meta.json"))
            m = json.loads(mp.read_text()); m["wcs"] = None; mp.write_text(json.dumps(m))
            r = subprocess.run([sys.executable, str(ROOT / "gallery" / "build" / "gallery_build.py"),
                                "--stacks", str(d / "s"), "--out", str(d / "w")], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            e = json.loads((d / "w" / "catalog.json").read_text())["entries"][0]
            self.assertIsNone(e["solve"])
            self.assertEqual(e["annotations"], [])

    def test_build_samples(self):
        with tempfile.TemporaryDirectory() as d:
            r = subprocess.run([sys.executable, str(ROOT / "gallery" / "build" / "gallery_build.py"),
                                "--stacks", str(SAMPLE), "--out", d], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            cat = json.loads((Path(d) / "catalog.json").read_text())
            self.assertEqual(len(cat["entries"]), 6)
            m27 = next(e for e in cat["entries"] if e["target"]["name"] == "M 27")
            self.assertAlmostEqual(m27["annotations"][0]["x"], 960, delta=1)
            self.assertTrue(math.isclose(m27["solve"]["pixscale"], 2.39, abs_tol=0.01))


if __name__ == "__main__":
    unittest.main()
