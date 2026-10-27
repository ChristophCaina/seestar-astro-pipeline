# meta.json – Format pro Stack

Neben jedem fertigen Stack-JPG liegt eine `<name>.meta.json`. Der Builder (`build/gallery_build.py`) sammelt sie rekursiv ein. Die Beispiele in [`sample-data/`](sample-data) zeigen echte Dateien.

```jsonc
{
  "schema": 1,
  "id": "m_31_master_ircut_siril",          // eindeutig, wird Dateiname im Web-Ordner
  "sample": false,                          // true = simulierte Beispieldaten
  "source": "siril",                        // "siril" oder "app" (nachgeholter App-Stack)
  "device": { "id": "s50", "label": "Seestar S50" },   // Aufnahmegerät, fehlt = Seestar S50
  "wide": false,                            // true ab 10° Bildbreite: dann zusätzlich "constellations"
  // "constellations": [ { "id": "Cyg", "name": "Schwan", "label": [ra, dec] | null,
  //                       "lines": [ [ [ra, dec], … ], … ] } ]   (Grad, als Großkreise verdichtet)
  "target": { "name": "M 31", "common": "Andromeda-Galaxie", "kind": "galaxy", "sort": 1031 },
  "stack": "master",                        // "master" (mehrere Nächte) oder "night"
  "filter": "IRCUT",                        // IRCUT | LP | IRCUT+LP (Kombination aller Filter, stack "master")
  "exposure_s": 10, "gain": 80,
  "nights": [ { "date": "2026-10-18", "frames": 212 } ],
  "image": "M_31_master_IRCUT_siril.jpg",   // relativ zur meta.json
  "width": 1920, "height": 1080,            // Pixelmaße des JPG
  "wcs": {
    "crval": [10.6845833, 41.2691667],      // RA/Dec (Grad) am Referenzpixel
    "crpix": [1160.8, 540.0],               // Referenzpixel, JPG-Konvention (oben links, 0-basiert)
    "cd": [[-1.6e-4, 5.7e-4], [-5.7e-4, -3.4e-4]]   // Grad/Pixel, (xi Ost, eta Nord) = CD · (dx, dy)
  },
  "objects": [
    {
      "name": "M 31", "alt": "NGC 224", "type": "Spiralgalaxie (Sb)",
      "ra": 10.684583, "dec": 41.269167,    // Grad
      "major": 190, "minor": 60, "pa": 38,  // Bogenminuten, Positionswinkel (Nord über Ost)
      "mag": 3.4, "dist": "2,5 Mio. Lj",
      "category": "dso",                    // "dso" oder "star"
      "main": true,                         // Hauptobjekt (Kachel-Hover)
      "marker": [84, 22],                   // optional: angezeigte Ellipse statt major/minor
      "label": [60, -120]                   // optional: Beschriftungsversatz in Pixeln
    }
  ],
  "caption": "Freitext unter dem Bild",
  "versions": [ { "label": "Seestar-App-Stack", "where": "Handy → Immich" } ]
}
```

## Woher die Werte kommen (siril-auto)

| Feld | Quelle |
|---|---|
| `wcs` | FITS-Header nach `platesolve` (`CRVAL`, `CRPIX`, `CD` bzw. `PC`+`CDELT`), gelesen von `siril/lib/fitsheader.py`. Umrechnung auf JPG-Konvention: `x = CRPIX1 − 1`, `y = NAXIS2 − CRPIX2`, und weil das FITS bottom-up gespeichert ist, wechselt die zweite CD-**Spalte** (Koeffizient von y) das Vorzeichen. Mit `NO_FLIP_Y=1` entfällt die Spiegelung. |
| `objects` | [OpenNGC](https://github.com/mattiaverga/OpenNGC) (Messier, NGC, IC mit Typ, Größe, Positionswinkel, Helligkeit), deutsche Namen und Entfernungen aus `siril/lib/objects_de.json`, alles über die WCS aufs Bildfeld gefiltert (`siril/lib/write_meta.py`) |
| `nights`, `exposure_s`, `filter` | aus der Gruppierung in siril-auto bzw. den FITS-Headern der Subs |

Liegt ein Objekt außerhalb des Bildes, zeigt die Galerie es automatisch als Randhinweis („M 110 · 14′ außerhalb“).
