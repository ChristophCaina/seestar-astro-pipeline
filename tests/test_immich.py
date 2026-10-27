"""Tests fuer Beschriftung, Immich-Sync und Backfill gegen einen Mock-Immich-Server."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PIL import Image, ImageChops

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "siril" / "lib"
sys.path.insert(0, str(ROOT / "gallery" / "build"))
sys.path.insert(0, str(LIB))

from annotate import annotate  # noqa: E402
from immich_sync import tokens  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "ngc_subset.csv"
SAMPLE = ROOT / "gallery" / "sample-data" / "stacks"
M31_DIR = SAMPLE / "M_31"
M31_META = M31_DIR / "M_31_master_IRCUT_siril.meta.json"
M31_JPG = M31_DIR / "M_31_master_IRCUT_siril.jpg"
LIB_ID = "lib-1"


class FakeImmich:
    """Minimaler Immich-Ersatz: Assets im Speicher, alle Aufrufe werden protokolliert."""

    def __init__(self):
        self.assets: list[dict] = []
        self.files: dict[str, bytes] = {}
        self.stacks: dict[str, list[str]] = {}
        self.albums: dict[str, dict] = {}
        self.calls: list[tuple[str, str]] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj=None, raw=None):
                body = raw if raw is not None else (json.dumps(obj).encode() if obj is not None else b"")
                self.send_response(code)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _body(self):
                n = int(self.headers.get("Content-Length") or 0)
                return json.loads(self.rfile.read(n)) if n else {}

            def do_GET(self):
                fake.calls.append(("GET", self.path))
                if self.headers.get("x-api-key") != "secret":
                    return self._send(401, {"message": "unauthorized"})
                if self.path.startswith("/api/assets/") and self.path.endswith("/original"):
                    aid = self.path.split("/")[3]
                    return self._send(200, raw=fake.files[aid])
                if self.path == "/api/albums":
                    return self._send(200, [{"id": k, "albumName": v["name"]} for k, v in fake.albums.items()])
                self._send(404, {})

            def do_POST(self):
                fake.calls.append(("POST", self.path))
                b = self._body()
                if self.path == "/api/search/metadata":
                    items = list(fake.assets)
                    if "originalFileName" in b:            # Immich: Teilstring, ohne Gross/Klein
                        q = b["originalFileName"].lower()
                        items = [x for x in items if q in x["originalFileName"].lower()]
                    page, size = b.get("page", 1), b.get("size", 250)
                    chunk = items[(page - 1) * size: page * size]
                    nxt = str(page + 1) if page * size < len(items) else None
                    return self._send(200, {"assets": {"items": chunk, "nextPage": nxt}})
                if self.path == "/api/stacks":
                    sid = f"stack-{len(fake.stacks) + 1}"
                    fake.stacks[sid] = b["assetIds"]
                    return self._send(201, {"id": sid})
                if self.path == "/api/albums":
                    aid = f"album-{len(fake.albums) + 1}"
                    fake.albums[aid] = {"name": b["albumName"], "assets": []}
                    return self._send(201, {"id": aid})
                if self.path.startswith("/api/libraries/") and self.path.endswith("/scan"):
                    return self._send(204)
                self._send(404, {})

            def do_PUT(self):
                fake.calls.append(("PUT", self.path))
                b = self._body()
                aid = self.path.split("/")[3]
                fake.albums[aid]["assets"].extend(i for i in b["ids"] if i not in fake.albums[aid]["assets"])
                self._send(200, [])

            def do_DELETE(self):
                fake.calls.append(("DELETE", self.path))
                if self.path.startswith("/api/albums/"):
                    b = self._body()
                    aid = self.path.split("/")[3]
                    fake.albums[aid]["assets"] = [i for i in fake.albums[aid]["assets"] if i not in b["ids"]]
                    return self._send(200, [])
                fake.stacks.pop(self.path.split("/")[3], None)
                self._send(204)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def add(self, name, library=None, data=b"", taken="2026-10-18T23:30:00.000Z"):
        aid = f"a{len(self.assets) + 1}"
        self.assets.append({"id": aid, "originalFileName": name, "libraryId": library,
                            "localDateTime": taken, "fileCreatedAt": taken, "type": "IMAGE"})
        self.files[aid] = data
        return aid

    def close(self):
        self.server.shutdown()


def run(script, *args, env_extra=None):
    env = dict(os.environ, PATH=f"{ROOT / 'tests' / 'bin'}:{os.environ['PATH']}", **(env_extra or {}))
    r = subprocess.run([sys.executable, str(LIB / script), *args], capture_output=True, text=True, env=env)
    if r.returncode != 0:
        raise AssertionError(f"{script} exit {r.returncode}\n{r.stdout}\n{r.stderr}")
    return r.stdout


class TestAnnotate(unittest.TestCase):
    def test_draws_and_keeps_size(self):
        with tempfile.TemporaryDirectory() as d:
            out = annotate(M31_META, Path(d) / "x_beschriftet.jpg")
            with Image.open(out) as a, Image.open(M31_JPG) as b:
                self.assertEqual(a.size, b.size)
                diff = ImageChops.difference(a.convert("RGB"), b.convert("RGB")).getbbox()
            self.assertIsNotNone(diff, "Bild unveraendert – nichts gezeichnet?")

    def test_without_wcs_only_footer(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            m = json.loads(M31_META.read_text())
            m["wcs"] = None
            shutil.copy(M31_JPG, d / m["image"])
            (d / "m.meta.json").write_text(json.dumps(m))
            out = annotate(d / "m.meta.json", d / "o.jpg")
            self.assertTrue(out.exists())


class TestTokens(unittest.TestCase):
    def test_tokens(self):
        self.assertEqual(tokens("Stacked_M 31_10.0s_IRCUT.jpg"), {"M31"})
        self.assertEqual(tokens("NGC_7000-2026.jpg"), {"NGC7000"})
        self.assertEqual(tokens("ic0434 und M42"), {"IC434", "M42"})
        self.assertEqual(tokens("IMG_2026.jpg"), set())
        self.assertEqual(tokens("Stacked_120_x.jpg"), set())


class ImmichCase(unittest.TestCase):
    def setUp(self):
        self.im = FakeImmich()
        self.tmp = Path(tempfile.mkdtemp())
        self.stacks = self.tmp / "stacks"
        self.export = self.tmp / "export"
        self.env = {"IMMICH_URL": self.im.url, "IMMICH_API_KEY": "secret", "IMMICH_LIBRARY_ID": LIB_ID}

    def tearDown(self):
        self.im.close()
        shutil.rmtree(self.tmp)


class TestImmichSync(ImmichCase):
    def _stack(self, name, stack, nights):
        d = self.stacks / "M_31"
        d.mkdir(parents=True, exist_ok=True)
        m = json.loads(M31_META.read_text())
        m.pop("sample")
        m.update(image=f"{name}.jpg", stack=stack, nights=nights)
        shutil.copy(M31_JPG, d / f"{name}.jpg")
        (d / f"{name}.meta.json").write_text(json.dumps(m))

    def test_groups_versions_and_app_stacks(self):
        self._stack("M_31_IRCUT_20261018", "night", [{"date": "2026-10-18", "frames": 212}])
        self._stack("M_31_master_IRCUT", "master", [{"date": "2026-10-18", "frames": 212}])
        (self.stacks / "M_31" / "M_31_master_IRCUT_beschriftet.jpg").write_bytes(b"x")
        night = self.im.add("M_31_IRCUT_20261018.jpg", LIB_ID)
        master = self.im.add("M_31_master_IRCUT.jpg", LIB_ID)
        ann = self.im.add("M_31_master_IRCUT_beschriftet.jpg", LIB_ID)
        app = self.im.add("Stacked_M 31_10.0s_IRCUT.jpg")
        self.im.add("Stacked_NGC 7000_10.0s_LP.jpg")      # anderes Objekt
        self.im.add("IMG_4711.jpg")                       # kein App-Stack

        out = run("immich_sync.py", "--stacks", str(self.stacks), env_extra=self.env)
        self.assertIn("1 Gruppe(n) aktualisiert", out)
        self.assertIn(("POST", f"/api/libraries/{LIB_ID}/scan"), self.im.calls)
        (members,) = self.im.stacks.values()
        self.assertEqual(members[0], master, "Master muss Titelbild sein")
        self.assertEqual(set(members), {master, night, ann, app})
        (album,) = self.im.albums.values()
        self.assertEqual(album["name"], "Astro")
        self.assertEqual(album["assets"], [master], "nur das Titelbild gehoert ins Album")

        # zweiter Lauf: nichts geaendert -> kein neuer Stack
        out = run("immich_sync.py", "--stacks", str(self.stacks), env_extra=self.env)
        self.assertIn("0 Gruppe(n) aktualisiert", out)
        self.assertEqual(len(self.im.stacks), 1)

    def test_one_stack_per_object_incl_seestar(self):
        """Filter, Kombination und Seestar-Mosaik eines Objekts -> ein Stack; alte Ziel|Filter-Stacks aufgeloest."""
        self._stack("M_31_master_IRCUT", "master", [{"date": "2026-10-18", "frames": 212}])
        self._stack("M_31_2026-10-19_LP", "night", [{"date": "2026-10-19", "frames": 50}])
        self._stack("M_31_kombi_IRCUT+LP", "master", [{"date": "2026-10-18", "frames": 262}])
        self._stack("M_31_mosaik_2026-10-20_IRCUT_seestar", "night", [{"date": "2026-10-20", "frames": 12}])
        d = self.stacks / "M_31"
        for name, extra in (("M_31_2026-10-19_LP", {"filter": "LP"}), ("M_31_kombi_IRCUT+LP", {"filter": "IRCUT+LP"}),
                            ("M_31_mosaik_2026-10-20_IRCUT_seestar", {"source": "seestar", "mosaic": True})):
            mp = d / f"{name}.meta.json"
            m = json.loads(mp.read_text()); m.update(extra); mp.write_text(json.dumps(m))
        ids = {n: self.im.add(f"{n}.jpg", LIB_ID) for n in
               ("M_31_master_IRCUT", "M_31_2026-10-19_LP", "M_31_kombi_IRCUT+LP", "M_31_mosaik_2026-10-20_IRCUT_seestar")}
        sp = self.stacks.parent / ".state" / "immich.json"
        sp.parent.mkdir(parents=True, exist_ok=True)
        old = self.im.stacks.setdefault("stack-old", [ids["M_31_master_IRCUT"]])
        sp.write_text(json.dumps({"M 31|IRCUT": {"stack_id": "stack-old", "assets": old}}))
        out = run("immich_sync.py", "--stacks", str(self.stacks), env_extra=self.env)
        self.assertIn("aufgelöst M 31|IRCUT", out)
        self.assertNotIn("stack-old", self.im.stacks)
        (members,) = self.im.stacks.values()
        self.assertEqual(members[0], ids["M_31_kombi_IRCUT+LP"], "Kombination ist Titelbild")
        self.assertEqual(set(members), set(ids.values()))
        (album,) = self.im.albums.values()
        self.assertEqual(album["assets"], [ids["M_31_kombi_IRCUT+LP"]])

    def test_album_cleanup_from_older_runs(self):
        """Aeltere Laeufe haben alle Versionen ins Album gelegt – die werden wieder herausgenommen."""
        self._stack("M_31_master_IRCUT", "master", [{"date": "2026-10-18", "frames": 212}])
        (self.stacks / "M_31" / "M_31_master_IRCUT_beschriftet.jpg").write_bytes(b"x")
        master = self.im.add("M_31_master_IRCUT.jpg", LIB_ID)
        ann = self.im.add("M_31_master_IRCUT_beschriftet.jpg", LIB_ID)
        run("immich_sync.py", "--stacks", str(self.stacks), env_extra=self.env)
        # Zustand wie nach einem alten Lauf: beide Versionen im Album, kein "album" im State
        (aid, album), = self.im.albums.items()
        album["assets"] = [master, ann]
        sp = self.stacks.parent / ".state" / "immich.json"
        st = json.loads(sp.read_text())
        for v in st.values():
            v.pop("album", None)
        sp.write_text(json.dumps(st))
        out = run("immich_sync.py", "--stacks", str(self.stacks), env_extra=self.env)
        self.assertIn("1 Gruppe(n) aktualisiert", out)
        self.assertEqual(self.im.albums[aid]["assets"], [master])

    def test_waits_until_scanned(self):
        self._stack("M_31_master_IRCUT", "master", [{"date": "2026-10-18", "frames": 212}])
        out = run("immich_sync.py", "--stacks", str(self.stacks), env_extra=self.env)
        self.assertIn("wartet", out)
        self.assertEqual(self.im.stacks, {})

    def test_ambiguous_night_without_token(self):
        self._stack("M_31_master_IRCUT", "master", [{"date": "2026-10-18", "frames": 212}])
        d = self.stacks / "M_13"
        d.mkdir()
        m = json.loads((SAMPLE / "M_13" / "M_13_master_IRCUT_siril.meta.json").read_text())
        m.pop("sample")
        m.update(image="M_13_master.jpg", nights=[{"date": "2026-10-18", "frames": 50}])
        (d / "M_13_master.meta.json").write_text(json.dumps(m))
        (d / "M_13_master.jpg").write_bytes(b"x")
        master = self.im.add("M_31_master_IRCUT.jpg", LIB_ID)
        self.im.add("Seestar_Stack_001.jpg")             # kein Objekt im Namen, Nacht mit 2 Zielen
        run("immich_sync.py", "--stacks", str(self.stacks), "--dry-run", env_extra=self.env)
        out = run("immich_sync.py", "--stacks", str(self.stacks), env_extra=self.env)
        self.assertIn("wartet", out)                     # M 13 noch nicht in Immich
        self.assertEqual(list(self.im.stacks.values()), [], "einzelnes Asset ergibt keinen Stack")
        (album,) = self.im.albums.values()
        self.assertEqual(album["assets"], [master])


class TestBackfill(ImmichCase):
    def test_solves_annotates_and_remembers(self):
        data = M31_JPG.read_bytes()
        self.im.add("Stacked_M 31_10.0s_IRCUT_20261018.jpg", data=data, taken="2026-10-19T01:15:00.000Z")
        self.im.add("Stacked_unsolvable.jpg", data=data)
        self.im.add("IMG_4711.jpg", data=data)                       # kein App-Stack
        self.im.add("Stacked_lib.jpg", LIB_ID, data=data)            # External Library -> ignorieren
        args = ["--stacks", str(self.stacks), "--export", str(self.export), "--since", "2026-10-01",
                "--until", "2026-10-31", "--catalog", str(FIXTURE)]

        out = run("backfill.py", *args, env_extra=self.env)
        self.assertIn("2 App-Stacks zu verarbeiten", out)
        self.assertIn("1 gelöst", out)
        metas = list((self.stacks / "_app").rglob("*.meta.json"))
        self.assertEqual(len(metas), 1)
        m = json.loads(metas[0].read_text())
        self.assertEqual(m["source"], "app")
        self.assertEqual(m["target"]["name"], "M 31")
        self.assertEqual(m["nights"], [{"date": "2026-10-18", "frames": 0}])
        self.assertTrue(m["id"].startswith("app_"))
        self.assertTrue(any(o["name"] == "M 110" for o in m["objects"]))
        ann = list(self.export.rglob("*_beschriftet.jpg"))
        self.assertEqual(len(ann), 1)

        out = run("backfill.py", *args, env_extra=self.env)
        self.assertIn("0 App-Stacks zu verarbeiten", out)

        # immich_sync stapelt Original + beschriftete Version, sobald beide in Immich sind
        self.im.add(ann[0].name, LIB_ID)
        out = run("immich_sync.py", "--stacks", str(self.stacks), env_extra=self.env)
        self.assertIn("Original + beschriftet", out)
        (members,) = self.im.stacks.values()
        self.assertEqual(members[0], "a1")

    def test_gallery_shows_app_stack(self):
        self.im.add("Stacked_M 31_x.jpg", data=M31_JPG.read_bytes())
        run("backfill.py", "--stacks", str(self.stacks), "--export", str(self.export), "--since", "2026-10-01",
            "--catalog", str(FIXTURE), env_extra=self.env)
        site = self.tmp / "site"
        subprocess.run([sys.executable, str(ROOT / "gallery" / "build" / "gallery_build.py"),
                        "--stacks", str(self.stacks), "--out", str(site)], check=True, capture_output=True)
        cat = json.loads((site / "catalog.json").read_text())
        entries = [v for g in cat["targets"] for v in g["versions"]] if "targets" in cat else cat.get("entries", [])
        self.assertTrue(any(e.get("source") == "app" for e in entries), cat)


if __name__ == "__main__":
    unittest.main()
