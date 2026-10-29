#!/usr/bin/env python3
"""Standort fuer seestar_alp automatisch setzen (Feld-Laptop, Astro-Pi).

Quellen, in dieser Reihenfolge (--source auto):
  1. gps   – ein GPS-Empfaenger ueber gpsd (localhost:2947), braucht einen 2D/3D-Fix
  2. scope – der Standort, den der Seestar selbst kennt (setzt die Seestar-App vom Handy),
             gelesen ueber seestar_alp: get_device_state -> location_lon_lat

Gefunden wird ein Standort -> lat/long in [seestar_initialization] der config.toml
eintragen (seestar_alp liest die Datei bei Aenderung neu ein). Mit --push wird er
zusaetzlich per set_user_location an den Seestar geschickt – sinnvoll bei GPS, weil
der Seestar sonst den alten Standort behaelt.

Nichts gefunden -> nichts aendern. Nie 0/0 schreiben: seestar_alp wuerde 0/0 als
"per IP-Adresse raten" verstehen und ohne Internet im Golf von Guinea landen.

Nur Python-Standardbibliothek.

  seestar_location_sync.py --config ~/Astro/seestar_alp/device/config.toml [--push]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import socket
import sys
import time
import urllib.parse
import urllib.request

SECTION = "seestar_initialization"
#: Kleinere Aenderungen (ca. 1 km) werden ignoriert, damit die Datei nicht staendig wechselt.
MIN_CHANGE_DEG = 0.01


def log(msg: str) -> None:
    print(f"[standort] {msg}", flush=True)


def valid(lat, lon) -> bool:
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return False
    if not (math.isfinite(lat) and math.isfinite(lon)):
        return False
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return False
    return not (abs(lat) < 1e-6 and abs(lon) < 1e-6)


# --------------------------------------------------------------------- GPS (gpsd)
def from_gpsd(host: str = "127.0.0.1", port: int = 2947, timeout: float = 30.0):
    """(lat, lon) aus dem ersten TPV-Bericht mit 2D/3D-Fix, sonst None."""
    deadline = time.monotonic() + timeout
    try:
        sock = socket.create_connection((host, port), timeout=5)
    except OSError as exc:
        log(f"gpsd nicht erreichbar ({exc})")
        return None
    with sock:
        sock.sendall(b'?WATCH={"enable":true,"json":true};\n')
        buf = b""
        while time.monotonic() < deadline:
            sock.settimeout(max(0.5, deadline - time.monotonic()))
            try:
                chunk = sock.recv(4096)
            except (socket.timeout, OSError):
                break
            if not chunk:
                break
            buf += chunk
            *lines, buf = buf.split(b"\n")
            for line in lines:
                pos = parse_gpsd_line(line)
                if pos:
                    return pos
    log("kein GPS-Fix innerhalb der Wartezeit")
    return None


def parse_gpsd_line(line: bytes | str):
    try:
        msg = json.loads(line)
    except (ValueError, TypeError):
        return None
    if not isinstance(msg, dict) or msg.get("class") != "TPV" or (msg.get("mode") or 0) < 2:
        return None
    lat, lon = msg.get("lat"), msg.get("lon")
    return (round(float(lat), 5), round(float(lon), 5)) if valid(lat, lon) else None


# ------------------------------------------------------------- Seestar (seestar_alp)
def alpaca_action(base: str, device: int, action: str, params: dict, timeout: float = 20.0):
    data = urllib.parse.urlencode({
        "Action": action, "Parameters": json.dumps(params),
        "ClientID": "1", "ClientTransactionID": "1",
    }).encode()
    req = urllib.request.Request(f"{base.rstrip('/')}/api/v1/telescope/{device}/action",
                                 data=data, method="PUT")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def from_scope(base: str, device: int):
    try:
        reply = alpaca_action(base, device, "method_sync", {"method": "get_device_state"})
    except (OSError, ValueError) as exc:
        log(f"seestar_alp nicht erreichbar ({exc})")
        return None
    return parse_device_state(reply)


def parse_device_state(reply):
    """location_lon_lat aus einer get_device_state-Antwort -> (lat, lon)."""
    value = reply.get("Value") if isinstance(reply, dict) else None
    result = value.get("result") if isinstance(value, dict) else None
    lonlat = result.get("location_lon_lat") if isinstance(result, dict) else None
    if not (isinstance(lonlat, (list, tuple)) and len(lonlat) == 2):
        return None
    lon, lat = lonlat                      # Reihenfolge: [Laenge, Breite]
    return (round(float(lat), 5), round(float(lon), 5)) if valid(lat, lon) else None


def push_to_scope(base: str, device: int, lat: float, lon: float) -> bool:
    try:
        reply = alpaca_action(base, device, "method_sync", {
            "method": "set_user_location", "params": {"lat": lat, "lon": lon, "force": True}})
    except (OSError, ValueError) as exc:
        log(f"Standort nicht an den Seestar gesendet ({exc})")
        return False
    ok = isinstance(reply, dict) and reply.get("ErrorNumber", 0) == 0
    log("Standort an den Seestar gesendet" if ok else f"Seestar lehnt ab: {reply}")
    return ok


# ----------------------------------------------------------------- config.toml
_SECTION_RE = re.compile(r"^\s*\[([^\[\]]+)\]\s*(#.*)?$")
_KEY_RE = re.compile(r"^(\s*)(lat|long)(\s*=\s*)([^#\n]*?)(\s*(#.*)?)$")


def read_location(text: str):
    """(lat, long) aus [seestar_initialization], fehlende Werte als None."""
    found, section = {}, None
    for line in text.splitlines():
        m = _SECTION_RE.match(line)
        if m:
            section = m.group(1).strip()
            continue
        if section == SECTION:
            k = _KEY_RE.match(line)
            if k:
                try:
                    found[k.group(2)] = float(k.group(4).strip().strip("'\""))
                except ValueError:
                    pass
    return found.get("lat"), found.get("long")


def write_location(text: str, lat: float, lon: float) -> str:
    """lat/long in [seestar_initialization] ersetzen, Kommentare und Rest unveraendert."""
    out, section, done, seen = [], None, set(), False
    for line in text.splitlines(keepends=True):
        m = _SECTION_RE.match(line.rstrip("\n"))
        if m:
            if section == SECTION:
                out.extend(_missing(done, lat, lon))
            section = m.group(1).strip()
            seen = seen or section == SECTION
            out.append(line)
            continue
        if section == SECTION:
            k = _KEY_RE.match(line.rstrip("\n"))
            if k:
                key = k.group(2)
                value = lat if key == "lat" else lon
                nl = "\n" if line.endswith("\n") else ""
                out.append(f"{k.group(1)}{key}{k.group(3)}{value}{k.group(5)}{nl}")
                done.add(key)
                continue
        out.append(line)
    if section == SECTION:
        if out and not out[-1].endswith("\n"):
            out.append("\n")
        out.extend(_missing(done, lat, lon))
    elif not seen:
        out.append(f"\n[{SECTION}]\n")
        out.extend(_missing(set(), lat, lon))
    return "".join(out)


def _missing(done, lat, lon):
    return [f"{k} = {v}\n" for k, v in (("lat", lat), ("long", lon)) if k not in done]


def update_config(path: str, lat: float, lon: float) -> bool:
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    old_lat, old_lon = read_location(text)
    if (old_lat is not None and old_lon is not None
            and abs(old_lat - lat) < MIN_CHANGE_DEG and abs(old_lon - lon) < MIN_CHANGE_DEG):
        log(f"Standort unveraendert ({lat}, {lon})")
        return False
    new = write_location(text, lat, lon)
    # Bewusst IN die Datei schreiben (kein Ersetzen per rename): seestar_alp laedt die
    # config.toml nur bei einem "modified"-Ereignis auf genau diesem Pfad neu
    # (root_app.ConfigChangeHandler.on_modified). Vorher eine Sicherung.
    with open(path + ".bak", "w", encoding="utf-8") as fh:
        fh.write(text)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(new)
    log(f"config.toml: lat {old_lat} -> {lat}, long {old_lon} -> {lon}")
    return True


# ------------------------------------------------------------------------ main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--config", required=True, help="Pfad zur config.toml von seestar_alp")
    ap.add_argument("--source", choices=("auto", "gps", "scope"), default="auto")
    ap.add_argument("--alpaca", default="http://localhost:5555", help="seestar_alp (Alpaca)")
    ap.add_argument("--device", type=int, default=1, help="device_num des Seestar")
    ap.add_argument("--gpsd", default="127.0.0.1:2947")
    ap.add_argument("--gps-timeout", type=float, default=30.0)
    ap.add_argument("--push", action="store_true",
                    help="GPS-Standort zusaetzlich an den Seestar senden (set_user_location)")
    args = ap.parse_args(argv)

    pos, src = None, None
    if args.source in ("auto", "gps"):
        host, _, port = args.gpsd.partition(":")
        pos, src = from_gpsd(host, int(port or 2947), args.gps_timeout), "GPS"
    if pos is None and args.source in ("auto", "scope"):
        pos, src = from_scope(args.alpaca, args.device), "Seestar"
    if pos is None:
        log("kein Standort gefunden – config.toml bleibt unveraendert")
        return 1

    lat, lon = pos
    log(f"Standort aus {src}: {lat}, {lon}")
    update_config(os.path.expanduser(args.config), lat, lon)
    if args.push and src == "GPS":
        push_to_scope(args.alpaca, args.device, lat, lon)
    return 0


if __name__ == "__main__":
    sys.exit(main())
