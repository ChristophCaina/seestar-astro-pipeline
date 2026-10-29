"""Tests fuer field/seestar_location_sync.py (Standort fuer seestar_alp)."""
import importlib.util
import socket
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("sls", ROOT / "field" / "seestar_location_sync.py")
sls = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sls)

CONFIG = """title = "Alpaca Sample Driver (Rotator)"

[network]
ip_address = '0.0.0.0'           # Any address

[seestar_initialization]
save_good_frames = true
lat = 37.12                         # telescope location (lat)
long = -123.45                      # telescope location (long)
interop_pem = '/home/x/key.pem'

[[seestars]]
name = "Seestar S50"
ip_address = "seestar.local"
"""


class ConfigTest(unittest.TestCase):
    def test_reads_and_replaces_only_the_init_section(self):
        self.assertEqual(sls.read_location(CONFIG), (37.12, -123.45))
        new = sls.write_location(CONFIG, 48.7, 8.9)
        self.assertEqual(sls.read_location(new), (48.7, 8.9))
        self.assertIn("lat = 48.7                         # telescope location (lat)", new)
        self.assertIn("ip_address = \"seestar.local\"", new)          # Seestar-IP bleibt
        self.assertIn("interop_pem = '/home/x/key.pem'", new)
        self.assertEqual(len(new.splitlines()), len(CONFIG.splitlines()))

    def test_adds_missing_keys(self):
        cfg = "[seestar_initialization]\nsave_good_frames = true\n\n[[seestars]]\nname = \"S\"\n"
        self.assertEqual(sls.read_location(sls.write_location(cfg, 1.5, 2.5)), (1.5, 2.5))
        self.assertEqual(sls.read_location(sls.write_location("[network]\nport = 5555\n", 1.5, 2.5)),
                         (1.5, 2.5))

    def test_update_config_skips_tiny_changes_and_keeps_a_backup(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.toml"
            p.write_text(CONFIG)
            self.assertTrue(sls.update_config(str(p), 48.77, 8.92))
            self.assertEqual(Path(str(p) + ".bak").read_text(), CONFIG)
            self.assertFalse(sls.update_config(str(p), 48.772, 8.921))   # < ~1 km
            self.assertEqual(sls.read_location(p.read_text()), (48.77, 8.92))


class SourceTest(unittest.TestCase):
    def test_gpsd_line(self):
        self.assertEqual(sls.parse_gpsd_line(b'{"class":"TPV","mode":3,"lat":48.765432,"lon":8.934567}'),
                         (48.76543, 8.93457))
        self.assertIsNone(sls.parse_gpsd_line(b'{"class":"TPV","mode":1}'))           # kein Fix
        self.assertIsNone(sls.parse_gpsd_line(b'{"class":"SKY"}'))
        self.assertIsNone(sls.parse_gpsd_line(b'kein json'))

    def test_device_state_lon_lat_order(self):
        reply = {"Value": {"result": {"location_lon_lat": [8.93, 48.77]}}, "ErrorNumber": 0}
        self.assertEqual(sls.parse_device_state(reply), (48.77, 8.93))
        self.assertIsNone(sls.parse_device_state({"Value": {"result": {"location_lon_lat": [0, 0]}}}))
        self.assertIsNone(sls.parse_device_state({"Value": {"result": {}}}))

    def test_never_zero_zero(self):
        self.assertFalse(sls.valid(0, 0))
        self.assertFalse(sls.valid(91, 0))
        self.assertTrue(sls.valid(-33.9, 18.4))

    def test_fake_gpsd(self):
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen()

        def serve():
            conn, _ = srv.accept()
            with conn:
                conn.recv(100)
                conn.sendall(b'{"class":"VERSION"}\n{"class":"TPV","mode":0}\n')
                conn.sendall(b'{"class":"TPV","mode":2,"lat":-33.9249,"lon":18.4241}\n')
        threading.Thread(target=serve, daemon=True).start()
        self.assertEqual(sls.from_gpsd("127.0.0.1", srv.getsockname()[1], timeout=5), (-33.9249, 18.4241))
        srv.close()

    def test_main_without_any_source_changes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.toml"
            p.write_text(CONFIG)
            rc = sls.main(["--config", str(p), "--gpsd", "127.0.0.1:1", "--alpaca", "http://127.0.0.1:1",
                           "--gps-timeout", "1"])
            self.assertEqual(rc, 1)
            self.assertEqual(p.read_text(), CONFIG)


if __name__ == "__main__":
    unittest.main()
