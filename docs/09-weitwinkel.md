# Weitwinkel und Milchstraße

Die EOS 6D (astromodifiziert) mit einem Fotoobjektiv – nachgeführt auf der iEXOS-100 oder fest auf dem Stativ – liefert Milchstraßen- und Sternbildaufnahmen. Diese laufen parallel zum Seestar durch dieselbe Pipeline: Aufnahme am Astro-Pi, Stacken mit siril-auto, Beschriftung mit Sternbildern in Galerie und Immich.

```
Astro-Pi-App (Modus Weitwinkel)  →  Light_<Ziel>_<Bel.>s_WIDE_<Datum>-<Zeit>.fits
        │                                     │  Filter-Kürzel WIDE
        ▼                                     ▼
   raw/eos6d/<Ziel>_sub/  ──siril-auto──>  wide_stack.ssf  (Homographie, Vordergrund-Stack)
                                              │  astrometry.net (solve-field) statt Siril-platesolve
                                              ▼
   stacks/…_WIDE_eos6d_siril.meta.json   →  Galerie: Sternbildlinien, Namen, helle Sterne
   export/…_WIDE_eos6d_siril.jpg (+ _vordergrund.jpg, _beschriftet.jpg) → Immich
```

## Aufnahme (Astro-Pi-App)

In der App unter **Kamera → Modus: Weitwinkel** ([07](07-astro-pi.md)):

| Element | Funktion |
|---|---|
| Objektiv | Liste (14/24/35/50/135 mm, eigene in `/etc/astro-pi.toml` unter `[wide] lenses`) oder Brennweite + Blende frei |
| Rechner | Bildfeld, Bildmaßstab, maximale Belichtung **ohne Nachführung** nach NPF-Regel (einfach `(35·N + 30·p)/f` und streng nach Michaud mit Deklination), 500er-Regel zum Vergleich |
| nachgeführt | iEXOS-100 als reine Nachführung: Empfehlung 60–180 s; ohne Haken warnt die App, wenn die Belichtung über der NPF-Grenze liegt |
| Vorlagen | „Milchstraße vom Stativ“ (NPF-Belichtung, 40 × ISO 3200), „Milchstraße nachgeführt“ (30 × 120 s), „Sternbild / Sternfeld“, „Nordamerika-/Cirrusregion (H-alpha)“ |
| Zielsuche | im Weitwinkel-Modus zuerst **Sternbilder** und Himmelsregionen (Milchstraßenzentrum, Schwan, Sommerdreieck, Kassiopeia–Perseus, Wintersechseck) – GoTo auf die Mitte |
| Dateinamen | `Light_<Ziel>_<Bel.>s_WIDE_<Datum>-<Zeit>.fits` → siril-auto erkennt den Weitwinkel-Modus; Darks/Flats/Bias bleiben `DSLR` und gelten für beide Modi |
| Plate-Solving | Suchbereich aus dem Objektiv (Bildmaßstab ±20 %) |

Beispiel 6D (6,54 µm Pixel), 24 mm f/2.8, ohne Nachführung: ≈ 12 s (NPF einfach), 73° × 49° Bildfeld, 56″/px.

## Planung (Home Assistant)

Das Astro-Audit kennt den Modus **weitfeld**: Es berechnet, wie hoch Milchstraßenzentrum, Schwan, Kassiopeia–Perseus und Fuhrmann–Orion im Dunkelfenster maximal stehen, und empfiehlt Weitwinkel bei mondfreier, klarer Nacht mit einer Region ≥ 35° ([02](02-home-assistant.md)). Gestartet wird wie immer nur nach Bestätigung. Hinweis für 48,7° Nord: Das Milchstraßenzentrum steht höchstens etwa 12° hoch (Juni–August, tief im Süden) – lohnender sind Schwan (Sommer/Herbst, fast im Zenit) und Kassiopeia–Perseus (Herbst/Winter).

## Stacken (siril-auto)

Bilder, deren Dateiname das Filter-Kürzel `WIDE` trägt (`WIDE_FILTERS`), stackt siril-auto mit [`siril/wide_stack.ssf`](../siril/wide_stack.ssf):

| Schritt | Warum |
|---|---|
| Kalibrieren mit Bias/Dark/Flat des Geräts (falls vorhanden), sonst nur debayern | wie DSLR-Profil ([siril/README](../siril/README.md#dslr-canon-eos-6d)) |
| Registrieren mit `-transf=homography` | Objektivverzeichnung und große Bildfelder |
| **kein** Hintergrundabzug (`subsky`) | würde das Milchstraßenband als „Gradient“ entfernen |
| **Vordergrund-Stack** ohne Registrierung (`WIDE_FOREGROUND=1`) | Landschaft bleibt scharf; Himmel und Vordergrund fügst du in GIMP/Photoshop mit einer Maske zusammen |
| Plate-Solving mit astrometry.net auf dem Vorschau-JPG | Siril-`platesolve` scheitert oft bei sehr großen Feldern |
| kein Master über mehrere Nächte | jede Nacht ist anders gerahmt |

Ergebnisse je Nacht: `…_WIDE_<gerät>_siril.fit/.tif/.jpg`, dazu `…_vordergrund.jpg` und die beschriftete Version.

**Indexdateien:** Für Bildfelder von ca. 10–75° braucht astrometry.net die Serie 4111–4119. Im Siril-LXC: `bash install-siril-lxc.sh container --astrometry-wide` (lädt 4107–4119).

Einstellungen in `/etc/siril-auto.env`:

```bash
WIDE_FILTERS="WIDE"          # Kürzel im Dateinamen
WIDE_FOREGROUND=1            # Vordergrund-Stack zusätzlich
WIDE_SOLVE=1                 # astrometry.net
WIDE_SCALE_LOW=8             # Suchbereich Bildbreite in Grad
WIDE_SCALE_HIGH=130
WIDE_SOLVE_FLIP_Y=0          # 1, falls die Beschriftung vertikal gespiegelt ist
```

## Beschriftung

Ab 4° Bildbreite gilt ein Bild als Weitwinkel (`siril/lib/sky.py`):

- **Sternbildlinien und -namen** (deutsch), Linien als Großkreise interpoliert
- **benannte helle Sterne** (bis 3,0/3,5/4,0 mag je nach Bildfeld) mit Bayer-Bezeichnung im Info-Kärtchen
- nur große oder helle Deep-Sky-Objekte (Messier, ≥ 1°, ≤ 6 mag), mit Trivialnamen
- Labels werden so versetzt, dass sie sich nicht überlappen
- Maßstab 5° statt 10′; in der Galerie eigene Kategorie **Weitwinkel** und Schalter **Sternbilder**

Daten: [`siril/lib/sky.json`](../siril/lib/sky.json), erzeugt aus [d3-celestial](https://github.com/ofrohn/d3-celestial) (© 2015 Olaf Frohn, BSD-3-Clause, siehe `siril/lib/sky.LICENSE`). Neu erzeugen: `python3 siril/lib/make_sky.py <d3-celestial/data>`.

Die Galerie-Beispieldaten enthalten ein simuliertes Weitwinkelbild „Milchstraße im Schwan“ (`gallery/tools/make_wide_sample.py`).
