# Siril-LXC und siril-auto

Container wie in [01](01-seestar-alp-lxc.md) anlegen, aber großzügiger, weil Siril beim Stacken viel CPU und RAM braucht:

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
