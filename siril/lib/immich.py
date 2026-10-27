"""Kleiner Immich-API-Client (nur Standardbibliothek).

Genutzte Endpunkte (Immich >= 1.122):
  POST   /api/search/metadata        Assets nach Dateiname / Zeitraum suchen
  POST   /api/libraries/{id}/scan    External Library neu einlesen
  GET    /api/assets/{id}/original   Original herunterladen
  POST   /api/stacks                 Stack anlegen (erstes Asset = Titelbild)
  DELETE /api/stacks/{id}            Stack aufloesen
  GET    /api/albums                 Alben auflisten
  POST   /api/albums                 Album anlegen
  PUT    /api/albums/{id}/assets     Assets zum Album hinzufuegen
  DELETE /api/albums/{id}/assets     Assets aus dem Album nehmen (bleiben in der Bibliothek)
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path


class ImmichError(RuntimeError):
    pass


class Immich:
    def __init__(self, url: str, api_key: str, timeout: int = 30):
        self.base = url.rstrip("/")
        if not self.base.endswith("/api"):
            self.base += "/api"
        self.key = api_key
        self.timeout = timeout

    # ---------------------------------------------------------------- HTTP
    def _req(self, method: str, path: str, body=None, raw: bool = False):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header("x-api-key", self.key)
        req.add_header("Accept", "application/octet-stream" if raw else "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                payload = r.read()
        except urllib.error.HTTPError as e:
            raise ImmichError(f"{method} {path}: HTTP {e.code} {e.read()[:200]!r}") from None
        except urllib.error.URLError as e:
            raise ImmichError(f"{method} {path}: {e.reason}") from None
        if raw:
            return payload
        return json.loads(payload) if payload else None

    # ---------------------------------------------------------------- Assets
    def search(self, **filters) -> list[dict]:
        """Metadaten-Suche, alle Seiten. Filter z. B. originalFileName, takenAfter, takenBefore, type."""
        out, page = [], 1
        while page:
            res = self._req("POST", "/search/metadata", {**filters, "page": page, "size": 250, "withExif": True})
            assets = (res or {}).get("assets", {})
            out.extend(assets.get("items", []))
            nxt = assets.get("nextPage")
            page = int(nxt) if nxt else 0
        return out

    def find_by_filename(self, name: str) -> list[dict]:
        return [a for a in self.search(originalFileName=name) if a.get("originalFileName") == name]

    def download_original(self, asset_id: str, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self._req("GET", f"/assets/{asset_id}/original", raw=True))
        return dest

    def scan_library(self, library_id: str) -> None:
        self._req("POST", f"/libraries/{library_id}/scan", {})

    # ---------------------------------------------------------------- Stacks
    def create_stack(self, asset_ids: list[str]) -> str:
        res = self._req("POST", "/stacks", {"assetIds": asset_ids})
        return res["id"]

    def delete_stack(self, stack_id: str) -> None:
        try:
            self._req("DELETE", f"/stacks/{stack_id}")
        except ImmichError as e:          # schon geloescht – egal
            if "404" not in str(e) and "400" not in str(e):
                raise

    # ---------------------------------------------------------------- Alben
    def album_id(self, name: str, create: bool = True) -> str | None:
        for al in self._req("GET", "/albums") or []:
            if al.get("albumName") == name:
                return al["id"]
        if not create:
            return None
        return self._req("POST", "/albums", {"albumName": name})["id"]

    def add_to_album(self, album_id: str, asset_ids: list[str]) -> None:
        if asset_ids:
            self._req("PUT", f"/albums/{album_id}/assets", {"ids": asset_ids})

    def remove_from_album(self, album_id: str, asset_ids: list[str]) -> None:
        if asset_ids:
            self._req("DELETE", f"/albums/{album_id}/assets", {"ids": asset_ids})


def from_env() -> Immich:
    url, key = os.environ.get("IMMICH_URL"), os.environ.get("IMMICH_API_KEY")
    if not url or not key:
        raise SystemExit("IMMICH_URL und IMMICH_API_KEY setzen (z. B. in /etc/siril-auto.env)")
    return Immich(url, key)
