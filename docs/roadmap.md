# Roadmap

- [x] seestar_alp-LXC (amd64-Anpassungen, systemd-Fix)
- [x] HA-Add-on im External-Modus, MQTT, Dashboard-Karte
- [x] siril-auto: Abholung, Gruppierung nach Nacht, Stacking, Export, MQTT (ungetestet mit echten Daten)
- [x] Galerie-Prototyp
- [x] Galerie: Frontend, Builder, meta.json-Format, Beispieldaten, nginx-vhost
- [ ] Seestar: feste IP, Station-Modus, Firmware prüfen, ggf. Interop-PEM
- [ ] Siril-LXC: NAS-Volume, erster echter Durchlauf (Share-Name, Dateinamen, Orientierung prüfen)
- [x] siril-auto: Ziel + Filter, Alias-Liste, Master-Stack, `--master-full`
- [x] siril-auto: Plate-Solving + `meta.json` (OpenNGC), Tests + CI (ungetestet mit echtem Siril)
- [x] Beschriftetes JPG für Immich (`annotate.py`)
- [x] Immich: Stack-Zuordnung App ↔ Siril, Album (`immich_sync.py`, ungetestet mit echtem Immich)
- [ ] Immich: External Library einrichten, API-Schlüssel
- [x] Nachhol-Job-Script für ältere App-Stacks (`backfill.py`; offen: astrometry.net-Indexdateien, Orientierung)
- [ ] Galerie im nginx-LXC einrichten (erst mit Beispieldaten), dann echte Stacks
- [ ] Nachhol-Job für ältere Bilder
- [x] Tailscale-LXC: Install-Script `scripts/install-tailscale-lxc.sh`
- [ ] Remote-Zugriff einrichten (Tailscale-LXC, GL.iNet unterwegs)
- [ ] Später: IoT-VLAN, Dachsternwarte (Velux/KLF-200, Regen- und Wolkensensor)
