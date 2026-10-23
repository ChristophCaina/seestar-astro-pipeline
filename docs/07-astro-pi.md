# Astro-Pi: Montierung und DSLR am Tablet

Ein Raspberry Pi am Stativ steuert die Montierung (Explore Scientific **iEXOS-100**, alternativ eine Celestron **NexStar**) und die **Canon EOS 6D**. Bedient wird alles im Browser oder als App am Tablet. Die Aufnahmen landen in der Namenskonvention des Seestar, damit siril-auto sie stacken kann.

```
Tablet / Handy ──WLAN──> GL.iNet ──> Raspberry Pi ──USB──> iEXOS-100 (PMC-Eight, seriell)
   Web-App :8080                         │            └─USB──> EOS 6D (gphoto, Live View)
   Ekos    :6080 (noVNC)                 ├─ indiserver (Treiber als Dienst)
                                         ├─ astro-pi (Web-App, Plate-Solving, Serien)
                                         └─ MQTT ──Tailscale──> Home Assistant (nur Anzeige)
```

## Hardware

| Teil | Hinweis |
|---|---|
| Raspberry Pi 5, 8 GB (4 GB geht) | Plate-Solving und KStars profitieren von RAM |
| Original-Netzteil 27 W | Pi 5 braucht 5 A, sonst Drosselung und USB-Abbrüche |
| microSD 128 GB oder NVMe | Aufnahmen: 6D-FITS ca. 40 MB pro Bild |
| Akku-Attrappe LP-E6 (DC-Kuppler) | Kamera läuft die ganze Nacht |
| Kabel iEXOS-100 → USB | seriell (laut INDI-Doku stabiler als WLAN) |
| NexStar GT: Handbox-Kabel + USB-Seriell-Adapter | nur für die alternative Montierung |

## Installation

Raspberry Pi OS **64 Bit mit Desktop** flashen (Raspberry Pi Imager: Hostname `astro-pi`, WLAN des GL.iNet, SSH an). Dann auf dem Pi:

```bash
curl -fsSLO https://raw.githubusercontent.com/ChristophCaina/seestar-astro-pipeline/main/scripts/install-astro-pi.sh
sudo bash install-astro-pi.sh --mount pmc8 --camera canon --lat 48.7 --lon 8.9 --hotspot
sudo reboot
```

| Option | Wirkung |
|---|---|
| `--mount pmc8` / `celestron` / `simulator` | Treiber `indi_pmc8_telescope` (iEXOS-100), `indi_celestron_gps` (NexStar) oder Simulator |
| `--camera canon` / `simulator` | `indi_canon_ccd` (gphoto, USB) oder Simulator |
| `--lat --lon` | Standort für Zielhöhe und Montierung (später in `/etc/astro-pi.toml` änderbar) |
| `--tailscale` | Pi direkt ins Tailnet (sonst reicht das geroutete Netz des GL.iNet) |
| `--hotspot` | eigenes WLAN „Astro-Pi“, falls kein bekanntes WLAN da ist (Passwort in `/root/astro-pi-hotspot-passwort.txt`) |
| `--no-desktop` | ohne KStars/noVNC, nur Web-App |

**Celestron NexStar GT als Zweitmontierung:** Treiber `indi_celestron_gps`, Verbindung über das serielle Kabel der Handbox (RS-232 → USB-Adapter). Bei sehr alten GT-Handboxen kann der Treiber nur einen Teil der Funktionen nutzen (GoTo und Sync gehen meist, Parken/Nachführmodi je nach Firmware) – am Gerät testen. Die GT ist azimutal: Belichtungen kurz halten (≈ 15–30 s), die Feldrotation gleicht die 2-Pass-Registrierung in siril-auto wie beim Seestar aus.

Montierung wechseln: `/etc/default/astro-pi-indi` anpassen (`INDI_DRIVERS="indi_celestron_gps indi_canon_ccd"`), dann `sudo systemctl restart indiserver`. Updates: `sudo bash install-astro-pi.sh update`.

> Ungetestet auf echter Hardware. Getestet ist der Dienst gegen die INDI-Simulatoren (CI). Am Pi zuerst mit `--mount simulator --camera simulator` ausprobieren, dann die echten Treiber eintragen.

## Bedienung am Tablet

`http://astro-pi.local:8080` im Chrome öffnen, Menü → **Zum Startbildschirm hinzufügen**. Die App läuft dann im Vollbild (PWA).

