"""Tests fuer siril/travel-sync.sh mit Fake-rclone und lokalem TCP-Port als 'Seestar'."""
import fcntl
import json
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "siril" / "travel-sync.sh"
BIN = ROOT / "tests" / "bin"


class TravelSyncTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen()
        self.port = self.srv.getsockname()[1]
        threading.Thread(target=self._accept, daemon=True).start()
        self.astro = self.tmp / "astro"
        self.log = self.tmp / "rclone.log"
        cfg = self.tmp / "env"
        cfg.write_text(f"""
RCLONE_REMOTE=seestar
RCLONE_SRC_PATH="EMMC Images/MyWorks"
SEESTAR_IP=
ASTRO_ROOT={self.astro}
MIRROR_DIR={self.astro}/raw/seestar
WORK_DIR={self.astro}/work
STACK_DIR={self.astro}/stacks
EXPORT_DIR={self.astro}/export
STATE_DIR={self.astro}/.state
MIN_FRAMES=1
MQTT_ENABLED=0
LOCK_FILE={self.tmp}/lock
""")
        self.env = {**os.environ, "PATH": f"{BIN}:{os.environ['PATH']}", "SIRIL_AUTO_CONFIG": str(cfg),
                    "SMB_PORT": str(self.port), "FAKE_RCLONE_LOG": str(self.log),
                    "SIRIL_BIN": str(BIN / "siril-cli"), "FAKE_SIRIL_LOG": str(self.tmp / "siril.log")}

    def _accept(self):
        while True:
            try:
                c, _ = self.srv.accept()
                c.close()
            except OSError:
                return

    def tearDown(self):
        self.srv.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_sync(self, *args, **env):
        return subprocess.run(["bash", str(SCRIPT), *args], env={**self.env, **env}, capture_output=True, text=True)

    def calls(self):
        return [json.loads(x) for x in self.log.read_text().splitlines()] if self.log.exists() else []

    def test_requires_ip(self):
        r = self.run_sync("--since", "2d")
        self.assertEqual(r.returncode, 2)
        self.assertIn("--ip", r.stderr)

    def test_unreachable_explains_route(self):
        r = self.run_sync("--ip", "127.0.0.1", SMB_PORT="1")
        self.assertEqual(r.returncode, 1)
        self.assertIn("ip route add 127.0.0.0/24", r.stderr)
        self.assertEqual(self.calls(), [])

    def test_size_estimate(self):
        r = self.run_sync("--ip", "127.0.0.1", "--size", "--bwlimit", "2M")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("3 Dateien, 3.60 GB", r.stdout)
        self.assertIn("bei 2MB/s ≈ 0.5 h", r.stdout)
        (call,) = self.calls()
        self.assertEqual(call[:3], ["size", "--json", "seestar,host=127.0.0.1:EMMC Images/MyWorks"])
        self.assertIn("*_sub/**", call)

    def test_copy_target_filters_and_stack(self):
        r = self.run_sync("--ip", "127.0.0.1", "--since", "12h", "--target", "M 31", "--bwlimit", "1M", "--stack")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        (call,) = self.calls()
        self.assertEqual(call[0], "copy")
        self.assertEqual(call[2], str(self.astro / "raw" / "seestar"))
        self.assertEqual(call[call.index("--include") + 1], "M 31_sub/**")
        self.assertEqual(call[call.index("--max-age") + 1], "12h")
        self.assertEqual(call[call.index("--bwlimit") + 1], "1M")
        self.assertIn("Starte siril-auto", r.stdout)
        self.assertTrue((self.astro / "stacks" / "M_31").exists(), r.stdout)

    def test_all_and_since_off(self):
        r = self.run_sync("--ip", "127.0.0.1", "--all", "--since", "off", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        (call,) = self.calls()
        self.assertNotIn("--include", call)
        self.assertNotIn("--max-age", call)
        self.assertIn("--dry-run", call)

    def test_lock_and_failure(self):
        lock = open(self.tmp / "lock", "w")
        fcntl.flock(lock, fcntl.LOCK_EX)        # simuliert laufendes siril-auto
        r = self.run_sync("--ip", "127.0.0.1")
        self.assertEqual(r.returncode, 2)
        self.assertIn("siril-auto läuft gerade", r.stderr)
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()
        r = self.run_sync("--ip", "127.0.0.1", FAKE_RCLONE_FAIL="1")
        self.assertEqual(r.returncode, 1)
        self.assertIn("setzt fort", r.stdout)


if __name__ == "__main__":
    unittest.main()
