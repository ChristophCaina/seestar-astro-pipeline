# seestar_alp als LXC auf Proxmox

seestar_alp läuft in einem eigenen Debian-13-LXC. Das offizielle Setup-Script (`raspberry_pi/setup.sh`) ist für Raspberry Pi gedacht, läuft aber mit kleinen Anpassungen auch auf amd64. [`scripts/install-seestar-alp-lxc.sh`](../scripts/install-seestar-alp-lxc.sh) erledigt diese Anpassungen automatisch.

## 1. Container anlegen (Proxmox-Host)

```bash
pveam update
pveam available | grep debian-13          # aktuellen Template-Namen ermitteln
pveam download local debian-13-standard_<version>_amd64.tar.zst

pct create <CTID> local:vztmpl/debian-13-standard_<version>_amd64.tar.zst \
  --hostname seestar-alp \
  --cores 2 --memory 1536 --swap 512 \
  --rootfs local-lvm:8 \
  --net0 name=eth0,bridge=vmbr0,ip=dhcp \
  --unprivileged 1 --onboot 1 --features nesting=1
pct start <CTID>
```

- **Hostname nicht `seestar`** nennen, sonst kollidiert er mit `seestar.local` des Teleskops.
- `pct list` prüfen, bevor es weitergeht. Schlägt `pct create` fehl, landen die folgenden Befehle sonst auf dem Host.
- Für den Container eine **DHCP-Reservierung** anlegen, die IP steht später fest im HA-Add-on.

## 2. Installation (im Container)

```bash
pct enter <CTID>
curl -fsSLO https://raw.githubusercontent.com/ChristophCaina/seestar-astro-pipeline/main/scripts/install-seestar-alp-lxc.sh
bash install-seestar-alp-lxc.sh
```

Das Script macht im Einzelnen:

| Schritt | Warum |
|---|---|
| Locale `en_US.UTF-8` generieren | Das Template bringt sie nicht mit, sonst gibt es Perl-Warnungen |
| User `seestar` mit passwortlosem sudo | Das Setup-Script darf nicht als root laufen und prüft `sudo -n` |
| Architektur-Check im Setup-Script entfernen | Das Script erlaubt nur `aarch64` |
| `sysctl -p` darf fehlschlagen | Im unprivilegierten LXC sind nicht alle sysctls erlaubt, `bash -e` würde abbrechen |
| `libglib2.0-0t64` installieren | OpenCV braucht `libgthread-2.0.so.0`, im Minimal-Template fehlt es |
| systemd: `WantedBy=multi-user.target` | Das Setup hängt die Services an `network-online.target`, das im LXC beim Boot nicht erreicht wird. Ohne die Änderung startet der Service nach einem Neustart nicht. |

Nach einem `~/seestar_alp/raspberry_pi/update.sh` solltest du den systemd-Fix prüfen, weil das Update die Service-Dateien neu schreiben kann. Ein erneuter Aufruf mit `bash install-seestar-alp-lxc.sh --fix-services` korrigiert das.

## 3. Konfiguration

`~/seestar_alp/device/config.toml` (Service vorher stoppen):

```toml
[seestar_initialization]
lat = 48.0        # eigener Standort
long = 9.0

[[seestars]]
name = "Seestar S50"            # endgültiger Name, bestimmt HA-Device- und Entity-IDs
ip_address = "192.168.1.XX"     # feste IP des Seestar (statt seestar.local)
device_num = 1
```

Ein paar Punkte zur Config:
- Der Name `[[seestars]] name` lässt sich später nur schwer ändern. Eine Umbenennung erzeugt in HA ein neues Gerät mit neuen Entity-IDs.
- In der Beispiel-Config stehen kalifornische Koordinaten, die musst du ersetzen.
- `interop_pem` erst eintragen, wenn die Firmware ≥ 7.18 ist (siehe README).

```bash
sudo systemctl restart seestar
ss -tlnp | grep -E '5432|5555|7556'    # Web-UI, Alpaca, Imaging
```

Das Seestar muss im **Station-Modus** im Heim-WLAN hängen (nicht im eigenen Hotspot) und im selben Subnetz wie der LXC.
