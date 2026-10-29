"""Live-Fenster: Live-Bild bzw. Stack-Vorschau des Seestar in einem eigenen Fenster neben Stellarium.

    python3 -m astro_station.livewin [--station http://localhost:8070] [--device s50] [--show]

Laeuft unsichtbar im Hintergrund (Start ueber "Astro-Session starten") und geht von selbst auf, sobald
der Seestar Bilder liefert: nach dem GoTo die Live-Ansicht, beim Stacking der wachsende Stack.
"Im Vordergrund" haelt es ueber Stellarium. Am Ende bleibt das letzte Bild stehen.
Schliessen versteckt das Fenster bis zur naechsten Ansicht.

Braucht GTK 3 fuer Python (Linux Mint: vorinstalliert, sonst sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-3.0).
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
try:
    gi.require_foreign("cairo")
except ImportError:
    raise SystemExit("Für das Live-Fenster fehlt python3-gi-cairo:  sudo apt install python3-gi-cairo") from None
from gi.repository import GdkPixbuf, GLib, Gtk  # noqa: E402

from .livestream import frames  # noqa: E402

CSS = b"""
window, .bar { background: #0a0e1a; color: #e8ebf4; }
.bar { background: #121829; padding: 6px 10px; }
.muted { color: #a4adc5; font-size: 90%; }
.title { font-weight: 600; }
.value { font-family: monospace; font-size: 110%; }
.pill { background: #1a2238; border-radius: 6px; padding: 2px 8px; }
button { background: #1a2238; color: #e8ebf4; border: 1px solid #2b3654; border-radius: 6px; padding: 4px 10px;
         background-image: none; text-shadow: none; box-shadow: none; }
button:checked { border-color: #f2a93b; }
"""
SAVE_DIR = Path(os.environ.get("XDG_PICTURES_DIR", Path.home() / "Bilder")) / "Astro-Live"


def fetch_json(url: str, timeout: float = 4):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode())


class LiveWindow(Gtk.Window):
    def __init__(self, dev: dict):
        super().__init__(title=f"{dev['name']} – Live")
        self.dev_id, self.name = dev["id"], dev["name"]
        self.pixbuf: GdkPixbuf.Pixbuf | None = None
        self.jpeg: bytes | None = None
        self.info: dict = dev
        self.dismissed: float | None = None          # live.since beim Schliessen – erst neue Ansicht oeffnet wieder
        self.fullscreen_on = False
        self.set_default_size(460, 780)
        self.set_keep_above(True)
        self.connect("delete-event", self._hide)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(box)
        top = Gtk.Box(spacing=8)
        top.get_style_context().add_class("bar")
        self.title = Gtk.Label(label=self.name, xalign=0)
        self.title.get_style_context().add_class("title")
        top.pack_start(self.title, True, True, 0)
        self.pin = Gtk.ToggleButton(label="Im Vordergrund")
        self.pin.set_active(True)
        self.pin.connect("toggled", lambda b: self.set_keep_above(b.get_active()))
        top.pack_end(self.pin, False, False, 0)
        box.pack_start(top, False, False, 0)

        self.area = Gtk.DrawingArea()
        self.area.connect("draw", self._draw)
        box.pack_start(self.area, True, True, 0)

        grid = Gtk.Grid(column_spacing=12, row_spacing=2, column_homogeneous=True)
        grid.get_style_context().add_class("bar")
        self.stats = []
        for i in range(4):
            lab, val = Gtk.Label(xalign=0), Gtk.Label(xalign=0)
            lab.get_style_context().add_class("muted")
            val.get_style_context().add_class("value")
            grid.attach(lab, i, 0, 1, 1)
            grid.attach(val, i, 1, 1, 1)
            self.stats.append((lab, val))
        box.pack_start(grid, False, False, 0)

        bottom = Gtk.Box(spacing=6)
        bottom.get_style_context().add_class("bar")
        self.status = Gtk.Label(label="wartet auf Bilder …", xalign=0)
        self.status.get_style_context().add_class("muted")
        self.status.set_ellipsize(3)
        bottom.pack_start(self.status, True, True, 0)
        save = Gtk.Button(label="Bild speichern")
        save.connect("clicked", self._save)
        full = Gtk.Button(label="Vollbild")
        full.connect("clicked", self._toggle_full)
        bottom.pack_end(full, False, False, 0)
        bottom.pack_end(save, False, False, 0)
        box.pack_start(bottom, False, False, 0)
        self.connect("key-press-event", self._key)
        self.update_info(dev)

    # ------------------------------------------------------------ Anzeige
    def _draw(self, area, cr):
        w, h = area.get_allocated_width(), area.get_allocated_height()
        cr.set_source_rgb(0.02, 0.03, 0.05)
        cr.paint()
        if not self.pixbuf:
            return False
        pw, ph = self.pixbuf.get_width(), self.pixbuf.get_height()
        s = min(w / pw, h / ph)
        sw, sh = max(1, int(pw * s)), max(1, int(ph * s))
        scaled = self.pixbuf.scale_simple(sw, sh, GdkPixbuf.InterpType.BILINEAR)
        from gi.repository import Gdk
        Gdk.cairo_set_source_pixbuf(cr, scaled, (w - sw) / 2, (h - sh) / 2)
        cr.paint()
        target = self.info.get("target")
        if target:
            cr.set_source_rgba(0.04, 0.05, 0.1, 0.8)
            cr.rectangle(10, 10, 12 + 7.5 * len(target), 24)
            cr.fill()
            cr.set_source_rgb(0.91, 0.92, 0.96)
            cr.select_font_face("Sans")
            cr.set_font_size(13)
            cr.move_to(16, 27)
            cr.show_text(target)
        return False

    def show_jpeg(self, data: bytes):
        try:
            loader = GdkPixbuf.PixbufLoader.new_with_type("jpeg")
            loader.write(data)
            loader.close()
            self.pixbuf, self.jpeg = loader.get_pixbuf(), data
        except GLib.Error:
            return
        self.status.set_text(f"letztes Bild {time.strftime('%H:%M:%S')}")
        self.area.queue_draw()

    def update_info(self, info: dict):
        self.info = info
        mode = "Stacking" if info.get("stacking") else ("Live" if info.get("active") else "beendet")
        self.title.set_text(f"{self.name} · {mode}" + (f" · {info['mode']}" if info.get("mode") and info.get("active") else ""))

        def num(v, unit=""):
            return "–" if v is None else (f"{v:g}".replace(".", ",") + unit)
        if info.get("stacking"):
            vals = [("Frames", num(info.get("frames"))), ("Gesamt", num(info.get("total_min"), " min")),
                    ("verworfen", num(info.get("dropped"))), ("Einzelbild", num(info.get("exposure_s"), " s"))]
        else:
            since = info.get("since")
            dur = f"{int((time.time() - since) // 60)} min" if since else "–"
            vals = [("Vorgang", info.get("stage") or "–"), ("Modus", info.get("mode") or "–"),
                    ("Belichtung", num(info.get("exposure_s"), " s")), ("seit", dur)]
        for (lab, val), (a, b) in zip(self.stats, vals):
            lab.set_text(a)
            val.set_text(b)
        if not info.get("active") and self.pixbuf:
            self.status.set_text("Ansicht beendet – letztes Bild bleibt stehen")

    def popup(self):
        if self.dismissed is not None and self.dismissed == self.info.get("since"):
            return
        if not self.get_visible():
            self.show_all()
        self.present()

    # ------------------------------------------------------------ Bedienung
    def _hide(self, *_):
        self.dismissed = self.info.get("since")
        self.hide()
        return True

    def _save(self, *_):
        if not self.jpeg:
            return
        SAVE_DIR.mkdir(parents=True, exist_ok=True)
        t = "".join(c if c.isalnum() or c in " +-_" else "_" for c in (self.info.get("target") or "Seestar")).strip()
        p = SAVE_DIR / f"{t.replace(' ', '_')}_{datetime.now():%Y%m%d-%H%M%S}.jpg"
        p.write_bytes(self.jpeg)
        self.status.set_text(f"gespeichert: {p}")

    def _toggle_full(self, *_):
        self.fullscreen_on = not self.fullscreen_on
        (self.fullscreen if self.fullscreen_on else self.unfullscreen)()

    def _key(self, _w, ev):
        from gi.repository import Gdk
        if ev.keyval in (Gdk.KEY_Escape,) and self.fullscreen_on:
            self._toggle_full()
        elif ev.keyval in (Gdk.KEY_f, Gdk.KEY_F11):
            self._toggle_full()


class Watcher(threading.Thread):
    """Fragt die Station, ob eine Ansicht laeuft, und liest dann den MJPEG-Strom."""

    def __init__(self, station: str, dev_id: str, app):
        super().__init__(daemon=True)
        self.station, self.dev_id, self.app = station.rstrip("/"), dev_id, app
        self.stopped = False

    def info(self) -> dict | None:
        try:
            return next((d for d in fetch_json(f"{self.station}/api/live") if d["id"] == self.dev_id), None)
        except (OSError, ValueError):
            return None

    def run(self):
        while not self.stopped:
            info = self.info()
            if info:
                GLib.idle_add(self.app.info, info)
            if info and info.get("active") and info.get("mjpeg"):
                ticker = threading.Thread(target=self._tick, daemon=True)
                self._streaming = True
                ticker.start()
                try:
                    for jpeg in frames(info["mjpeg"], lambda: self.stopped):
                        GLib.idle_add(self.app.frame, self.dev_id, jpeg)
                except OSError:
                    pass
                self._streaming = False
                time.sleep(1)
            else:
                time.sleep(2)

    def _tick(self):
        """Waehrend des Stroms Stack-Zahlen weiter aktualisieren."""
        while getattr(self, "_streaming", False) and not self.stopped:
            time.sleep(3)
            info = self.info()
            if info:
                GLib.idle_add(self.app.info, info)


class App:
    def __init__(self, station: str, only: str | None, show: bool):
        self.station, self.only, self.show = station, only, show
        self.windows: dict[str, LiveWindow] = {}
        self.watchers: dict[str, Watcher] = {}
        prov = Gtk.CssProvider()
        prov.load_from_data(CSS)
        from gi.repository import Gdk
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        GLib.timeout_add_seconds(5, self.discover)
        self.discover()

    def discover(self):
        """Neue Seestars bei der Station finden (Geraet in der Session eingeschaltet)."""
        try:
            devs = fetch_json(f"{self.station}/api/live")
        except (OSError, ValueError):
            return True
        for d in devs:
            if (self.only and d["id"] != self.only) or d["id"] in self.windows:
                continue
            self.windows[d["id"]] = LiveWindow(d)
            if self.show:
                self.windows[d["id"]].show_all()
            w = Watcher(self.station, d["id"], self)
            self.watchers[d["id"]] = w
            w.start()
        return True

    def info(self, info: dict):
        w = self.windows.get(info["id"])
        if w:
            w.update_info(info)
        return False

    def frame(self, dev_id: str, jpeg: bytes):
        w = self.windows.get(dev_id)
        if w:
            first = w.pixbuf is None or not w.get_visible()
            w.show_jpeg(jpeg)
            if first:
                w.popup()
        return False


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--station", default="http://localhost:8070")
    ap.add_argument("--device", help="nur dieses Gerät (id aus astro-station.toml)")
    ap.add_argument("--show", action="store_true", help="Fenster sofort zeigen, nicht erst bei Bildern")
    a = ap.parse_args()
    App(a.station, a.device, a.show)
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, Gtk.main_quit)
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, Gtk.main_quit)
    Gtk.main()


if __name__ == "__main__":
    main()
