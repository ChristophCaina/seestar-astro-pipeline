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

Ein Handy-Hotspot allein reicht nicht, wegen Carrier-NAT, und Android routet Hotspot-Traffic nicht durchs VPN. Gelöst wird das mit einem **Reiserouter** (vorhanden: GL.iNet). Er hat Tailscale eingebaut, meldet sich im selben Tailnet an und gibt sein eigenes Subnetz frei, z. B. `192.168.8.0/24`. Damit erreichen sich Heimnetz und Reiserouter-Netz in beide Richtungen. Die Route des GL.iNet musst du in der Adminkonsole ebenfalls freigeben, und der Tailscale-LXC zu Hause braucht `--accept-routes`. Das reichst du einfach an das Script durch: `bash install-tailscale-lxc.sh container --accept-routes`. Das Seestar hängt im WLAN des Reiserouters, das Handy liefert per USB-Tethering das Internet.

Offen ist noch, ob das HA-Add-on auch mit einem gerouteten Subnetz klappt. Laut Doku erwartet es dasselbe Subnetz, das muss getestet werden.

**UniFi Travel Router:** Er braucht ein UniFi-Gateway zu Hause, Teleport ist Client-to-Site. Ob Geräte im Heimnetz die Geräte hinter dem Travel Router erreichen, ist nicht dokumentiert.

## Bilder unterwegs → NAS

- Unterwegs steuerst du über die Seestar-App, die Frames speichert das Gerät lokal.
- **Zu Hause:** Das Seestar ist wieder im Heim-WLAN, siril-auto holt die Frames ab und stackt sie.
- **Von unterwegs:** Ein Gerät vor Ort kopiert von der Seestar-Freigabe und lädt per VPN auf den NAS. Das Datenvolumen ist dabei der Engpass, Einzelframes sind mehrere GB pro Nacht.