| Bereich | Funktionen |
|---|---|
| Montierung | Ziel suchen (OpenNGC, mit aktueller Höhe), GoTo, **GoTo + zentrieren** (Aufnahme → Plate-Solving → Sync, bis das Ziel < 2′ in der Mitte steht), Richtungstasten mit Tempo, Nachführung, Parken |
| Kamera | Serie (Ziel, Belichtung, Anzahl, Pause, ISO), Testbild ohne Speichern, letzte Aufnahme mit HFR, Plate-Solve / Solve + Sync |
| Fokus | **Live View** der 6D mit Zoom auf einen Stern, Testbild-Schleife mit HFR-Verlauf |
| Status | Geräte, Speicher, Meldungen der Treiber, Link zu Ekos (noVNC) |

- **STOPP** oben rechts bricht Schwenk und Serie sofort ab.
- **Rotlicht** (Punkt-Taste) für die Nachtsicht.
- Gestartet wird nur in der App. Home Assistant bekommt per MQTT nur Status und Vorschau, keine Befehle.
- Unter 10° Zielhöhe fragt die App vor dem GoTo nach.

**Ekos** (Polausrichtung, Guiding, Scheduler): `http://astro-pi.local:6080/vnc.html`, VNC-Passwort in `/root/astro-pi-vnc-passwort.txt`. In KStars ein Ekos-Profil mit **Remote: localhost:7624** anlegen – der INDI-Server läuft schon als Dienst. Web-App und Ekos können gleichzeitig verbunden sein.

## Unterwegs mit dem GL.iNet

- Pi, Seestar und Tablet hängen im WLAN des GL.iNet. Vor Ort braucht es kein Internet.
- Das GL.iNet-Netz ist per Tailscale ins Heimnetz geroutet ([04](04-remote-access.md)): MQTT zu HA funktioniert auch unterwegs (in `/etc/astro-pi.toml` die Broker-IP im Heimnetz eintragen).
- Ohne GL.iNet: `--hotspot` – der Pi spannt sein eigenes WLAN auf, das Tablet verbindet sich direkt.

## Aufnahmen → NAS → siril-auto

Die App speichert `/data/captures/<Ziel>_sub/Light_<Ziel>_<Belichtung>s_DSLR_<Datum>-<Zeit>.fits` – dieselbe Struktur wie beim Seestar. Damit gruppiert siril-auto nach Ziel, Filter (`DSLR`) und Nacht. Als zweites Gerät in `/etc/siril-auto.env`:

```bash
DEVICES="s50 eos6d"
DEV_eos6d_TYPE=dslr                     # Kalibrierung mit Bias/Darks/Flats (siril/README)
DEV_eos6d_LABEL="Canon EOS 6D"
DEV_eos6d_IP="192.168.8.XX"              # Pi im GL.iNet-Netz bzw. zu Hause
DEV_eos6d_PORT=22                       # SFTP
DEV_eos6d_REMOTE="astropi"              # rclone config create astropi sftp host=<ip> user=astro key_file=~/.ssh/id_ed25519
DEV_eos6d_SRC="/data/captures"
```

Unterwegs: `travel-sync.sh --device eos6d --ip <pi-ip>`.

**Kalibrierbilder:** In der App unter *Kamera → Bildtyp* **Dark**, **Flat** oder **Bias** wählen. Die Bilder landen in `/data/captures/_calib/`, siril-auto baut daraus Masters und kalibriert die Lights damit. Darks mit gleicher Belichtung und ISO wie die Lights (Objektivdeckel drauf), Flats direkt nach der Session ohne Fokus zu verstellen, Bias mit kürzester Belichtung.

## Home Assistant

Mit `[mqtt] host` legt der Dienst per Discovery das Gerät **Astro-Pi** an: Montierung (Status), Ziel, Zielhöhe, Serie (Fortschritt, Attribute), HFR, Speicher frei und ein **Bild „Letzte Aufnahme“** (MQTT-Image). Fürs Dashboard reicht eine Picture-Entity-Karte auf `image.astro_pi_letzte_aufnahme`.

## Vergleich mit StellarMate

Für den Vergleich auf derselben Hardware einfach die SD-Karte tauschen. StellarMate OS (59 $) bringt eine eigene App mit; die Montierungs- und Kameratreiber sind dieselben (INDI). MQTT, Dateinamen für siril-auto und die Web-App dieses Repos lassen sich dort ebenfalls nachinstallieren (Python-Dienst, siehe `pi/`).
