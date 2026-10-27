# siril-auto – Seestar-Frames automatisch stacken

Läuft im Siril-LXC. Ablauf:
1. Die Einzelframes vom Seestar abholen.
2. Pro **Ziel, Filter und Nacht** stacken.
3. Für jedes Ziel und jeden Filter mit mehreren Nächten einen **Master** bilden.
4. Gibt es ein Ziel mit **mehreren Filtern** (z. B. IRCUT und LP), zusätzlich eine **Kombination über alle Filter**.
5. Jeden Stack **plate-solven**.
6. Ergebnisse auf dem NAS ablegen, ein JPG mit Aufnahmedatum für Immich schreiben, eine beschriftete Version und eine `meta.json` für die Galerie.
7. Optional: Immich-Library scannen und alle Versionen eines Ziels zu einem Immich-Stack gruppieren.

```
Seestar (SMB) ──rclone──> /data/astro/raw/seestar/            1:1-Archiv, nie löschen
                                 │  *_sub/*.fit → Ziel (Alias-Liste) + Filter + Nacht
                                 ▼
         Nacht-Stacks  <ziel>_<nacht>_<filter>_siril   (seestar_stack.ssf)
                                 │  ≥ 2 Nächte
                                 ▼
         Master        <ziel>_master_<filter>_siril    (seestar_master.ssf, gewichtet nach Frames)
                                 │  ≥ 2 Filter
                                 ▼
         Kombination   <ziel>_kombi_<f1+f2>_siril      (je Filter Master bzw. jüngste Nacht, gewichtet)
                                 │  jeder Stack: seestar_solve.ssf (platesolve) + write_meta.py
                                 ▼
   /data/astro/stacks/<ziel>/  *.fit  *.tif  *.log  *.meta.json
   /data/astro/export/<ziel>/  *.jpg + *_beschriftet.jpg (annotate.py) → Immich (immich_sync.py)
   /data/astro/web/            Galerie (optional, gallery_build.py)
```

## Regeln

- **Nacht** = 12 bis 12 Uhr. Frames nach Mitternacht zählen zur Nacht davor.
- **Filter und Belichtung** kommen aus dem Dateinamen (`Light_M 31_10.0s_IRCUT_20261018-213045.fit`). Nacht-Stacks und Master bleiben je Filter getrennt. Die **Kombination** führt danach die Ergebnisse aller Filter zusammen: je Filter der Master, sonst die jüngste Nacht, registriert und nach Frames gewichtet (`COMBINE_FILTERS=1`, Standard). Typisch für das Seestar: IRCUT liefert Sterne und Farben, LP das Nebel-Signal (Hα/OIII). Die Farben der Kombination sind ein Kompromiss, für eine gezielte Mischung (z. B. LP nur für den Nebel) bleibt die manuelle Bearbeitung.
- **Alias-Liste** (`/etc/siril-auto.aliases`): Bekommt dasselbe Objekt in der App mal unterschiedliche Namen, z. B. „Andromeda Galaxy“ und „M 31“, landet es mit Alias trotzdem in einer Gruppe.
- **Neu gestackt** wird eine Nacht, sobald neue Frames dazukommen. Danach wird auch der Master neu berechnet.
- **Fehlschläge** werden nicht bei jedem Lauf wiederholt. Ein neuer Versuch startet erst, wenn neue Frames dazukommen oder du `--force` aufrufst.
- **Plate-Solving fehlgeschlagen?** Der Stack bleibt trotzdem erhalten. In der Galerie erscheint er dann ohne Annotationen.

## Installation (im Siril-LXC als root)

Am schnellsten mit dem Installer, siehe [docs/03](../docs/03-siril-lxc.md): `bash install-siril-lxc.sh host <CTID> --astro <host-pfad>`. Von Hand:

```bash
apt install -y siril rclone libimage-exiftool-perl mosquitto-clients smbclient python3-pil fonts-dejavu-core git
siril-cli --version          # mindestens 1.2.0

git clone https://github.com/ChristophCaina/seestar-astro-pipeline /opt/seestar-astro-pipeline
cd /opt/seestar-astro-pipeline/siril

# Deep-Sky-Katalog OpenNGC (CC-BY-SA 4.0) für die Objektbeschriftung
mkdir -p data
curl -fsSL -o data/NGC.csv      https://raw.githubusercontent.com/mattiaverga/OpenNGC/master/database_files/NGC.csv
curl -fsSL -o data/addendum.csv https://raw.githubusercontent.com/mattiaverga/OpenNGC/master/database_files/addendum.csv

useradd -r -m -s /usr/sbin/nologin siril
cp siril-auto.env.example /etc/siril-auto.env
cp siril-auto.aliases.example /etc/siril-auto.aliases
chown root:siril /etc/siril-auto.env && chmod 640 /etc/siril-auto.env
```

