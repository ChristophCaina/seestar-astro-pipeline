"""Ende-zu-Ende-Test fuer siril-auto.sh mit Ersatz-Siril (tests/bin/siril-cli)."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "siril" / "siril-auto.sh"
FAKE = ROOT / "tests" / "bin" / "siril-cli"
FIXTURE = ROOT / "tests" / "fixtures" / "ngc_subset.csv"


def frames(folder: Path, target: str, filt: str, date: str, hhmm: str, n: int, start: int = 0):
    folder.mkdir(parents=True, exist_ok=True)
    for i in range(start, start + n):
        (folder / f"Light_{target}_10.0s_{filt}_{date}-{hhmm}{i:02d}.fit").write_bytes(b"x")


class SirilAutoTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.astro = self.tmp / "astro"
        mirror = self.astro / "raw" / "seestar"
        # M 31 IRCUT: 18.10. (25, ueber Mitternacht), 19.10. (22); LP am 19.10. (21)
        frames(mirror / "M 31_sub", "M 31", "IRCUT", "20261018", "2130", 15)
        frames(mirror / "M 31_sub", "M 31", "IRCUT", "20261019", "0115", 10)
        frames(mirror / "M 31_sub", "M 31", "IRCUT", "20261019", "2200", 22)
        frames(mirror / "M 31_sub", "M 31", "LP", "20261019", "2300", 21)
        # anderer Ordnername per Alias -> gleiche Gruppe, dritte Nacht
        frames(mirror / "Andromeda Galaxy_sub", "Andromeda Galaxy", "IRCUT", "20261021", "2045", 20)
        # zu wenige Frames
        frames(mirror / "M 42_sub", "M 42", "IRCUT", "20261021", "2350", 5)
        aliases = self.tmp / "aliases"
        aliases.write_text("# Test\nAndromeda Galaxy = M 31\n")
        self.calls = self.tmp / "calls.log"
        env = self.tmp / "env"
        env.write_text(f"""
