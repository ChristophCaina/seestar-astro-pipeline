# Immich und Astro-Galerie

## Rollen

| Version | Woher | Immich | Galerie |
|---|---|---|---|
| App-Stack | Seestar-App → Handy-Backup | ja (Schnellansicht) | nur nachgeholt (`backfill.py`) |
| Seestar-Auto-Stack | Gerät → `raw/seestar/` | nein (Duplikat zum App-Stack) | – |
| Siril-Stack (Nacht / Master) | siril-auto → `export/` | ja | ja |
| Beschriftet (`*_beschriftet.jpg`) | siril-auto / `annotate.py` | ja | – (die Galerie zeichnet selbst) |

- `export/` wird **read-only** in den Immich-LXC eingebunden und als **External Library** angelegt. Es entstehen keine Kopien.
- Das Aufnahmedatum setzt exiftool ins JPG, damit Immich das Bild in die richtige Nacht einsortiert. Die beschriftete Version übernimmt die EXIF-Daten.
- `immich_sync.py` gruppiert alle Versionen eines Ziels zu einem **Immich-Stack** (Titelbild: Siril-Master, sonst die neueste Nacht) und legt sie ins Album „Astro“.

## Einrichtung

### 1. External Library

Auf dem Proxmox-Host das Export-Verzeichnis read-only in den Immich-LXC einbinden (Pfad und CTID anpassen):

```bash
pct set <IMMICH-CTID> -mp1 /<host-pfad>/astro/export,mp=/mnt/astro,ro=1
pct reboot <IMMICH-CTID>
```

Läuft Immich dort per Docker, braucht der `immich-server`-Container zusätzlich das Volume:

```yaml
    volumes:
      - /mnt/astro:/mnt/astro:ro
```

In Immich: **Administration → External Libraries → Create** (Besitzer: dein Konto), Import-Pfad `/mnt/astro`. Die ID der Library steht danach in der URL (`/admin/library-management/<id>`); sie kommt als `IMMICH_LIBRARY_ID` in die Config.

### 2. API-Schlüssel

**Kontoeinstellungen → API-Schlüssel → Neuer API-Schlüssel**. Berechtigungen, falls du den Schlüssel einschränken willst: `asset.read`, `asset.download`, `library.update` (Scan), `stack.create`, `stack.delete`, `album.read`, `album.create`, `albumAsset.create`.

In `/etc/siril-auto.env`:

```bash
IMMICH_SYNC=1
IMMICH_URL="http://192.168.1.XX:2283"
IMMICH_API_KEY="…"
IMMICH_LIBRARY_ID="…"
```

Test ohne Änderungen:

```bash
set -a; . /etc/siril-auto.env; set +a
python3 /opt/seestar-astro-pipeline/siril/lib/immich_sync.py --stacks /data/astro/stacks --dry-run
```

## Was der Sync macht

Nach jedem siril-auto-Lauf mit neuen Stacks (bei `IMMICH_SYNC=1`):

1. Library-Scan anstoßen, damit neue Exporte in Immich auftauchen. Was beim ersten Lauf noch fehlt („wartet …“), wird beim nächsten Lauf nachgezogen.
2. Je Ziel + Filter einen Stack bilden: Master, Einzelnächte, beschriftete Versionen und die **App-Stacks vom Handy** aus denselben Nächten.
3. Alles ins Album `IMMICH_ALBUM` legen.

App-Stacks werden über Aufnahmezeit (Nacht = 12–12 Uhr, `TZ_LOCAL`) und Dateiname erkannt (`IMMICH_APP_PATTERN`, Default `seestar|stack`). Nennt der Dateiname ein Objekt (`M 31`, `NGC7000`, `IC_434`), muss es zum Ziel passen. Ohne Objekt im Namen wird ein App-Stack nur zugeordnet, wenn in dieser Nacht nur ein Ziel aufgenommen wurde.

Der Zustand liegt in `/data/astro/.state/immich.json`. Ein Stack wird nur neu gebaut, wenn sich seine Mitglieder ändern. Stacks, die du in Immich von Hand auflöst, legt der Sync wieder an, solange der Zustand sie kennt; zum Zurücksetzen den Eintrag aus der Datei löschen.

> Geprüft gegen die Immich-API ab 1.122. Ändert Immich die Endpunkte, liegt alles in `siril/lib/immich.py`.

## Astro-Galerie

Umsetzung in [`gallery/`](../gallery): statisches Frontend, Builder und Beispieldaten. Das Datenformat steht in [`gallery/SCHEMA.md`](../gallery/SCHEMA.md), das Hosting in [06](06-galerie-hosting.md).

- Übersicht: Kennzahlen, Beobachtungskalender (Filter, Mondphasen), Kacheln mit Filter und Sortierung
- Detailseite: Bild mit SVG-Overlay, das beim Mouseover die Annotationen zeigt, dazu Infokarten, RA/Dec-Gitter, Kompass und Maßstab. Am Rand ein Hinweis auf Objekte außerhalb des Bildfelds.
- Rotlicht-Modus für die Nachtsicht
- Nachgeholte App-Stacks erscheinen als „Seestar-App-Stack“, ohne Frame- und Belichtungsangaben (die kennt nur die App).

siril-auto baut die Galerie nach jedem Lauf neu (`GALLERY_BUILD`).

## Ältere Bilder nachträglich

| Quelle | Weg |
|---|---|
| Siril-FIT | Batch-`platesolve` |
| Seestar-FIT vom Gerät | RA/Dec steht im Header, damit schnell gelöst |
| App-JPG in Immich | `backfill.py`: per API laden, `solve-field`, meta.json + beschriftete Version |

```bash
apt install -y astrometry.net
# Indexdateien für ca. 0,7–1,3° Bildfeld (Seestar S50), z. B. 4107–4110:
cd /usr/share/astrometry && for i in 4107 4108 4109 4110; do
  curl -fsSLO "http://data.astrometry.net/4100/index-$i.fits"; done

set -a; . /etc/siril-auto.env; set +a
cd /opt/seestar-astro-pipeline/siril/lib
python3 backfill.py --since 2025-01-01 --limit 5 --dry-run   # was würde verarbeitet?
python3 backfill.py --since 2025-01-01 --limit 5             # erst ein paar testen
python3 annotate.py --meta /data/astro/stacks/_app/<ziel>/<datei>.meta.json --out /tmp/test.jpg
```

- Die Originale bleiben, wo sie sind. Eine Kopie landet unter `stacks/_app/<ziel>/` (für die Galerie), die beschriftete Version unter `export/<ziel>/` (für Immich).
- Danach stapelt `immich_sync.py` Original und beschriftete Version zusammen.
- Liegen die Beschriftungen vertikal gespiegelt, mit `--flip-y` neu laufen lassen (vorher `.state/backfill.json` löschen bzw. betroffene IDs entfernen).
- Bilder, die sich nicht lösen lassen, merkt sich der Job ebenfalls und versucht sie nicht erneut.
