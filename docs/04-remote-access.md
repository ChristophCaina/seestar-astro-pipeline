# Remote-Zugriff

## Szenario A: Seestar zu Hause, ich unterwegs

Dafür reicht ein VPN ins Heimnetz:
- **Tailscale-LXC als Subnet-Router**: braucht keine Portfreigabe und funktioniert auch hinter CGNAT. Ein unprivilegierter LXC braucht das TUN-Device:
  ```
  # /etc/pve/lxc/<CTID>.conf
  lxc.cgroup2.devices.allow: c 10:200 rwm
  lxc.mount.entry: /dev/net/tun dev/net/tun none bind,create=file
  ```
- Alternativen: Headscale oder NetBird (selbst gehostet), wg-easy oder WireGuard direkt auf dem Router (Fritzbox, UniFi-Gateway)

## Szenario B: Seestar unterwegs

Ein Handy-Hotspot allein reicht nicht, wegen Carrier-NAT, und Android routet Hotspot-Traffic nicht durchs VPN. Gelöst wird das mit einem **Reiserouter** (z. B. GL.iNet), der per Tailscale oder WireGuard einen Tunnel nach Hause aufbaut und sein Subnetz freigibt. Das Seestar hängt im WLAN des Reiserouters, das Handy liefert per USB-Tethering das Internet.

Offen ist noch, ob das HA-Add-on auch mit einem gerouteten Subnetz klappt. Laut Doku erwartet es dasselbe Subnetz, das muss getestet werden.

**UniFi Travel Router:** Er braucht ein UniFi-Gateway zu Hause, Teleport ist Client-to-Site. Ob Geräte im Heimnetz die Geräte hinter dem Travel Router erreichen, ist nicht dokumentiert.

## Bilder unterwegs → NAS

- Unterwegs steuerst du über die Seestar-App, die Frames speichert das Gerät lokal.
- **Zu Hause:** Das Seestar ist wieder im Heim-WLAN, siril-auto holt die Frames ab und stackt sie.
- **Von unterwegs:** Ein Gerät vor Ort kopiert von der Seestar-Freigabe und lädt per VPN auf den NAS. Das Datenvolumen ist dabei der Engpass, Einzelframes sind mehrere GB pro Nacht.