Ein Update holst du dir mit `git -C /opt/seestar-astro-pipeline pull`. Deine Config in `/etc` bleibt dabei unangetastet.

**NAS-Volume** (auf dem Proxmox-Host, Pfad anpassen):

```bash
pct set <CTID> -mp0 /<host-pfad>/astro,mp=/data/astro
pct reboot <CTID>
```

Der User `siril` braucht Schreibrechte auf `/data/astro`. Im unprivilegierten LXC entspricht das auf dem Host der UID `100000 + <uid von siril>` (`id siril` im Container).

**Seestar-Freigabe**, sobald das Seestar im WLAN ist:

```bash
smbclient -L //<seestar-ip> -N                      # Share-Namen anzeigen
su -s /bin/bash siril -c 'rclone config create seestar smb host=<seestar-ip> user=guest'
su -s /bin/bash siril -c 'rclone lsd "seestar:<share-name>"'   # Test
```

Danach `SEESTAR_IP` und `RCLONE_SRC_PATH` in `/etc/siril-auto.env` eintragen.

## Test

```bash
runuser -u siril -- /opt/seestar-astro-pipeline/siril/siril-auto.sh --dry-run    # zeigt nur, was gestackt würde (meldet nichts per MQTT)
runuser -u siril -- /opt/seestar-astro-pipeline/siril/siril-auto.sh --no-fetch   # nur vorhandenes Archiv
runuser -u siril -- /opt/seestar-astro-pipeline/siril/siril-auto.sh --force      # alles neu
runuser -u siril -- /opt/seestar-astro-pipeline/siril/siril-auto.sh --master-full   # Master aus allen Subs
```

Vom Proxmox-Host aus: `pct exec <ctid> -- runuser -u siril -- …` (im LXC gibt es kein `sudo`). Längere Läufe nicht mit `&` aus `pct exec` starten – der Prozess endet mit der Shell. Stattdessen den Dienst nutzen: `pct exec <ctid> -- systemctl start --no-block siril-auto.service` und `journalctl -u siril-auto -f`.

**Ohne Bildschirm:** `siril-cli` aus Debian 1.2.x initialisiert GTK und bricht ohne Display mit „cannot open display“ ab. siril-auto startet es deshalb automatisch über `xvfb-run` (Paket `xvfb`, installiert der Installer mit).

**Am ersten echten Datensatz prüfen:**

| Was | Wo | Falls falsch |
|---|---|---|
| Bild gespiegelt? | `export/…jpg` | in `seestar_stack.ssf` `mirrorx -bottomup` aktivieren |
| Overlay vertikal gespiegelt? | Galerie | `NO_FLIP_Y=1` in `/etc/siril-auto.env` |
| Plate-Solving klappt? | `stacks/…log` | `SOLVE_ARGS` anpassen (z. B. `-focal=250 -pixelsize=2.9`) |
| Master-Stack-Optionen | `stacks/…_master_…log` | `MASTER_STACK_METHOD`. Die Gewichtung wählt das Script automatisch nach Siril-Version (`-weight_from_nbstack` für 1.2, `-weight=nbstack` ab 1.4). |
| Share-Name, Ordner `<Ziel>_sub/`, Dateinamen | `raw/seestar/` | `RCLONE_SRC_PATH`; die Erkennung steht in `frame_info()` |

## Automatisch laufen lassen

```bash
cp systemd/siril-auto.service systemd/siril-auto.timer /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now siril-auto.timer
journalctl -u siril-auto -f
```

## Mehrere Geräte

Ein zweites Seestar (z. B. S30 Pro) oder später andere Kameras kommen als weiteres Gerät in `/etc/siril-auto.env`:

```bash
DEVICES="s50 s30"
DEV_s30_LABEL="Seestar S30 Pro"
DEV_s30_IP="192.168.1.XX"
DEV_s30_REMOTE="seestar30"        # rclone config create seestar30 smb host=<ip> user=guest
```