RCLONE_REMOTE=seestar
RCLONE_SRC_PATH=x
SEESTAR_IP=
ASTRO_ROOT={self.astro}
MIRROR_DIR={mirror}
WORK_DIR={self.astro}/work
STACK_DIR={self.astro}/stacks
EXPORT_DIR={self.astro}/export
STATE_DIR={self.astro}/.state
ALIAS_FILE={aliases}
CATALOG={FIXTURE}
MIN_FRAMES=20
MQTT_ENABLED=0
LOCK_FILE={self.tmp}/lock
GALLERY_BUILD={ROOT}/gallery/build/gallery_build.py
GALLERY_OUT={self.astro}/web
""")
        self.env = {**os.environ, "SIRIL_AUTO_CONFIG": str(env), "SIRIL_BIN": str(FAKE), "FAKE_SIRIL_LOG": str(self.calls)}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_auto(self, *args, **extra_env):
        return subprocess.run(["bash", str(SCRIPT), "--no-fetch", *args], env={**self.env, **extra_env},
                              capture_output=True, text=True)

    def calls_of(self, kind):
        if not self.calls.exists():
            return []
        return [c for c in map(json.loads, self.calls.read_text().splitlines()) if c["kind"] == kind]

    def test_full_flow(self):
        r = self.run_auto()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        st = self.astro / "stacks" / "M_31"
        for night in ("2026-10-18_IRCUT", "2026-10-19_IRCUT", "2026-10-21_IRCUT", "2026-10-19_LP", "master_IRCUT"):
            self.assertTrue((st / f"M_31_{night}_siril.fit").exists(), night)
            self.assertTrue((st / f"M_31_{night}_siril.meta.json").exists(), night)
            self.assertTrue((self.astro / "export" / "M_31" / f"M_31_{night}_siril.jpg").exists(), night)
        self.assertFalse((st / "M_31_master_LP_siril.fit").exists())       # nur eine LP-Nacht
        self.assertFalse((self.astro / "stacks" / "M_42").exists())          # zu wenige Frames
        self.assertIn("SKIP M 42", r.stdout)

        # Mitternacht: Frames von 01:15 gehoeren zur Nacht 18.10.
        self.assertEqual((self.astro / ".state" / "M_31_IRCUT_2026-10-18.done").read_text().strip(), "25")
        # Master: 3 Naechte, Gewichtung fuer Siril 1.2
        master_calls = self.calls_of("master")
        self.assertEqual(len(master_calls), 1)
        self.assertEqual(master_calls[0]["lights"], 3)
        self.assertIn("-weight_from_nbstack", master_calls[0]["script"])
        self.assertIn("rej n", master_calls[0]["script"])
        m = json.loads((st / "M_31_master_IRCUT_siril.meta.json").read_text())
        self.assertEqual(m["stack"], "master")
        self.assertEqual([n["date"] for n in m["nights"]], ["2026-10-18", "2026-10-19", "2026-10-21"])
        self.assertEqual(sum(n["frames"] for n in m["nights"]), 67)
        self.assertIsNotNone(m["wcs"])
        self.assertEqual(m["objects"][0]["name"], "M 31")
        # Solve-Script mit -noflip
        self.assertIn("platesolve -noflip", self.calls_of("solve")[0]["script"])
        # Galerie gebaut
        cat = json.loads((self.astro / "web" / "catalog.json").read_text())
        self.assertEqual(len(cat["entries"]), 5)

        # zweiter Lauf: nichts zu tun
        n_calls = len(self.calls.read_text().splitlines())
        r2 = self.run_auto()
        self.assertEqual(r2.returncode, 0, r2.stdout)
        self.assertIn("0 verarbeitet", r2.stdout)
        self.assertEqual(len(self.calls.read_text().splitlines()), n_calls)

        # neue Frames fuer den 19.10. -> Nacht + Master neu
        frames(self.astro / "raw" / "seestar" / "M 31_sub", "M 31", "IRCUT", "20261019", "2230", 3)
        r3 = self.run_auto()
        self.assertIn("STACK M 31 IRCUT 2026-10-19: 25 Frames", r3.stdout)
        self.assertIn("MASTER M 31 IRCUT (nights): 3 Nächte, 70 Frames", r3.stdout)

        # --master-full: Master aus allen Einzelframes mit dem Nacht-Script
        r4 = self.run_auto("--master-full")
        self.assertIn("(full)", r4.stdout)
        full = [c for c in self.calls_of("stack") if c["work"] == "M_31_master_IRCUT_siril"]
        self.assertEqual(full[-1]["lights"], 70)

    def test_mqtt_payload_links_gallery(self):
        cfg = Path(self.env["SIRIL_AUTO_CONFIG"])
        cfg.write_text(cfg.read_text() + "MQTT_ENABLED=1\nMQTT_HOST=localhost\nMQTT_TOPIC=seestar/siril\n")
        log = self.tmp / "mqtt.log"
        r = self.run_auto(PATH=f"{ROOT / 'tests' / 'bin'}:{os.environ['PATH']}", FAKE_MQTT_LOG=str(log))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        msgs = [json.loads(x) for x in log.read_text().splitlines()]
        topics = {m["topic"] for m in msgs}
        self.assertIn("homeassistant/sensor/siril_auto_last/config", topics)
        last = [json.loads(m["payload"]) for m in msgs if m["topic"] == "seestar/siril/last"]
        self.assertTrue(all(m["retain"] for m in msgs if m["topic"] == "seestar/siril/last"))
        master = next(p for p in last if p["stack"] == "master")
        self.assertEqual(master["id"], "m_31_master_ircut_siril")
        self.assertEqual(master["group"], "m-31-ircut")
        self.assertRegex(master["timestamp"], r"^20\d\d-\d\d-\d\dT")
        # id muss zum Galerie-Eintrag passen, group zum Schluessel im Frontend
        cat = json.loads((self.astro / "web" / "catalog.json").read_text())
        self.assertIn(master["id"], {e["id"] for e in cat["entries"]})
        status = [json.loads(m["payload"]) for m in msgs if m["topic"] == "seestar/siril/status"]
        self.assertEqual(status[-1]["state"], "idle")

    def test_second_device_kept_separate(self):
        s30 = self.astro / "raw" / "s30"
        frames(s30 / "M 31_sub", "M 31", "IRCUT", "20261018", "2200", 20)
        frames(s30 / "M 31_sub", "M 31", "IRCUT", "20261019", "2200", 20)
        cfg = Path(self.env["SIRIL_AUTO_CONFIG"])
        cfg.write_text(cfg.read_text() + 'DEVICES="s50 s30"\nDEV_s30_LABEL="Seestar S30 Pro"\n')
        r = self.run_auto()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("STACK [s30] M 31 IRCUT 2026-10-18: 20 Frames", r.stdout)
        st = self.astro / "stacks" / "M_31"
        self.assertTrue((st / "M_31_2026-10-18_IRCUT_siril.fit").exists())          # Hauptgeraet unveraendert
        self.assertTrue((st / "M_31_2026-10-18_IRCUT_s30_siril.fit").exists())
        self.assertTrue((st / "M_31_master_IRCUT_s30_siril.fit").exists())
        m = json.loads((st / "M_31_master_IRCUT_s30_siril.meta.json").read_text())
        self.assertEqual(m["device"], {"id": "s30", "label": "Seestar S30 Pro"})
        self.assertEqual(sum(n["frames"] for n in m["nights"]), 40)                  # nur S30-Frames
        main = json.loads((st / "M_31_master_IRCUT_siril.meta.json").read_text())
        self.assertEqual(main["device"]["id"], "s50")
        self.assertEqual(sum(n["frames"] for n in main["nights"]), 25 + 22 + 20)
        masters = [c for c in self.calls_of("master")]
        self.assertEqual(len(masters), 2)
        cat = json.loads((self.astro / "web" / "catalog.json").read_text())
        self.assertEqual({e["device"]["id"] for e in cat["entries"]}, {"s50", "s30"})

    def test_dslr_with_calibration(self):
        eos = self.astro / "raw" / "eos6d"
        sub = eos / "M 31_sub"
        sub.mkdir(parents=True)
        for i in range(20):
            (sub / f"Light_M 31_60.0s_DSLR_20261018-22{i:02d}00.fits").write_bytes(b"x")
        cal = eos / "_calib"
        cal.mkdir()
        for i in range(5):
            (cal / f"Bias_0.001s_DSLR_20261018-2100{i:02d}.fits").write_bytes(b"x")
            (cal / f"Dark_60.0s_DSLR_20261019-0300{i:02d}.fits").write_bytes(b"x")
            (cal / f"Flat_0.5s_DSLR_20261017-2000{i:02d}.fits").write_bytes(b"x")   # alte Flats
            (cal / f"Flat_0.8s_DSLR_20261018-2030{i:02d}.fits").write_bytes(b"x")   # neueste Nacht
        cfg = Path(self.env["SIRIL_AUTO_CONFIG"])
        cfg.write_text(cfg.read_text() + 'DEVICES="s50 eos6d"\nDEV_eos6d_TYPE=dslr\nDEV_eos6d_LABEL="Canon EOS 6D"\n')
        r = self.run_auto()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        caldir = self.astro / "calib" / "eos6d"
        for m in ("bias.fit", "dark_60s.fit", "flat.fit"):
            self.assertTrue((caldir / m).exists(), m)
        calib = [c for c in self.calls_of("calib")]
        self.assertEqual(len(calib), 3)
        flat = next(c for c in calib if "-norm=mul" in c["script"])
        self.assertEqual(flat["lights"], 5)                              # nur die neueste Nacht
        self.assertIn("calibrate frame -bias=../bias.fit", flat["script"])
        night = next(c for c in self.calls_of("stack") if c["work"] == "M_31_2026-10-18_DSLR_eos6d_siril")
        self.assertIn("calibrate light -dark=../dark.fit -cc=dark -flat=../flat.fit -cfa -equalize_cfa -debayer",
                      night["script"])
        self.assertIn("Kalibrierung:", r.stdout)
        m = json.loads((self.astro / "stacks" / "M_31" / "M_31_2026-10-18_DSLR_eos6d_siril.meta.json").read_text())
        self.assertEqual(m["device"]["label"], "Canon EOS 6D")
        # zweiter Lauf: Masters unveraendert -> nicht neu gebaut
        n = len(self.calls_of("calib"))
        self.run_auto()
        self.assertEqual(len(self.calls_of("calib")), n)

    def test_widefield_night(self):
        eos = self.astro / "raw" / "eos6d" / "Milchstrasse_sub"
        eos.mkdir(parents=True)
        for i in range(25):
            (eos / f"Light_Milchstrasse_12.0s_WIDE_20260815-23{i:02d}00.fits").write_bytes(b"x")
        for i in range(25):     # zweite Nacht: trotzdem kein Master
            (eos / f"Light_Milchstrasse_12.0s_WIDE_20260816-23{i:02d}00.fits").write_bytes(b"x")
        cfg = Path(self.env["SIRIL_AUTO_CONFIG"])
        cfg.write_text(cfg.read_text() + 'DEVICES="s50 eos6d"\nDEV_eos6d_TYPE=dslr\nDEV_eos6d_LABEL="Canon EOS 6D"\n')
        path = f"{ROOT / 'tests' / 'bin'}:{os.environ['PATH']}"
        r = self.run_auto(PATH=path, FAKE_WCS_SCALE="20", FAKE_WCS_CENTER="305,38")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        st = self.astro / "stacks" / "Milchstrasse"
        night = [c for c in self.calls_of("stack") if c["work"] == "Milchstrasse_2026-08-15_WIDE_eos6d_siril"][0]
        self.assertIn("-transf=homography", night["script"])
        self.assertIn("\nstack pp_light rej", night["script"])                        # Vordergrund aktiv
        self.assertIn("convert pp_light -debayer", night["script"])                  # ohne Masters: nur debayern
        self.assertNotIn("platesolve", "".join(c["script"] for c in self.calls_of("solve")
                                               if "Milchstrasse" in c["work"]))
        self.assertFalse((st / "Milchstrasse_master_WIDE_eos6d_siril.fit").exists())
        exp = self.astro / "export" / "Milchstrasse"
        self.assertTrue((exp / "Milchstrasse_2026-08-15_WIDE_eos6d_siril_vordergrund.jpg").exists())
        m = json.loads((st / "Milchstrasse_2026-08-15_WIDE_eos6d_siril.meta.json").read_text())
        self.assertTrue(m.get("wide"), m.get("wcs"))
        self.assertEqual(m["target"]["kind"], "wide")
        names = {c["name"] for c in m["constellations"]}
        self.assertIn("Schwan", names)
        self.assertTrue(any(o["category"] == "star" for o in m["objects"]))
        self.assertIn("Plate-Solving (astrometry.net) ok", r.stdout)

    def test_invalid_device_id(self):
        cfg = Path(self.env["SIRIL_AUTO_CONFIG"])
        cfg.write_text(cfg.read_text() + 'DEVICES="S50 x-1"\n')
        r = self.run_auto("--dry-run")
        self.assertEqual(r.returncode, 2)
        self.assertIn("Ungueltige Geraete-ID", r.stderr)

    def test_failure_is_not_retried(self):
        r = self.run_auto(FAKE_SIRIL_FAIL="stack")
        self.assertEqual(r.returncode, 1)
        self.assertTrue((self.astro / ".state" / "M_31_IRCUT_2026-10-18.failed").exists())
        n = len(self.calls_of("stack"))
        r2 = self.run_auto(FAKE_SIRIL_FAIL="stack")
        self.assertEqual(len(self.calls_of("stack")), n)          # nicht erneut versucht
        self.assertEqual(r2.returncode, 0)

    def test_solve_failure_keeps_stack(self):
        r = self.run_auto(FAKE_SIRIL_FAIL="solve")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("Plate-Solving fehlgeschlagen", r.stdout)
        m = json.loads((self.astro / "stacks" / "M_31" / "M_31_2026-10-18_IRCUT_siril.meta.json").read_text())
        self.assertIsNone(m["wcs"])
        self.assertEqual(m["objects"], [])

    def test_dry_run_and_siril14(self):
        r = self.run_auto("--dry-run")
        self.assertEqual(r.returncode, 0)
        self.assertIn("STACK M 31 IRCUT 2026-10-18: 25 Frames", r.stdout)
        self.assertFalse(self.calls.exists())                      # Siril nie aufgerufen
        self.assertFalse(any((self.astro / "stacks").iterdir()))
        r = self.run_auto(FAKE_SIRIL_VERSION="1.4.0")
        self.assertIn("-weight=nbstack", self.calls_of("master")[0]["script"])


if __name__ == "__main__":
    unittest.main()
