# Siril-LXC und siril-auto

Siril braucht beim Stacken viel CPU und RAM, der Container ist deshalb großzügiger als der für seestar_alp (4 Cores, 8 GB, 32 GB Disk).

## Schnellweg: Installer (Proxmox-Host)

[`scripts/install-siril-lxc.sh`](../scripts/install-siril-lxc.sh) legt den Container bei Bedarf an, bindet das NAS-Volume ein und installiert alles im Container:

```bash
curl -fsSLO https://raw.githubusercontent.com/ChristophCaina/seestar-astro-pipeline/main/scripts/install-siril-lxc.sh

# Container existiert schon (z. B. 132), NAS-Pfad einbinden, Rechte setzen:
bash install-siril-lxc.sh host 132 --astro /<host-pfad>/astro --chown

# oder neu anlegen, gleich mit Seestar-Freigabe und astrometry.net (für den Nachhol-Job):
bash install-siril-lxc.sh host 132 --create --astro /<host-pfad>/astro --chown \
  --seestar-ip 192.168.1.XX --astrometry
```

| Schritt | Details |
|---|---|
| Container (nur `--create`) | Debian 13, unprivilegiert, DHCP, Hostname `siril` |
| NAS-Volume (`--astro`) | Bind-Mount als `/data/astro`, nächster freier `mpN` |
| Rechte (`--chown`) | Ordner gehört Host-UID 101500 = User `siril` (UID 1500) im Container |
| Pakete | Siril (Debian 13: 1.2.x), rclone, exiftool, mosquitto-clients, smbclient, Pillow, DejaVu-Fonts |
| Repo | nach `/opt/seestar-astro-pipeline`, OpenNGC-Katalog nach `siril/data/` |
| Config | `/etc/siril-auto.env` und `.aliases` aus den Vorlagen, **nur wenn noch nicht vorhanden** |
| Seestar (`--seestar-ip`) | rclone-Remote `seestar` (Gast) und Liste der Freigaben |
| astrometry.net (`--astrometry`) | Paket + Indexdateien 4107–4110 für den Nachhol-Job ([05](05-immich-galerie.md)) |
| systemd | Service + Timer installiert, **nicht aktiviert** (erst nach dem Testlauf, oder `--enable-timer`) |

Das Script ist wiederholbar. Updates im Container: `bash /root/install-siril-lxc.sh update` (git pull, Units neu).

> **Rechte auf dem NAS-Ordner:** `--chown` setzt nur den Wurzelordner `astro/` auf UID 101500 und `chmod 2775`. Greift auch der Samba-LXC darauf zu, braucht dessen User Schreibrechte über die Gruppe oder ACLs. Ohne `--chown` zeigt das Script nur die benötigte Host-UID an.

## Von Hand

```bash
pct create <CTID> local:vztmpl/debian-13-standard_<version>_amd64.tar.zst \
  --hostname siril \
  --cores 4 --memory 8192 --swap 1024 \
  --rootfs local-lvm:32 \
  --net0 name=eth0,bridge=vmbr0,ip=dhcp \
  --unprivileged 1 --onboot 1 --features nesting=1

# NAS-Volume für Rohdaten, Stacks und Exporte
pct set <CTID> -mp0 /<host-pfad>/astro,mp=/data/astro
```

Läuft die Freigabe schon über einen Samba-LXC, lohnt ein Blick in dessen Config (`pct config <samba-ctid> | grep ^mp`). Denselben Host-Pfad bindest du direkt ein, ein SMB-Umweg ist dann nicht nötig.

Installation, Konfiguration und Test von siril-auto stehen in [`siril/README.md`](../siril/README.md).

## Ordnerstruktur

```
/data/astro/
├── raw/seestar/   1:1-Kopie vom Seestar (Subs + Seestar-eigene Stacks), Archiv
├── work/          temporär
├── stacks/        Siril-Ergebnisse (FIT/TIF/Log) pro Ziel
├── export/        JPG mit Aufnahmedatum → Immich (External Library)
└── .state/        welche Nächte verarbeitet sind
```

## Funktionsumfang

Gruppierung nach Ziel und Filter, Alias-Liste, Master-Stack über alle Nächte, Plate-Solving und `meta.json` für die Galerie. Details stehen in [`siril/README.md`](../siril/README.md).
