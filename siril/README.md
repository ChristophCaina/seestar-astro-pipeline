# siril-auto – Seestar-Frames automatisch stacken

Läuft im Siril-LXC 132 (`siril`). Holt die Einzelframes vom Seestar, stackt sie pro **Ziel und Nacht** mit Siril und legt die Ergebnisse auf dem NAS ab. Die JPGs landen im Export-Ordner für Immich.

```
Seestar (SMB) ──rclone──> /data/astro/raw/seestar/   (1:1-Archiv, nie löschen)
                                  │  *_sub/*.fit, gruppiert nach Ziel + Nacht
                                  ▼
                         /data/astro/work/<ziel>_<nacht>/   (temporär)
                                  │  siril-cli + seestar_stack.ssf
                                  ▼
   /data/astro/stacks/<ziel>/<ziel>_<nacht>_siril.fit|.tif|.log
   /data/astro/export/<ziel>/<ziel>_<nacht>_siril.jpg   → Immich External Library
```

Eine Nacht reicht von 12 Uhr bis 12 Uhr. Frames nach Mitternacht gehören also zur Nacht davor. Kommen später Frames für dieselbe Nacht dazu, z. B. weil die Session fortgesetzt wurde, wird die Nacht neu gestackt und das Ergebnis überschrieben.

## Dateien

| Datei | Ziel im LXC |
|---|---|
| `seestar_stack.ssf` | `/opt/siril-auto/seestar_stack.ssf` |
| `siril-auto.sh` | `/opt/siril-auto/siril-auto.sh` |
| `siril-auto.env.example` | `/etc/siril-auto.env` |
| `systemd/siril-auto.service`, `systemd/siril-auto.timer` | `/etc/systemd/system/` |

## Installation (im LXC 132 als root)

```bash
apt install -y siril rclone libimage-exiftool-perl mosquitto-clients smbclient
siril-cli --version          # mindestens 1.2.0

useradd -r -m -s /usr/sbin/nologin siril
mkdir -p /opt/siril-auto
cp seestar_stack.ssf siril-auto.sh /opt/siril-auto/
chmod +x /opt/siril-auto/siril-auto.sh
cp siril-auto.env.example /etc/siril-auto.env
chown root:siril /etc/siril-auto.env && chmod 640 /etc/siril-auto.env
```

**NAS-Volume** (auf dem Proxmox-Host, Pfad anpassen, siehe `pct config 100 | grep ^mp`):

```bash
pct set 132 -mp0 /<host-pfad>/astro,mp=/data/astro
pct reboot 132
```

Der User `siril` braucht Schreibrechte auf `/data/astro`. Im unprivilegierten LXC entspricht das auf dem Host der UID `100000 + <uid von siril>` (`id siril` im Container).

**Seestar-Freigabe einrichten**, sobald das Seestar im WLAN ist:

```bash
smbclient -L //<seestar-ip> -N                      # Share-Namen anzeigen
su -s /bin/bash siril -c 'rclone config create seestar smb host=<seestar-ip> user=guest'
su -s /bin/bash siril -c 'rclone lsd "seestar:<share-name>"'   # Test
```

Trag danach in `/etc/siril-auto.env` `SEESTAR_IP` und `RCLONE_SRC_PATH` ein, also Share-Name und Pfad bis zum Ordner, der die `<Ziel>_sub`-Ordner enthält. Falls der Gastzugang nicht klappt, kannst du `user=`/`pass=` in der rclone-Config anpassen. Das Passwort setzt du mit `rclone obscure`.

## Test

```bash
sudo -u siril /opt/siril-auto/siril-auto.sh --dry-run       # zeigt nur, was gestackt würde
sudo -u siril /opt/siril-auto/siril-auto.sh --no-fetch      # nur vorhandenes Archiv verarbeiten
sudo -u siril /opt/siril-auto/siril-auto.sh --force         # alles neu stacken
```

Beim ersten echten Datensatz solltest du `result_preview.jpg` prüfen. Ist das Bild gespiegelt, aktivierst du in `seestar_stack.ssf` die Zeile `mirrorx -bottomup`.

## Automatisch laufen lassen

```bash
cp systemd/siril-auto.service systemd/siril-auto.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now siril-auto.timer
journalctl -u siril-auto -f
```

Der Timer läuft alle 30 Minuten. Ist das Seestar nicht erreichbar, wird nur das vorhandene Archiv verarbeitet. Das ist z. B. praktisch, wenn du Frames von unterwegs manuell nach `raw/seestar/` kopiert hast.

## Home Assistant (MQTT)

Mit `MQTT_ENABLED=1` legt das Script per Discovery automatisch das Gerät **„Siril Auto-Stacking“** an:

- **Status**: `idle` / `fetching` / `processing` / `error`, mit den Attributen Ziel, Nacht, Frames und Meldung
- **Letztes Ergebnis**: „Ziel (Nacht)“, mit den Attributen Frames, Pfad zum JPG/FIT und Zeitstempel

Topics: `seestar/siril/status`, `seestar/siril/last`, beide retained.

## Hinweise

- **Speicherplatz**: Pro Nacht entstehen im `work/`-Ordner temporär etwa das 2- bis 3-Fache der Lights. Nach Erfolg wird er gelöscht, bei einem Fehler bleibt er zur Analyse liegen (`KEEP_WORK_ON_ERROR`).
- **Fehlgeschlagene Nächte** werden nicht bei jedem Lauf erneut versucht. Erst wenn neue Frames dazukommen oder du `--force` aufrufst.
- **Immich**: Bind-Mount nur von `/data/astro/export` (read-only) in LXC 113 und dort als External Library anlegen. Das Aufnahmedatum steht per exiftool im JPG, damit das Bild in Immich in der richtigen Nacht einsortiert wird.
- **Annahmen, die du am echten Gerät prüfen solltest**: der Share-Name und die Ordnerstruktur (`<Ziel>_sub/`) sowie das Dateinamensschema `…_YYYYMMDD-HHMMSS.fit`. Ohne passenden Zeitstempel im Namen nimmt das Script die Änderungszeit der Datei.
