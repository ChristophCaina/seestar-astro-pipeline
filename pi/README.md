# Astro-Pi – INDI-Steuerung mit Web-App

Python-Dienst (`astro_pi/`) und Web-App (`web/`, PWA) für Montierung und Kamera über INDI. Einrichtung, Bedienung und Hardware: [docs/07](../docs/07-astro-pi.md).

| Datei | Zweck |
|---|---|
| `astro_pi/indi.py` | asyncio-Client für das INDI-Protokoll (Properties, BLOBs, Setzen) |
| `astro_pi/control.py` | GoTo, Zentrieren per Plate-Solving, Serien, Live View, Dateinamen für siril-auto |
| `astro_pi/image.py` | FITS lesen, Debayern, Strecken, JPEG, HFR |
| `astro_pi/targets.py` | Zielsuche im OpenNGC-Katalog |
| `astro_pi/mqtt.py` | Status + Vorschau an Home Assistant (nur Anzeige) |
| `astro_pi/server.py` | aiohttp: REST, WebSocket, Web-App |
| `astro-pi.toml.example` | Konfiguration (`/etc/astro-pi.toml`) |
| `systemd/` | `indiserver`, `astro-pi`, `novnc` |

## Lokal ausprobieren (ohne Hardware)

```bash
sudo apt install indi-bin python3-aiohttp python3-numpy python3-pil
indiserver -p 7624 indi_simulator_telescope indi_simulator_ccd &
cd pi && python3 -m astro_pi.server --config ../pi/astro-pi.toml.example --port 8080
# → http://localhost:8080
```

## API

| Methode | Pfad | Body |
|---|---|---|
| GET | `/api/state` | – |
| GET | `/api/targets?q=m31` | – |
| POST | `/api/mount/goto` | `{"name": "M 31"}` oder `{"ra": 0.71, "dec": 41.3, "center": true}` (RA in Stunden) |
| POST | `/api/mount/park` / `track` / `move` | `{"park": true}` / `{"on": true}` / `{"dir": "N", "on": true}`, `{"rate": 2}` |
| POST | `/api/camera/capture` | `{"exposure": 5, "save": false, "iso": "800"}` |
| POST | `/api/camera/series` | `{"exposure": 60, "count": 30, "delay": 2, "target": "M 31"}` / `{"stop": true}` |
| POST | `/api/camera/live` | `{"on": true}` |
| POST | `/api/solve` | `{"sync": true}` |
| POST | `/api/abort` | – |
| GET | `/api/preview.jpg`, `/api/live.jpg` | – |
| WS | `/ws` | Ereignisse `state`, `preview`, `live`, `center`, `done`, `error` |
