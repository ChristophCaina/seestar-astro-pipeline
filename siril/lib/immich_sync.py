#!/usr/bin/env python3
"""Gruppiert in Immich alle Versionen eines Ziels zu einem Stack und legt sie ins Album.

    immich_sync.py --stacks /data/astro/stacks [--dry-run]

Je Ziel + Filter entsteht EIN Immich-Stack:
  Titelbild  = Siril-Master (falls vorhanden), sonst die neueste Siril-Nacht
  dahinter   = uebrige Siril-Naechte, beschriftete Versionen (*_beschriftet.jpg)
               und die Seestar-App-Stacks vom Handy aus denselben Naechten

App-Stacks werden ueber Aufnahmezeit (Nacht = 12–12 Uhr) und Dateiname erkannt
(IMMICH_APP_PATTERN, Default: "seestar|stack", ohne Gross-/Kleinschreibung).
Nennt der Dateiname ein Objekt (M/NGC/IC + Nummer), muss es zum Ziel passen;
ohne Objekt im Namen nur, wenn in dieser Nacht nur ein Ziel aufgenommen wurde.
Assets der External Library (IMMICH_LIBRARY_ID) gelten nie als App-Stack.

Konfiguration per Umgebung (z. B. aus /etc/siril-auto.env):
  IMMICH_URL, IMMICH_API_KEY, IMMICH_LIBRARY_ID, IMMICH_ALBUM (Default "Astro"),
  IMMICH_APP_PATTERN, TZ_LOCAL (Default Europe/Berlin)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from immich import ImmichError, from_env  # noqa: E402


CAT_TOKEN = re.compile(r"(?<![A-Za-z])(M|NGC|IC)[\s_-]*0*(\d+)(?!\d)", re.I)


def tokens(name: str) -> set[str]:
    """Katalognummern im Dateinamen: 'Stacked_M 31_x.jpg' -> {'M31'}"""
    return {f"{p.upper()}{n}" for p, n in CAT_TOKEN.findall(name)}


def night_window(date: str, tz: ZoneInfo) -> tuple[str, str]:
    d = datetime.strptime(date, "%Y-%m-%d").replace(hour=12, tzinfo=tz)
    return d.isoformat(), (d + timedelta(days=1)).isoformat()


def collect_groups(stacks: Path) -> dict[str, dict]:
    """meta.json-Dateien nach Ziel + Filter gruppieren."""
    groups: dict[str, dict] = {}
    for mp in sorted(stacks.rglob("*.meta.json")):
        m = json.loads(mp.read_text(encoding="utf-8"))
        if m.get("sample") or m.get("source", "siril") != "siril":
            continue
        key = f'{m["target"]["name"]}|{m.get("filter", "")}'
        g = groups.setdefault(key, {"target": m["target"]["name"], "filter": m.get("filter", ""),
                                    "files": [], "nights": set(), "primary": None, "primary_rank": None})
        jpg = (mp.parent / m["image"]).resolve()
        g["files"].append(jpg.name)
        ann = jpg.with_name(jpg.stem + "_beschriftet.jpg")
        if ann.exists():
            g["files"].append(ann.name)
        g["nights"].update(n["date"] for n in m.get("nights", []))
        last = max((n["date"] for n in m.get("nights", [])), default="")
        rank = (1 if m.get("stack") == "master" else 0, last)
        if g["primary_rank"] is None or rank > g["primary_rank"]:
            g["primary"], g["primary_rank"] = jpg.name, rank
    return groups


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stacks", required=True)
    ap.add_argument("--state", help="Default: <stacks>/../.state/immich.json")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-scan", action="store_true", help="Library-Scan nicht anstossen")
    a = ap.parse_args()

    tz = ZoneInfo(os.environ.get("TZ_LOCAL", "Europe/Berlin"))
    lib = os.environ.get("IMMICH_LIBRARY_ID", "")
    album_name = os.environ.get("IMMICH_ALBUM", "Astro")
    app_re = re.compile(os.environ.get("IMMICH_APP_PATTERN", "seestar|stack"), re.I)
    stacks = Path(a.stacks)
    state_path = Path(a.state) if a.state else stacks.parent / ".state" / "immich.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    im = from_env()

    if lib and not a.no_scan and not a.dry_run:
        try:
            im.scan_library(lib)
        except ImmichError as e:
            print(f"WARN: Library-Scan fehlgeschlagen: {e}")

    groups = collect_groups(stacks)
    targets_per_night: dict[str, set[str]] = {}
    for g in groups.values():
        for n in g["nights"]:
            targets_per_night.setdefault(n, set()).add(g["target"])

    album = None
    changed = 0
    for key, g in groups.items():
        own = tokens(g["target"])
        ids: dict[str, str] = {}
        for name in g["files"]:
            hits = [x for x in im.find_by_filename(name) if not lib or x.get("libraryId") == lib]
            if hits:
                ids[name] = hits[0]["id"]
        if g["primary"] not in ids:
            print(f"wartet  {key}: {g['primary']} noch nicht in Immich (Scan läuft?)")
            continue
        app = []
        for night in sorted(g["nights"]):
            after, before = night_window(night, tz)
            for x in im.search(takenAfter=after, takenBefore=before, type="IMAGE"):
                if (lib and x.get("libraryId") == lib) or x["id"] in ids.values():
                    continue
                fn = x.get("originalFileName", "")
                if not app_re.search(fn):
                    continue
                t = tokens(fn)
                # Dateiname nennt ein Objekt -> nur passendes; sonst nur, wenn die Nacht eindeutig ist
                if (t and own and not (t & own)) or (not t and len(targets_per_night.get(night, ())) > 1):
                    continue
                app.append(x["id"])
        members = [ids[g["primary"]]] + [v for k, v in ids.items() if k != g["primary"]] + sorted(set(app))
        prev = state.get(key, {})
        if prev.get("assets") == members:
            continue
        print(f"stack   {key}: {len(members)} Assets ({len(app)} App-Stacks), Titelbild {g['primary']}")
        changed += 1
        if a.dry_run:
            continue
        if prev.get("stack_id"):
            im.delete_stack(prev["stack_id"])
        stack_id = im.create_stack(members) if len(members) > 1 else None
        if album is None:
            album = im.album_id(album_name)
        im.add_to_album(album, members)
        state[key] = {"stack_id": stack_id, "assets": members}

    # Nachgeholte App-Stacks (backfill.py): Original vom Handy + beschriftete Version
    for mp in sorted(stacks.rglob("*.meta.json")):
        m = json.loads(mp.read_text(encoding="utf-8"))
        if m.get("source") != "app":
            continue
        stem = (mp.parent / m["image"]).stem
        key = f"app|{stem}"
        orig = [x for x in im.search(originalFileName=stem) if not (lib and x.get("libraryId") == lib)
                and Path(x.get("originalFileName", "")).stem == stem]
        ann = [x for x in im.find_by_filename(f"{stem}_beschriftet.jpg") if not lib or x.get("libraryId") == lib]
        if not orig or not ann:
            continue
        members = [orig[0]["id"], ann[0]["id"]]
        if state.get(key, {}).get("assets") == members:
            continue
        print(f"stack   {key}: Original + beschriftet")
        changed += 1
        if a.dry_run:
            continue
        if state.get(key, {}).get("stack_id"):
            im.delete_stack(state[key]["stack_id"])
        if album is None:
            album = im.album_id(album_name)
        state[key] = {"stack_id": im.create_stack(members), "assets": members}
        im.add_to_album(album, members)

    if not a.dry_run:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state, indent=2))
    print(f"{changed} Gruppe(n) aktualisiert")
    return 0


if __name__ == "__main__":
    sys.exit(main())
