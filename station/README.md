# Astro-Station – eine Oberfläche für alle Geräte der Session

Web-App (`http://localhost:8070`) für den Feld-Laptop: ein Tab pro Gerät, Geräte pro Session ein- und ausschalten, GoTo aus der Liste „Heute Nacht“, Steuern mit Pfeiltasten, Stellarium als Himmelskarte und ein **Live-Fenster** mit Live-Bild bzw. Stack-Vorschau des Seestar.

```
Stellarium ──Telescope Control :10001/:10002──┐
Browser ─────────────── http :8070 ───────────┤ Astro-Station ──Alpaca :5555──> seestar_alp ──> Seestar
Live-Fenster <── MJPEG :7556 (seestar_alp) ───┘               └──INDI :7624──> indiserver ──> iEXOS + Kamera
```

**Nichts läuft von selbst.** GoTo, Arm ausfahren, Stacking und Serien passieren nur auf Knopfdruck (oder mit Strg+1 in Stellarium). GoTo und Bewegen lehnt die Station ab, solange der Seestar-Arm eingeklappt ist. Die Meldung erscheint in der Web-App und als Desktop-Benachrichtigung.

| Datei | Zweck |
|---|---|
| `astro_station/server.py` | aiohttp: REST-API, Web-App, Stellarium-Ports |
| `astro_station/devices/` | Gerätetypen: `seestar.py` (über seestar_alp), `indi_mount.py` (Montierung + Kamera über INDI, nutzt `pi/astro_pi`) |
| `astro_station/stellarium.py` | Telescope-Control-Protokoll von Stellarium („Externe Software“) |
| `astro_station/tonight.py`, `astro.py` | Nacht, Mond, Sichtbarkeitsfenster, Zielsuche (ohne Zusatzpakete) |
| `astro_station/livewin.py` | Live-Fenster (GTK 3), öffnet sich bei den ersten Bilddaten |
| `astro_station/data/highlights.json` | Zielliste für „Heute Nacht“ (aus OpenNGC, CC-BY-SA 4.0) |
| `tools/fake_seestar.py` | Übungs-Seestar für Tests und Trockenläufe |
| `astro-station.toml.example` | Geräte-Konfiguration (`~/.config/astro-station.toml`) |

## Einrichten

Der Feld-Laptop ist wie in [`field/README.md`](../field/README.md) eingerichtet. Danach:

```bash
sudo apt install python3-aiohttp python3-gi python3-gi-cairo gir1.2-gtk-3.0
sudo apt install python3-numpy python3-pil        # nur für die INDI-Montierung (Kamera-Vorschau)
cd ~/Astro/seestar-astro-pipeline && git pull
bash field/install-desktop.sh --server-alp 192.168.1.XX:5555
```

`install-desktop.sh` legt `~/.config/astro-station.toml` an (aus dem Beispiel), lädt den OpenNGC-Katalog für die Zielsuche und richtet den Dienst `astro-station.service` ein. **Astro-Session starten** startet ihn mit, öffnet die Station im Browser und startet das Live-Fenster unsichtbar im Hintergrund.

## Stellarium verbinden (einmalig)

1. Konfiguration (F2) → Erweiterungen → **Teleskopsteuerung** → „Beim Start laden“, Stellarium neu starten
2. Teleskopsteuerung (Strg+0) → **Hinzufügen**
   - Steuerung: **Externe Software oder ein entfernter Computer**
   - Name: `Seestar S50`, Koordinatensystem: **JNow**
   - Host `localhost`, Port `10001` (iEXOS: `10002`)
   - „Beim Start verbinden“ anhaken
3. Objekt anklicken → **Strg+1** fährt hin. Das Fadenkreuz zeigt, wohin der Seestar gerade schaut.

## Live-Fenster

Geht auf, sobald seestar_alp Bilder vom Seestar liefert. Nach dem GoTo zeigt es die Live-Ansicht, beim Stacking den wachsenden Stack mit Frames, Gesamtzeit, verworfenen Frames und Belichtung. Das Umschalten zwischen Live und Stack macht seestar_alp selbst: Während des Stackings gibt es nur das Stack-Bild.

- **Im Vordergrund** hält das Fenster über Stellarium.
- **Vollbild** (oder F), **Bild speichern** legt eine JPEG-Datei unter `~/Bilder/Astro-Live/` ab.
- Schließen versteckt das Fenster bis zur nächsten Ansicht. Ist eine Ansicht beendet, bleibt das letzte Bild stehen.
- Von Hand: `cd station && python3 -m astro_station.livewin --show`

## Geräte

Jedes `[[device]]` in `~/.config/astro-station.toml` ist ein Gerät. In der Web-App schaltest du oben ein, welche Geräte in dieser Session mitmachen.

- `type = "seestar"`: Seestar über seestar_alp. Ein zweiter Seestar (S30 Pro) wird ein weiteres `[[device]]` mit `device_num = 2`.
- `type = "indi-mount"`: Montierung über INDI, zum Beispiel die iEXOS-100 mit `indi_pmc8_telescope`. Unter **Aufbau** wählst du, welche Kamera (`[[device.cameras]]`) und welche Optik (`[[device.optics]]`) dranhängen. Daraus berechnet die Station Bildfeld und Bildmaßstab (für das Plate-Solving). Jede Aufnahme bekommt Ziel, Montierung, Kamera, Optik, Brennweite und Standort in den FITS-Header (`TELESCOP`, `INSTRUME`, `FOCALLEN`, `APTDIA`, `SITELAT` …). Treiber startet die Station beim Einschalten über die FIFO des laufenden indiserver.

Neuer Gerätetyp: eine Klasse nach `devices/base.py` schreiben und in `devices/__init__.py` unter `TYPES` eintragen.

## Trockenlauf ohne Seestar

```bash
cd ~/Astro/seestar-astro-pipeline/station
python3 tools/fake_seestar.py --offset 10000 &          # Übungs-Seestar auf :15555/:15432/:17556
cat > /tmp/uebung.toml <<'EOF'
[web]
port = 18070
[[device]]
id = "s50"
type = "seestar"
name = "Übungs-Seestar"
alp = "http://localhost:15555"
imaging = "http://localhost:17556"
webui = "http://localhost:15432"
stellarium_port = 10001
EOF
python3 -m astro_station.server --config /tmp/uebung.toml &
python3 -m astro_station.livewin --station http://localhost:18070 &
xdg-open http://localhost:18070
```

Arm ausfahren → Ziel wählen → GoTo: Das Live-Fenster geht auf. Danach „Stacking starten“: Das Fenster zeigt Frames und Gesamtzeit.

## API

| Methode | Pfad | Body |
|---|---|---|
| GET | `/api/state` | – (Geräte, Standort, Nacht, Meldungen) |
| GET | `/api/tonight`, `/api/search?q=` | – |
| GET | `/api/live` | – (Seestars mit Bildserver-Adresse und Stack-Zahlen, für das Live-Fenster) |
| POST | `/api/session` | `{"id": "iexos", "enabled": true}` |
| POST | `/api/dev/<id>/goto` | `{"target": {...aus /api/tonight}, "center": false}` |
| POST | `/api/dev/<id>/move` | `{"dir": "N", "speed": 0..3, "on": true}` |
| POST | `/api/dev/<id>/action` | `{"action": "unfold" \| "park" \| "live" \| "stack" \| "moon" \| "sun" \| "stop" \| "rig" \| "series" …, "params": {}}` |
| POST | `/api/stop_all` | – |
