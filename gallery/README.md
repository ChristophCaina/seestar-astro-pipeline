# Astro-Galerie

Eine statische Website aus HTML, JS, JSON und JPGs, ohne Datenbank oder Backend.

```
gallery/
├── site/          Frontend (index.html, app.js, styles.css)
├── build/         gallery_build.py + wcs.py  → baut den Web-Ordner
├── sample-data/   6 Beispiel-Stacks (Bilder simuliert, Objektdaten/WCS real)
├── tools/         Erzeugung der Beispieldaten
├── nginx/         vhost-Beispiel
├── SCHEMA.md      Format der meta.json
└── prototype.html ursprünglicher Einzeldatei-Prototyp
```

## Lokal ausprobieren

```bash
python3 gallery/build/gallery_build.py --stacks gallery/sample-data/stacks --out /tmp/astro-web
cd /tmp/astro-web && python3 -m http.server 8080      # → http://localhost:8080
```

## Funktionen

- **Übersicht** mit Kennzahlen, Beobachtungskalender (Filter und berechnete Mondphasen), Filter nach Objekttyp und Sortierung. Pro Objekt gibt es eine Kachel. Titelbild ist die Kombination aller Filter, sonst der Master mit der längsten Belichtung, sonst die neueste Nacht.
- **Detailseite** mit Bild und SVG-Overlay, das beim Mouseover oder Tippen erscheint:
  - Infokarten zu den Objekten
  - RA/Dec-Gitter, berechnet aus der WCS
  - Kompass und Maßstab
  - Randhinweise für Objekte außerhalb des Bildfelds
- **Versionen:** zwischen Kombination, Master je Filter und Einzelnächten umschalten
- **Rotlicht-Modus** für die Nachtsicht

Hosting: [`docs/06-galerie-hosting.md`](../docs/06-galerie-hosting.md)
