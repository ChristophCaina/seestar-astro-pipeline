# Feld-Laptop: seestar_alp, INDI und Standort

Ein Linux-Laptop (getestet mit Linux Mint 22) als Steuerrechner im Feld. Dort laufen seestar_alp, der INDI-Server mit dem Seestar-Treiber für KStars/Ekos und ein kleiner Dienst, der den Standort aktuell hält.

```
KStars/Ekos ──INDI :7624──> indiserver ──> indi/seestar.py ──Alpaca :5555──> seestar_alp ──> Seestar
                                                                  ▲
                         seestar_location_sync.py ── GPS (gpsd) ──┘  oder Standort aus der Seestar-App
```

Zu Hause übernimmt seestar_alp im LXC (`docs/01-seestar-alp-lxc.md`). **Pro Seestar läuft immer nur ein seestar_alp.** Ist der Seestar im Heim-WLAN, den Laptop-Dienst aus lassen oder den Dienst im LXC vorher stoppen.

## Voraussetzungen

- seestar_alp unter `~/Astro/seestar_alp` mit venv (`venv/`), `device/config.toml` eingerichtet (Seestar `ip_address = "seestar.local"`, `interop_pem`)
- `sudo apt install indi-bin kstars`
- dieses Repo unter `~/Astro/seestar-astro-pipeline` (`git clone`)
- optional für GPS: USB-GPS-Empfänger und `sudo apt install gpsd gpsd-clients` (Test: `cgps`)

Andere Pfade: in den Dateien unter `systemd/` anpassen.

## Einrichten mit Desktop-Starter (empfohlen)

```bash
bash ~/Astro/seestar-astro-pipeline/field/install-desktop.sh --server-alp 192.168.1.XX:5555
```

`--server-alp` ist die Adresse von seestar_alp im Heimnetz (LXC). Läuft der Dienst dort, fragt der Starter nach, bevor er einen zweiten seestar_alp startet.

Das Skript kopiert die Dienste nach `~/.config/systemd/user`, schaltet den Autostart beim Anmelden ab und legt auf dem Desktop und im Menü zwei Starter an:

- **Astro-Session starten**: seestar_alp, INDI-Server und Standort-Timer starten, warten bis seestar_alp antwortet, dann Web-UI (`http://localhost:5432`) und Stellarium öffnen. Fortschritt als Desktop-Benachrichtigung.
- **Astro-Session beenden**: alles wieder stoppen.

Einstellungen in `~/.config/astro-session.env` (`OPEN_STELLARIUM=0`, `OPEN_BROWSER=0`, andere `WEBUI_URL`). Im Terminal: `field/astro-session.sh start|stop|status`.

## Dienste einrichten (systemd --user, ohne Starter)

```bash
mkdir -p ~/.config/systemd/user
cp ~/Astro/seestar-astro-pipeline/field/systemd/* ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now seestar-alp.service seestar-indi.service seestar-location.timer
```

Status und Log:

```bash
systemctl --user status seestar-alp seestar-indi --no-pager
journalctl --user -u seestar-alp -f
```

Zu Hause stoppen: `systemctl --user stop seestar-alp` (stoppt den INDI-Server mit). Autostart abschalten: `systemctl --user disable seestar-alp seestar-indi`.

Die Dienste laufen, solange du angemeldet bist. Sollen sie schon beim Hochfahren starten, ohne Anmeldung: `sudo loginctl enable-linger $USER`.

## Standort

`seestar_location_sync.py` trägt den Standort in `[seestar_initialization]` der `config.toml` ein (`lat`, `long`). seestar_alp liest die Datei bei Änderungen neu ein.

| Quelle | Wann | Was passiert |
|---|---|---|
| **GPS** (gpsd, `localhost:2947`) | ein Empfänger mit 2D/3D-Fix | Standort in die Config **und** per `set_user_location` an den Seestar (`--push`) |
| **Seestar** (`get_device_state` → `location_lon_lat`) | kein GPS – den Standort hat die Seestar-App vom Handy gesetzt | nur in die Config (der Seestar kennt ihn ja schon) |
| nichts gefunden | – | nichts ändern |

Der Timer prüft 2 Minuten nach dem Start und danach alle 15 Minuten. Änderungen unter etwa 1 km werden ignoriert. 0/0 wird nie geschrieben: seestar_alp würde 0/0 als „per IP-Adresse raten“ verstehen, und ohne Internet ginge das schief.

Von Hand:

```bash
python3 ~/Astro/seestar-astro-pipeline/field/seestar_location_sync.py \
  --config ~/Astro/seestar_alp/device/config.toml            # auto: GPS, sonst Seestar
  # --source gps|scope   --push   --gps-timeout 60
```

**Unterwegs ohne GPS:** einmal die Seestar-App öffnen und verbinden, dann übernimmt der Timer den Standort aus dem Seestar.

KStars hat einen eigenen Standort (Strg+G). Mit GPS lässt er sich über einen INDI-GPS-Treiber ebenfalls automatisch setzen.

## KStars mit dem Seestar verbinden

1. Werkzeuge → Geräte → **Geräte-Manager** → Reiter **Client** → Hinzufügen: Host `localhost`, Port `7624` → Verbinden
2. Im INDI-Bedienfeld beim Gerät **Seestar S50** auf **Connect**
3. Rechtsklick auf ein Objekt → Seestar S50 → **Slew**

Vor dem ersten GoTo muss der Arm ausgefahren sein: Web-UI `http://localhost:5432` → Command → „Scope Move to Horizon“.

`indi_getprop` zeigt beim Seestar-Treiber immer RA 0 / Dec −90: Der Treiber meldet bei jeder neuen Abfrage zuerst seine Startwerte, die echte Position kommt erst mit der nächsten Aktualisierung (alle 2 s). KStars bleibt verbunden und sieht die echten Werte.

## Stellarium

Die Stellarium-Pakete für Ubuntu 24.04 / Mint 22 (PPA, Flatpak) haben keine INDI-Teleskopsteuerung: Stellarium 26.x braucht INDI 2.x, Ubuntu 24.04 liefert 1.9.9. Stellarium also zum Planen nutzen und mit KStars steuern, oder für Deep-Sky-Ziele in der Web-UI von seestar_alp *Goto Target → Retrieve RA/Dec from Stellarium* (Plugin *Remote Control*, Port 8090, `sthost`/`stport` in der config.toml).
