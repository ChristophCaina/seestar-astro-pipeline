# Remote-Zugriff

## Szenario A: Seestar zu Hause, ich unterwegs

Dafür reicht ein VPN ins Heimnetz. Empfohlen ist ein **Tailscale-LXC als Subnet-Router**: Er braucht keine Portfreigabe, funktioniert auch hinter CGNAT, und mit dem Repo-Script ist er in wenigen Minuten eingerichtet.

```bash
# auf dem Proxmox-Host – legt CT 140 an (Debian 13, 1 Core, 512 MB), richtet TUN ein, installiert Tailscale
curl -fsSLO https://raw.githubusercontent.com/ChristophCaina/seestar-astro-pipeline/main/scripts/install-tailscale-lxc.sh
bash install-tailscale-lxc.sh host 140 --create --hostname heimnetz
```

- Ohne `--auth-key` gibt `tailscale up` einen Anmeldelink aus. Den öffnest du im Browser.
- Danach in der [Adminkonsole](https://login.tailscale.com/admin/machines) die **Route freigeben**, z. B. `192.168.52.0/24`, und optional **Key expiry** abschalten.
- Auf Handy und Laptop die Tailscale-App installieren und mit demselben Konto anmelden. Dann sind HA, Galerie (`astro.lan`), Immich und seestar_alp von unterwegs erreichbar.
- Für die Galerie lässt der nginx-vhost den Tailscale-Bereich `100.64.0.0/10` bereits zu. Erreichst du sie über die geroutete LAN-IP, greift ohnehin die Regel für `192.168.0.0/16`.
- Das Heimnetz erkennt das Script automatisch über die Route von `eth0`, andernfalls gibst du es mit `--routes` an.

Alternativen: Headscale oder NetBird (selbst gehostet), wg-easy oder WireGuard direkt auf dem Router (Fritzbox, später UniFi-Gateway).

## Szenario B: Seestar unterwegs

Ein Handy-Hotspot allein reicht nicht, wegen Carrier-NAT, und Android routet Hotspot-Traffic nicht durchs VPN. Gelöst wird das mit einem **Reiserouter** (vorhanden: GL.iNet). Er hat Tailscale eingebaut, meldet sich im selben Tailnet an und gibt sein eigenes Subnetz frei, z. B. `192.168.8.0/24`. Damit erreichen sich Heimnetz und Reiserouter-Netz in beide Richtungen. Die Route des GL.iNet musst du in der Adminkonsole ebenfalls freigeben, und der Tailscale-LXC zu Hause braucht `--accept-routes`. Das reichst du einfach an das Script durch: `bash install-tailscale-lxc.sh container --accept-routes`. Damit Geräte im Heimnetz, z. B. der seestar_alp-LXC, das Reiserouter-Netz finden, brauchen sie außerdem eine **statische Route**: `192.168.8.0/24` über die IP des Tailscale-LXC. Die trägst du am besten im Router ein, bei der Fritzbox unter Heimnetz → Netzwerk → Netzwerkeinstellungen → Statische Routingtabelle. Alternativ setzt du sie nur im seestar_alp-LXC. Das Seestar hängt im WLAN des Reiserouters, das Handy liefert per USB-Tethering das Internet.

Offen ist noch, ob das HA-Add-on auch mit einem gerouteten Subnetz klappt. Laut Doku erwartet es dasselbe Subnetz, das muss getestet werden.

**UniFi Travel Router:** Er braucht ein UniFi-Gateway zu Hause, Teleport ist Client-to-Site. Ob Geräte im Heimnetz die Geräte hinter dem Travel Router erreichen, ist nicht dokumentiert.

## Bilder unterwegs → NAS

Unterwegs steuerst du über die Seestar-App, die Frames speichert das Gerät lokal. Wieder zu Hause im Heim-WLAN holt siril-auto sie automatisch ab. Wer nicht warten will, hat zwei Wege:

### A) Von zu Hause abholen: `travel-sync.sh` (empfohlen)

Der Siril-LXC holt die Frames über Tailscale direkt vom Seestar am Reiserouter. Vor Ort brauchst du nichts außer GL.iNet und Seestar. Voraussetzung ist Szenario B oben, inklusive statischer Route zum Reiserouter-Netz (am besten in der Fritzbox, dann gilt sie auch für den Siril-LXC).

Per Tailscale-SSH oder Proxmox-Konsole im Siril-LXC:

```bash
cd /opt/seestar-astro-pipeline/siril
./travel-sync.sh --ip 192.168.8.XX --size                       # erst schauen: Dateien, GB, Dauer
./travel-sync.sh --ip 192.168.8.XX --since 1d --bwlimit 2M --stack --background
journalctl -u seestar-travel-sync -f                            # Fortschritt
```

| Option | Wirkung |
|---|---|
| `--since 12h` / `2d` / `off` | nur Dateien jünger als … (Default `2d`) |
| `--target "M 31"` | nur ein Ziel (Ordnername wie in der App) |
| `--all` | auch die Seestar-eigenen Stacks/JPGs, nicht nur `*_sub` |
| `--bwlimit 2M` | max. 2 MB/s, schont Handy-Tarif und Hotel-WLAN |
| `--stack` | danach siril-auto starten, die Ergebnisse kommen wie gewohnt per Push |
| `--background` | läuft als systemd-Unit weiter, auch wenn die SSH-Verbindung abreißt |

- Abgebrochene Übertragungen setzen beim nächsten Aufruf fort, schon Kopiertes wird übersprungen.
- Während des Kopierens hält das Script die Sperre von siril-auto, der Timer stackt also keine halbe Nacht.
- Die IP des Seestar zeigt die GL.iNet-Oberfläche unter *Clients*. Am besten dort eine feste Zuweisung anlegen.
- **Volumen:** Ein Einzelframe des S50 hat rund 4 MB, eine Nacht mit 3 h × 10 s also gut 4 GB. Für mobile Daten lohnt `--target` oder später zu Hause holen.

### B) Vor Ort hochladen (Laptop)

Hängt der Laptop im WLAN des Reiserouters und ist Tailscale aktiv, kopiert rclone direkt von der Seestar-Freigabe auf die NAS-Freigabe zu Hause. Beispiel mit Samba-Freigabe `astro` (IP und Zugang anpassen):

```bash
rclone copy ":smb,host=192.168.8.XX,user=guest:EMMC Images/MyWorks" \
  ":smb,host=192.168.1.XX,user=<samba-user>,pass=$(rclone obscure '<passwort>'):astro/raw/seestar" \
  --include "*_sub/**" --max-age 2d --bwlimit 2M --progress
```

Danach zu Hause einmal `siril-auto.sh --no-fetch` laufen lassen, oder auf den Timer warten.
