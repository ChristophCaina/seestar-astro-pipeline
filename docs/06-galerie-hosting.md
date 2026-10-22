# Galerie hosten (nginx-LXC)

Die Galerie ist statisch. Gebaut wird sie im Siril-LXC, ausgeliefert vom vorhandenen nginx.

```
Siril-LXC:  gallery_build.py  →  /data/astro/web/      (schreibt)
nginx-LXC:  /srv/astro-gallery  ← Bind-Mount, read-only (liefert aus)
```

## 1. Repo im Siril-LXC

```bash
git clone https://github.com/ChristophCaina/seestar-astro-pipeline /opt/seestar-astro-pipeline
apt install -y python3-pil
```

## 2. Erst mit Beispieldaten testen

```bash
python3 /opt/seestar-astro-pipeline/gallery/build/gallery_build.py \
  --stacks /opt/seestar-astro-pipeline/gallery/sample-data/stacks \
  --out /data/astro/web
```

Später, mit echten Stacks, ruft siril-auto den Builder automatisch auf (`GALLERY_BUILD` in `/etc/siril-auto.env`). Dann zeigt `--stacks` auf `/data/astro/stacks`. Vorher den Ordner `/data/astro/web` leeren, damit die Beispielbilder verschwinden.

## 3. Web-Ordner in den nginx-LXC einbinden (Proxmox-Host)

```bash
pct set <nginx-ctid> -mp1 /<host-pfad>/astro/web,mp=/srv/astro-gallery,ro=1
pct reboot <nginx-ctid>
```

`mp1` nimmst du nur, wenn `mp0` dort schon belegt ist. Prüfen kannst du das mit `pct config <nginx-ctid> | grep ^mp`. Weil nginx nur lesen muss, reichen read-only-Rechte. Bei einem unprivilegierten LXC müssen die Dateien für UID `100000 + <uid von www-data>` lesbar sein, `chmod -R o+rX` genügt.

## 4. vhost

```bash
cp gallery/nginx/astro-gallery.conf /etc/nginx/sites-available/astro-gallery
ln -s ../sites-available/astro-gallery /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx
```

Den `server_name` (z. B. `astro.lan`) trägst du als DNS-Eintrag im Router ein. Bei der Fritzbox geht das nur über den Gerätenamen, alternativ über einen lokalen DNS wie Pi-hole oder AdGuard. Die `allow`-Regeln lassen nur das Heimnetz und Tailscale zu.

## Optional: HTTPS

Läuft der nginx schon mit einem Zertifikat (z. B. Wildcard für eine eigene Domain), ergänzt du `listen 443 ssl;` und die Zertifikatspfade wie bei den anderen vhosts.
