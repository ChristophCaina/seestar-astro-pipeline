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

Für Zugriff von außen eine eigene (Sub-)Domain, z. B. `astro.example.org` (Platzhalter – eigene Domain eintragen; A-Eintrag beim DNS-Anbieter, Portweiterleitung 80/443 auf den nginx-LXC). Passwort anlegen und Zertifikat holen:

```bash
apt install -y apache2-utils
htpasswd -c /etc/nginx/.htpasswd-astro <name>        # weitere Benutzer ohne -c
nginx -t && systemctl reload nginx
certbot --nginx -d astro.example.org
```

Heimnetz und Tailscale kommen ohne Passwort rein, von außen nur mit. Ruft man die eigene Domain aus dem Heimnetz auf, schickt der Router (z. B. Fritzbox) das über die öffentliche IP zurück (NAT-Loopback) – dann fragt nginx ebenfalls nach dem Passwort; der Browser merkt es sich. `/img/` ist ohne Passwort erreichbar, damit die Vorschaubilder im HA-Dashboard und in Push-Nachrichten laden.

## Optional: HTTPS

Läuft der nginx schon mit einem Zertifikat (z. B. Wildcard für eine eigene Domain), ergänzt du `listen 443 ssl;` und die Zertifikatspfade wie bei den anderen vhosts.
