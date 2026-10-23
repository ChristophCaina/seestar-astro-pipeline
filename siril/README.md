# siril-auto – Seestar-Frames automatisch stacken

Läuft im Siril-LXC. Ablauf:
1. Die Einzelframes vom Seestar abholen.
2. Pro **Ziel, Filter und Nacht** stacken.
3. Für jedes Ziel mit mehreren Nächten einen **Master** bilden.
4. Jeden Stack **plate-solven**.
5. Ergebnisse auf dem NAS ablegen, ein JPG mit Aufnahmedatum für Immich schreiben, eine beschriftete Version und eine `meta.json` für die Galerie.
6. Optional: Immich-Library scannen und alle Versionen eines Ziels zu einem Immich-Stack gruppieren.

```
Seestar (SMB) ──rclone──> /data/astro/raw/seestar/            1:1-Archiv, nie löschen
                                 │  *_sub/*.fit → Ziel (Alias-Liste) + Filter + Nacht
                                 ▼
         Nacht-Stacks  <ziel>_<nacht>_<filter>_siril   (seestar_stack.ssf)
                                 │  ≥ 2 Nächte
                                 ▼
         Master        <ziel>_master_<filter>_siril    (seestar_master.ssf, gewichtet nach Frames)
                                 │  jeder Stack: seestar_solve.ssf (platesolve) + write_meta.py
                                 ▼
   /data/astro/stacks/<ziel>/  *.fit  *.tif  *.log  *.meta.json
   /data/astro/export/<ziel>/  *.jpg + *_beschriftet.jpg (annotate.py) → Immich (immich_sync.py)
   /data/astro/web/            Galerie (optional, gallery_build.py)
```

## Regeln

- **Nacht** = 12 bis 12 Uhr. Frames nach Mitternacht zählen zur Nacht davor.
- **Filter und Belichtung** kommen aus dem Dateinamen (`Light_M 31_10.0s_IRCUT_20261018-213045.fit`). IRCUT und LP werden nie gemischt.
- **Alias-Liste** (`/etc/siril-auto.aliases`): Bekommt dasselbe Objekt in der App mal unterschiedliche Namen, z. B. „Andromeda Galaxy“ und „M 31“, landet es mit Alias trotzdem in einer Gruppe.
- **Neu gestackt** wird eine Nacht, sobald neue Frames dazukommen. Danach wird auch der Master neu berechnet.
- **Fehlschläge** werden nicht bei jedem Lauf wiederholt. Ein neuer Versuch startet erst, wenn neue Frames dazukommen oder du `--force` aufrufst.
- **Plate-Solving fehlgeschlagen?** Der Stack bleibt trotzdem erhalten. In der Galerie erscheint er dann ohne Annotationen.

## Installation (im Siril-LXC als root)

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
sudo -u siril /opt/seestar-astro-pipeline/siril/siril-auto.sh --dry-run    # zeigt nur, was gestackt würde
sudo -u siril /opt/seestar-astro-pipeline/siril/siril-auto.sh --no-fetch   # nur vorhandenes Archiv
sudo -u siril /opt/seestar-astro-pipeline/siril/siril-auto.sh --force      # alles neu
sudo -u siril /opt/seestar-astro-pipeline/siril/siril-auto.sh --master-full   # Master aus allen Subs
```

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

## Unterwegs

`travel-sync.sh` holt Frames von unterwegs über Tailscale vom Seestar am Reiserouter (nur Einzelframes, Zeitraum und Ziel wählbar, Bandbreitenlimit, im Hintergrund). Details in [docs/04](../docs/04-remote-access.md#bilder-unterwegs--nas).

## Home Assistant (MQTT)

Mit `MQTT_ENABLED=1` legt das Script per Discovery das Gerät **„Siril Auto-Stacking“** an. Es hat zwei Sensoren:
- **Status**: `idle`, `fetching`, `processing` oder `error`
- **Letztes Ergebnis**: Ziel und Nacht. Als Attribute kommen Filter, `stack` (`night` oder `master`), Frames und die Pfade zu JPG und FIT dazu.

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
