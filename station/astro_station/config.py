"""Konfiguration der Astro-Station (~/.config/astro-station.toml) und Standort aus seestar_alp."""
from __future__ import annotations

import json
import os
import tomllib
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
DEFAULT_PATH = CONFIG_DIR / "astro-station.toml"

DEFAULTS = {
    "web": {"host": "127.0.0.1", "port": 8070},
    "location": {"lat": None, "lon": None, "alp_config": "~/Astro/seestar_alp/device/config.toml"},
    "catalog": {"paths": []},
    "device": [
        {"id": "s50", "type": "seestar", "name": "Seestar S50", "alp": "http://localhost:5555", "device_num": 1,
         "imaging": "http://localhost:7556", "webui": "http://localhost:5432", "stellarium_port": 10001},
    ],
}


def load(path: str | Path | None = None) -> dict:
    cfg = json.loads(json.dumps(DEFAULTS))
    p = Path(path).expanduser() if path else DEFAULT_PATH
    if p.exists():
        with open(p, "rb") as f:
            data = tomllib.load(f)
        for sec, vals in data.items():
            if sec == "device":
                cfg["device"] = vals
            elif isinstance(vals, dict):
                cfg.setdefault(sec, {}).update(vals)
    ids = [d.get("id") for d in cfg["device"]]
    if len(set(ids)) != len(ids) or not all(ids):
        raise ValueError("Jedes [[device]] braucht eine eindeutige id")
    return cfg


def location(cfg: dict) -> dict:
    """Standort: feste Werte aus [location], sonst lat/long aus der config.toml von seestar_alp
    (dort traegt field/seestar_location_sync.py GPS- bzw. Seestar-Standort ein)."""
    loc = cfg.get("location", {})
    if loc.get("lat") is not None and loc.get("lon") is not None:
        return {"lat": float(loc["lat"]), "lon": float(loc["lon"]), "source": "Konfiguration"}
    p = Path(str(loc.get("alp_config") or "")).expanduser()
    try:
        with open(p, "rb") as f:
            init = tomllib.load(f).get("seestar_initialization", {})
        lat, lon = float(init.get("lat", 0)), float(init.get("long", 0))
        if lat or lon:
            return {"lat": lat, "lon": lon, "source": "seestar_alp"}
    except (OSError, ValueError, tomllib.TOMLDecodeError):
        pass
    return {"lat": None, "lon": None, "source": "unbekannt"}