- Jedes Gerät hat ein eigenes Rohdaten-Archiv (`raw/<id>/`, das Hauptgerät behält `raw/seestar/`) und eigene Stacks. Nächte und Master werden **nie zwischen Geräten gemischt**, weil Sensor und Bildfeld verschieden sind.
- Das erste Gerät in `DEVICES` ist das Hauptgerät, seine Dateinamen bleiben wie bisher. Weitere Geräte bekommen `_<id>` angehängt: `M_31_master_IRCUT_s30_siril`.
- In der Galerie landen die Stacks beider Geräte beim selben Objekt; Versionen zeigen dann das Gerät, Titelbild ist der Master mit der längsten Belichtung.
- `meta.json`, beschriftete Version und MQTT-Meldung enthalten das Gerät (`device`, `device_label`).
- Optional je Gerät: `DEV_<id>_SRC` (Pfad auf der Freigabe), `DEV_<id>_MIRROR`, `DEV_<id>_SCRIPT` (eigenes Siril-Script).

## DSLR (Canon EOS 6D)

Mit `DEV_<id>_TYPE=dslr` stackt siril-auto ein Gerät mit `dslr_stack.ssf` und **kalibriert** vorher:

| Rohframes (Astro-Pi-App, Bildtyp wählen) | Master in `calib/<id>/` | Verwendung |
|---|---|---|
| `_calib/Bias_*.fits` (≥ 3) | `bias.fit` | für Flats; für Lights nur, wenn kein passendes Dark da ist |
| `_calib/Dark_<belichtung>s_*.fits` (≥ 3 je Belichtung) | `dark_<belichtung>s.fit` | Lights mit gleicher Belichtung (`-cc=dark`, enthält Bias) |
| `_calib/Flat_*.fits` (≥ 3) | `flat.fit` (Flats der **neuesten** Nacht, mit Bias kalibriert) | alle Lights |

- Masters werden neu gebaut, sobald sich die Zahl der Rohframes ändert (oder mit `--force`).
- Ohne Masters wird nur debayert – das Ergebnis ist trotzdem brauchbar, Darks lohnen sich bei langen Belichtungen und warmen Nächten.
- Darks am besten in derselben Nacht bei ähnlicher Temperatur aufnehmen (Objektivdeckel drauf), Flats ohne Fokus- oder Kameraverstellung direkt nach der Session.

## Weitwinkel

Frames mit dem Filter-Kürzel `WIDE` (Astro-Pi-App, Modus Weitwinkel) stackt siril-auto mit `wide_stack.ssf`: Homographie-Registrierung, kein Hintergrundabzug, zusätzlicher Vordergrund-Stack, Plate-Solving per astrometry.net, Beschriftung mit Sternbildern. Details: [docs/09](../docs/09-weitwinkel.md).

## Unterwegs

`travel-sync.sh` holt Frames von unterwegs über Tailscale vom Seestar am Reiserouter (nur Einzelframes, Zeitraum und Ziel wählbar, Bandbreitenlimit, im Hintergrund). Details in [docs/04](../docs/04-remote-access.md#bilder-unterwegs--nas). Bei mehreren Geräten wählt `--device <id>` Freigabe und Archiv.

## Home Assistant (MQTT)

Mit `MQTT_ENABLED=1` legt das Script per Discovery das Gerät **„Siril Auto-Stacking“** an. Es hat zwei Sensoren:
- **Status**: `idle`, `fetching`, `processing` oder `error`
- **Letztes Ergebnis**: Ziel und Nacht. Als Attribute kommen Filter, `stack` (`night` oder `master`), Frames, Gerät (`device`, `device_label`), Galerie-Bezug (`id`, `group`) und die Pfade zu JPG und FIT dazu.

## Immich

`IMMICH_SYNC=1` plus URL, API-Schlüssel und Library-ID in `/etc/siril-auto.env`. Einrichtung, Zuordnung der App-Stacks und den Nachhol-Job `lib/backfill.py` für ältere App-Bilder beschreibt [docs/05](../docs/05-immich-galerie.md).

| Script | Zweck |
|---|---|
| `lib/annotate.py` | beschriftete Version aus JPG + meta.json |
| `lib/immich_sync.py` | Library-Scan, Immich-Stacks, Album |
| `lib/backfill.py` | ältere App-Stacks aus Immich plate-solven und beschriften |

## Entwicklung

Alle Tests laufen ohne Siril, Teleskop und Immich. `tests/bin/siril-cli` und `tests/bin/solve-field` ersetzen Siril bzw. astrometry.net, Immich wird in `tests/test_immich.py` durch einen kleinen HTTP-Server simuliert.

```bash
python3 -m unittest discover -s tests -v
```
